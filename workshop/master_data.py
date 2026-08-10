"""
Shared master-data operations — one implementation of the rename rule.

The Spare Parts and Concerns master lists are reachable from two screens, and
both offer a rename: Master Lists (`views/master_lists.py`) and Data Cleanup
(`cleanup_views.py`). Until 2026-08-02 they were two implementations of one
rule and behaved differently on the same row:

  * Data Cleanup deduped case-insensitively, merged into an existing entry,
    and rewrote every job-card line that used the old name.
  * Master Lists saved a plain ModelForm — no dedupe (so "Oil Filter" and
    "oil filter" could coexist, which the taxonomy rule in CLAUDE.md exists to
    prevent), and no propagation, so a rename left the job-card history
    stranded on the old spelling while the master list moved on.

Which screen someone happened to open decided what a rename meant. These
functions are the single implementation both now call.
"""
from django.db import transaction
from django.db.models.functions import Lower, Trim

from .models import (
    SparePart, ConcernSolution, JobCardSpareItem, JobCardConcern, DeletionLog,
)


def spare_usage_count(name):
    """How many job-card lines carry this spare's name (SHOP rows only)."""
    return JobCardSpareItem.objects.filter(
        source=JobCardSpareItem.SOURCE_SHOP,
        spare_part_name__iexact=(name or '').strip(),
    ).count()


def concern_usage_count(text):
    """How many job-card concerns carry this text."""
    return JobCardConcern.objects.filter(
        concern_text__iexact=(text or '').strip()).count()


def brand_usage_count(name):
    """How many job cards carry this brand's name."""
    from .models import JobCard
    return JobCard.objects.filter(brand_name__iexact=(name or '').strip()).count()


def model_usage_count(brand_name, name):
    """How many job cards of that brand carry this model name.

    Scoped to the brand for the same reason `rename_model` is: Toyota's
    "Corolla" and another make's are different cars.
    """
    from .models import JobCard
    return JobCard.objects.filter(
        brand_name__iexact=(brand_name or '').strip(),
        model_name__iexact=(name or '').strip()).count()


# ---------------------------------------------------------------------------
# Would this rename MERGE?
#
# Each `*_rename_target` normalises the typed name and finds the entry the
# rename would merge into, returning `(final_name, existing_or_None)`. The
# matching `rename_*` calls it, and so does the view that renders the
# confirmation page — so the page cannot promise one outcome while the rename
# performs another. Keeping that as ONE lookup per entity is the same rule that
# put the rename itself in this module: two implementations of "does this
# collide" would be two answers free to disagree, and the disagreement would
# only show up as a merge nobody was warned about.
# ---------------------------------------------------------------------------

def spare_rename_target(spare, new_name):
    field = SparePart._meta.get_field('name')
    new_name = _collapse(new_name)[:field.max_length]
    existing = SparePart.objects.filter(
        name__iexact=new_name).exclude(pk=spare.pk).first()
    return (existing.name if existing else new_name), existing


def concern_rename_target(concern, new_text):
    new_text = _collapse(new_text)
    existing = ConcernSolution.objects.filter(
        concern__iexact=new_text).exclude(pk=concern.pk).first()
    return (existing.concern if existing else new_text), existing


def brand_rename_target(brand, new_name):
    from .models import CarBrand
    field = CarBrand._meta.get_field('name')
    new_name = _collapse(new_name)[:field.max_length]
    existing = CarBrand.objects.filter(
        name__iexact=new_name).exclude(pk=brand.pk).first()
    return (existing.name if existing else new_name), existing


def model_rename_target(model, new_name):
    from .models import CarModel
    field = CarModel._meta.get_field('name')
    new_name = _collapse(new_name)[:field.max_length]
    existing = CarModel.objects.filter(
        brand=model.brand, name__iexact=new_name).exclude(pk=model.pk).first()
    return (existing.name if existing else new_name), existing


MERGE_CONFIRM_TEMPLATE = 'workshop/manage/master_confirm_merge.html'


