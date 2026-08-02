"""
One implementation of "is this a usable rupee amount?".

Every screen that takes a typed amount needs the same three answers, and the
audit of 2026-08-02 found the same three holes in Salary & Advance and in the
Cashbook independently — which is the argument for this living in one place
rather than being re-derived per view:

  * **Out of range.** A figure too large for its column behaved differently on
    each database. SQLite stored `999999999999` in a `numeric(10,2)` field,
    silently violating the declared precision and poisoning every later SUM;
    PostgreSQL — what actually ships — raises `numeric field overflow`, so the
    same typo is a 500. Neither is an answer to a fat finger. The bound is read
    from `max_digits`/`decimal_places`, so it cannot drift from the schema.

  * **NaN and Infinity.** Both parse as perfectly valid `Decimal`s. `NaN`
    compares False against everything, so a bare `amount > 0` guard lets it
    through in one direction and `Infinity` sails through in the other. Either
    one in a money column makes every aggregate that touches it meaningless.

  * **Not a number at all.** Returns None rather than raising, so callers stay
    a simple `if amount is None: <message>` instead of each inventing its own
    try/except.
"""
from decimal import Decimal, ROUND_HALF_UP


def parse_money(raw, model, field_name, allow_zero=False):
    """
    Parse a posted amount against the column that will store it.

    Returns a Decimal quantised to the field's precision, or None if the value
    is unusable. Callers decide what to tell the user.
    """
    try:
        value = Decimal(str(raw).strip())
    except Exception:
        return None
    if not value.is_finite():          # NaN, Infinity, -Infinity
        return None

    field = model._meta.get_field(field_name)
    limit = Decimal(10) ** (field.max_digits - field.decimal_places)
    if value >= limit or -value >= limit:
        return None
    if value < 0 or (value == 0 and not allow_zero):
        return None

    exponent = Decimal(1).scaleb(-field.decimal_places)
    return value.quantize(exponent, rounding=ROUND_HALF_UP)


def fit_text(value, model, field_name):
    """
    Trim a posted string to what its column can hold.

    A 400-character note into a `max_length=255` field is another
    SQLite-accepts / Postgres-500s split. Truncating matches how
    `_build_unassigned_spare` already handles an oversized name: keep the
    record, lose the overflow, never crash on a typo.
    """
    if value is None:
        return None
    limit = model._meta.get_field(field_name).max_length
    return value[:limit] if limit else value
