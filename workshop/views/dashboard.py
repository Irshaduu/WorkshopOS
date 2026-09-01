from django.utils import timezone

from django.shortcuts import render
from django.db.models import Count, Exists, OuterRef, Q, F, Value
from django.db.models.functions import Coalesce, Trim
from django.core.paginator import Paginator

from ..models import (
    JobCard, JobCardConcern, JobCardLabourItem, JobCardSpareItem,
)
from ..decorators import office_required, staff_required
from ..settlement import unfilled


@staff_required
def home(request):
    """
    Dashboard homepage showing all active job cards.
    Completion date is a planning field, not a filter.
    Cars only move to Completed when the "Completed" button is clicked.
    """
    # Get only non-completed job cards (where completed=False)
    # Optimized with select_related and prefetch_related for 1M+ records
    active_jobcards = JobCard.objects.filter(
        completed=False, is_deleted=False
    ).select_related('lead_mechanic').prefetch_related(
        'concerns', 'labours', 'spares', 'spares__item', 'spares__shop'
    ).annotate(
        total_concerns=Count('concerns'),
        fixed_concerns=Count('concerns', filter=Q(concerns__status='FIXED'))
    ).order_by('-updated_at', '-pk')

    # Count completed today (Active only) — timezone.localdate() is IST-aware
    completed_count = JobCard.objects.filter(
        completed=True,
        is_deleted=False,
        completed_date=timezone.localdate()
    ).count()

    # Count pending bills (Completed but not fully paid, Active only)
    pending_bills_count = JobCard.objects.filter(
        is_deleted=False,
        payment_status__in=['PENDING', 'PARTIAL']
    ).count()
    
    # 5. Pagination for Floor (45 items per page)
    paginator = Paginator(active_jobcards, 45)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    today = timezone.localdate()
    _attach_home_live_details(page_obj.object_list, today)
    
    return render(request, 'workshop/dashboard/dashboard_home.html', {
        'active_jobcards': page_obj, # Pass page_obj as active_jobcards
        'completed_count': completed_count,
        'pending_bills_count': pending_bills_count,
        'page_obj': page_obj,
        'today': today,  # IST-aware — respects TIME_ZONE = 'Asia/Kolkata'
    })


#: Rows shown per section inside one card's drawer before the rest are summed
#: into a "+N more" line. Chosen at 25 on the owner's instruction — this page is
#: for taking in the whole floor at a glance, so it is deliberately generous
#: where the Live Report's own `UNFILLED_ROW_CAP` is 8.
#:
#: A cap is needed at all because a rebuild in the live data carries 91 spares,
#: and 45 cards on a page multiply whatever one card costs. It is safe HERE and
#: would not be on a money list: no total sits above these rows for the hidden
#: ones to fall out of, the exact number left is printed rather than implied,
#: the heading still reports the true count so the two add back up, and every
#: hidden row is on the job card the card already opens.
HOME_SECTION_ROW_CAP = 25


def _capped(rows, cap):
    """`(rows_to_show, how_many_were_left_out)` — ONE implementation, two callers.

    The home board and the Live Report cap at different numbers (25 and 10) for
    different reasons, but they cap the same way. Two functions doing this were
    written and the second silently shadowed the first — the home board took the
    Live Report's 10 while every comment said 25, which nothing on the page
    would have shown because the remainder line was still arithmetically
    correct. Hence one function and an explicit cap at each call site.

    Never `|slice:":25"` in a template: a cap in the markup and a remainder
    computed from a constant are two versions of one rule, free to disagree,
    and they would disagree as a "+3 more" beside twenty-six visible rows.
    """
    return rows[:cap], max(0, len(rows) - cap)