def merge_preview(obj, new_name):
    """Describe the merge this rename would perform, or None if it only renames.

    A rename that lands on a name already in the list is a MERGE: the row being
    edited is deleted and every job card carrying its wording is relabelled onto
    the survivor's. That is the right tool for two spellings of one part, and it
    is also the one irreversible thing on these screens — renaming back does not
    undo it, because it would then drag the survivor's own rows along too.

    It used to happen with no warning: the only sign was the success message
    afterwards, by which point the history had already moved. This is what the
    confirmation page reads.

    Returns pure data, never an HttpResponse, so Master Lists and Data Cleanup
    can each render it their own way while agreeing on what it says.
    """
    from .models import CarBrand, CarModel

    if isinstance(obj, SparePart):
        final, existing = spare_rename_target(obj, new_name)
        if not existing:
            return None
        return {
            'kind': 'Spare Part', 'usage_noun': 'job-card part line',
            'from_label': obj.name, 'from_usage': spare_usage_count(obj.name),
            'into_label': existing.name, 'into_usage': spare_usage_count(existing.name),
            'final_name': final, 'extra': [],
        }

    if isinstance(obj, ConcernSolution):
        final, existing = concern_rename_target(obj, new_name)
        if not existing:
            return None
        return {
            'kind': 'Concern', 'usage_noun': 'job-card concern',
            'from_label': obj.concern, 'from_usage': concern_usage_count(obj.concern),
            'into_label': existing.concern,
            'into_usage': concern_usage_count(existing.concern),
            'final_name': final, 'extra': [],
        }

    if isinstance(obj, CarBrand):
        final, existing = brand_rename_target(obj, new_name)
        if not existing:
            return None
        absorbed, dropped = brand_merge_model_split(obj, existing)
        extra = []
        if absorbed:
            extra.append(f"{len(absorbed)} model(s) move across: {', '.join(absorbed)}")
        if dropped:
            extra.append(
                f"{len(dropped)} model(s) already exist under '{existing.name}' and "
                f"will be dropped: {', '.join(dropped)}")
        return {
            'kind': 'Car Brand', 'usage_noun': 'job card',
            'from_label': obj.name, 'from_usage': brand_usage_count(obj.name),
            'into_label': existing.name, 'into_usage': brand_usage_count(existing.name),
            'final_name': final, 'extra': extra,
        }

    if isinstance(obj, CarModel):
        final, existing = model_rename_target(obj, new_name)
        if not existing:
            return None
        brand_name = obj.brand.name
        return {
            'kind': 'Car Model', 'usage_noun': f'{brand_name} job card',
            'from_label': f'{brand_name} {obj.name}',
            'from_usage': model_usage_count(brand_name, obj.name),
            'into_label': f'{brand_name} {existing.name}',
            'into_usage': model_usage_count(brand_name, existing.name),
            'final_name': final, 'extra': [],
        }

    raise TypeError(f"merge_preview does not handle {type(obj).__name__}")


def brand_merge_model_split(brand, survivor):
    """Which of `brand`'s models would move across, and which would be dropped.

    A brand merge carries the dying brand's models to the survivor, except any
    whose name already exists there — `CarModel` is
    `unique_together('brand', 'name')`, so moving one would violate it. Returns
    `(absorbed, dropped)` as sorted name lists, for the confirmation page to
    show before the merge rather than the DeletionLog to record after it.
    """
    kept = {n.lower() for n in survivor.models.values_list('name', flat=True)}
    absorbed, dropped = [], []
    for name in brand.models.values_list('name', flat=True):
        (dropped if name.lower() in kept else absorbed).append(name)
    return sorted(absorbed), sorted(dropped)


def _collapse(text):
    """Trim and collapse runs of whitespace, without touching case.

    Deliberately NOT `.title()`. Data Cleanup used to title-case, which turns
    "ABS Sensor" into "Abs Sensor"; the master lists did not. Preserving what
    was typed is the behaviour that can't surprise anyone, and dedupe is
    case-insensitive anyway, so casing no longer decides whether a duplicate
    is created.
    """
    return ' '.join((text or '').split())


@transaction.atomic
def rename_spare(spare, new_name, user=None):
    """
    Rename one master spare part. Returns (final_name, merged).

    Job-card lines carrying the old name are rewritten so history stays in step
    with the list. If the new name already exists (case-insensitively) this is a
    MERGE: the duplicate row is deleted and the surviving entry's own spelling
    wins, so the master list and the job cards can never end up saying the same
    thing two ways.
    """
    old_name = spare.name
    final_name, existing = spare_rename_target(spare, new_name)

    # SHOP rows only.
    #
    # This is the Spare Parts master list, which only feeds the free-text
    # shop-purchase autocomplete. An inventory draw takes its name from the
    # `Item` it points at, so renaming it from here would put a job card's
    # displayed name out of step with the product it is actually linked to.
    # Rename a stock product on its supplier catalog instead.
    #
    # `.update()` deliberately bypasses signals, which is safe *because* this is
    # scoped to shop rows: they move no stock. It would not be safe on an
    # inventory draw.
    moved = JobCardSpareItem.objects.filter(
        source=JobCardSpareItem.SOURCE_SHOP,
        spare_part_name__iexact=old_name,
    ).update(spare_part_name=final_name)

    if existing:
        # A merge removes a master row, so it is logged like any other permanent
        # delete — the point being that "where did 'Wheel Bearing Front Left'
        # go?" has an answer months later. No job card, amount or ledger moves;
        # the lines are relabelled onto the surviving wording.
        DeletionLog.record(
            DeletionLog.ENTITY_MASTER_DATA, spare, user=user,
            reason=f"Merged into '{final_name}'",
            label=f"Spare part '{old_name}' merged into '{final_name}'",
            extra={'merged_into': final_name, 'job_card_lines_relabelled': moved},
        )
        spare.delete()
        return final_name, True

    spare.name = final_name
    spare.save(update_fields=['name'])
    return final_name, False


