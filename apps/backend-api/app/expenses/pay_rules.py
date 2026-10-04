"""Company pay rules — automatic per-sale pay for every agent.

One set of rules per tenant (models.expense.PayRules) replaces the per-agent
sale rates. Nothing is posted or stored per deal: pay is DERIVED from approved
deals every time it is read, so All Deals, My Deals and Expenses always agree.

How a week is priced (weeks are Monday-Sunday, Eastern):

  1. Count the agent's ACA COMMISSIONS for the week, per APPLICATION (the people
     logged in one Log Sale submission share deals.application_id):
       * an application marked EAP           -> always 1
       * a carrier in `per_member_carriers`  -> 1 per member (Anthem)
       * anything else                       -> 1
  2. The weekly total picks the ACA tier; EVERY ACA commission that week is paid
     at that tier's rate, so crossing a threshold reprices the whole week.
  3. Dental pays once per application by household size (the number of people on
     the application), ancillary pays a flat amount once per application, vision
     pays `vision_cents` (None = not offered yet = $0). None of these move the tier.

Past weeks never move when the rules are edited: a closed week is priced with
the rules version that was in force when it closed, and with the tier exception
(if any) that was active at that moment. The CURRENT week uses the latest rules.
Deals themselves are still read live, so approving or blocking a deal from an
earlier week does change that week.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.date_ranges import APPROVED_STATUSES
from app.models.compliance import Deal
from app.models.expense import PayException, PayRules

# Seeded the first time a tenant's pay is read. STARTING POINTS only — every
# value is editable from Expenses -> Company pay rules.
DEFAULT_RULES = {
    # Weekly ACA commission total -> pay per commission. `min` is the weekly
    # total that unlocks the tier; the first tier always starts at 0.
    "aca_tiers": [
        {"min": 0, "cents": 2000},
        {"min": 80, "cents": 2500},
        {"min": 130, "cents": 3000},
    ],
    "work_days": 5,                    # the "daily average" shown beside each tier
    "dental_small_cents": 2000,        # household below dental_large_min
    "dental_large_cents": 4000,        # household of dental_large_min or more
    "dental_large_min": 3,
    "ancillary_cents": 2000,
    "vision_cents": None,              # None = not offered yet (pays nothing)
    "per_member_carriers": ["anthem"], # carrier keys that count 1 commission per member
}

MAX_TIERS = 8


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.AGENT_TZ)


def _carrier_key(value) -> str:
    return " ".join(str(value or "").strip().lower().split())


def week_start_of(d: date) -> date:
    """The Monday of d's week."""
    return d - timedelta(days=d.weekday())


def week_bounds_utc(week_start: date) -> tuple[datetime, datetime]:
    """Monday 00:00 Eastern -> next Monday 00:00 Eastern, as UTC [start, end)."""
    tz = _tz()
    a = datetime(week_start.year, week_start.month, week_start.day, tzinfo=tz)
    nxt = week_start + timedelta(days=7)
    b = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)
    return a.astimezone(timezone.utc), b.astimezone(timezone.utc)


def today_et() -> date:
    return datetime.now(_tz()).date()


