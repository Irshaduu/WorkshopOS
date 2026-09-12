from decimal import Decimal

from django.shortcuts import redirect, render
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.db.models import Count, Max, Prefetch, Q, Sum, F, DecimalField
from django.db.models.functions import Coalesce, Greatest, TruncDate
from django.core.paginator import Paginator

from ..analysis_engine import MONEY, SPARE_COST
from ..models import (
    JobCard, JobCardConcern, JobCardLabourItem, JobCardSpareItem,
)
from ..decorators import office_required, is_owner
from ..return_to import safe_return
from ..mileage import parse_km
from ..invoice import build_invoice, document_title
from ..service_history import build_service_history, current_km_problem
# How long a car was here, as one ready phrase. Imported rather than restated:
# the read-only job card already prints this figure, and the two screens are
# opened one after the other on the same card — a history row saying "3 days"
# over a card saying "2 days" is the app disagreeing with itself about the one
# thing this row was added to answer. It also carries the four edge cases a
# second copy would get wrong: a card with no admitted date, a completion dated
# before the admission, the singular, and an OPEN card counting to
# `localdate()` rather than to a UTC "today".
from .jobcard import _time_in_workshop


ZERO = Decimal('0')

# One page of a car's own history. Same 45 as every other list view in the app —
# a car with 60 visits is rare but real (a fleet vehicle), and rendering all of
# them costs an owner on a phone the whole page.
VISITS_PER_PAGE = 45


def _parts_cost(spares_qs):
    """
    What the parts on these job cards cost the workshop.

    `SPARE_COST` is imported from `analysis_engine`, not restated — it is the
    app's one definition of what a spare cost (`unit_price x quantity`, a
    missing price counting as ₹0 and a missing quantity as 1), shared with the
    Profit page and `SpareShop.update_totals()`. A second copy here would be a
    second answer to "what did this part cost", and the two would be free to
    disagree on the screen an owner reads to judge a customer.

    **Both routes are counted, and that is NOT the double-count rule being
    broken.** That rule governs the workshop-wide Profit page, where a warehouse
    draw must never be charged again because a Supplies Shop restock bill
    already paid for it. Here the question is a different one — what did THIS
    car cost us — and a part that came off the shelf cost exactly what the
    shelf paid for it. Nothing is being added to a total that already contains
    the restock bills.
    """
    return spares_qs.aggregate(
        cost=Coalesce(Sum(SPARE_COST, output_field=MONEY), ZERO, output_field=MONEY),
        # Parts whose cost is genuinely unknown. `SPARE_COST` counts a NULL
        # `unit_price` as ₹0, so an uncosted part reads as FREE and inflates the
        # gross profit silently — the one way this figure can be wrong without
        # looking wrong. Counted so the screen can say so.
        uncosted=Count('id', filter=Q(unit_price__isnull=True)),
    )


def _gross_profit(revenue, cost):
    """
    Revenue minus what the parts cost — and what share of the bill that is.

    This is GROSS profit and the name is load-bearing. It is the labour charge
    (which carries no direct cost of its own) plus the margin on both part
    routes, and it is **before wages, rent, power and every other overhead**,
    because this workshop attributes none of those to a car: labour is quoted
    whole with no hours recorded, so there is nothing to apportion by.

    Measured against the current data it runs about 13 points above the
    workshop's real margin, and that gap widens as payroll grows. The Profit
    page (`analysis_engine.build_profit_report`) is the one true profit figure
    in this app; this one answers a narrower question — was this car's work
    priced well — and must never be labelled as though it answered the other.
    """
    profit = revenue - cost
    share = (profit / revenue * 100) if revenue > 0 else None
    return profit, share


