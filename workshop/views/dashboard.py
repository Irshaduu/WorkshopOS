from django.utils import timezone

from django.shortcuts import render
from django.db.models import Count, Q, F
from django.core.paginator import Paginator

from ..models import JobCard, JobCardSpareItem
from ..decorators import staff_required, is_office_or_owner


@staff_required
def home(request):
    """
    Dashboard homepage showing all active job cards.
    Completion date is a planning field, not a filter.
    Cars only move to Completed when the "Completed" button is clicked.
    """
    # Get only non-completed job cards (where completed=False)
    # Optimized with select_related and prefetch_related for 1M+ records
    active_jobcards = JobCard.objects.filter(completed=False, is_deleted=False).select_related('lead_mechanic').prefetch_related('concerns').annotate(
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
    
    return render(request, 'workshop/dashboard/dashboard_home.html', {
        'active_jobcards': page_obj, # Pass page_obj as active_jobcards
        'completed_count': completed_count,
        'pending_bills_count': pending_bills_count,
        'page_obj': page_obj,
        'today': timezone.localdate(),  # IST-aware — respects TIME_ZONE = 'Asia/Kolkata'
    })


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


#: How many rows a Live Jobs section shows before it says how many are left.
#: Per SECTION, not per card — a card with 12 concerns and 40 spares shows 10
#: of each. The tail is never lost: it is on the job card the row already opens.
SECTION_ROW_CAP = 10


def _capped(rows):
    """(the first SECTION_ROW_CAP rows, how many were left over)."""
    return rows[:SECTION_ROW_CAP], max(0, len(rows) - SECTION_ROW_CAP)


def _card_sections(jobs):
    """Attach each Live Jobs card's four sections, already capped.

    Capped HERE rather than with `|slice:":10"` in the template so the number
    lives in exactly one place — a cap in the markup and a remainder computed
    from a constant are two versions of one rule, free to disagree, and they
    would disagree as a "+3 more" beside eleven visible rows.

    The four sections are four different questions: what the customer
    complained of, what we did, what came off our own shelf, and what we bought
    in. The last two used to be one "Parts" list; they are split because only a
    shop part has an ordering state anyone can act on — every warehouse draw
    carried an identical "STOCK" badge that distinguished nothing.

    Note this is the LIVE REPORT only. The printed invoice deliberately merges
    both routes into one PART NAME list — a customer has no interest in which
    shelf a part came off — and that rule is untouched by this one.
    """
    for job in jobs:
        stock, shop = [], []
        for spare in job.spares.all():
            (stock if spare.source == JobCardSpareItem.SOURCE_INVENTORY else shop).append(spare)

        concerns = list(job.concerns.all())
        labours = list(job.labours.all())

        job.concerns_shown, job.concerns_more = _capped(concerns)
        job.labours_shown, job.labours_more = _capped(labours)
        job.stock_shown, job.stock_more = _capped(stock)
        job.shop_shown, job.shop_more = _capped(shop)

        job.concerns_total = len(concerns)
        job.labours_total = len(labours)
        job.stock_total = len(stock)
        job.shop_total = len(shop)

        # An empty section is not rendered at all — four headings with "none"
        # under two of them, on every card, is noise multiplied by the list.
        # A card with nothing in any of them says so once, instead.
        job.has_any_detail = bool(concerns or labours or stock or shop)


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


@staff_required
def live_report(request):
    """
    SECTION 2.1: LIVE REPORT - Quick scroll for all roles.
    Shows active jobs, concerns, and spares status.

    Two audiences on one page, and the split is deliberate:

      * the operations board at the top — who is holding which car, which
        parts are on the way, which parts nobody has ordered yet — is
        Office and Owner only, matching every other screen where a spare
        shop is named. Floor is shown no supplier and no ordering state
        anywhere else in the app.
      * "Live Jobs" underneath is for all three roles, unchanged in reach.

    The board is deliberately NOT narrowed by `q`/`status`. Those filter the
    Live Jobs list, as they always have; the board answers "what is the state
    of the workshop right now", and a half-filtered answer to that question is
    worse than no answer.
    """
    # Search and Filter support (Titan Exhaustive)
    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()

    today = timezone.localdate()
    can_see_ops = is_office_or_owner(request.user)

    on_the_floor = JobCard.objects.filter(is_deleted=False, completed=False)

    # The figure in the page heading, for every role. It counts the WORKSHOP,
    # never the filtered list beneath it — a heading reading "3 in workshop"
    # because somebody left a search in the URL would be the one number on the
    # page that is simply untrue.
    floor_count = on_the_floor.count()

    mechanic_groups = []
    ordered_spares = []
    pending_spares = []

    if can_see_ops:
        floor_jobs = list(
            on_the_floor
            .select_related('lead_mechanic')
            # Longest-standing car first: on a live board the car that has been
            # here the longest is the one worth looking at.
            .order_by('admitted_date', 'pk')
        )
        _stamp_age(floor_jobs, today)
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

    # Same population as the board above, chained off the one definition of
    # "in the workshop" rather than a second copy of that filter — the two are
    # the same claim, and a later change to one of them would otherwise put a
    # different number in the heading from the list underneath it.
    active_jobs = on_the_floor.select_related('lead_mechanic').prefetch_related('concerns', 'spares', 'labours').annotate(
        total_concerns=Count('concerns'),
        fixed_concerns=Count('concerns', filter=Q(concerns__status='FIXED'))
    )

    if q:
        for word in q.split():
            active_jobs = active_jobs.filter(
                Q(registration_number__icontains=word) |
                Q(bill_number__icontains=word) |
                Q(brand_name__icontains=word) |
                Q(model_name__icontains=word)
            )
            
    if status == 'PAID':
        active_jobs = active_jobs.filter(payment_status='PAID')
    elif status == 'PENDING':
        active_jobs = active_jobs.filter(payment_status='PENDING')

    active_jobs = active_jobs.order_by('-updated_at')
    
    # Pagination (prevents performance degradation at scale)
    paginator = Paginator(active_jobs, 45)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    _stamp_age(page_obj.object_list, today)
    _card_sections(page_obj.object_list)

    return render(request, 'workshop/jobcard/live_report.html', {
        'active_jobs': page_obj,
        'page_obj': page_obj,
        'q': q,
        'status_filter': status,
        'can_see_ops': can_see_ops,
        'mechanic_groups': mechanic_groups,
        'floor_count': floor_count,
        'ordered_spares': ordered_spares,
        'pending_spares': pending_spares,
    })
