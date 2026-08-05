"""
ESTIMATES — create, print, and the history of what was quoted.

Five views and no machinery. An estimate touches no ledger, no stock, no job
card and no report (see the `Estimate` model for why that isolation is the
design), so these views do exactly what they look like: save a form, render a
sheet, list what exists.

The one thing worth knowing before changing anything here: **the printed
document is not decided in this file.** `workshop/invoice.py` owns what a
customer sees, for the estimate and the invoice alike, so the two cannot drift
apart. These views resolve a record and render.
"""

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Prefetch
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from ..decorators import office_required
from ..forms import EstimateForm, EstimateJobFormSet, EstimatePartFormSet
from ..invoice import build_estimate
from ..models import Estimate, EstimateJobLine, EstimatePartLine, SparePart

# Same page size as every other list view in the app.
PAGE_SIZE = 45

# TWO filters, not the eight the day-to-day list views carry.
#
# Those pages sort a stream of daily activity, so Today / This Week / Last Month
# each answer a real question. Estimates are written a handful of times a month
# and are looked up months later — "was this car quoted before, and for how
# much?" Six of the eight would return an empty page most of the time, which
# reads as a broken screen rather than an empty period. This year, or everything.
DATE_FILTERS = [
    ('this_year', 'This Year'),
    ('all', 'All Time'),
]
DEFAULT_FILTER = 'this_year'


def _ordered_lines():
    """
    Both line relations, in insertion order.

    Neither model declares a default ordering, so without this the same estimate
    could print its rows in two different sequences on two different days — the
    identical reason `invoice_view` prefetches with an explicit `order_by('pk')`.
    """
    return [
        Prefetch('job_lines', queryset=EstimateJobLine.objects.order_by('pk')),
        Prefetch('parts', queryset=EstimatePartLine.objects.order_by('pk')),
    ]


