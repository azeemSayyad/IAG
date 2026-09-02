"""Single source of truth for portal date ranges + the approved-deal scope.

Every number-bearing endpoint (sales dashboard, leaderboard, all-deals, my-deals)
resolves BOTH its date window AND its status scope here, so the same metric for the
same filter reads identically on every page and cannot drift.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.config import settings

# Deals counted on EVERY surface = APPROVED only (approved / paid / won). Excludes
# submitted / blocked / denied. ONE definition imported everywhere.
APPROVED_STATUSES = ("approved", "paid", "won")


def resolve_range(from_iso=None, to_iso=None, tz_name=None):
    """Resolve a picker window to UTC [start, end) using Eastern calendar days.

    Replaces the duplicate sales_dashboard._range + compliance._eastern_range.
    Picker dates are YYYY-MM-DD Eastern calendar days; default (no args) = today
    (Eastern). The frontend presets (today/this-week/this-month/this-year/all-time)
    always send BOTH from and to. If only `from` is given (no `to`), the range is
    that single day (`to` := `from`) — the unified semantics across both helpers;
    the no-arg call stays "today only". Returns (start_utc, end_utc, from_date, to_date).
    """
    tz = ZoneInfo(tz_name or settings.AGENT_TZ)
    today = datetime.now(tz).date()
    from_d = to_d = today
    try:
        if from_iso:
            from_d = to_d = date.fromisoformat(str(from_iso)[:10])
        if to_iso:
            to_d = date.fromisoformat(str(to_iso)[:10])
    except ValueError:
        pass
    if from_d > to_d:
        from_d, to_d = to_d, from_d
    start = datetime(from_d.year, from_d.month, from_d.day, tzinfo=tz).astimezone(timezone.utc)
    end = (datetime(to_d.year, to_d.month, to_d.day, tzinfo=tz) + timedelta(days=1)).astimezone(timezone.utc)
    return start, end, from_d, to_d
