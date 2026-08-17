from decimal import Decimal

from django.utils import timezone

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.core.paginator import Paginator

from ..models import (
    CarBrand, CarModel, SparePart, ConcernSolution,
    JobCard, JobCardConcern, JobCardSpareItem, JobCardLabourItem,
    SpareShop, DeletionLog,
)
from ..forms import (
    JobCardForm, JobCardConcernFormSet, JobCardSpareFormSet,
    JobCardInventoryFormSet, JobCardLabourFormSet
)
from ..decorators import staff_required, office_required, is_office_or_owner




def _shop_options(jobcard=None):
    """
    Shops offered in a spare row's dropdown: active ones, plus any archived shop
    this card's spares already point at.

    Including the archived one is not cosmetic. If it is absent from the options
    the `<select>` has nothing to mark selected, so the browser falls back to the
    blank first option and posts an EMPTY value — and the resolution pass then
    clears the FK and wipes that purchase off the shop's ledger. Fixing only the
    server-side lookup would leave this half of the same bug in place.
    """
    linked = set()
    if jobcard is not None and jobcard.pk:
        linked = {sp.shop_id for sp in jobcard.spares.all() if sp.shop_id}
    return SpareShop.objects.filter(Q(is_trashed=False) | Q(pk__in=linked)).order_by('name')

def _resolvable_shops(spares):
    """
    Shops the job-card form may resolve a spare to: every ACTIVE shop, plus any
    archived shop these rows are already linked to.

    That second half is load-bearing. The resolution pass rebuilds each spare's
    shop FK from the posted pk, and it used to look only at `is_trashed=False`
    shops — so once a shop was archived, saving ANY job card holding one of its
    spares (even just to fix a customer name) failed to find it, set `shop=None`,
    and silently erased that purchase from the shop's ledger. The debt simply
    disappeared. Archiving is meant to hide a shop from new work, not to rewrite
    what was already bought from it.

    Archived shops still never appear in the dropdown for a NEW selection; they
    are only resolvable where a row already points at them.
    """
    linked_ids = {sp.shop_id for sp in spares if sp.shop_id}
    qs = SpareShop.objects.filter(Q(is_trashed=False) | Q(pk__in=linked_ids))
    return {shop.pk: shop for shop in qs}

# Fields on a job-card part that only Office/Owner may set.
PRICE_FIELDS = ('unit_price', 'total_price', 'customer_rate')

# Fields on the job CARD itself that only Office/Owner may set — who the customer
# is, as opposed to what was done to the car. `labour_amount` belongs to the same
# rule and is handled separately in `_floor_locked_data`, because its stored
# default is a Decimal rather than a string.
OFFICE_ONLY_CARD_FIELDS = ('customer_name', 'customer_contact')


def _row_where(section_name, row_form, position):
    """
    How one failing row is named at the top of the page.

    A row that knows what it is says so — `InventoryDrawForm.row_label()`
    returns the product name — because "Inventory item · Castrol Edge" is
    something you can go and find, while "Inventory item 7" means counting rows.
    Everything else falls back to its position, which is still better than the
    section alone.
    """
    labeller = getattr(row_form, 'row_label', None)
    if callable(labeller):
        # A middot, not a dash: the template already joins `where` and `what`
        # with an em-dash, and two of them in one line ("Inventory item — Liqui
        # Moly — Quantity: …") reads as three unrelated fragments.
        return f"{section_name} · {labeller()}"
    return f"{section_name} {position}"


def _collect_problems(form, formsets):
    """
    Every reason this job card was refused, as flat ("where", "what") pairs.

    Assembled here rather than by walking the formsets in the template, because
    the template version had rotted in three separate ways and each was
    invisible to the test suite:

      * It never mentioned `inventory_formset` at all. A warehouse draw saved
        with a blank Qty is refused by `InventoryDrawForm.clean`, so the page
        came back unsaved with **no banner, no message and no sound** — the only
        sign was one line of small red text several screens down, inside a
        horizontally scrolling table. From the front it looked like the Save
        button had done nothing.
      * What it did print was "Check Spares section for errors", which repeats
        what the person already knows (something is wrong) and withholds the
        only part they need (what, and in which row).
      * It carried a leftover debugging loop that printed Django's raw error
        dict — `Labour Error - job_description: <ul class="errorlist">…` — onto
        the screen of whoever hit it.

    Reads `formset.forms` rather than `formset.errors` so a row can be named by
    what it holds; both have already been validated by the time this runs, so
    neither costs anything extra.
    """
    problems = []

    for field in form:
        for error in field.errors:
            problems.append((field.label or 'Job card', error))
    for error in form.non_field_errors():
        problems.append(('Job card', error))

    for section_name, formset in formsets:
        for error in formset.non_form_errors():
            problems.append((section_name, error))
        for position, row_form in enumerate(formset.forms, start=1):
            if not row_form.errors:
                continue
            where = _row_where(section_name, row_form, position)
            for field in row_form:
                for error in field.errors:
                    label = field.label or field.name
                    problems.append((where, f"{label}: {error}"))
            for error in row_form.non_field_errors():
                problems.append((where, error))

    return problems