def validate_rules(raw: dict) -> dict:
    """Normalise an edited rule set, or raise ValueError with a readable reason."""
    def cents(v, label, allow_none=False):
        if v is None and allow_none:
            return None
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise ValueError(f"{label} must be an amount")
        if n < 0 or n > 10_000_00:
            raise ValueError(f"{label} must be between $0 and $10,000")
        return n

    tiers_in = raw.get("aca_tiers")
    if not isinstance(tiers_in, list) or not (1 <= len(tiers_in) <= MAX_TIERS):
        raise ValueError(f"Set between 1 and {MAX_TIERS} ACA tiers")
    tiers = []
    for i, t in enumerate(tiers_in):
        try:
            m = int((t or {}).get("min"))
        except (TypeError, ValueError):
            raise ValueError(f"Tier {i + 1}: weekly total must be a whole number")
        tiers.append({"min": m, "cents": cents((t or {}).get("cents"), f"Tier {i + 1} pay")})
    tiers[0]["min"] = 0
    for i in range(1, len(tiers)):
        if tiers[i]["min"] <= tiers[i - 1]["min"]:
            raise ValueError(f"Tier {i + 1} must unlock at a higher weekly total than Tier {i}")
    try:
        work_days = int(raw.get("work_days", 5))
        large_min = int(raw.get("dental_large_min", 3))
    except (TypeError, ValueError):
        raise ValueError("Working days and household size must be whole numbers")
    if not 1 <= work_days <= 7:
        raise ValueError("Working days must be between 1 and 7")
    if not 2 <= large_min <= 20:
        raise ValueError("The larger dental household must start between 2 and 20 people")
    carriers = raw.get("per_member_carriers") or []
    if not isinstance(carriers, list):
        raise ValueError("per_member_carriers must be a list")
    keys = sorted({_carrier_key(c) for c in carriers if _carrier_key(c)})
    return {
        "aca_tiers": tiers,
        "work_days": work_days,
        "dental_small_cents": cents(raw.get("dental_small_cents"), "Dental (small household)"),
        "dental_large_cents": cents(raw.get("dental_large_cents"), "Dental (large household)"),
        "dental_large_min": large_min,
        "ancillary_cents": cents(raw.get("ancillary_cents"), "Ancillary"),
        "vision_cents": cents(raw.get("vision_cents"), "Vision", allow_none=True),
        "per_member_carriers": keys,
    }


def rule_versions(db: Session, tenant_id) -> list[PayRules]:
    """Every rules version, oldest first — seeding the defaults the first time.
    The seed starts on the Monday of the current week, so the week in progress is
    the first one the rules pay; deals before it keep their per-agent rates."""
    rows = (db.query(PayRules).filter(PayRules.tenant_id == tenant_id)
            .order_by(PayRules.created_at).all())
    if rows:
        return rows
    db.add(PayRules(tenant_id=tenant_id, rules=validate_rules(DEFAULT_RULES),
                    starts_on=week_start_of(today_et())))
    db.commit()
    return (db.query(PayRules).filter(PayRules.tenant_id == tenant_id)
            .order_by(PayRules.created_at).all())


def cutover_utc(versions: list[PayRules]) -> datetime:
    """Deals logged from this instant on are paid by the rules; earlier deals keep
    their per-agent sale rate. It is the LATEST version's starts_on (always a
    Monday), which the owner can move from Edit rules — e.g. back a week so sales
    logged before the rules existed are paid by them too."""
    return week_bounds_utc(week_start_of(versions[-1].starts_on))[0]


def rules_for_week(versions: list[PayRules], week_start: date, now: datetime) -> dict:
    """Current week -> the latest rules. Closed week -> the version in force when
    it closed (the earliest version for weeks that closed before any was saved)."""
    _a, close = week_bounds_utc(week_start)
    if close > now:
        return versions[-1].rules
    chosen = versions[0]
    for v in versions:
        if v.created_at < close:
            chosen = v
    return chosen.rules


def tier_index(rules: dict, units: int) -> int:
    """0-based index of the highest tier the weekly total has unlocked."""
    idx = 0
    for i, t in enumerate(rules["aca_tiers"]):
        if units >= int(t["min"]):
            idx = i
    return idx


def _exception_at(excs: list[PayException], at: datetime) -> Optional[PayException]:
    """The exception active at instant `at` (latest created wins)."""
    day = at.astimezone(_tz()).date()
    live = [e for e in excs
            if e.created_at <= at
            and (e.revoked_at is None or e.revoked_at > at)
            and (e.ends_on is None or e.ends_on >= day)]
    return live[-1] if live else None


@dataclass
class WeekPay:
    agent_id: object
    week_start: date
    commissions: int = 0          # ACA commissions counted toward the tier
    auto_tier: int = 1            # 1-based tier the count earns
    tier: int = 1                 # 1-based tier actually paid (differs under an exception)
    rate_cents: int = 0           # pay per ACA commission this week
    exception_id: object = None
    aca_cents: int = 0
    other_cents: int = 0          # dental + ancillary + vision
    closed: bool = False


