"""Time helpers.

Postgres returns timezone-aware datetimes for timestamptz columns; sqlite (in
tests) returns naive ones. Normalize to aware UTC before comparing.
"""

from datetime import UTC, datetime


def ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)
