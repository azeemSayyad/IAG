"""
Reporting export endpoints (PDF). Phase 3.

GET /reports/daily/export.pdf
GET /reports/compliance/export.pdf
GET /reports/agent/{agent_id}/export.pdf
GET /reports/manager/{manager_id}/export.pdf
GET /reports/sales/export.pdf

All endpoints are tenant-scoped, authenticated, and role-gated. Data is pulled
live from PostgreSQL so report totals always match the database.
"""
from datetime import datetime, timezone, timedelta, date as date_cls
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user, get_tenant_id
from app.models.user import User
from app.models.agent import Agent
from app.models.appointment import Appointment, AppointmentDisposition
from app.models.compliance import Deal, ComplianceEvent, AgentCarrierAppointment
from app.reports.pdf import build_pdf

router = APIRouter(prefix="/reports", tags=["reports"])

ADMIN_ROLES = {"manager", "tenant_admin", "super_admin"}


def _require_manager(user: User):
    if user.role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Manager or admin role required")


def _day_bounds(d: date_cls):
    start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _range_bounds(date_from: Optional[date_cls], date_to: Optional[date_cls], default_days: int = 30):
    today = datetime.now(timezone.utc).date()
    df = date_from or (today - timedelta(days=default_days))
    dt = date_to or today
    start = datetime(df.year, df.month, df.day, tzinfo=timezone.utc)
    end = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc) + timedelta(days=1)
    return start, end, df, dt


def _agent_label(db: Session, agent: Agent) -> str:
    u = db.query(User).filter(User.id == agent.user_id).first()
    return f"{u.first_name} {u.last_name}".strip() if u else str(agent.id)


def _pdf_response(filename: str, pdf: bytes) -> Response:
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _money(v) -> str:
    return f"${float(v or 0):,.2f}"