@office_required
def car_profile_list(request):
    """Show all unique cars (grouped by registration) with optimized queries and AJAX search."""
    # 1. Base Query: one row per registration, ordered by the car's most
    #    recent ACTIVITY — not by when it was last admitted.
    #
    #    It ordered on `Max(admitted_date)` alone, so a car admitted in June,
    #    finished in July and settled in August sat below one admitted in July
    #    and still untouched since. Everything that happens to a car after it
    #    arrives — being completed, being settled — is activity, and the list an
    #    owner opens to find "the car we were just dealing with" has to say so.
    #
    #    ⚠ EVERY ARGUMENT TO `Greatest` IS COALESCED, and that is a
    #    cross-database correctness matter rather than tidiness. On PostgreSQL
    #    `GREATEST` ignores NULLs and returns the largest non-null; on SQLite —
    #    which is what the test suite runs on — it returns NULL if ANY argument
    #    is null. A car with no completed_date would therefore sort correctly in
    #    production and vanish to the bottom under test, or the reverse.
    #    `admitted_date` is non-null on every card, so it is the floor.
    #
    #    ⚠ `TruncDate`, never `Cast(... DateField)`, for `paid_date`. It is the
    #    one DateTimeField of the three and it is stored UTC; casting takes the
    #    UTC calendar day, which for anything settled after 18:30 IST is
    #    yesterday. TruncDate converts to TIME_ZONE first, the same thing a
    #    `__date` lookup does.
    #
    #    `-latest_id` breaks ties. Most cars share a date with several others,
    #    and without it the order inside a day is whatever the database happens
    #    to return — which differs between PostgreSQL and SQLite, so the list
    #    would not even be stable between production and the tests. Same lesson
    #    the Completed list learned.
    cars_query = JobCard.objects.values('registration_number').annotate(
        total_visits=Count('id'),
        last_activity=Greatest(
            Max('admitted_date'),
            Coalesce(Max('completed_date'), Max('admitted_date')),
            Coalesce(Max(TruncDate('paid_date')), Max('admitted_date')),
        ),
        latest_id=Max('id')
    ).order_by('-last_activity', '-latest_id')

    # 2. The search term, read from the URL on EVERY request — not only on the
    #    AJAX one.
    #
    #    It used to be `... if is_ajax else ''`, "Smart Reset: clear on full
    #    refresh", and that quietly broke paging a search. The pager renders
    #    ordinary `<a href="?page=2&q=KL07">` links, so following one is a FULL
    #    page load — which cleared `q` and returned page 2 of every car in the
    #    workshop under a heading that still said the search term. Wrong results
    #    that look like results.
    #
    #    The intent behind the reset survives untouched: opening /cars/ with no
    #    query string still shows everything, because there is no `q` to read.
    #    Only an explicit `?q=` is now honoured, which is what `completed_list`
    #    has always done.
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    q = request.GET.get('q', '').strip()

    # 3. Apply Multi-Field Search (Database Level)
    if q:
        for word in q.split():
            cars_query = cars_query.filter(
                Q(registration_number__icontains=word) |
                Q(customer_name__icontains=word) |
                Q(brand_name__icontains=word) |
                Q(model_name__icontains=word)
            )

    # 4. Pagination (Pro-Active Scaling)
    paginator = Paginator(cars_query, 45)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # 5. Fetch Full Details for the current page only (N+1 Resolution)
    # We get the full JobCard objects for the latest_ids on this page
    latest_ids = [car['latest_id'] for car in page_obj]
    
    # Materialize the data into a list of dicts for the template
    # (Using a dict for fast lookup)
    details_map = {
        jc.id: jc for jc in JobCard.objects.filter(id__in=latest_ids)
    }
    
    car_profiles = []
    for car in page_obj:
        jc = details_map.get(car['latest_id'])
        if jc:
            car_profiles.append({
                'registration': car['registration_number'],
                'brand': jc.brand_name,
                'model': jc.model_name,
                'customer': jc.customer_name,
                'total_visits': car['total_visits'],
                # The card prints what the list is SORTED by. Printing the
                # admitted date beside an activity ordering would put the dates
                # on screen out of order, which reads as a broken list rather
                # than as two different facts.
                'last_activity': car['last_activity'],
                'color_hex': jc.get_car_color_hex,
                'color_name': jc.get_car_color_display,
                # The two exceptions the colour wash has to know about, exactly
                # as `live_report` handles them: a WHITE car's rail would vanish
                # against the card, and a car with NO colour recorded gets no
                # wash at all — a slate tint would read as "this car is grey",
                # which is a different fact from "nobody wrote it down".
                'has_color': bool(jc.car_color),
                'is_white': jc.car_color == 'White',
                # Whether this car is in the workshop RIGHT NOW. Only one job
                # card per registration can be active at a time (the hard block
                # in `get_active_conflict`), and `latest_id` is that card when
                # there is one, so this needs no extra query. It is the single
                # most useful thing a list of cars can tell you — "is this one
                # of the cars I am looking after today?" — and it was not on the
                # page at all.
                'on_floor': (not jc.completed) and (not jc.is_deleted),
            })

    context = {
        'car_profiles': car_profiles,
        'page_obj': page_obj,
        'q': q,
    }

    # AJAX Search: Return only the partial template
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return render(request, 'workshop/car_profiles/car_list_partial.html', context)
    
    return render(request, 'workshop/car_profiles/car_profile_list.html', context)


