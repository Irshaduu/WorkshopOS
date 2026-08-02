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
    field = SparePart._meta.get_field('name')
    new_name = _collapse(new_name)[:field.max_length]
    old_name = spare.name

    existing = SparePart.objects.filter(name__iexact=new_name).exclude(pk=spare.pk).first()
    final_name = existing.name if existing else new_name

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
def rename_concern(concern, new_text, user=None):
    """
    Rename one master concern. Returns (final_text, merged). Same rules as
    rename_spare — propagate to job cards, merge on a case-insensitive match.
    """
    new_text = _collapse(new_text)
    old_text = concern.concern

    existing = ConcernSolution.objects.filter(
        concern__iexact=new_text).exclude(pk=concern.pk).first()
    final_text = existing.concern if existing else new_text

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