def _problems_for(request, form, concern_formset, labour_formset,
                  inventory_formset, spare_formset, subject):
    """
    Collect the problems AND say so where the eye already is.

    Both views call this rather than listing the four section names each — two
    copies of those labels would be free to drift, and they would drift into a
    page that names the same section two different ways depending on whether you
    were creating or editing.

    The message matters as much as the list: base.html renders messages at the
    top of every page and sound.js plays its error tone off the message tag, so
    a refusal with no message is a refusal nobody hears and, at the bottom of a
    form this long, nobody sees either.

    Named in the order the sections appear on the page, so the list reads as a
    route down it.
    """
    problems = _collect_problems(form, [
        ('Customer concern', concern_formset),
        ('Job', labour_formset),
        ('Inventory item', inventory_formset),
        ('Spare part', spare_formset),
    ])
    if problems:
        count = len(problems)
        # Short, because it is not the only thing on screen: the summary box
        # below it lists what is wrong, and the boxes themselves are marked.
        # The banner's job is to say the save did not happen and that nothing
        # was thrown away — two facts, one line. It used to run to three
        # clauses and repeat the box's own heading almost word for word.
        messages.error(
            request,
            f"{subject} not saved — {count} thing{'s' if count != 1 else ''} "
            f"to fix. Nothing you typed was lost."
        )
    return problems


def _form_context(request, *, form, concern_formset, spare_formset,
                  inventory_formset, labour_formset, jobcard=None, problems=None):
    """
    The one context every render of the job-card form goes through.

    There were three of these — create, edit, and the duplicate-registration
    refusal — and they had drifted apart. The refusal path passed **no
    `spare_shops`**, so every spare row's shop `<select>` re-rendered holding
    nothing but "-- Shop --". Correct the registration number, press save, and
    each of those selects posts an empty value; the resolution pass then clears
    the FK and the purchase disappears off that shop's ledger. That is exactly
    the failure the archived-shop rule (`_shop_options`) exists to prevent,
    reached through a different door — and it needed no archived shop and no
    unusual data, only a customer bringing a car back before the last card on it
    was closed.

    One builder means a fourth render cannot reintroduce it.
    """
    return {
        'form': form,
        'concern_formset': concern_formset,
        'spare_formset': spare_formset,
        'inventory_formset': inventory_formset,
        'labour_formset': labour_formset,
        'jobcard': jobcard,
        'is_edit': jobcard is not None,
        # Whether to render the customer's name and number at all. Resolved
        # ONCE here rather than as a `has_group` test in the template, so the
        # gate and `_floor_locked_data`'s pinning read the same rule — a
        # template asking the question its own way is how a hidden box and an
        # unprotected field come to disagree.
        'can_see_customer': is_office_or_owner(request.user),
        'next_url': request.GET.get('next'),
        'spare_shops': _shop_options(jobcard),
        'unassigned_spares': JobCardSpareItem.objects.filter(
            job_card__isnull=True
        ).select_related('shop').order_by('-ordered_date'),
        'problems': problems or [],
    }