def _attach_home_live_details(jobs, today):
    """Attach the age label and the four live-detail sections to each card.

    Read off the prefetched relations rather than re-queried per card — the
    queryset prefetches concerns, labours and spares (with their item and shop),
    so this loop costs no further queries however many cards are on the page.
    """
    for job in jobs:
        days = (today - job.admitted_date).days if job.admitted_date else None
        job.age_label = _age_label(days)

        concerns = list(job.concerns.all())
        labours = list(job.labours.all())
        stock = []
        shop = []
        for spare in job.spares.all():
            # `source`, never a guess from the name — the deliberate rule in
            # CLAUDE.md. A draw came off the shelf already fitted; a shop part
            # has an ordering state somebody can act on.
            if spare.source == JobCardSpareItem.SOURCE_INVENTORY:
                stock.append(spare)
            else:
                shop.append(spare)

        job.concern_total, job.labour_total = len(concerns), len(labours)
        job.stock_total, job.shop_total = len(stock), len(shop)

        job.all_concerns, job.concerns_more = _capped(concerns, HOME_SECTION_ROW_CAP)
        job.all_labours, job.labours_more = _capped(labours, HOME_SECTION_ROW_CAP)
        job.all_stock, job.stock_more = _capped(stock, HOME_SECTION_ROW_CAP)
        job.all_shop, job.shop_more = _capped(shop, HOME_SECTION_ROW_CAP)

        job.has_any_live_detail = bool(concerns or labours or stock or shop)


def _age_label(days):
    """How long a car has been in — `New`, `1d`, `213d`.

    ONE label, used by the board's car card and by the Live Jobs card alike.
    There were briefly two (a long "213 days" for the roomier card), and the
    owner collapsed them: the same fact worded two ways on one screen invites
    being read as two different facts, and the short form fits everywhere.
    Day zero reads "New" rather than "Today" because the question being asked
    is how long the car has been here, not what today's date is.
    """
    if days is None:
        return ''
    if days <= 0:
        return 'New'
    return f'{days}d'


def _stamp_age(jobs, today):
    """Attach `age_label` to each card, in Python.

    Deliberately not a database annotation: SQLite (tests) and PostgreSQL
    (everything else) do not agree on date arithmetic, and this is a handful
    of rows on a low-volume floor.
    """
    for job in jobs:
        days = (today - job.admitted_date).days if job.admitted_date else None
        job.age_label = _age_label(days)


#: Unfixed concerns listed under one car on the Live Report's floor board
#: before the rest are counted into a "+N more" line.
#:
#: Generous on purpose, and a guard rather than a window: every row on that
#: board is a decision an owner is about to make, so a hidden concern is a
#: hidden decision. It exists only so that one card carrying a long list cannot
#: flood the board.
#:
#: It happens to equal `UNFILLED_ROW_CAP`, and they are two rules rather than
#: one — this one is about how much of a car's remaining work is worth printing
#: on a decision board, that one about how many money-side gaps fit on a chase
#: card. Do not collapse them into a single constant.
FLOOR_CONCERN_ROW_CAP = 8

#: WORKING sorts above PENDING inside one car: what the mechanic is on right
#: now, then what is queued behind it. That is the order the owner's own
#: sentence is spoken in — "finish this, then do that". FIXED never reaches
#: here, so its position in the map is only a fallback for an unknown status.
_CONCERN_ORDER = {'WORKING': 0, 'PENDING': 1}


def _attach_floor_concerns(jobs):
    """Attach each car's still-open concerns, for the Live Report floor board.

    That board exists so an owner can decide what to tell each mechanic to do
    next, and the decision is made per person, per car, per concern — so the
    CONCERN is the row and the car is only its heading. Nobody but Office and
    the owners commands this work, which is why it lives on their page and not
    on the floor's own board.

    Only UNFIXED concerns are listed: a fixed one is not a decision left to
    make. The fixed ones are counted instead, because that count is what says
    how close the car is to being finished — and a car whose every concern is
    fixed is itself an action, since nobody has closed it yet.

    Read off the prefetched relation, so this costs no query per card.
    """
    for job in jobs:
        concerns = list(job.concerns.all())
        job.concern_total = len(concerns)
        open_concerns = [c for c in concerns if c.status != 'FIXED']
        open_concerns.sort(key=lambda c: (_CONCERN_ORDER.get(c.status, 2), c.pk))
        job.fixed_count = len(concerns) - len(open_concerns)
        job.open_concerns, job.open_concerns_more = _capped(
            open_concerns, FLOOR_CONCERN_ROW_CAP
        )