def _safe_back(request):
    """Honour ?back= only when it points back into this site — same check the
    invoice's back button goes through."""
    target = request.GET.get('back') or ''
    if target and url_has_allowed_host_and_scheme(
        url=target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return None


# -----------------------------------------------------------------------------
# HISTORY
# -----------------------------------------------------------------------------

@office_required
def estimate_list(request):
    """
    Every estimate ever written, newest first.

    Search is live, returning `estimate_list_partial.html` to an AJAX request —
    the same shape Paid Bills and Completed use, so the box behaves the way it
    does everywhere else in the app rather than being the one that needs Enter.
    """
    estimates = Estimate.objects.only(
        # The list row reads exactly these. `only()` keeps a page of 45 off the
        # two line tables entirely — the rows are never touched here, and the
        # total is a stored column precisely so they don't have to be.
        'estimate_number', 'date', 'registration_number',
        'brand_name', 'model_name', 'customer_name', 'total_amount',
        # Both, because `get_car_color_hex` reads car_color_other for an
        # 'Other' colour — deferring it would cost a query per row.
        'car_color', 'car_color_other',
    )

    filter_type = request.GET.get('filter', DEFAULT_FILTER)
    if filter_type not in dict(DATE_FILTERS):
        filter_type = DEFAULT_FILTER
    q = (request.GET.get('q') or '').strip()

    if q:
        for word in q.split():
            estimates = estimates.filter(
                Q(registration_number__icontains=word) |
                Q(customer_name__icontains=word) |
                Q(brand_name__icontains=word) |
                Q(model_name__icontains=word) |
                Q(estimate_number__icontains=word)
            )

    today = timezone.localdate()   # IST-aware; never date.today()
    if filter_type == 'this_year':
        estimates = estimates.filter(date__gte=today.replace(month=1, day=1))
    # 'all' → no date filter

    total_count = estimates.count()
    page_obj = Paginator(estimates, PAGE_SIZE).get_page(request.GET.get('page'))

    context = {
        'estimates': page_obj,
        'page_obj': page_obj,
        'total_count': total_count,
        'q': q,
        'filters': DATE_FILTERS,
        'filter_type': filter_type,
    }

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return render(request, 'workshop/estimate/estimate_list_partial.html', context)
    return render(request, 'workshop/estimate/estimate_list.html', context)


# -----------------------------------------------------------------------------
# CREATE / EDIT
# -----------------------------------------------------------------------------

def _save_estimate(request, estimate=None):
    """
    The one create-and-edit body. Both routes bind the same three forms and
    differ only in what they say afterwards, so they share this rather than
    keeping two copies that can fall out of step.
    """
    is_new = estimate is None

    if request.method == 'POST':
        form = EstimateForm(request.POST, instance=estimate)
        job_formset = EstimateJobFormSet(request.POST, instance=estimate, prefix='jobs')
        part_formset = EstimatePartFormSet(request.POST, instance=estimate, prefix='parts')

        if form.is_valid() and job_formset.is_valid() and part_formset.is_valid():
            with transaction.atomic():
                estimate = form.save(commit=False)
                if is_new:
                    estimate.created_by = request.user
                estimate.save()

                job_formset.instance = estimate
                part_formset.instance = estimate
                job_formset.save()
                part_formset.save()

                # Explicit, because there are no signals on these models — the
                # lines have no side effects worth a save-time hook. Miss this
                # call and the history list shows a stale total forever.
                estimate.update_totals()

            messages.success(
                request,
                f"Estimate {estimate.estimate_number} "
                f"{'created' if is_new else 'updated'}."
            )
            return redirect('estimate_print', pk=estimate.pk)

        messages.error(request, "Check the highlighted fields and try again.")
    else:
        form = EstimateForm(instance=estimate)
        job_formset = EstimateJobFormSet(instance=estimate, prefix='jobs')
        part_formset = EstimatePartFormSet(instance=estimate, prefix='parts')

    return render(request, 'workshop/estimate/estimate_form.html', {
        'form': form,
        'job_formset': job_formset,
        'part_formset': part_formset,
        'estimate': estimate if not is_new else None,
        'is_new': is_new,
        # The master spare list, rendered as a <datalist>. One indexed read of a
        # couple of hundred short strings — cheaper than the per-keystroke
        # endpoint the Job Card uses, and it needs no JavaScript, so a row added
        # after page load gets the same suggestions with nothing to re-wire.
        'spare_names': SparePart.objects.order_by('name').values_list('name', flat=True),
    })


@office_required
def estimate_create(request):
    return _save_estimate(request)


@office_required
def estimate_edit(request, pk):
    estimate = get_object_or_404(
        Estimate.objects.prefetch_related(*_ordered_lines()), pk=pk
    )
    return _save_estimate(request, estimate)


# -----------------------------------------------------------------------------
# PRINT
# -----------------------------------------------------------------------------

@office_required
def estimate_print(request, pk):
    """The printable quotation. Every figure and naming rule is decided in
    `workshop/invoice.py`; this resolves the record and renders."""
    estimate = get_object_or_404(
        Estimate.objects.prefetch_related(*_ordered_lines()), pk=pk
    )

    context = build_estimate(estimate)
    context.update({
        'estimate': estimate,
        'back_url': _safe_back(request),
    })
    return render(request, 'workshop/estimate/estimate_print.html', context)


# -----------------------------------------------------------------------------
# DELETE
# -----------------------------------------------------------------------------

@office_required
def estimate_delete(request, pk):
    """
    Remove a quotation. Permanent, confirmed, and deliberately NOT written to
    Deletion History.

    That is the one place this section departs from the app's deletion model,
    and it is a decision rather than an omission. `DeletionLog.record()` is the
    choke point for permanent deletes *and* the origin of the `RECORD_DELETED`
    notification, which is CRITICAL — it sends a Web Push to both owners' phones.
    An estimate moves no money, sits in no ledger and appears in no report; it
    is a draft that is expected to be rewritten and discarded. Buzzing two
    phones every time Office tidies up a superseded quote is exactly how a
    critical alert stops being read for the things that matter.

    The alternative — logging without notifying — would mean weakening that
    choke point for one entity type, and the choke point is the reason the other
    ten stay correct. So: no log, and this comment instead of a silent gap.
    """
    estimate = get_object_or_404(Estimate, pk=pk)

    if request.method == 'POST':
        number = estimate.estimate_number
        estimate.delete()          # CASCADEs its own two line tables, nothing else
        messages.success(request, f"Estimate {number} deleted.")
        return redirect('estimate_list')

    return render(request, 'workshop/estimate/estimate_confirm_delete.html', {
        'estimate': estimate,
    })