def _reconcile_settled_bill(jobcard):
    """
    Make the payment state honest again after an unlocked edit moved the bill on
    an already-settled card.

    The Financial Lock exists because editing a settled card is a real need, not
    an accident — but nothing used to follow the money afterwards, and the two
    settlement routes fail in opposite directions:

      PAID (walk-in) — `discount_amount` stayed at whatever the original
        settlement computed, so the Profit page kept reading revenue as
        `bill − discount` off the NEW bill while `received_amount` never moved.
        Adding a ₹500 part to a ₹1,000 card settled at ₹800 turned ₹800 of
        turnover into ₹1,300, ₹500 of it never earned by anyone. A walk-in has
        exactly one payment event (see CLAUDE.md), so the shortfall *is* the
        discount — recomputing it is that rule applied to the new total, and it
        restores `bill − discount == received`. A large jump trips the existing
        HIGH_DISCOUNT alert and shows up in `audit_high_discounts`, which is
        precisely the compensating control for it.

      BULK_PAID (fleet) — a fleet genuinely does pay later, so the extra is not
        a discount, it is owed. But `bulk_payer_pay` only cascades over
        PENDING/PARTIAL cards, so a BULK_PAID card whose bill grew was skipped
        forever: the fleet page showed "₹0 outstanding across 0 cards" while
        `get_pending_balance` said ₹500, and paying another ₹500 parked it as
        advance credit instead of clearing the card. Dropping it back to
        PARTIAL puts it in front of the cascade again.

    A bill that shrank below what was received is left alone in both cases —
    that is an overpayment, not a shortfall, and inventing a refund here would
    be guessing.
    """
    if jobcard.payment_status == 'PAID':
        new_discount = max(
            Decimal('0'),
            (jobcard.total_bill_amount or Decimal('0')) - (jobcard.received_amount or Decimal('0')),
        )
        if new_discount != jobcard.discount_amount:
            jobcard.discount_amount = new_discount
            jobcard.save(update_fields=['discount_amount'])
            return True

    elif jobcard.payment_status == 'BULK_PAID':
        received = jobcard.received_amount or Decimal('0')
        if received < (jobcard.total_bill_amount or Decimal('0')):
            jobcard.payment_status = 'PARTIAL' if received > 0 else 'PENDING'
            jobcard.discount_amount = Decimal('0')
            jobcard.paid_date = None
            jobcard.save(update_fields=['payment_status', 'discount_amount', 'paid_date'])
            return True

    return False


def _floor_locked_data(request, jobcard=None):
    """
    POST data with every Office-only field forced back to what is already stored.

    Renamed from `_price_locked_data` on 2026-08-16, when the customer's name and
    number joined the prices behind the same gate. The rule it enforces was never
    about money as such: **a field Floor cannot see on any screen must be a field
    Floor cannot post from any screen.** A helper called "price locked" that also
    pins a phone number is precisely the drift this codebase keeps records to
    avoid.

    Prices are hidden from Floor in the template, but the inputs are still
    rendered — inside a `d-none` cell — because leaving them out would make the
    formset save blanks over what Office entered. That left the rule enforced in
    the UI only: a Floor login POSTing `total_price=1` turned a ₹5,000 bill into
    ₹1, and the same hole existed in the Spare Parts section long before the
    Inventory one was added.

    Dropping a FORMSET field is not an option (it wipes the row), so each is
    overwritten with the value on the existing row: a crafted POST simply has no
    effect. Rows with no stored counterpart get blank, since a Floor user has no
    prices to preserve on a part they are adding.

    Three groups, and only the first is a formset:

      1. the per-part prices, in the `spares` and `inventory` prefixes;
      2. `labour_amount`, the whole labour charge, which lives on the job card;
      3. `customer_name` / `customer_contact`, which live there too.

    Groups 2 and 3 are fields on the CARD, so the returned data has to be what
    binds `JobCardForm` as well as the formsets — binding the form from raw
    `request.POST` would leave exactly those unprotected. Unlike a formset field
    they can also safely be left out of the template for Floor: an absent field
    on a ModelForm leaves the stored value alone, which is why the template omits
    them and this only has to answer a crafted payload.

    Office and Owner get `request.POST` untouched.
    """
    if is_office_or_owner(request.user):
        return request.POST

    data = request.POST.copy()
    stored = {str(s.pk): s for s in jobcard.spares.all()} if jobcard is not None else {}

    for prefix in ('spares', 'inventory'):
        try:
            total = int(data.get(f'{prefix}-TOTAL_FORMS') or 0)
        except (TypeError, ValueError):
            continue
        for i in range(total):
            row = stored.get((data.get(f'{prefix}-{i}-id') or '').strip())
            for field in PRICE_FIELDS:
                key = f'{prefix}-{i}-{field}'
                if key in data:
                    value = getattr(row, field, None) if row else None
                    data[key] = '' if value is None else str(value)

    # The card's own labour charge. On a NEW card there is nothing stored yet, so
    # it is pinned at zero — a Floor user opening a job records what was done and
    # Office prices it afterwards.
    data['labour_amount'] = str(jobcard.labour_amount if jobcard is not None else Decimal('0'))

    # The customer's name and number. Owner 1 keeps those relationships himself
    # and the workshop identifies a car by its registration, so this is who the
    # customer IS rather than what was done to the car — Office and Owner only,
    # the same reach as the invoice that carries it. Pinned to the stored value,
    # blank on a new card, so a crafted POST can neither invent a customer nor
    # erase one.
    for field in OFFICE_ONLY_CARD_FIELDS:
        stored = getattr(jobcard, field, None) if jobcard is not None else None
        data[field] = stored or ''
    return data


