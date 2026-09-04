"""GREEN -- nothing here reaches the outside world.

A file with no outbound write passes for the same reason a file with a confirmed
one does: there is nothing left unconfirmed.  It is here because "scanned, found
nothing" and "did not scan" must not share an exit code -- the checker's 0 has
to mean the first, and a file like this is the cheapest way to hold that down.

Note what it deliberately contains: ``uuid.uuid4()`` used as a trace id, and a
``for attempt in range(...)`` retry loop.  Neither is a defect without an
outbound write, and both would fire under a rule that looked for the tell
instead of the effect.
"""

import uuid


def build_summary(rows: list[dict]) -> dict:
    trace_id = uuid.uuid4().hex
    totals: dict[str, int] = {}
    for row in rows:
        totals[row["kind"]] = totals.get(row["kind"], 0) + int(row["count"])
    return {"trace_id": trace_id, "totals": totals, "row_count": len(rows)}


def parse_with_retry(text: str) -> dict:
    import json

    last_error = None
    for attempt in range(1, 4):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            last_error = exc
            text = text.strip().strip(",")
    raise ValueError("could not parse payload") from last_error
