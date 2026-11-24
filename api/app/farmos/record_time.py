"""When a fact happened (OrigamiFarmOS backend/app/core/record_time.py;
handbook/03-Behavioral-Model.md §13: backdating is allowed and keeps its
recorded-at; a fact from the future is not a fact).

The tablet's calendars stop at today, but "no business-critical validation
only in UI": every record endpoint that takes its own date passes it through
`not_in_future`, so an offline tablet with a wrong clock, or any other
client, cannot file tomorrow's milk.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.exceptions import HTTPException

from app.farmos.livestock_service import ensure_utc

# A tablet's clock may run a little ahead of the server's.
CLOCK_TOLERANCE = timedelta(minutes=5)


def not_in_future(at: datetime | None, what: str) -> datetime | None:
    """Returns [at] as UTC (None stays None, meaning "now"); refuses a date
    later than now with "<what> cannot be dated in the future."."""
    if at is None:
        return None
    utc = ensure_utc(at)
    assert utc is not None
    if utc > datetime.now(timezone.utc) + CLOCK_TOLERANCE:
        raise HTTPException(status_code=422, detail=f"{what} cannot be dated in the future.")
    return utc