@staff_required
def jobcard_create(request):
    """
    Create a new job card with formsets for concerns, spares, and labour.
    Admitted date defaults to today but is editable.
    Redirects to edit page after save with success message.
    Prevents duplicate job cards with 3-attempt confirmation.
    """
    if request.method == 'POST':
        # One locked copy of the POST binds BOTH the card and its parts — the
        # labour charge is a field on the card, so binding the form from raw
        # request.POST would leave that one price unprotected.
        parts_data = _floor_locked_data(request)
        form = JobCardForm(parts_data)

        if form.is_valid():
            jobcard = form.save(commit=False)

            # Hard block: only one active (not completed, not trashed) job card
            # is allowed per registration number at a time. No bypass — the old
            # "3-attempt confirmation" let staff push through anyway, which is
            # exactly how duplicate active job cards for the same car happened.
            registration = jobcard.registration_number.strip().upper()
            existing_job = JobCard.get_active_conflict(registration)

            if existing_job:
                vehicle_info = f"{existing_job.brand_name} {existing_job.model_name}" if existing_job.brand_name else registration
                messages.error(
                    request,
                    f'{vehicle_info} ({registration}) already has an active job card '
                    f'(not yet Completed). Complete or trash that job card before creating a new one.'
                )

                concern_formset = JobCardConcernFormSet(request.POST, prefix='concerns')
                spare_formset = JobCardSpareFormSet(parts_data, prefix='spares')
                inventory_formset = JobCardInventoryFormSet(parts_data, prefix='inventory')
                labour_formset = JobCardLabourFormSet(request.POST, prefix='labours')

                return render(
                    request,
                    'workshop/jobcard/jobcard_form.html',
                    _form_context(
                        request,
                        form=form,
                        concern_formset=concern_formset,
                        spare_formset=spare_formset,
                        inventory_formset=inventory_formset,
                        labour_formset=labour_formset,
                    ),
                )

            # Formsets initialization for standard save
            concern_formset = JobCardConcernFormSet(request.POST, prefix='concerns')
            spare_formset = JobCardSpareFormSet(parts_data, prefix='spares')
            inventory_formset = JobCardInventoryFormSet(parts_data, prefix='inventory')
            labour_formset = JobCardLabourFormSet(request.POST, prefix='labours')

            if (concern_formset.is_valid() and spare_formset.is_valid()
                    and inventory_formset.is_valid() and labour_formset.is_valid()):
                # AUD-0014: Wrap all formset saves in a single atomic transaction.
                # Without this, a partial failure (e.g. a spare save fails after the
                # JobCard itself is committed) would leave an orphaned record.
                with transaction.atomic():
                    jobcard.save()

                    # Associate instances with jobcard before saving
                    concern_formset.instance = jobcard
                    spare_formset.instance = jobcard
                    inventory_formset.instance = jobcard
                    labour_formset.instance = jobcard

                    saved_concerns = concern_formset.save()
                    saved_spares = spare_formset.save()
                    inventory_formset.save()
                    labour_formset.save()
                    
                    # AUD-0052: Auto-learn — use case-insensitive lookup to prevent
                    # ghost duplicates like 'Brake Pad' vs 'brake pad'.
                    new_concern_texts = [c.concern_text.strip() for c in saved_concerns if c.concern_text and c.concern_text.strip()]
                    if new_concern_texts:
                        existing_concern_texts = set()
                        for t in new_concern_texts:
                            if ConcernSolution.objects.filter(concern__iexact=t).exists():
                                existing_concern_texts.add(t)
                        new_concerns = [ConcernSolution(concern=t) for t in new_concern_texts if t not in existing_concern_texts]
                        ConcernSolution.objects.bulk_create(new_concerns, ignore_conflicts=True)
                    
                    new_spare_names = [s.spare_part_name.strip() for s in saved_spares if s.spare_part_name and s.spare_part_name.strip()]
                    if new_spare_names:
                        existing_spare_names = set()
                        for n in new_spare_names:
                            if SparePart.objects.filter(name__iexact=n).exists():
                                existing_spare_names.add(n)
                        new_spare_parts = [SparePart(name=n) for n in new_spare_names if n not in existing_spare_names]
                        SparePart.objects.bulk_create(new_spare_parts, ignore_conflicts=True)

                    # AUD-0023: Resolve spare → shop FK using the posted PK, not free-text name.
                    # The template submits shop.pk as the option value, so we can do a direct
                    # ID-based lookup — no case-folding or name-parsing needed.
                    all_spares = list(jobcard.spares.filter(source=JobCardSpareItem.SOURCE_SHOP))

                    # Active shops, plus any archived one these rows already use.
                    shops_by_pk = _resolvable_shops(all_spares)

                    shops_to_update = set()
                    for spare in all_spares:
                        # The formset does not touch the `shop` FK (it only carries `shop_name`),
                        # so this still holds the shop the row was billed to BEFORE this edit.
                        # It must be refreshed too: updating only the new shop left the old one
                        # still counting a row it no longer owns, showing one Rs1,000 purchase
                        # as Rs1,000 owed to each of two shops, permanently. This path uses
                        # .update(), so the same guard in JobCardSpareItem.save() never runs here.
                        if spare.shop_id:
                            shops_to_update.add(spare.shop_id)
                        # spare_formset.save() just saved the posted PK into spare.shop_name
                        raw_pk = spare.shop_name.strip() if spare.shop_name else ''
                        shop_obj = None
                        if raw_pk:
                            try:
                                shop_obj = shops_by_pk.get(int(raw_pk))
                            except (ValueError, TypeError):
                                shop_obj = None
                        # Set both the FK and the human-readable display name
                        shop_name_val = shop_obj.name if shop_obj else ''
                        JobCardSpareItem.objects.filter(pk=spare.pk).update(
                            shop=shop_obj,
                            shop_name=shop_name_val,
                        )
                        if shop_obj:
                            shops_to_update.add(shop_obj.pk)

                    # Delete imported unassigned spares to prevent duplicates
                    imported_ids = request.POST.getlist('imported_unassigned_ids')
                    if imported_ids:
                        old_items = JobCardSpareItem.objects.filter(pk__in=imported_ids, job_card__isnull=True)
                        for old_item in old_items.select_related('shop'):
                            if old_item.shop_id:
                                shops_to_update.add(old_item.shop_id)
                        old_items.delete()

                    # Update totals for every affected shop, old and new alike.
                    for shop in SpareShop.objects.filter(pk__in=shops_to_update):
                        shop.update_totals()

                    # See jobcard_edit: the labour charge lives on the card, so
                    # nothing recomputes the bill for a card created with labour
                    # and no parts. No-ops when the total already agrees.
                    jobcard.update_totals()

                messages.success(request, f'Job card for {jobcard.registration_number} created successfully!')
                return redirect('jobcard_edit', pk=jobcard.pk)
        else:
            # If form is invalid, we still need to initialize formsets for the context
            concern_formset = JobCardConcernFormSet(request.POST, prefix='concerns')
            spare_formset = JobCardSpareFormSet(parts_data, prefix='spares')
            inventory_formset = JobCardInventoryFormSet(parts_data, prefix='inventory')
            labour_formset = JobCardLabourFormSet(request.POST, prefix='labours')
    else:
        # Pre-fill admitted_date with today's date
        initial_data = {'admitted_date': timezone.localdate()}  # IST-aware — respects TIME_ZONE = 'Asia/Kolkata'
        
        # Pre-fill from GET parameters (Cloning/New Visit feature)
        for field in ['registration_number', 'brand_name', 'model_name', 'customer_name', 'customer_contact']:
            val = request.GET.get(field)
            if val:
                initial_data[field] = val
                
        form = JobCardForm(initial=initial_data)
        concern_formset = JobCardConcernFormSet(prefix='concerns')
        spare_formset = JobCardSpareFormSet(prefix='spares')
        inventory_formset = JobCardInventoryFormSet(prefix='inventory')
        labour_formset = JobCardLabourFormSet(prefix='labours')

    # Reaching here after a POST means nothing was saved.
    problems = []
    if request.method == 'POST':
        problems = _problems_for(
            request, form, concern_formset, labour_formset,
            inventory_formset, spare_formset, subject='Job card')

    return render(
        request,
        'workshop/jobcard/jobcard_form.html',
        _form_context(
            request,
            form=form,
            concern_formset=concern_formset,
            spare_formset=spare_formset,
            inventory_formset=inventory_formset,
            labour_formset=labour_formset,
            problems=problems,
        ),
    )