# ----------------------------------------------------------------------------
@router.get("/daily/export.pdf")
def daily_report_pdf(
    date: Optional[date_cls] = None,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    _require_manager(user)
    d = date or datetime.now(timezone.utc).date()
    start, end = _day_bounds(d)

    appts = db.query(Appointment).filter(
        Appointment.tenant_id == tenant_id,
        Appointment.start_time >= start, Appointment.start_time < end).all()
    by_status = {}
    for a in appts:
        by_status[a.status] = by_status.get(a.status, 0) + 1

    disps = db.query(AppointmentDisposition).filter(
        AppointmentDisposition.tenant_id == tenant_id,
        AppointmentDisposition.appointment_start_time >= start,
        AppointmentDisposition.appointment_start_time < end).all()
    disp_counts = {}
    sales = 0
    premium = 0.0
    for dz in disps:
        disp_counts[dz.disposition_label] = disp_counts.get(dz.disposition_label, 0) + 1
        if dz.insurance_sold:
            sales += 1
            premium += float(dz.premium_amount or 0)

    lines = [
        f"DAILY OPERATIONS REPORT  —  {d.isoformat()}", "",
        "APPOINTMENTS", f"  Total: {len(appts)}",
    ]
    for stt, n in sorted(by_status.items()):
        lines.append(f"    {stt}: {n}")
    lines += [
        "", "SALES",
        f"  Sales closed: {sales}",
        f"  Premium total: {_money(premium)}",
        "", "DISPOSITIONS", f"  Total: {len(disps)}",
    ]
    for label, n in sorted(disp_counts.items(), key=lambda x: -x[1]):
        lines.append(f"    {label}: {n}")
    follow_pending = db.query(func.count(AppointmentDisposition.id)).filter(
        AppointmentDisposition.tenant_id == tenant_id,
        AppointmentDisposition.outcome_category == "follow_up",
        AppointmentDisposition.appointment_start_time >= start,
        AppointmentDisposition.appointment_start_time < end).scalar()
    lines += ["", "FOLLOW-UPS", f"  Follow-up dispositions: {follow_pending or 0}"]
    return _pdf_response(f"daily-report-{d.isoformat()}.pdf", build_pdf("Daily Operations Report", lines))


# ----------------------------------------------------------------------------
@router.get("/compliance/export.pdf")
def compliance_report_pdf(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    _require_manager(user)
    approved = db.query(func.count(Deal.id)).filter(Deal.tenant_id == tenant_id, Deal.status == "approved").scalar() or 0
    blocked = db.query(func.count(Deal.id)).filter(Deal.tenant_id == tenant_id, Deal.status == "blocked").scalar() or 0
    events = db.query(ComplianceEvent).filter(ComplianceEvent.tenant_id == tenant_id).all()
    by_sev = {}
    for e in events:
        by_sev[e.severity] = by_sev.get(e.severity, 0) + 1
    unresolved = sum(1 for e in events if not e.resolved)
    today = datetime.now(timezone.utc).date()
    soon = today + timedelta(days=60)
    expiring = db.query(AgentCarrierAppointment).filter(
        AgentCarrierAppointment.tenant_id == tenant_id,
        AgentCarrierAppointment.expiration_date != None,  # noqa: E711
        AgentCarrierAppointment.expiration_date >= today,
        AgentCarrierAppointment.expiration_date <= soon).all()
    risk = [e for e in events if e.severity in ("high", "critical") and not e.resolved]

    lines = [
        "DEALS", f"  Approved: {approved}", f"  Blocked: {blocked}",
        f"  Approval rate: {round(approved/(approved+blocked)*100,1) if (approved+blocked) else 0}%",
        "", "COMPLIANCE EVENTS", f"  Total: {len(events)}", f"  Unresolved: {unresolved}",
    ]
    for sev, n in sorted(by_sev.items()):
        lines.append(f"    {sev}: {n}")
    lines += ["", "EXPIRING CARRIER APPOINTMENTS (<=60 days)", f"  Count: {len(expiring)}"]
    for ca in expiring[:40]:
        lines.append(f"    {ca.carrier_name} / {ca.state_code} expires {ca.expiration_date}")
    lines += ["", "RISK ALERTS (high/critical unresolved)", f"  Count: {len(risk)}"]
    for e in risk[:40]:
        lines.append(f"    [{e.severity}] {e.event_type}: {(e.message or '')[:80]}")
    return _pdf_response("compliance-report.pdf", build_pdf("Compliance Report", lines))


# ----------------------------------------------------------------------------
@router.get("/agent/{agent_id}/export.pdf")
def agent_report_pdf(
    agent_id: UUID,
    date_from: Optional[date_cls] = None,
    date_to: Optional[date_cls] = None,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    agent = db.query(Agent).filter(Agent.id == agent_id, Agent.tenant_id == tenant_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    # agents may only pull their own report; managers+ any
    if user.role == "agent" and str(agent.user_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Agents can only export their own report")
    start, end, df, dt = _range_bounds(date_from, date_to)

    appts = db.query(Appointment).filter(
        Appointment.tenant_id == tenant_id, Appointment.agent_id == agent_id,
        Appointment.start_time >= start, Appointment.start_time < end).all()
    completed = sum(1 for a in appts if a.status == "completed")
    no_show = sum(1 for a in appts if a.status == "no_show")
    disps = db.query(AppointmentDisposition).filter(
        AppointmentDisposition.tenant_id == tenant_id, AppointmentDisposition.agent_id == agent_id,
        AppointmentDisposition.appointment_start_time >= start,
        AppointmentDisposition.appointment_start_time < end).all()
    sales = sum(1 for d in disps if d.insurance_sold)
    premium = sum(float(d.premium_amount or 0) for d in disps)
    durations = [d.call_duration_seconds for d in disps if d.call_duration_seconds]
    avg_talk = round(sum(durations) / len(durations)) if durations else 0
    close_rate = round(sales / len(appts) * 100, 1) if appts else 0
    disp_counts = {}
    for d in disps:
        disp_counts[d.disposition_label] = disp_counts.get(d.disposition_label, 0) + 1

    lines = [
        f"Agent: {_agent_label(db, agent)}", f"Period: {df.isoformat()} to {dt.isoformat()}", "",
        "APPOINTMENTS", f"  Total: {len(appts)}", f"  Completed: {completed}", f"  No-show: {no_show}",
        "", "SALES", f"  Sales closed: {sales}", f"  Premium total: {_money(premium)}",
        f"  Close rate: {close_rate}%",
        "", "TALK TIME", f"  Avg call duration: {avg_talk}s",
        "", "DISPOSITIONS", f"  Total: {len(disps)}",
    ]
    for label, n in sorted(disp_counts.items(), key=lambda x: -x[1]):
        lines.append(f"    {label}: {n}")
    return _pdf_response(f"agent-report-{agent_id}.pdf", build_pdf("Agent Performance Report", lines))


# ----------------------------------------------------------------------------
@router.get("/manager/{manager_id}/export.pdf")
def manager_report_pdf(
    manager_id: UUID,
    date_from: Optional[date_cls] = None,
    date_to: Optional[date_cls] = None,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    _require_manager(user)
    start, end, df, dt = _range_bounds(date_from, date_to)
    agents = db.query(Agent).filter(Agent.tenant_id == tenant_id).all()
    lines = [f"Period: {df.isoformat()} to {dt.isoformat()}", "", "TEAM PERFORMANCE", ""]
    tot_appts = tot_sales = 0
    tot_premium = 0.0
    for ag in agents:
        a_appts = db.query(func.count(Appointment.id)).filter(
            Appointment.tenant_id == tenant_id, Appointment.agent_id == ag.id,
            Appointment.start_time >= start, Appointment.start_time < end).scalar() or 0
        a_sales = db.query(func.count(AppointmentDisposition.id)).filter(
            AppointmentDisposition.tenant_id == tenant_id, AppointmentDisposition.agent_id == ag.id,
            AppointmentDisposition.insurance_sold == True,  # noqa: E712
            AppointmentDisposition.appointment_start_time >= start,
            AppointmentDisposition.appointment_start_time < end).scalar() or 0
        a_prem = db.query(func.coalesce(func.sum(AppointmentDisposition.premium_amount), 0)).filter(
            AppointmentDisposition.tenant_id == tenant_id, AppointmentDisposition.agent_id == ag.id,
            AppointmentDisposition.insurance_sold == True,  # noqa: E712
            AppointmentDisposition.appointment_start_time >= start,
            AppointmentDisposition.appointment_start_time < end).scalar() or 0
        tot_appts += a_appts; tot_sales += a_sales; tot_premium += float(a_prem)
        lines.append(f"  {_agent_label(db, ag):28s}  appts={a_appts:3d}  sales={a_sales:3d}  premium={_money(a_prem)}")
    lines += [
        "", "TEAM TOTALS", f"  Appointments: {tot_appts}", f"  Sales: {tot_sales}",
        f"  Premium: {_money(tot_premium)}",
        f"  Team close rate: {round(tot_sales/tot_appts*100,1) if tot_appts else 0}%",
    ]
    approved = db.query(func.count(Deal.id)).filter(Deal.tenant_id == tenant_id, Deal.status == "approved").scalar() or 0
    blocked = db.query(func.count(Deal.id)).filter(Deal.tenant_id == tenant_id, Deal.status == "blocked").scalar() or 0
    lines += ["", "COMPLIANCE", f"  Approved deals: {approved}", f"  Blocked deals: {blocked}"]
    return _pdf_response(f"manager-report-{manager_id}.pdf", build_pdf("Manager Summary Report", lines))


# ----------------------------------------------------------------------------
@router.get("/sales/export.pdf")
def sales_report_pdf(
    date_from: Optional[date_cls] = None,
    date_to: Optional[date_cls] = None,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user: User = Depends(get_current_active_user),
):
    _require_manager(user)
    start, end, df, dt = _range_bounds(date_from, date_to)
    # sold dispositions in range
    disps = db.query(AppointmentDisposition).filter(
        AppointmentDisposition.tenant_id == tenant_id,
        AppointmentDisposition.appointment_start_time >= start,
        AppointmentDisposition.appointment_start_time < end).all()
    sold = [d for d in disps if d.insurance_sold]
    by_carrier = {}
    total_premium = 0.0
    for d in sold:
        c = d.sale_carrier or "Unknown"
        by_carrier.setdefault(c, {"count": 0, "premium": 0.0})
        by_carrier[c]["count"] += 1
        by_carrier[c]["premium"] += float(d.premium_amount or 0)
        total_premium += float(d.premium_amount or 0)
    # state breakdown from approved deals
    deals = db.query(Deal).filter(Deal.tenant_id == tenant_id, Deal.status == "approved",
                                  Deal.created_at >= start, Deal.created_at < end).all()
    by_state = {}
    for dl in deals:
        by_state.setdefault(dl.state, {"count": 0, "premium": 0.0})
        by_state[dl.state]["count"] += 1
        by_state[dl.state]["premium"] += float(dl.premium or 0)
    close_rate = round(len(sold) / len(disps) * 100, 1) if disps else 0

    lines = [
        f"Period: {df.isoformat()} to {dt.isoformat()}", "",
        "SUMMARY", f"  Sales closed: {len(sold)}", f"  Premium total: {_money(total_premium)}",
        f"  Dispositions: {len(disps)}", f"  Close rate: {close_rate}%",
        "", "CARRIER BREAKDOWN",
    ]
    for c, v in sorted(by_carrier.items(), key=lambda x: -x[1]["premium"]):
        lines.append(f"    {c:24s}  sales={v['count']:3d}  premium={_money(v['premium'])}")
    lines += ["", "STATE BREAKDOWN (approved deals)"]
    for s, v in sorted(by_state.items(), key=lambda x: -x[1]["premium"]):
        lines.append(f"    {s:6s}  deals={v['count']:3d}  premium={_money(v['premium'])}")
    return _pdf_response("sales-report.pdf", build_pdf("Sales Report", lines))
