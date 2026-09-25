"""The type boundary between computed figures and everything outside.

Numeric columns hand back Decimal and Date columns hand back date. Both are
fine inside Python and neither is JSON. Two places in this application have to
cross that gap: the cache write, where a report is persisted as JSON, and the
prompt, where computed figures are handed to the model.

Both use the same function, and it is strict on purpose. The obvious
alternative, json.dumps(..., default=str), never fails - it quietly turns
Decimal("0.2727272727272727272727272727") into a 28-character string. At the
cache boundary that produced rows whose numbers came back as text and crashed
the formatters a day later. At the prompt boundary it meant the model was
asked to interpret "2500" rather than 2500, which undercuts the rule that the
model narrates figures it is given rather than deriving them.

So an unexpected type raises here rather than being stringified. Failing at
the boundary is cheap; discovering the failure in a cached row or a published
verdict is not.
"""

from decimal import Decimal


def to_jsonable(v):
    """Decimals become floats, dates become ISO strings.

    Anything else unexpected fails loudly instead of being silently
    stringified. Used as the `default=` hook for json.dumps, so it is called
    only for types json does not already handle.
    """
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "isoformat"):  # date, datetime
        return v.isoformat()
    raise TypeError(f"Not JSON serializable: {type(v)}")