@office_required
def jobcard_list(request):
    """
    SECTION 2: JOBS - List of active saved job cards.
    """
    jobcard_list_query = JobCard.objects.filter(is_deleted=False).select_related('lead_mechanic').prefetch_related('spares', 'labours').order_by('-updated_at', '-pk')
    
    # Detect AJAX vs Full Refresh for "Smart Reset"
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    q = request.GET.get('q', '').strip() if is_ajax else ''
    
    if q:
        for word in q.split():
            jobcard_list_query = jobcard_list_query.filter(
                Q(registration_number__icontains=word) |
                Q(bill_number__icontains=word) |
                Q(brand_name__icontains=word) |
                Q(model_name__icontains=word) |
                Q(customer_name__icontains=word) |
                Q(customer_contact__icontains=word) |
                Q(lead_mechanic__name__icontains=word)
            )
        
    paginator = Paginator(jobcard_list_query, 45)  # Show 45 jobs per page
    
    page_number = request.GET.get('page')
    jobcards = paginator.get_page(page_number)
    
    # AJAX Search: Return only the partial template for thousands-ready performance
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return render(request, 'workshop/jobcard/job_list_partial.html', {'jobcards': jobcards, 'page_obj': jobcards, 'q': q})
    
    return render(request, 'workshop/jobcard/jobcard_list.html', {'jobcards': jobcards, 'page_obj': jobcards, 'q': q})