@office_required
def car_profile_detail(request, registration):
    """
    One car's whole history: who owns it, what it has cost, and every visit.

    Three things this view has to get right, and none of them were:

    * **It must not load the car's entire history at once.** It listed every
      job card with no pagination. Most cars have two or three, but a fleet
      vehicle in this workshop's own data has dozens, and an owner opens this on
      a phone.
    * **It must not ask the template for related data.** It printed the first
      concern per row via `bill.concerns.first` with `bill.concerns.count`
      beside it — two queries per row, on an unpaginated list. The concern line
      is gone from the row entirely now (it was the only free-text line there,
      it made every row a different height, and a history is scanned for *when*
      and *how much*), so the relation is not touched at all.
    * **The summary figures come from the DATABASE, not from the page.** The
      list is paginated, so anything totalled from `bills` would silently start
      describing "this page" while being labelled "this car" — the same reason
      the Cashbook's totals are a separate aggregate from its rows.
    """
    all_visits = JobCard.objects.filter(registration_number=registration)

    # ⚠ THE MONEY COUNTS COMPLETED VISITS ONLY, and every word it shares with
    # the service history sheet names the sheet's own figure (2026-09-11, the
    # owners' structure):
    #
    #     Total billed − Discount = Paid + Still owed
    #
    # "Total billed" used to be `total_bill_amount − discount_amount` over
    # EVERY visit, while the sheet — a button on this page — prints TOTAL
    # BILLED for the invoices' own totals over completed visits. One word, two
    # figures, seconds apart. The sheet's NET TOTAL is Paid + Still owed here.
    #
    # A car ON THE FLOOR is in none of them: its bill is not final, which is
    # why the sheet and Pending Bills leave it out too. It gets its own figure,
    # `on_floor_so_far`, added to nothing.
    #
    # The discount counts positive rows only — the floor the sheet applies to
    # each visit's discount, so the two can never print different DISCOUNTs.
    done = Q(completed=True, is_deleted=False)
    total_field = DecimalField(max_digits=14, decimal_places=2)
    money = all_visits.aggregate(
        visits=Count('id'),
        billed=Coalesce(Sum('total_bill_amount', filter=done),
                        ZERO, output_field=total_field),
        discount=Coalesce(Sum('discount_amount', filter=done & Q(discount_amount__gt=0)),
                          ZERO, output_field=total_field),
        paid=Coalesce(Sum('received_amount', filter=done),
                      ZERO, output_field=total_field),
        outstanding=Coalesce(
            Sum(F('total_bill_amount') - F('discount_amount') - F('received_amount'),
                output_field=total_field,
                filter=done & Q(payment_status__in=('PENDING', 'PARTIAL'))),
            ZERO, output_field=total_field,
        ),
        on_floor_so_far=Coalesce(
            Sum('total_bill_amount', filter=Q(completed=False, is_deleted=False)),
            ZERO, output_field=total_field,
        ),
    )

    if not money['visits']:
        raise Http404("Car not found")

    bills = (
        all_visits
        .select_related('lead_mechanic', 'bulk_payer')
        .order_by('-admitted_date', '-pk')
    )

    paginator = Paginator(bills, VISITS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get('page'))
    visits = list(page_obj.object_list)

    # Visit numbers are chronological across the WHOLE history (1 = oldest), so
    # they must be derived from the page's offset rather than from its own
    # length — otherwise page 2 would start counting at 1 again and two
    # different visits would carry the same number.
    total_visits = money['visits']
    offset = (page_obj.number - 1) * VISITS_PER_PAGE
    for index, bill in enumerate(visits):
        bill.visit_number = total_visits - offset - index

        # How long the car was here on that visit — the gap between admitted
        # and completed, which is the question this list could not answer.
        # Every other fact on a row is a single stored value; this one is a
        # subtraction, so it is the one an owner had to do in their head across
        # two dates only one of which was ever printed.
        #
        # Attached in PYTHON, never as a database annotation: SQLite (tests)
        # and PostgreSQL (everything else) do not agree on date arithmetic,
        # and this is 45 rows at most. It costs no query either — both columns
        # are already on the row.
        #
        # An open card counts to today and says so ("12 days in"), which is the
        # case where the number is actually changing. `None` where the
        # arithmetic would be nonsense, and the row then prints nothing rather
        # than a negative.
        bill.span = _time_in_workshop(bill)

    # ---- gross profit, OWNER ONLY -------------------------------------
    #
    # Not merely hidden from Office in the template: not computed at all, so
    # the two aggregates below are never run for them. Same shape as the Live
    # Report's operations board, and for the same reason — this is the only
    # place in the app where a per-car cost figure appears, and Office is shown
    # the workshop's own cost side nowhere else.
    show_profit = is_owner(request.user)
    if show_profit:
        # One row per visit on this page, so each card can show its own figure.
        per_card = dict(
            JobCardSpareItem.objects
            .filter(job_card_id__in=[bill.pk for bill in visits])
            .values('job_card_id')
            .annotate(cost=Coalesce(Sum(SPARE_COST, output_field=MONEY),
                                    ZERO, output_field=MONEY))
            .values_list('job_card_id', 'cost')
        )
        for bill in visits:
            revenue = (bill.total_bill_amount or ZERO) - (bill.discount_amount or ZERO)
            bill.gross_profit, bill.gross_profit_pct = _gross_profit(
                revenue, per_card.get(bill.pk, ZERO))

        # The headline is over the WHOLE history, not this page — the same rule
        # as every other figure in the hero. Summed in the database so it can
        # never drift from the rows, and so page 2 does not report a different
        # total from page 1. And over COMPLETED visits only, like the money
        # tiles beside it, so revenue and parts cost are cut from the same cards.
        totals = _parts_cost(
            JobCardSpareItem.objects.filter(job_card__in=all_visits.filter(done)))
        car_profit, car_profit_pct = _gross_profit(
            money['billed'] - money['discount'], totals['cost'])
        uncosted_parts = totals['uncosted']
    else:
        car_profit = car_profit_pct = None
        uncosted_parts = 0

    # Always the NEWEST card, whatever page is being read. The hero describes
    # the car as it is now — its colour, its owner, whether it is on the floor —
    # and on page 2 `object_list[0]` is an older visit whose owner name and
    # mileage may since have changed.
    latest = (page_obj.object_list[0]
              if page_obj.number == 1 and page_obj.object_list
              else bills.first())

    car_info = {
        'registration': registration,
        'brand': latest.brand_name,
        'model': latest.model_name,
        'customer': latest.customer_name,
        'contact': latest.customer_contact,
        # The colour is the rail down the left edge of the hero plus a wash
        # across it, and is deliberately not ALSO spelled out as a chip — "Red"
        # printed beside a red bar is the same fact twice. `has_color` /
        # `is_white` carry the two exceptions the wash needs; see the list view.
        'color_hex': latest.get_car_color_hex,
        'has_color': bool(latest.car_color),
        'is_white': latest.car_color == 'White',
        'mileage': latest.mileage,
        # Only one job card per registration can be active at a time, and the
        # newest is it when there is one.
        'on_floor': (not latest.completed) and (not latest.is_deleted),
        'visits': total_visits,
        # Completed visits only — see the aggregate above.
        'billed': money['billed'],
        'discount': money['discount'],
        'paid': money['paid'],
        'outstanding': money['outstanding'],
        'on_floor_so_far': money['on_floor_so_far'],
        # None for anyone but an owner, so the template gates on the value
        # itself and there is no second role check to fall out of step.
        'gross_profit': car_profit,
        'gross_profit_pct': car_profit_pct,
        'uncosted_parts': uncosted_parts,
    }

    return render(request, 'workshop/car_profiles/car_profile_detail.html', {
        'car_info': car_info,
        'bills': visits,
        'page_obj': page_obj,
        'show_profit': show_profit,
    })