@dataclass
class DealPay:
    deal_id: object
    agent_id: object
    created_at: datetime
    aca: bool
    dental: bool
    vision: bool
    ancillary: bool
    units: int = 0                # ACA commissions this deal carries
    flat_cents: int = 0           # dental + ancillary + vision
    cents: int = 0


def compute(db: Session, tenant_id, start: Optional[datetime] = None,
            end: Optional[datetime] = None, agent_id=None,
            now: Optional[datetime] = None) -> tuple[list[DealPay], dict]:
    """Price every approved deal logged in [start, end) under the pay rules.

    Returns (deal lines inside the window, {(agent_id, week_start): WeekPay} for
    every week the window touches). Whole weeks are always loaded, because one
    deal's pay depends on the agent's total for its week."""
    now = now or datetime.now(timezone.utc)
    tz = _tz()
    versions = rule_versions(db, tenant_id)
    cut = cutover_utc(versions)
    lo = max(start, cut) if start is not None else cut
    if end is not None and end <= lo:
        return [], {}
    # widen to whole weeks
    q_lo = week_bounds_utc(week_start_of(lo.astimezone(tz).date()))[0]
    q_lo = max(q_lo, cut)
    q = db.query(Deal).filter(
        Deal.tenant_id == tenant_id,
        func.lower(Deal.status).in_(APPROVED_STATUSES),
        Deal.created_at >= q_lo,
    )
    if end is not None:
        last_day = (end - timedelta(microseconds=1)).astimezone(tz).date()
        q = q.filter(Deal.created_at < week_bounds_utc(week_start_of(last_day))[1])
    if agent_id is not None:
        q = q.filter(Deal.agent_id == agent_id)
    deals = q.order_by(Deal.created_at, Deal.id).all()

    # Household size = everyone logged on the application, whatever their status.
    app_ids = {d.application_id for d in deals if d.application_id}
    household: dict = {}
    if app_ids:
        for aid, n in (db.query(Deal.application_id, func.count(Deal.id))
                       .filter(Deal.tenant_id == tenant_id, Deal.application_id.in_(app_ids))
                       .group_by(Deal.application_id).all()):
            household[aid] = n

    eq = db.query(PayException).filter(PayException.tenant_id == tenant_id)
    if agent_id is not None:
        eq = eq.filter(PayException.agent_id == agent_id)
    excs: dict = {}
    for e in eq.order_by(PayException.created_at).all():
        excs.setdefault(e.agent_id, []).append(e)

    lines, weeks = price(deals, household, excs, versions, now)
    out = [p for p in lines if p.created_at >= lo and (end is None or p.created_at < end)]
    return out, weeks