@staff_required
def jobcard_detail(request, pk):
    """
    Clean View for a Job Card (Read-Only).
    """
    jobcard = get_object_or_404(
        JobCard.objects.select_related('lead_mechanic').prefetch_related('concerns', 'spares', 'labours'),
        pk=pk
    )

    # Split the one relation for display, mirroring the two sections on the edit
    # form. Partitioned in Python off the existing prefetch rather than with two
    # queries, so this stays one round trip.
    all_spares = list(jobcard.spares.all())

    return render(request, 'workshop/jobcard/jobcard_detail.html', {
        'jobcard': jobcard,
        'inventory_draws': [s for s in all_spares
                            if s.source == JobCardSpareItem.SOURCE_INVENTORY],
        'shop_spares': [s for s in all_spares
                        if s.source == JobCardSpareItem.SOURCE_SHOP],
    })


@staff_required
def jobcard_edit(request, pk):
    """
    Edit an existing Job Card. Pre-populates form and formsets.
    Stays on same page after save with success message.
    """
    jobcard = get_object_or_404(JobCard, pk=pk)

    if request.method == 'POST':
        # Financial Lock: the client disables the form for PAID/BULK_PAID
        # records, but that's UI-only — enforce it here too, since a raw POST
        # would otherwise bypass it entirely. The "Unlock Record" button sets
        # financial_unlock=true before it lets the form submit.
        if jobcard.payment_status in ('PAID', 'BULK_PAID') and request.POST.get('financial_unlock') != 'true':
            messages.error(
                request,
                f"{jobcard.registration_number}'s bill is finalized ({jobcard.get_payment_status_display()}) — "
                f"unlock the record on this page before editing."
            )
            return redirect('jobcard_edit', pk=pk)

        # See jobcard_create: one locked copy binds the card and its parts alike,
        # because `labour_amount` is a price that sits on the card.
        parts_data = _floor_locked_data(request, jobcard)
        form = JobCardForm(parts_data, instance=jobcard)
        concern_formset = JobCardConcernFormSet(request.POST, instance=jobcard, prefix='concerns')
        spare_formset = JobCardSpareFormSet(parts_data, instance=jobcard, prefix='spares')
        inventory_formset = JobCardInventoryFormSet(parts_data, instance=jobcard, prefix='inventory')
        labour_formset = JobCardLabourFormSet(request.POST, instance=jobcard, prefix='labours')

        if (form.is_valid() and concern_formset.is_valid() and spare_formset.is_valid()
                and inventory_formset.is_valid() and labour_formset.is_valid()):
            # Hard block: editing this job card's registration number must not collide
            # with a different job card that's already active for that vehicle. Excludes
            # this job card's own pk, so leaving the registration number unchanged never
            # conflicts with itself.
            registration = form.cleaned_data['registration_number'].strip().upper()
            existing_job = JobCard.get_active_conflict(registration, exclude_pk=jobcard.pk)

            if existing_job:
                vehicle_info = f"{existing_job.brand_name} {existing_job.model_name}" if existing_job.brand_name else registration
                messages.error(
                    request,
                    f'{vehicle_info} ({registration}) already has a different active job card '
                    f'(not yet Completed). Complete or trash that job card first.'
                )
                return render(
                    request,
                    'workshop/jobcard/jobcard_form.html',
                    _form_context(
                        request,
                        form=form,
                        concern_formset=concern_formset,
                        spare_formset=spare_formset,
                        inventory_formset=inventory_formset,
                        labour_formset=labour_formset,
                        jobcard=jobcard,
                    ),
                )

            # AUD-0014: Wrap all formset saves in a single atomic transaction.
            with transaction.atomic():
                form.save()
                saved_concerns = concern_formset.save()
                saved_spares = spare_formset.save()
                inventory_formset.save()
                labour_formset.save()
                
                # AUD-0052: Auto-learn — case-insensitive duplicate check.
                new_concern_texts = [c.concern_text.strip() for c in saved_concerns if c.concern_text and c.concern_text.strip()]
                if new_concern_texts:
                    existing_concern_texts = set()
                    for t in new_concern_texts:
                        if ConcernSolution.objects.filter(concern__iexact=t).exists():
                            existing_concern_texts.add(t)
                    new_concerns = [ConcernSolution(concern=t) for t in new_concern_texts if t not in existing_concern_texts]
                    ConcernSolution.objects.bulk_create(new_concerns, ignore_conflicts=True)
                
                new_spare_names = [s.spare_part_name.strip() for s in saved_spares if s.spare_part_name and s.spare_part_name.strip()]
                if new_spare_names:
                    existing_spare_names = set()
                    for n in new_spare_names:
                        if SparePart.objects.filter(name__iexact=n).exists():
                            existing_spare_names.add(n)
                    new_spare_parts = [SparePart(name=n) for n in new_spare_names if n not in existing_spare_names]
                    SparePart.objects.bulk_create(new_spare_parts, ignore_conflicts=True)

                # AUD-0023: Resolve spare → shop FK using the posted PK, not free-text name.
                all_spares = list(jobcard.spares.filter(source=JobCardSpareItem.SOURCE_SHOP))

                # Active shops, plus any archived one these rows already use.
                shops_by_pk = _resolvable_shops(all_spares)

                shops_to_update = set()
                for spare in all_spares:
                    # The formset does not touch the `shop` FK (it only carries `shop_name`),
                    # so this still holds the shop the row was billed to BEFORE this edit.
                    # It must be refreshed too: updating only the new shop left the old one
                    # still counting a row it no longer owns, showing one Rs1,000 purchase
                    # as Rs1,000 owed to each of two shops, permanently. This path uses
                    # .update(), so the same guard in JobCardSpareItem.save() never runs here.
                    if spare.shop_id:
                        shops_to_update.add(spare.shop_id)
                    # spare_formset.save() just saved the posted PK into spare.shop_name
                    raw_pk = spare.shop_name.strip() if spare.shop_name else ''
                    shop_obj = None
                    if raw_pk:
                        try:
                            shop_obj = shops_by_pk.get(int(raw_pk))
                        except (ValueError, TypeError):
                            shop_obj = None
                    # Set both the FK and the human-readable display name
                    shop_name_val = shop_obj.name if shop_obj else ''
                    JobCardSpareItem.objects.filter(pk=spare.pk).update(
                        shop=shop_obj,
                        shop_name=shop_name_val,
                    )
                    if shop_obj:
                        shops_to_update.add(shop_obj.pk)

                # Delete imported unassigned spares to prevent duplicates
                imported_ids = request.POST.getlist('imported_unassigned_ids')
                if imported_ids:
                    old_items = JobCardSpareItem.objects.filter(pk__in=imported_ids, job_card__isnull=True)
                    for old_item in old_items.select_related('shop'):
                        if old_item.shop_id:
                            shops_to_update.add(old_item.shop_id)
                    old_items.delete()

                # Update totals for every affected shop, old and new alike.
                for shop in SpareShop.objects.filter(pk__in=shops_to_update):
                    shop.update_totals()

                # Recompute the bill explicitly.
                #
                # A spare save still triggers JobCard.update_totals() through the
                # model, but the labour charge no longer does — it is a field on
                # the card now, written by form.save(), and a card whose ONLY
                # change was its labour figure would otherwise keep the old
                # total_bill_amount forever. update_totals() no-ops when nothing
                # moved, so calling it here costs one aggregate and closes that.
                jobcard.update_totals()

                # Re-read before deciding whether a settled bill still adds up.
                jobcard.refresh_from_db()
                reopened = _reconcile_settled_bill(jobcard)

            messages.success(request, f'Job card for {jobcard.registration_number} updated successfully!')
            if reopened:
                if jobcard.payment_status in ('PARTIAL', 'PENDING'):
                    messages.warning(
                        request,
                        f"The bill changed after this card was settled by "
                        f"{jobcard.bulk_payer.customer_name if jobcard.bulk_payer_id else 'the Fleet Account'} — "
                        f"₹{jobcard.get_balance_amount:,.0f} is now outstanding again and will be "
                        f"picked up by that account's next payment."
                    )
                else:
                    messages.warning(
                        request,
                        f"The bill changed after this card was paid. The unpaid "
                        f"₹{jobcard.discount_amount:,.0f} is now booked as a discount — "
                        f"collect it and re-enter the payment if that is wrong."
                    )
            
            # Smart Redirect based on original context
            next_url = request.GET.get('next')
            if next_url == 'mini':
                return redirect('live_report')
                
            return redirect('jobcard_edit', pk=jobcard.pk)
    else:
        form = JobCardForm(instance=jobcard)
        concern_formset = JobCardConcernFormSet(instance=jobcard, prefix='concerns')
        spare_formset = JobCardSpareFormSet(instance=jobcard, prefix='spares')
        inventory_formset = JobCardInventoryFormSet(instance=jobcard, prefix='inventory')
        labour_formset = JobCardLabourFormSet(instance=jobcard, prefix='labours')

    # See jobcard_create: arriving here on a POST means nothing was written.
    problems = []
    if request.method == 'POST':
        problems = _problems_for(
            request, form, concern_formset, labour_formset,
            inventory_formset, spare_formset,
            subject=jobcard.registration_number)

    return render(
        request,
        'workshop/jobcard/jobcard_form.html',
        _form_context(
            request,
            form=form,
            concern_formset=concern_formset,
            spare_formset=spare_formset,
            inventory_formset=inventory_formset,
            labour_formset=labour_formset,
            jobcard=jobcard,
            problems=problems,
        ),
    )