# =====================================================================
# SERVICE HISTORY — the third customer document
# =====================================================================
#
# TWO views for one document, and the split is the feature rather than
# plumbing. The button on the car profile opens the OPTIONS page, which asks
# the two questions only a person can answer — what should be on this copy, and
# what is the car showing now — and only then opens the SHEET.
#
# The second question is the one worth having a page for. A customer rings up
# asking for their history; the office asks "what is it reading now?" and types
# it in, and every part currently on the car can then say how far it has run.
# Without it those figures stop at the last visit, which is the one reading the
# customer already knows.
#
# ⚠ THE ANSWER IS NEVER STORED. It is one person's word on one day, the
# workshop did not measure it, and writing it to `JobCard.mileage` would put an
# unverified figure into the column every other screen reads and every future
# interval is computed from. It rides in the query string and leaves with the
# page.


def _history_cards(registration):
    """
    Every job card for one registration, with what the document reads.

    The prefetches are not an optimisation, they are what makes the sheet
    affordable: it prints EVERY visit with EVERY part, concern and job line on
    it, so a fleet car with forty visits would otherwise cost a hundred and
    twenty queries. `item__category` is selected because a warehouse draw is
    named on a customer document by its category, never by the branded SKU.

    ⚠ Every card is returned, not a filtered list. Which visits count is
    `build_service_history`'s decision — completed only, deleted excluded — and
    it also has to COUNT the ones it leaves out, so a customer whose car is in
    the workshop today is told so rather than reading a history that silently
    omits it.
    """
    cards = list(
        JobCard.objects
        .filter(registration_number=registration)
        .prefetch_related(
            # All three ordered by pk — insertion order, so a visit lists its
            # concerns, work and parts the way they were typed. None of these
            # models declares a default ordering, so without this one visit
            # could read two ways on two different days.
            Prefetch('labours', queryset=JobCardLabourItem.objects.order_by('pk')),
            Prefetch('concerns', queryset=JobCardConcern.objects.order_by('pk')),
            Prefetch(
                'spares',
                queryset=JobCardSpareItem.objects
                .select_related('item__category').order_by('pk'),
            ),
        )
    )
    # The same 404 the profile page gives — a registration with no cards at all
    # is not a car this workshop knows. A car whose only visit is still in
    # progress is a different thing and renders normally, with the sheet saying
    # so.
    if not cards:
        raise Http404("Car not found")
    return cards


