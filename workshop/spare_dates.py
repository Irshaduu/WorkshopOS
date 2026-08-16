"""
What a valid ordered/received pair looks like — one module, no views.

A spare part is ordered from a shop and then arrives. Those are two dates on one
row, and there is exactly one mistake the pair can express that neither date can
express alone: **arriving before it was ordered.** Nothing in the schema stops
it, both boxes are independent `<input type="date">`, and the result is a row
that reads as time travel on the shop's ledger and on the printed history.

The rule lived only in `views/spare_shop._clean_spare_dates`, which guards the
Unassigned Spares hub — so an unassigned purchase was checked and the *same two
boxes on a job card* were not. That is where most spares are actually entered.
Extracting it here rather than copying it into the job-card form is the point:
two implementations of "is this pair the right way round" would be two answers
free to disagree, and they would disagree on the ledger.

Pure functions over dates. `_clean_spare_dates` parses raw POST text and then
calls this; the job-card form gets real `date` objects from its own DateFields
and calls it directly.
"""

from django.utils import timezone


def pair_problem(ordered, received):
    """
    What is wrong with this ordered/received pair, or None if nothing is.

    Either may be None — a part ordered and not yet arrived is the normal
    mid-workflow state, and a row with neither date is simply unfilled. Only a
    pair where BOTH are present can be the wrong way round.

    A FUTURE date is refused as well, and the two callers need that for slightly
    different reasons: an unassigned row is created already RECEIVED, so it
    cannot have arrived on a day that has not come; a job-card spare could in
    principle be pre-ordered, but a date after today is far more often a typed
    year — 2027 for 2026 — than a plan, and the workshop has no forward-ordering
    workflow to protect. Same reasoning as `_parse_money`'s future-advance
    refusal.
    """
    today = timezone.localdate()

    if ordered and ordered > today:
        return "Ordered date cannot be in the future."
    if received and received > today:
        return "Received date cannot be in the future."
    if ordered and received and received < ordered:
        return (
            "Received date cannot be before the ordered date — "
            "this part would have arrived before it was ordered."
        )
    return None
