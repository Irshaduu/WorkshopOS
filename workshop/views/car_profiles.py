from decimal import Decimal

from django.shortcuts import render
from django.http import Http404
from django.db.models import Count, Max, Q, Sum, F, DecimalField
from django.db.models.functions import Coalesce, Greatest, TruncDate
from django.core.paginator import Paginator

from ..analysis_engine import MONEY, SPARE_COST
from ..models import JobCard, JobCardSpareItem
from ..decorators import office_required, is_owner


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

    # Revenue, defined exactly as `analysis_engine` defines it: a discount is
    # money never earned, not an expense. Any other sum here would put a second
    # definition of "what this customer has paid us" in the app, and it would be
    # the one an owner quotes at the counter.
    money = all_visits.aggregate(
        visits=Count('id'),
        billed=Coalesce(
            Sum(F('total_bill_amount') - F('discount_amount'),
                output_field=DecimalField(max_digits=14, decimal_places=2)),
            ZERO, output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
        outstanding=Coalesce(
            Sum(F('total_bill_amount') - F('discount_amount') - F('received_amount'),
                output_field=DecimalField(max_digits=14, decimal_places=2),
                filter=Q(payment_status__in=('PENDING', 'PARTIAL'))),
            ZERO, output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
        last_visit=Max('admitted_date'),
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
        # total from page 1.
        totals = _parts_cost(JobCardSpareItem.objects.filter(job_card__in=all_visits))
        car_profit, car_profit_pct = _gross_profit(money['billed'], totals['cost'])
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
        'billed': money['billed'],
        'outstanding': money['outstanding'],
        'last_visit': money['last_visit'],
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