#: What the sheet can be asked to include. PARTS are not on this list because
#: they are the document — everything else is a choice about who this copy is
#: for. The keys are the query parameters, so one list drives the tick boxes,
#: the redirect and the sheet, and a fourth option cannot be added to two of
#: the three.
#:
#: ⚠ EACH LABEL IS THE WORD THE SHEET PRINTS for the block it switches —
#: AMOUNT, WORK DONE, REPORTED. They read "Job Performed" and "Customer
#: Concerns" (the job card's own section names) while the sheet said something
#: else, so a tick named one thing turned on a block called another. There is
#: no hint line under each any more: every one restated its own label.
HISTORY_OPTIONS = (
    ('amount', 'Amount'),
    ('work', 'Work done'),
    ('concerns', 'What was reported'),
)


@office_required
def car_service_history(request, registration):
    """
    Choose what goes on the sheet, then open it.

    A plain GET form rather than a POST: nothing here changes anything, and the
    sheet has to stay a bookmarkable, re-printable URL.

    ⚠ `go` MARKS A SUBMISSION, AND WITHOUT IT THE TICK BOXES CANNOT WORK. An
    unticked checkbox sends nothing at all, so "the user unticked Amount" and
    "the page has just opened" are the identical payload — an empty one. The
    marker is what lets the first load default every box to ticked while a
    submission is read literally.

    ⚠ **`edit` IS A SECOND MARKER AND IT IS NOT THE SAME QUESTION.** Reading
    the ticks literally and LEAVING for the sheet are two different decisions,
    and collapsing them into one flag broke the sheet's "Change" button
    outright. That link has to carry the current choices or changing one tick
    would mean setting all of them again — so it carried `go`, this view read
    that as a submission, and it redirected straight back to the sheet the
    person had just left. One 302, nothing on screen, a button that looked
    dead. `edit` says *read literally and stop here*; only `go` says *leave*.
    """
    cards = _history_cards(registration)
    history = build_service_history(cards)
    summary = history['summary']

    submitted = 'go' in request.GET
    ticks = {
        key: (bool(request.GET.get(key)) if submitted or 'edit' in request.GET
              else True)
        for key, _label in HISTORY_OPTIONS
    }

    typed_km = (request.GET.get('km') or '').strip()
    current_km = parse_km(typed_km) if typed_km else None
    if typed_km and current_km is None:
        km_error = (
            "That is not a reading this can use. Enter the kilometres on the "
            "odometer, like 130000."
        )
    else:
        # The one rule about a typed reading, read from the module that also
        # enforces it on the sheet — so the message shown here and the refusal
        # there can never come to mean different things.
        km_error = current_km_problem(current_km, summary.latest_reading)

    # Where the SHEET's own Back should point once this form has been
    # submitted. The sheet already sends it along with the ticks, and it was
    # being dropped here — so opening the sheet from anywhere but the car
    # profile, pressing Change and submitting quietly moved its exit. Already
    # through `safe_return`, so what travels is the validated form.
    back_url = safe_return(request)

    if submitted and not km_error:
        chosen = {key: '1' for key in ticks if ticks[key]}
        if current_km is not None:
            chosen['km'] = current_km
        if back_url:
            chosen['back'] = back_url
        target = reverse('car_service_history_sheet', args=[registration])
        query = urlencode(chosen)
        return redirect(f'{target}?{query}' if query else target)

    return render(request, 'workshop/car_profiles/service_history_options.html', {
        'car': max(cards, key=lambda card: (card.admitted_date, card.pk)),
        'registration': registration,
        'summary': summary,
        # Resolved here rather than looked up in the template. Django has no
        # dictionary lookup by variable key, and adding a filter for one screen
        # would be a new piece of app-wide machinery to carry a boolean.
        'options': [
            {'key': key, 'label': label, 'checked': ticks[key]}
            for key, label in HISTORY_OPTIONS
        ],
        'typed_km': typed_km,
        'km_error': km_error,
        'profile_url': reverse('car_profile_detail', args=[registration]),
        # Rendered as a hidden field so the GET form carries it forward. This
        # page's own way out stays the car profile whatever it holds — that is
        # where the button is — so it only travels.
        'back_url': back_url,
    })