def _floor_by_mechanic(jobs):
    """Active cards grouped by the mechanic holding them, unassigned cars last.

    Grouped in Python rather than by `order_by('lead_mechanic__name')` because
    the two databases disagree about where NULL sorts — PostgreSQL puts it
    last on an ascending sort, SQLite puts it first — so the "Not assigned"
    group would appear at a different end of the page in tests than in
    production. Its position is decided here instead, once.

    A mechanic holding no car does not appear: every name on this board has
    work under it, which is what keeps it short enough to read at a glance.
    """
    crews = {}
    unassigned = []

    for job in jobs:
        if job.lead_mechanic_id:
            crew = crews.setdefault(
                job.lead_mechanic_id,
                {'name': job.lead_mechanic.name, 'jobs': [], 'unassigned': False},
            )
            crew['jobs'].append(job)
        else:
            unassigned.append(job)

    groups = sorted(crews.values(), key=lambda c: c['name'].lower())
    if unassigned:
        groups.append({'name': 'Not assigned', 'jobs': unassigned, 'unassigned': True})
    return groups


#: A card that has been BILLED. Money has moved, so the Financial Lock is now
#: standing between the card and anyone correcting it — which is exactly what
#: makes an unfilled box on one of these worth chasing.
#:
#: PARTIAL is in the list deliberately. For a walk-in it never occurs (a
#: part-paid walk-in books the shortfall as a discount and is marked PAID — the
#: deliberate rule in CLAUDE.md), so every PARTIAL here is a Fleet card that has
#: been invoiced and is still being collected. That has been billed.
BILLED_STATUSES = ('PAID', 'BULK_PAID', 'PARTIAL')

#: Rows shown per section on one "Billed but not filled" card before the rest
#: are counted into a "+N more" line. Small on purpose: this container is read
#: to decide which car to walk over to, and a rebuild in the live data carries
#: 91 spares. Safe here for the usual reason — no total sits above these rows,
#: the exact remainder is printed rather than implied, the section heading still
#: reports the true count, and every hidden row is on the job card the header
#: already opens.
UNFILLED_ROW_CAP = 8


def _billed_but_unfilled():
    """Billed job cards that still have an empty box somewhere, newest first.

    The narrowing is done in the DATABASE and the detail in Python, which is two
    readings of one rule — so they are kept deliberately in step:

      * every clause below is an exact mirror of a check in
        `workshop.settlement.unfilled`, including `Trim` on the mileage, so the
        queryset is neither wider nor narrower than the truth;
      * and `live_report` still drops any card whose computed gaps come back
        empty, so if they ever DO drift the page can only show fewer cards —
        never a card with an empty red box under it, which is the failure that
        would teach an owner to stop reading this container.

    `settlement.unfilled` is the authority. This is an index lookup in front of
    it, not a second opinion.
    """
    unfixed_concern = (
        JobCardConcern.objects.filter(job_card=OuterRef('pk')).exclude(status='FIXED')
    )
    has_labour_line = JobCardLabourItem.objects.filter(job_card=OuterRef('pk'))
    holey_shop_part = JobCardSpareItem.objects.filter(
        job_card=OuterRef('pk'), source=JobCardSpareItem.SOURCE_SHOP,
    ).filter(
        Q(shop__isnull=True)
        | Q(ordered_date__isnull=True) | Q(received_date__isnull=True)
        | Q(unit_price__isnull=True) | Q(total_price__isnull=True)
    )
    unpriced_draw = JobCardSpareItem.objects.filter(
        job_card=OuterRef('pk'),
        source=JobCardSpareItem.SOURCE_INVENTORY,
        total_price__isnull=True,
    )

    return (
        JobCard.objects
        .filter(is_deleted=False, payment_status__in=BILLED_STATUSES)
        .annotate(
            # Coalesce first: TRIM(NULL) is NULL, so a card that never had a
            # mileage would otherwise match no clause at all.
            _mileage=Trim(Coalesce('mileage', Value(''))),
            _has_labour_line=Exists(has_labour_line),
            _unfixed_concern=Exists(unfixed_concern),
            _holey_shop_part=Exists(holey_shop_part),
            _unpriced_draw=Exists(unpriced_draw),
            # One ordering key for cards that reached PAID (which stamps
            # `paid_date`) and cards still at PARTIAL (which does not). Newest
            # first: a bill settled this morning is the one still fresh enough
            # for somebody to remember what belongs in the empty box.
            _settled_at=Coalesce('paid_date', 'updated_at'),
        )
        .filter(
            Q(_mileage='')
            | Q(lead_mechanic__isnull=True)
            | Q(_has_labour_line=True, labour_amount__lte=0)
            | Q(_unfixed_concern=True)
            | Q(_holey_shop_part=True)
            | Q(_unpriced_draw=True)
        )
        .select_related('lead_mechanic')
        .prefetch_related('concerns', 'labours', 'spares')
        .order_by('-_settled_at', '-pk')
    )


