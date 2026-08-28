from decimal import Decimal

from django.conf import settings
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
from django.contrib.humanize.templatetags.humanize import intcomma
from django.template.defaultfilters import floatformat

from ..decorators import staff_required, office_required, is_office_or_owner
# The app's ONE way of printing a quantity — 1.00 → "1", 1.50 → "1.5". Imported
# rather than restated so the read-only card cannot disagree with every other
# screen about how many of something there are.
from ..templatetags.custom_filters import clean_qty




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


def _photo_context(jobcard):
    """
    What the job-card form needs to render the photo box, and nothing more.

    Three deliberate properties.

    **It is silent on an unsaved card.** `jobcard` is None on the create screen,
    so `photo_subject_id` is None and `_photo_box.html` renders nothing. A
    photo needs a row to attach to and a brand-new card has no primary key —
    and this is not a workaround so much as the real workflow: nobody
    photographs a car while typing its registration, they do it when it is on
    the ramp and the card already exists.

    **It is silent with no storage configured**, the same degradation Web Push
    has with no VAPID keys. The section is optional; a deploy without R2
    credentials simply has no photo box, and every other thing on this form
    behaves identically.

    **`can_edit` mirrors the Financial Lock.** A settled card's photos can be
    looked at and not changed, which is the same boundary the money stops at.
    The server enforces it again in `views/photos.py` — this only decides
    whether the camera opens, and a page cannot be the rule.
    """
    from .. import photos as photo_storage
    from ..models import JobCardPhoto

    if not photo_storage.photos_are_configured():
        return {'photos_configured': False, 'photo_subject_id': None}

    if jobcard is None:
        return {'photos_configured': True, 'photo_subject_id': None}

    return {
        'photos_configured': True,
        'photo_subject': 'card',
        'photo_subject_id': jobcard.pk,
        'photo_count': JobCardPhoto.objects.filter(job_card=jobcard).count(),
        'photo_limit': settings.PHOTO_LIMIT_CAR,
        # Each spare row includes the same box with its own subject and count;
        # only the ceiling differs, so it travels separately. The per-row counts
        # are annotated onto the formset queryset, never counted in the
        # template — a card can carry dozens of parts.
        'spare_photo_limit': settings.PHOTO_LIMIT_SPARE,
        'photo_can_edit': jobcard.payment_status not in ('PAID', 'BULK_PAID'),
    }


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
        **_photo_context(jobcard),
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


@office_required
def jobcard_detail(request, pk):
    """
    The job card, read only. OFFICE AND OWNER ONLY.

    It was `@staff_required` until 2026-08-18 and Floor really could reach it —
    by URL, and by the "View" button in the Vehicles-in-Workshop sidebar on the
    new-job-card screen, which is a Floor page. What kept that honest was a pile
    of gates INSIDE the template hiding the customer and every figure.

    Closing the door replaces all of them, and the reason is the layout rather
    than the secrecy. The page is now three lines of identity and four lists,
    with no labels — the owner's own design, and it only works because position
    carries the meaning. Line 2 runs mileage, mechanic, customer and phone
    number into one comma-separated line, and every part sets the workshop's
    COST beside the customer's price. There is no version of either that is safe
    to show a mechanic with some words removed: what Floor would get is a line
    with holes in it and a parts list where the figures sometimes appear, which
    is worse than not having the page.

    FLOOR LOSES NOTHING IT CANNOT GET SOMEWHERE BETTER. The dashboard car card's
    live-details drawer is these same four lists, on the board Floor works from
    all day, and the job card itself is still `@staff_required`. The one
    Floor-visible link is gated in the same edit that closed this view, which is
    the `InvoiceLinkVisibilityTests` rule: a template gate must mirror its view's
    decorator, in both directions — a door Floor can see but not open is worse
    than no door.

    What each part PRINTS is built here too, by `_describe_spare` — "join the
    values that exist", which a template does as a chain of `{% if %}`s that has
    to get every separator right, and gets wrong on the row with no shop. The
    identity line's separators are the one exception and are drawn in CSS
    (`.dv-fact + .dv-fact::before`), where a missing value cannot leave one
    behind at all.
    """
    jobcard = get_object_or_404(
        JobCard.objects.select_related('lead_mechanic')
                       .prefetch_related('concerns', 'spares__shop', 'labours'),
        pk=pk
    )

    # Split the one relation for display, mirroring the two sections on the edit
    # form. Partitioned in Python off the existing prefetch rather than with two
    # queries, so this stays one round trip.
    all_spares = list(jobcard.spares.all())
    shop_spares = [s for s in all_spares if s.source == JobCardSpareItem.SOURCE_SHOP]
    draws = [s for s in all_spares if s.source == JobCardSpareItem.SOURCE_INVENTORY]

    # The card's own year, so a part's dates can drop theirs. See
    # `_describe_spare` — this is the comparison, not a formatting preference.
    year = jobcard.admitted_date.year if jobcard.admitted_date else None
    for spare in shop_spares:
        _describe_spare(spare, is_draw=False, card_year=year)
    for draw in draws:
        _describe_spare(draw, is_draw=True, card_year=year)

    # The three section subtotals, summed here off the SAME lists the page
    # prints — never re-queried. `update_totals()` is
    # `Σ spares.total_price + labour_amount` over BOTH routes, so these three
    # add up to the bill on the money line at the foot, exactly. That is the
    # point of showing them: the total becomes checkable by eye instead of
    # taken on trust, and it costs no query, because a second aggregate could
    # disagree with the rows above it.
    def _sum(rows):
        return sum((r.total_price or Decimal('0')) for r in rows)

    return render(request, 'workshop/jobcard/jobcard_detail.html', {
        'jobcard': jobcard,
        'inventory_draws': draws,
        'shop_spares': shop_spares,
        'stages': _lifecycle(jobcard),
        'span': _time_in_workshop(jobcard),
        'draws_total': _sum(draws) or None,
        'spares_total': _sum(shop_spares) or None,
    })