@office_required
def car_service_history_sheet(request, registration):
    """
    The printable sheet.

    All the arithmetic and every naming decision live in
    `workshop/service_history.py`; this resolves the records and renders, the
    same division of labour `invoice_view` follows. If a figure looks wrong,
    that module is where it is decided.
    """
    cards = _history_cards(registration)

    # A hand-edited URL reaches here without passing the options page, so the
    # reading is parsed here too. `build_service_history` DROPS one that cannot
    # be true rather than clamping it — a single bad figure would otherwise
    # poison every RUNNING row on the page at once.
    current_km = parse_km(request.GET.get('km') or '')
    context = build_service_history(cards, current_km=current_km)

    newest = max(cards, key=lambda card: (card.admitted_date, card.pk))

    # Where Back goes. Resolved to a single value HERE rather than left to the
    # template to choose between two, because a document must never be able to
    # render with no way out at all. The fallback is the car's own profile,
    # deliberately not Home: the invoice falls back to Home because it is
    # reached from half a dozen screens and has no single parent, while this
    # one describes exactly one car.
    profile_url = reverse('car_profile_detail', args=[registration])
    back_url = safe_return(request) or profile_url

    # Back to the OPTIONS page carrying what is already chosen, so changing one
    # tick does not mean setting all four again.
    #
    # ⚠ REBUILT FROM THE PARAMETERS THIS VIEW RECOGNISES — never by echoing
    # `QUERY_STRING`. That was the first version and it put ANY parameter
    # somebody appended to the URL straight into an href on a page about to be
    # handed to a customer: `?back=https://evil.example` came through untouched
    # and rendered as a link on Formula D's own letterhead. The `back` value is
    # the one thing here that has already been through `safe_return`, so it is
    # the validated form that travels, and nothing else does.
    carried = {
        key: request.GET[key]
        for key in ('amount', 'work', 'concerns', 'km')
        if request.GET.get(key)
    }
    # `edit`, never `go` — see `car_service_history`. `go` means "this form was
    # submitted, open the sheet", so sending it from here bounced the person
    # straight back to the page they were trying to leave.
    carried['edit'] = '1'
    if back_url != profile_url:
        carried['back'] = back_url
    options_url = (
        f"{reverse('car_service_history', args=[registration])}"
        f"?{urlencode(carried)}"
    )

    context.update({
        'car': newest,
        'registration': registration,
        'back_url': back_url,
        'options_url': options_url,
        # `timezone.localdate()`, never `date.today()` — the server can run in
        # UTC while the workshop is on IST, so a sheet printed late on a Kerala
        # evening would otherwise be issued "tomorrow".
        'issued': timezone.localdate(),
        # Presentation only; every figure is computed either way. An unticked
        # box means this copy is not for whoever would read that column.
        'show_amount': bool(request.GET.get('amount')),
        'show_work': bool(request.GET.get('work')),
        'show_concerns': bool(request.GET.get('concerns')),
        # Whether the footnote explaining the '*' mark is needed at all. A
        # legend for a mark that appears nowhere on the page is the same defect
        # as a door somebody can see and cannot open.
        'has_flagged_reading': any(
            visit.rate_implausible for visit in context['visits']
        ),
    })
    return render(request, 'workshop/car_profiles/service_history_print.html', context)