@transaction.atomic
def rename_brand(brand, new_name, user=None):
    """
    Rename a car brand, updating every job card that carries the old name.
    Returns (final_name, merged).

    Brands were the asymmetry: renaming a spare or a concern reached the job
    cards, renaming a brand did not. Reports group by `JobCard.brand_name`
    (`_insight_vehicles`), so a brand typed "Toyta" on one card stayed a
    permanent second brand in Deep Analysis — and correcting the master list
    changed nothing, which is the least useful place for a fix to fail.

    A merge also has to take the dying brand's MODELS with it, and
    `CarModel` is `unique_together('brand', 'name')` — so a model whose name
    already exists under the surviving brand is dropped rather than moved,
    which would violate the constraint.
    """
    from .models import JobCard

    old_name = brand.name
    final_name, existing = brand_rename_target(brand, new_name)

    moved = JobCard.objects.filter(brand_name__iexact=old_name).update(brand_name=final_name)

    if existing:
        # Split by the same helper the confirmation page reads, so the models
        # the page says will be dropped are exactly the ones that are.
        absorbed, dropped = brand_merge_model_split(brand, existing)
        brand.models.filter(name__in=dropped).delete()
        brand.models.filter(name__in=absorbed).update(brand=existing)
        DeletionLog.record(
            DeletionLog.ENTITY_MASTER_DATA, brand, user=user,
            reason=f"Merged into '{final_name}'",
            label=f"Brand '{old_name}' merged into '{final_name}'",
            extra={'merged_into': final_name, 'job_cards_relabelled': moved,
                   'models_moved': absorbed, 'models_dropped_as_duplicates': dropped},
        )
        brand.delete()
        return final_name, True

    brand.name = final_name
    brand.save(update_fields=['name'])
    return final_name, False


@transaction.atomic
def rename_model(model, new_name, user=None):
    """
    Rename a car model, updating the job cards of that BRAND which carry the old
    model name. Returns (final_name, merged).

    Scoped to the brand on purpose: "Corolla" under Toyota and a same-named
    model under another make are different cars, which is exactly what
    `unique_together('brand', 'name')` already says.
    """
    from .models import JobCard

    old_name = model.name
    brand_name = model.brand.name
    final_name, existing = model_rename_target(model, new_name)

    moved = JobCard.objects.filter(
        brand_name__iexact=brand_name, model_name__iexact=old_name,
    ).update(model_name=final_name)

    if existing:
        DeletionLog.record(
            DeletionLog.ENTITY_MASTER_DATA, model, user=user,
            reason=f"Merged into '{final_name}'",
            label=f"Model '{brand_name} {old_name}' merged into '{final_name}'",
            extra={'merged_into': final_name, 'job_cards_relabelled': moved},
        )
        model.delete()
        return final_name, True

    model.name = final_name
    model.save(update_fields=['name'])
    return final_name, False


@transaction.atomic
def rename_concern(concern, new_text, user=None):
    """
    Rename one master concern. Returns (final_text, merged). Same rules as
    rename_spare — propagate to job cards, merge on a case-insensitive match.
    """
    old_text = concern.concern
    final_text, existing = concern_rename_target(concern, new_text)

    moved = JobCardConcern.objects.filter(concern_text__iexact=old_text).update(
        concern_text=final_text)

    if existing:
        DeletionLog.record(
            DeletionLog.ENTITY_MASTER_DATA, concern, user=user,
            reason=f"Merged into '{final_text[:80]}'",
            label=f"Concern '{old_text[:60]}' merged into '{final_text[:60]}'",
            extra={'merged_into': final_text, 'job_card_lines_relabelled': moved},
        )
        concern.delete()
        return final_text, True

    concern.concern = final_text
    concern.save(update_fields=['concern'])
    return final_text, False