#: The three moments a job card has, in order, paired with the column each one
#: reads. Kept as data rather than as three branches so the template loops once
#: and no date can be drawn differently from its neighbours.
LIFECYCLE = ('Admitted', 'Completed', 'Settled')


def _lifecycle(jobcard):
    """
    The card's three dates, ready to print: admitted, completed, settled.

    THE THIRD IS CALLED **SETTLED**, NOT "BILLED", AND THE WORD IS THE POINT.
    `paid_date` is written only when `payment_status` becomes PAID/BULK_PAID, so
    it is the day the money was taken — and "billed" already means something else
    on a screen an owner reads in the same sitting: Deep Analysis calls a
    Supplies Shop purchase "billed" precisely BECAUSE it is not yet a cost. There
    is no separate bill-issue date on a job card to point at either: the bill
    exists from the moment the card does, since `bill_number` is assigned on
    first save. So a stage called "Billed" would either restate the admitted date
    or quietly mean settled. It means settled, so it says settled — the same word
    the lock chip on this page and the settle dialog already use.

    ALL THREE ARE ALWAYS RETURNED, with `date=None` where nothing has happened
    yet, and the page prints a dash there. A fixed structure is what makes this
    page learnable, the same rule that keeps an empty section drawn rather than
    omitted.

    The third one earns its column on FLEET cards. A walk-in has exactly one
    payment event and it happens at pickup, so settled and completed are the same
    day and the column repeats — but a fleet collector comes round weeks or
    months later against several months of cars, and those are the largest single
    receipts the workshop takes. That is the case worth having a column for, and
    it is precisely the case the demo seeders flatten: all three of them write
    `paid_date` from `completed_date`, so no measurement taken against seeded
    data can say anything about this.

    Each date is read from its OWN column and never inferred from the one before
    it, so a card that reached a state out of order still prints honestly rather
    than the page inventing a sequence the data does not support.
    """
    # `paid_date` is a DateTimeField while the other two are DateFields. Take it
    # through localtime() or a payment made late on an IST evening is filed under
    # the previous day — the same rule every "today" in this codebase follows.
    paid_on = None
    if jobcard.paid_date:
        paid_on = timezone.localtime(jobcard.paid_date).date()

    settled = jobcard.payment_status in ('PAID', 'BULK_PAID')
    dates = (
        jobcard.admitted_date,
        jobcard.completed_date if jobcard.completed else None,
        paid_on if settled else None,
    )
    return [{'label': label, 'date': when}
            for label, when in zip(LIFECYCLE, dates)]


