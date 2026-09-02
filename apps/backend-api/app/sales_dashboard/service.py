"""Sales Dashboard aggregation (admin-only).

Mirrors the Gamified sales dashboard's data/logic over the ACA `deals` model:
  Applications = # deals
  Members      = Σ(aca_count + dental_count + vision_count)
  Medical/Dental/Vision = Σ aca_count / dental_count / vision_count
Plus upcoming appointments (calendar), recent inbound SMS (conversations) and
recent deals (applications feed). Read-only.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.appointment import Appointment
from app.models.compliance import Deal
from app.models.lead import Lead
from app.models.sms import SmsLead, SmsMessage
from app.models.user import User

from app.core.date_ranges import resolve_range, APPROVED_STATUSES

WON_STATUSES = APPROVED_STATUSES  # back-compat alias; canonical scope lives in core.date_ranges


def _name(u: User | None) -> str:
    if not u:
        return "Unassigned"
    full = f"{u.first_name or ''} {u.last_name or ''}".strip()
    return full or u.email


def _range(from_iso: str | None, to_iso: str | None):
    """Thin wrapper over core.date_ranges.resolve_range — the single shared helper."""
    return resolve_range(from_iso, to_iso)


def _members_expr():
    return (
        func.coalesce(Deal.aca_count, 0)
        + func.coalesce(Deal.dental_count, 0)
        + func.coalesce(Deal.vision_count, 0)
    )


def _deals_expr():
    # A "deal" = people × deal-types they took, i.e. the per-product head counts
    # summed: aca_count + dental_count + vision_count. 12 ACA + 7 Dental + 7 Vision
    # = 26 deals. This matches the leaderboard / KPI "Total deals" so every surface
    # reconciles to the same number.
    return _members_expr()


def get_overview(db: Session, tenant_id: str, from_iso: str | None = None, to_iso: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    start, end, from_label, to_label = _range(from_iso, to_iso)
    members = _members_expr()

    in_range = (
        Deal.tenant_id == tenant_id,
        Deal.created_at >= start,
        Deal.created_at < end,
        # Deals counted = APPROVED only (approved / paid / won). Denied/blocked and
        # not-yet-approved (submitted) deals are excluded from every count below.
        func.lower(Deal.status).in_(WON_STATUSES),
    )

    # Agent name lookup: deals.agent_id -> agents.id -> users.
    agent_names = {
        str(a.id): _name(u)
        for a, u in db.query(Agent, User)
        .outerjoin(User, User.id == Agent.user_id)
        .filter(Agent.tenant_id == tenant_id)
        .all()
    }

    # --- Agent leaderboard + deals-by-agent donut ---
    # "deals" = COUNT of deal ROWS (one row = one deal), not the policy-count sum.
    agent_rows = (
        db.query(Deal.agent_id, func.count(Deal.id), func.sum(members))
        .filter(*in_range)
        .group_by(Deal.agent_id)
        .all()
    )
    agents = [
        {
            "agent_name": agent_names.get(str(aid), "Unassigned"),
            "deals": int(cnt or 0),
            "members": int(mem or 0),
        }
        for aid, cnt, mem in agent_rows
    ]
    agents.sort(key=lambda x: (x["members"], x["deals"]), reverse=True)

    # --- Sales mix ---
    # Medical/Dental/Vision = COUNT of deal ROWS carrying that coverage (count>0),
    # not the policy-count sums. members = covered-lives sum (kept as-is).
    mix = (
        db.query(
            func.count(Deal.id),
            func.count(Deal.id).filter(Deal.aca_count > 0),
            func.count(Deal.id).filter(Deal.dental_count > 0),
            func.count(Deal.id).filter(Deal.vision_count > 0),
            func.coalesce(func.sum(members), 0),
        )
        .filter(*in_range)
        .first()
    )
    applications = int(mix[0] or 0)
    medical, dental, vision = int(mix[1] or 0), int(mix[2] or 0), int(mix[3] or 0)
    total_members = int(mix[4] or 0)

    # --- Carrier mix: deals per carrier (which company the sales go to) ---
    # Uses the same _deals_expr() as the donut so per-carrier deals reconcile
    # with deals_total. Top carriers shown; the rest roll up into "Other".
    carrier_rows = (
        db.query(Deal.carrier, func.count(Deal.id))
        .filter(*in_range)
        .group_by(Deal.carrier)
        .all()
    )
    carrier_list = sorted(
        ({"carrier": (c or "Unknown"), "deals": int(n or 0)} for c, n in carrier_rows),
        key=lambda x: x["deals"],
        reverse=True,
    )
    _TOP_CARRIERS = 6
    if len(carrier_list) > _TOP_CARRIERS:
        head = carrier_list[:_TOP_CARRIERS]
        other = sum(x["deals"] for x in carrier_list[_TOP_CARRIERS:])
        if other:
            head.append({"carrier": "Other", "deals": other})
        carrier_list = head
    carrier_mix = [c for c in carrier_list if c["deals"] > 0]

    # --- Snapshot bucketed across the SELECTED range (stays in sync with the
    # filter). Uses the SAME in_range filter as every other card, so the bars
    # always sum to the headline total — a 1-day filter shows exactly one bar.
    # Granularity auto-scales so long ranges ("All time") stay readable:
    #   <= 31 days -> per day, <= ~6 months -> per week, else per month.
    from app.core.config import settings

    n_days = (to_label - from_label).days + 1
    # <=1mo: daily, <=1yr: monthly, <=10yr: yearly, longer: per decade.
    if n_days <= 31:
        granularity = "day"
    elif n_days <= 366:
        granularity = "month"
    elif n_days <= 3653:
        granularity = "year"
    else:
        granularity = "decade"
    multi_year = from_label.year != to_label.year

    # Truncate created_at in Eastern (Florida) so buckets align with the business
    # day, matching _range()'s day boundaries.
    local_ts = func.timezone(settings.AGENT_TZ, Deal.created_at)
    bucket = func.date_trunc(granularity, local_ts)
    snap_rows = (
        db.query(bucket.label("b"), func.count(Deal.id), func.sum(members))
        .filter(*in_range)
        .group_by("b")
        .all()
    )
    by_bucket = {}
    for b, cnt, mem in snap_rows:
        key = b.date() if hasattr(b, "date") else b
        by_bucket[key] = {"deals": int(cnt or 0), "members": int(mem or 0)}

    def _add_month(d):
        return (d.replace(day=28) + timedelta(days=4)).replace(day=1)

    weekly = []
    if granularity == "day":
        # A week-or-less range -> weekday names (Mon/Tue/…); longer daily ranges
        # -> day numbers (weekday names would repeat and dates overlap).
        wd_names = n_days <= 7
        d = from_label
        while d <= to_label:
            b = by_bucket.get(d, {"deals": 0, "members": 0})
            label = d.strftime("%a") if wd_names else str(d.day)
            weekly.append({"label": label, "date": d.isoformat(), **b})
            d += timedelta(days=1)
    elif granularity == "month":
        d = from_label.replace(day=1)
        while d <= to_label:
            b = by_bucket.get(d, {"deals": 0, "members": 0})
            label = f"{d:%b %y}" if multi_year else f"{d:%b}"  # "Jan" (or "Jan 26" across years)
            weekly.append({"label": label, "date": d.isoformat(), **b})
            d = _add_month(d)
    elif granularity == "year":
        d = from_label.replace(month=1, day=1)
        while d <= to_label:
            b = by_bucket.get(d, {"deals": 0, "members": 0})
            weekly.append({"label": str(d.year), "date": d.isoformat(), **b})
            d = d.replace(year=d.year + 1)
    else:  # decade — ranges spanning more than ~10 years
        d = from_label.replace(year=(from_label.year // 10) * 10, month=1, day=1)
        while d <= to_label:
            b = by_bucket.get(d, {"deals": 0, "members": 0})
            weekly.append({"label": f"{d.year}s", "date": d.isoformat(), **b})  # "2000s", "2020s"
            d = d.replace(year=d.year + 10)

    # For long views, drop leading empty buckets so "All time" (which starts in
    # 2000) shows only the years/decades that actually have data — not a flat run
    # of zero bars stretching back decades.
    if granularity in ("year", "decade"):
        while len(weekly) > 1 and weekly[0]["deals"] == 0 and weekly[0]["members"] == 0:
            weekly.pop(0)

    # --- Calendar: upcoming appointments ---
    appt_rows = (
        db.query(Appointment, Lead, Agent, User)
        .outerjoin(Lead, Lead.id == Appointment.lead_id)
        .outerjoin(Agent, Agent.id == Appointment.agent_id)
        .outerjoin(User, User.id == Agent.user_id)
        .filter(
            Appointment.tenant_id == tenant_id,
            Appointment.start_time >= now,
            Appointment.status.in_(("confirmed", "scheduled", "booked")),
        )
        .order_by(Appointment.start_time.asc())
        .limit(12)
        .all()
    )
    calendar = [
        {
            "start_time": ap.start_time.isoformat() if ap.start_time else None,
            "customer": (f"{(ld.first_name or '').strip()} {(ld.last_name or '').strip()}".strip() if ld else "") or (ld.phone if ld else "Lead"),
            "agent_name": _name(u),
        }
        for ap, ld, ag, u in appt_rows
    ]

    # --- Recent conversations: recent inbound SMS ---
    convo_rows = (
        db.query(SmsMessage, SmsLead, User)
        .outerjoin(SmsLead, SmsLead.id == SmsMessage.sms_lead_id)
        .outerjoin(User, User.id == SmsLead.assigned_agent_id)
        .filter(SmsMessage.tenant_id == tenant_id, SmsMessage.direction == "INBOUND")
        .order_by(SmsMessage.created_at.desc())
        .limit(8)
        .all()
    )
    recent_conversations = [
        {
            "phone_number": m.phone_number,
            "agent_name": _name(u) if u else (sl.customer_name if sl else None),
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m, sl, u in convo_rows
    ]

    # --- Recent applications: recent deals ---
    deal_rows = (
        db.query(Deal, Agent, User)
        .outerjoin(Agent, Agent.id == Deal.agent_id)
        .outerjoin(User, User.id == Agent.user_id)
        .filter(Deal.tenant_id == tenant_id)
        .order_by(Deal.created_at.desc())
        .limit(8)
        .all()
    )

    def _product(d: Deal) -> str:
        if (d.aca_count or 0) > 0:
            return "Medical"
        if (d.dental_count or 0) > 0:
            return "Dental"
        if (d.vision_count or 0) > 0:
            return "Vision"
        return "—"

    recent_applications = [
        {
            "customer_name": d.customer_name or "—",
            "agent_name": _name(u),
            "status": d.status,
            "won": (d.status or "").lower() in WON_STATUSES,
            "product": _product(d),
            "members": int((d.aca_count or 0) + (d.dental_count or 0) + (d.vision_count or 0)),
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d, ag, u in deal_rows
    ]

    # Total leads created in the selected range (drives the "Total leads" card).
    leads_total = (
        db.query(func.count(Lead.id))
        .filter(
            Lead.tenant_id == tenant_id,
            Lead.created_at >= start,
            Lead.created_at < end,
        )
        .scalar()
        or 0
    )

    return {
        # Eastern (Florida) calendar dates shown in the picker. Defaults to today.
        "range": {"from": from_label.isoformat(), "to": to_label.isoformat()},
        "agents": agents,
        "deals_total": sum(a["deals"] for a in agents),
        "leads_total": leads_total,
        "sales_mix": {
            "applications": applications,
            "members": total_members,
            "medical": medical,
            "dental": dental,
            "vision": vision,
        },
        "carrier_mix": carrier_mix,
        "weekly": weekly,
        "weekly_granularity": granularity,  # day | week | month — drives the snapshot title
        "calendar": calendar,
        "recent_conversations": recent_conversations,
        "recent_applications": recent_applications,
    }