@office_required
def car_all_invoices(request, registration):
    """
    Every bill for one car, one per page, as a single PDF.

    The second half of what the service history is for. That sheet SUMMARISES
    the visits; this hands over the bills themselves — which is the other thing
    a customer asks for, and which today means opening each job card, printing
    it, and sending them one at a time.

    ⚠ **IT IS THE SAME BILL, NOT A COPY THAT LOOKS LIKE ONE.** Both the
    arithmetic and the markup are shared: `build_invoice` per card, rendered
    through `includes/_invoice_sheet.html`, which `invoice_view` renders too. A
    customer holding this PDF and the paper invoice they were handed last year
    must find them identical to the millimetre, and the only way to promise
    that is for there to be one implementation.

    NEWEST FIRST, matching the service history. One vocabulary: an owner
    opening both documents for the same car in one sitting should not have to
    work out that they run in opposite directions.

    Completed visits only, again matching the service history — a car still on
    the floor has a total that is not final, so its bill is not a bill yet.
    """
    cards = [
        card for card in _history_cards(registration)
        if card.completed and not card.is_deleted
    ]
    cards.sort(key=lambda card: (card.admitted_date, card.pk), reverse=True)

    newest = max(
        _history_cards(registration),
        key=lambda card: (card.admitted_date, card.pk),
    )
    profile_url = reverse('car_profile_detail', args=[registration])

    return render(request, 'workshop/car_profiles/all_invoices_print.html', {
        # One entry per bill. `build_invoice` is called per card and its result
        # handed to the shared partial, so every sheet here is built by exactly
        # the code that builds the single invoice.
        'sheets': [{'doc': build_invoice(card), 'jobcard': card} for card in cards],
        'car': newest,
        'registration': registration,
        'count': len(cards),
        # Named like the invoices it contains, so it files beside them:
        #     Audi A4 KL11 AJ 2266 (JB-26-037).pdf
        #     Audi A4 KL11 AJ 2266 (All Invoices).pdf
        'document_title': document_title(newest, 'All Invoices', 'All Invoices'),
        # A document must never render with no way out at all — see the sheet
        # view. Same fallback: this one describes exactly one car.
        'back_url': safe_return(request) or profile_url,
        'issued': timezone.localdate(),
    })