@office_required
def jobcard_delete(request, pk):
    """
    Permanently delete a job card (Owner + Office). Logged to the Owner-only
    Deletion History; there is no restore.

    GUARD: a job card carrying financial/work data cannot be deleted. Its parts —
    from either section — must first be removed (shop spares can also be moved to
    Unassigned), and its labour cleared. This makes deletion a deliberate act and
    prevents accidental loss of a car's financial history. Because a deletable card
    holds no parts at all, no warehouse stock is affected by the delete.
    """
    jobcard = get_object_or_404(JobCard, pk=pk)

    # Anything that makes this card financially/operationally "heavy" blocks delete.
    blockers = []
    if jobcard.spares.filter(source=JobCardSpareItem.SOURCE_INVENTORY).exists():
        blockers.append("inventory items")
    if jobcard.spares.filter(source=JobCardSpareItem.SOURCE_SHOP).exists():
        blockers.append("spare parts")
    if jobcard.labours.exists():
        blockers.append("jobs performed")
    # The charge itself, which now lives on the card rather than on the lines.
    # Without this a card whose job lines were cleared but whose labour figure
    # was left standing would delete with real money still on it.
    if (jobcard.labour_amount or 0) > 0:
        blockers.append("a labour charge")
    if (jobcard.received_amount or 0) > 0:
        blockers.append("a received payment")

    if request.method != 'POST':
        return render(request, 'workshop/jobcard/jobcard_confirm_delete.html', {
            'jobcard': jobcard,
            'blockers': blockers,
        })

    if blockers:
        messages.error(
            request,
            f"Can't delete {jobcard.registration_number}: it still has {', '.join(blockers)}. "
            "Clear both parts sections (shop spares can be moved to Unassigned instead) "
            "and remove the labour first."
        )
        return redirect('jobcard_detail', pk=pk)

    reason = request.POST.get('reason', '').strip()
    reg = jobcard.registration_number
    label = f"{jobcard.bill_number or '#' + str(jobcard.pk)} · {reg}"

    with transaction.atomic():
        DeletionLog.record(
            DeletionLog.ENTITY_JOBCARD, jobcard,
            user=request.user, reason=reason, amount=jobcard.total_bill_amount, label=label,
            extra={'concerns': [c.concern_text for c in jobcard.concerns.all()]},
        )
        jobcard.delete()

    messages.success(request, f"Job Card {reg} permanently deleted (logged to Deletion History).")
    return redirect('jobcard_list')