def price(deals: list, household: dict, excs: dict, versions: list, now: datetime) -> tuple[list[DealPay], dict]:
    """The pay maths, with no database in it: approved deals (whole weeks, oldest
    first) -> a DealPay per deal and a WeekPay per (agent, week)."""
    tz = _tz()
    # agent -> week -> application -> deals
    grouped: dict = {}
    for d in deals:
        wk = week_start_of(d.created_at.astimezone(tz).date())
        grouped.setdefault((d.agent_id, wk), {}).setdefault(d.application_id or d.id, []).append(d)

    lines: list[DealPay] = []
    weeks: dict = {}
    for (aid, wk), apps in grouped.items():
        rules = rules_for_week(versions, wk, now)
        per_member = set(rules.get("per_member_carriers") or [])
        week_lines: list[DealPay] = []
        for app_key, members in apps.items():
            eap = any((m.deal_source or "") == "eap" for m in members)
            size = household.get(app_key, len(members)) if members[0].application_id else 1
            single_given = dental_given = anc_given = False
            for m in members:
                p = DealPay(m.id, aid, m.created_at, (m.aca_count or 0) > 0, (m.dental_count or 0) > 0,
                            (m.vision_count or 0) > 0, (m.ancillary_count or 0) > 0)
                if p.aca:
                    if not eap and _carrier_key(m.carrier) in per_member:
                        p.units = 1                       # counted per member
                    elif not single_given:
                        p.units, single_given = 1, True   # the application's one commission
                if p.dental and not dental_given:
                    dental_given = True
                    p.flat_cents += int(rules["dental_large_cents"] if size >= int(rules["dental_large_min"])
                                        else rules["dental_small_cents"])
                if p.ancillary and not anc_given:
                    anc_given = True
                    p.flat_cents += int(rules["ancillary_cents"])
                if p.vision:
                    p.flat_cents += int(rules.get("vision_cents") or 0)
                week_lines.append(p)

        units = sum(p.units for p in week_lines)
        a, close = week_bounds_utc(wk)
        closed = close <= now
        auto = tier_index(rules, units)
        exc = _exception_at(excs.get(aid, []), (close - timedelta(seconds=1)) if closed else now)
        idx = min(max(exc.tier - 1, 0), len(rules["aca_tiers"]) - 1) if exc else auto
        rate = int(rules["aca_tiers"][idx]["cents"])
        wp = WeekPay(aid, wk, units, auto + 1, idx + 1, rate, exc.id if exc else None, closed=closed)
        for p in week_lines:
            p.cents = p.units * rate + p.flat_cents
            wp.aca_cents += p.units * rate
            wp.other_cents += p.flat_cents
        weeks[(aid, wk)] = wp
        lines.extend(week_lines)
    return lines, weeks


def active_exception(db: Session, tenant_id, agent_id, now: Optional[datetime] = None) -> Optional[PayException]:
    now = now or datetime.now(timezone.utc)
    rows = (db.query(PayException)
            .filter(PayException.tenant_id == tenant_id, PayException.agent_id == agent_id)
            .order_by(PayException.created_at).all())
    return _exception_at(rows, now)


def week_status(db: Session, tenant_id, agent_id, week_start: Optional[date] = None,
                now: Optional[datetime] = None) -> dict:
    """An agent's tier standing for one week (default: the current one)."""
    now = now or datetime.now(timezone.utc)
    wk = week_start or week_start_of(now.astimezone(_tz()).date())
    a, b = week_bounds_utc(wk)
    versions = rule_versions(db, tenant_id)
    rules = rules_for_week(versions, wk, now)
    _lines, weeks = compute(db, tenant_id, a, b, agent_id=agent_id, now=now)
    wp = weeks.get((agent_id, wk))
    if wp is None:
        # No approved deals yet this week — still on tier 1 (or the locked tier).
        exc = _exception_at(
            db.query(PayException).filter(PayException.tenant_id == tenant_id,
                                          PayException.agent_id == agent_id)
            .order_by(PayException.created_at).all(),
            (b - timedelta(seconds=1)) if b <= now else now)
        idx = min(max(exc.tier - 1, 0), len(rules["aca_tiers"]) - 1) if exc else 0
        wp = WeekPay(agent_id, wk, 0, 1, idx + 1, int(rules["aca_tiers"][idx]["cents"]),
                     exc.id if exc else None, closed=b <= now)
    tiers = rules["aca_tiers"]
    nxt = tiers[wp.auto_tier] if wp.auto_tier < len(tiers) else None
    return {
        "week_start": wk.isoformat(),
        "commissions": wp.commissions,
        "auto_tier": wp.auto_tier,
        "tier": wp.tier,
        "rate_cents": wp.rate_cents,
        "exception": wp.exception_id is not None,
        "aca_cents": wp.aca_cents,
        "other_cents": wp.other_cents,
        "closed": wp.closed,
        # How far to the next automatic tier (None on the top tier).
        "next_tier": (wp.auto_tier + 1) if nxt else None,
        "next_at": int(nxt["min"]) if nxt else None,
        "next_rate_cents": int(nxt["cents"]) if nxt else None,
        "to_next": max(int(nxt["min"]) - wp.commissions, 0) if nxt else None,
    }