def _time_in_workshop(jobcard):
    """
    How long this car has been here, as one ready phrase — or None when there
    is nothing honest to say.

    The page prints TWO dates, admitted and completed, and the fact worth having
    between them is the one neither of them states: the gap. An owner reading
    "06/06/2026" and "08/06/2026" is doing subtraction to answer "how long did we
    hold this car", which is the question they actually opened the card with.

    ONLY TWO DATES, DELIBERATELY. `paid_date` is tracked and is NOT shown here:
    measured over the 150 settled cards in the demo set, 149 of them were settled
    on the very day they were completed, so a third date would print the same
    number twice on all but one card — the rule the money line at the foot of
    this page already follows. What the payment side needs is a STATE, not a
    date, and the chip down there already carries it.

    An OPEN card counts to today and says so ("12 days in"), because a car still
    on the floor is the case where the number is actually changing and worth
    watching. `localdate()`, never `date.today()`: the server can run in UTC
    while the workshop works in IST, and near midnight the two disagree about
    which day it is — which on a counter that starts at "Today" would be a
    visible off-by-one.

    Nothing is returned when the arithmetic would be nonsense: no admitted date,
    or a completion dated before the admission. A negative day count is a typo
    somewhere upstream, and printing "-3 days" would make this page the one that
    looks broken rather than the data.
    """
    if not jobcard.admitted_date:
        return None

    end = jobcard.completed_date if jobcard.completed else timezone.localdate()
    if not end:
        return None

    days = (end - jobcard.admitted_date).days
    if days < 0:
        return None

    if jobcard.completed:
        text = 'Same day' if days == 0 else ('1 day' if days == 1 else '%d days' % days)
    else:
        text = 'Today' if days == 0 else ('1 day in' if days == 1 else '%d days in' % days)

    return {'text': text, 'open': not jobcard.completed}


def _describe_spare(spare, is_draw, card_year=None):
    """
    Everything the read-only card prints about one part, as three ready strings
    on the object: `meta_line`, `cost_str` and `price_str`.

    Three rather than one because the page puts them in different places — the
    facts read left-to-right under the part's name, the two figures sit
    right-aligned in their own column so they form a line you can run an eye
    down, and the cost is drawn quieter than the price. Joined into a single
    string they ran together as "10/07/2026 – 10/07/2026 · ₹5,727 – ₹7,967",
    where the eye had to find the ₹ to know where the dates stopped. That is
    what the owner asked to have fixed.

    Built here rather than in the template because a template doing this ends up
    as a chain of `{% if %}`s that has to get every separator right, and gets it
    wrong on the row with no shop. No captions in any of them — that is the
    page's whole rule.

    THE TWO ROUTES DIFFER, and the difference is the `source` rule this codebase
    keeps everywhere. A warehouse draw came off the shelf already fitted: no
    shop, no order, no arrival, so it has no dates and no supplier. It also
    prints ONE figure, because its `unit_price` is the warehouse average PER
    UNIT while a shop row's is what the shop billed for the whole line — setting
    those two either side of one dash would be two kinds of number pretending to
    be a range.
    """
    meta = []

    if not is_draw:
        # The pair is ONE item, with an em dash for the half not in yet: a spare
        # is finished when it has been ordered AND received, so half-filled is
        # still incomplete. Same rule the job card's date chip follows.
        if spare.ordered_date or spare.received_date:
            ordered = _short_date(spare.ordered_date, card_year)
            received = _short_date(spare.received_date, card_year)
            meta.append(f'{ordered} – {received}')

        if spare.shop_id and spare.shop:
            meta.append(spare.shop.name)

    # Only above one — this workshop writes a quantity down only when there is
    # more than one of something, which the invoice and the Live Report follow.
    if spare.quantity is not None and spare.quantity > 1:
        meta.append(f'× {clean_qty(spare.quantity)}')

    def rupees(value):
        return None if value is None else f'₹{intcomma(floatformat(value, 0))}'

    spare.meta_line = ' · '.join(meta)
    spare.cost_str = None if is_draw else rupees(spare.unit_price)
    spare.price_str = rupees(spare.total_price)


def _short_date(value, card_year):
    """
    A part's date, with the YEAR dropped when it is the card's own.

    Not a formatting preference — a width fix with a measurement behind it. The
    full pair plus a shop name ("16/07/2026 – 17/07/2026 · Spare club") is 38
    characters and wrapped to two lines on a 375px phone, so rows in the same
    list came out different heights and the list read as broken. Dropping a
    year that is already stated twice in the card above takes it to 30 and it
    fits.

    The year is KEPT the moment it differs, because then it is the whole point:
    a part ordered in December for a car admitted in January is the one case
    where the reader must not have to assume. Both halves are compared
    separately, so a pair that straddles New Year prints one short and one long
    rather than hiding the crossing.

    An em dash for the half not in yet: a spare is finished when it has been
    ordered AND received, so half-filled is still incomplete — the rule the job
    card's own date chip follows.
    """
    if value is None:
        return '—'
    if card_year is not None and value.year == card_year:
        return value.strftime('%d/%m')
    return value.strftime('%d/%m/%Y')


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