def _attach_unfilled(jobs):
    """Compute each card's gaps, cap the long sections, drop anything clean.

    Returns the rows to render. A card whose gaps come back empty is dropped
    rather than printed with nothing under it — see `_billed_but_unfilled` for
    why that guard exists at all.
    """
    rows = []
    for job in jobs:
        holes = unfilled(job)
        if not holes:
            continue
        job.unfilled = holes
        job.uf_concerns, job.uf_concerns_more = _capped(list(holes.concerns), UNFILLED_ROW_CAP)
        job.uf_inventory, job.uf_inventory_more = _capped(list(holes.inventory), UNFILLED_ROW_CAP)
        job.uf_spares, job.uf_spares_more = _capped(list(holes.spares), UNFILLED_ROW_CAP)
        rows.append(job)
    return rows


@office_required
def live_report(request):
    """
    The Live Report — the workshop's state, for the two roles that act on it.

    Office and Owner only. It was `@staff_required` with the board gated inside
    it, because "Live Jobs" underneath was for everybody; that list has gone —
    the home page's own car cards, and the live details inside them, do that job
    better and are where Floor already works. What is left is entirely supplier
    names, ordering state and money-side gaps, none of which Floor is shown
    anywhere else in the app. The nav pill has always been gated `is_owner or
    is_office`, so the template gate and the decorator now agree.

    Three questions, in the order an owner asks them:

      1. what has already been BILLED with holes in it — the critical one,
         because settling is what closed the door on correcting it;
      2. who is holding which car, and what is still open on each of them —
         the board the next instruction is given from;
      3. which parts are travelling, and which nobody has ordered.

    None of this is narrowed by a search box, deliberately. The page answers
    "what is the state of the workshop right now", and a half-filtered answer
    to that is worse than no answer.
    """
    today = timezone.localdate()

    on_the_floor = JobCard.objects.filter(is_deleted=False, completed=False)

    # The figure in the page heading. It counts the WORKSHOP, never a filtered
    # list — a heading reading "3 in workshop" because somebody left a query in
    # the URL would be the one number on the page that is simply untrue.
    floor_count = on_the_floor.count()

    floor_jobs = list(
        on_the_floor
        .select_related('lead_mechanic')
        # The board lists each car's still-open concerns underneath it, so the
        # concerns come with the cards — one extra query for the whole page
        # rather than one per card.
        .prefetch_related('concerns')
        # Longest-standing car first: on a live board the car that has been
        # here the longest is the one worth looking at. It is also the order
        # the owner reasons in — "his first car, then his second".
        .order_by('admitted_date', 'pk')
    )
    _stamp_age(floor_jobs, today)
    _attach_floor_concerns(floor_jobs)
    mechanic_groups = _floor_by_mechanic(floor_jobs)

    # Only SHOP parts carry an ordering workflow. A warehouse draw
    # (source=INVENTORY) came off the shelf already fitted, so its status
    # column means nothing — listing one as "waiting" would send someone
    # chasing a part that is already on the car.
    awaited = (
        JobCardSpareItem.objects
        .filter(
            source=JobCardSpareItem.SOURCE_SHOP,
            job_card__isnull=False,
            job_card__is_deleted=False,
            job_card__completed=False,
        )
        .select_related('job_card', 'shop')
    )
    ordered_spares = list(
        awaited.filter(status='ORDERED')
        .order_by(F('ordered_date').asc(nulls_last=True), 'pk')
    )
    pending_spares = list(
        awaited.filter(status='PENDING')
        .order_by('job_card__admitted_date', 'pk')
    )

    # Paginated rather than windowed by date. This is a queue to be worked
    # down, not a period report: the heading carries the true total so an owner
    # can see the size of it, and nothing is hidden behind a filter that would
    # have to be widened to find the oldest — and worst — cards.
    page_obj = Paginator(_billed_but_unfilled(), 45).get_page(request.GET.get('page'))
    unfilled_cards = _attach_unfilled(page_obj.object_list)

    return render(request, 'workshop/jobcard/live_report.html', {
        'page_obj': page_obj,
        'unfilled_cards': unfilled_cards,
        'unfilled_count': page_obj.paginator.count,
        'mechanic_groups': mechanic_groups,
        'floor_count': floor_count,
        'ordered_spares': ordered_spares,
        'pending_spares': pending_spares,
    })
