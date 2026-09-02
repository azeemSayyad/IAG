import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import httpx
import socketio
from sqlalchemy import func, text

from app.core.database import SessionLocal
from app.core.security import create_access_token, hash_password
from app.models.agent import Agent
from app.models.appointment import Appointment, AppointmentDisposition
from app.models.audit_log import AuditLog
from app.models.compliance import (
    AgentCarrierAppointment,
    AgentStateLicense,
    ComplianceEvent,
    Deal,
    DealApprovalLog,
)
from app.models.conversation import Conversation
from app.models.lead import Lead
from app.models.message import Message
from app.models.tenant import Tenant
from app.models.user import User


BASE_URL = "http://127.0.0.1:8000"


def require(condition, label, details=None):
    if not condition:
        raise AssertionError(f"{label}: {details or 'failed'}")


def create_runtime_identity():
    stamp = int(time.time())
    db = SessionLocal()
    try:
        tenant = Tenant(
            name=f"Runtime Validation Tenant {stamp}",
            subscription_plan="enterprise",
            status="active",
            max_agents=25,
            max_leads_per_month=50000,
        )
        db.add(tenant)
        db.flush()

        admin = User(
            tenant_id=tenant.id,
            email=f"runtime-admin-{stamp}@example.com",
            password_hash=hash_password("RuntimePass123!"),
            first_name="Runtime",
            last_name="Admin",
            role="tenant_admin",
            status="active",
        )
        db.add(admin)
        db.flush()
        agent = Agent(
            tenant_id=tenant.id,
            user_id=admin.id,
            timezone="America/New_York",
            daily_capacity=8,
            max_concurrent=1,
            status="active",
        )
        db.add(agent)
        db.commit()
        token = create_access_token({"sub": str(admin.id), "tenant_id": str(tenant.id), "role": admin.role})
        return {
            "tenant_id": str(tenant.id),
            "user_id": str(admin.id),
            "agent_id": str(agent.id),
            "email": admin.email,
            "token": token,
        }
    finally:
        db.close()


def db_counts(tenant_id):
    db = SessionLocal()
    try:
        return {
            "leads": db.query(Lead).filter(Lead.tenant_id == tenant_id).count(),
            "conversations": db.query(Conversation).filter(Conversation.tenant_id == tenant_id).count(),
            "messages": db.query(Message).filter(Message.tenant_id == tenant_id).count(),
            "appointments": db.query(Appointment).filter(Appointment.tenant_id == tenant_id).count(),
            "dispositions": db.query(AppointmentDisposition).filter(AppointmentDisposition.tenant_id == tenant_id).count(),
            "state_licenses": db.query(AgentStateLicense).filter(AgentStateLicense.tenant_id == tenant_id).count(),
            "carrier_appointments": db.query(AgentCarrierAppointment).filter(AgentCarrierAppointment.tenant_id == tenant_id).count(),
            "deals": db.query(Deal).filter(Deal.tenant_id == tenant_id).count(),
            "approval_logs": db.query(DealApprovalLog).filter(DealApprovalLog.tenant_id == tenant_id).count(),
            "compliance_events": db.query(ComplianceEvent).filter(ComplianceEvent.tenant_id == tenant_id).count(),
            "audit_logs": db.query(AuditLog).filter(AuditLog.tenant_id == tenant_id).count(),
        }
    finally:
        db.close()


def verify_db_row(model, tenant_id, **filters):
    db = SessionLocal()
    try:
        query = db.query(model).filter(model.tenant_id == tenant_id)
        for field, value in filters.items():
            query = query.filter(getattr(model, field) == value)
        return query.first()
    finally:
        db.close()


async def run():
    identity = create_runtime_identity()
    headers = {"Authorization": f"Bearer {identity['token']}"}
    events = []
    sio = socketio.Client(logger=False, engineio_logger=False)

    @sio.event
    def connected(data):
        events.append(("connected", data))

    for name in [
        "conversation_message_created",
        "appointment_created",
        "appointment_updated",
        "appointment_disposition_saved",
        "deal_approved",
        "deal_not_approved",
        "compliance_event_created",
        "compliance_scan_completed",
    ]:
        sio.on(name, lambda data, event_name=name: events.append((event_name, data)))

    async with httpx.AsyncClient(base_url=BASE_URL, headers=headers, timeout=45) as client:
        sio.connect(BASE_URL, auth={"token": identity["token"]})
        await asyncio.sleep(0.2)

        health = await client.get("/health")
        require(health.status_code == 200 and health.json().get("status") == "ok", "health endpoint", health.text)

        me = await client.get("/api/v1/auth/me")
        require(me.status_code == 200 and me.json()["email"] == identity["email"], "auth/me contract", me.text)

        before = db_counts(identity["tenant_id"])

        invalid_lead = await client.post("/api/v1/leads", json={"first_name": "Missing requireds"})
        require(invalid_lead.status_code == 422, "lead invalid payload rejected", invalid_lead.text)

        lead_payload = {
            "source": "runtime_validation",
            "first_name": "Runtime",
            "last_name": "Lead",
            "phone": f"+1555{str(uuid4().int)[0:7]}",
            "email": f"runtime-lead-{uuid4().hex[:8]}@example.com",
            "state": "NV",
            "city": "Reno",
            "zip_code": "89501",
            "timezone": "America/New_York",
            "tags": ["runtime", "qa"],
            "custom_fields": {"validation": True},
        }
        lead = await client.post("/api/v1/leads", json=lead_payload)
        require(lead.status_code == 201, "lead create", lead.text)
        lead_id = lead.json()["id"]
        require(verify_db_row(Lead, identity["tenant_id"], id=lead_id), "lead DB persistence")

        patch = await client.patch(f"/api/v1/leads/{lead_id}", json={"status": "contacted", "lead_score": 77})
        require(patch.status_code == 200 and patch.json()["lead_score"] == 77, "lead update business fields", patch.text)

        score = await client.post("/api/v1/internal/lead-score", json={"lead_id": lead_id})
        require(score.status_code == 200 and score.json()["lead_score"] >= 0, "AI/ML lead scoring", score.text)
        db_lead = verify_db_row(Lead, identity["tenant_id"], id=lead_id)
        require(db_lead and db_lead.lead_score == int(score.json()["lead_score"]), "lead score DB update")

        conv = await client.post("/api/v1/conversations", json={"lead_id": lead_id, "status": "active"})
        require(conv.status_code == 201, "conversation create", conv.text)
        conversation_id = conv.json()["id"]
        require(verify_db_row(Conversation, identity["tenant_id"], id=conversation_id), "conversation DB persistence")

        msg = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "Customer prefers a callback tomorrow.", "sender": "customer", "send_sms": False},
        )
        require(msg.status_code == 201, "message create no-SMS path", msg.text)
        require(verify_db_row(Message, identity["tenant_id"], id=msg.json()["id"]), "message DB persistence")
        await asyncio.sleep(0.3)

        intent = await client.post("/api/v1/internal/intent-detect", json={"text": "Yes, I want an appointment", "lead_id": lead_id})
        require(intent.status_code == 200 and intent.json()["intent"], "intent detection", intent.text)

        objection = await client.post("/api/v1/internal/handle-objection", json={"message": "I need to think about it", "lead_name": "Runtime"})
        require(objection.status_code == 200 and objection.json()["response"], "objection handling", objection.text)

        generated = await client.post(
            "/api/v1/internal/generate-response",
            json={
                "message": "Customer said yes and wants to book an insurance appointment. Reply with one concise helpful sentence.",
                "lead_id": lead_id,
                "tone": "friendly",
            },
        )
        require(generated.status_code == 200 and generated.json()["response"], "Ollama response generation", generated.text)

        start = datetime.now(timezone.utc) + timedelta(days=2, hours=3)
        appt = await client.post(
            "/api/v1/appointments",
            json={
                "lead_id": lead_id,
                "agent_id": identity["agent_id"],
                "conversation_id": conversation_id,
                "start_time": start.isoformat(),
                "end_time": (start + timedelta(minutes=30)).isoformat(),
            },
        )
        require(appt.status_code == 201, "appointment create", appt.text)
        appointment_id = appt.json()["id"]
        require(verify_db_row(Appointment, identity["tenant_id"], id=appointment_id), "appointment DB persistence")
        await asyncio.sleep(0.3)

        appt_update = await client.patch(f"/api/v1/appointments/{appointment_id}", json={"status": "completed", "notes": "Runtime completion"})
        require(appt_update.status_code == 200 and appt_update.json()["status"] == "completed", "appointment update", appt_update.text)

        options = await client.get("/api/v1/appointments/dispositions/options")
        require(options.status_code == 200 and len(options.json()) == 7, "disposition options", options.text)

        invalid_disp = await client.post(f"/api/v1/appointments/{appointment_id}/disposition", json={"disposition_key": "not_real"})
        require(invalid_disp.status_code == 422, "invalid disposition rejected", invalid_disp.text)

        disp = await client.post(
            f"/api/v1/appointments/{appointment_id}/disposition",
            json={
                "disposition_key": "sale",
                "notes": "Sold during runtime validation.",
                "call_duration_seconds": 420,
                "sale_carrier": "Cigna",
                "sale_product": "ACA",
                "premium_amount": "123.45",
                "policy_number": f"POL-{uuid4().hex[:8]}",
            },
        )
        require(disp.status_code == 200 and disp.json()["insurance_sold"] is True, "sale disposition save", disp.text)
        disp_id = disp.json()["id"]
        require(verify_db_row(AppointmentDisposition, identity["tenant_id"], id=disp_id), "disposition DB persistence")
        await asyncio.sleep(0.3)

        dup_disp = await client.post(f"/api/v1/appointments/{appointment_id}/disposition", json={"disposition_key": "attempted"})
        require(dup_disp.status_code == 200 and dup_disp.json()["disposition_key"] == "attempted", "disposition update/edit", dup_disp.text)

        report = await client.get("/api/v1/appointments/dispositions", params={"size": 20})
        require(report.status_code == 200 and report.json()["summary"]["total"] >= 1, "disposition report summary", report.text)
        pdf = await client.get("/api/v1/appointments/dispositions/export.pdf")
        require(pdf.status_code == 200 and pdf.content.startswith(b"%PDF"), "disposition PDF export", pdf.text[:100])

        license_payload = {
            "agent_id": identity["agent_id"],
            "state_code": "NV",
            "license_number": f"NV-{uuid4().hex[:6]}",
            "effective_date": datetime.now(timezone.utc).date().isoformat(),
            "expiration_date": (datetime.now(timezone.utc).date() + timedelta(days=365)).isoformat(),
            "status": "ACTIVE",
        }
        lic = await client.post("/api/v1/compliance/state-licenses", json=license_payload)
        require(lic.status_code == 201, "state license create", lic.text)
        require(verify_db_row(AgentStateLicense, identity["tenant_id"], id=lic.json()["id"]), "state license DB persistence")

        carrier_payload = {
            "agent_id": identity["agent_id"],
            "carrier_name": "Cigna",
            "state_code": "NV",
            "appointment_number": f"APPT-{uuid4().hex[:6]}",
            "effective_date": datetime.now(timezone.utc).date().isoformat(),
            "expiration_date": (datetime.now(timezone.utc).date() + timedelta(days=120)).isoformat(),
            "status": "ACTIVE",
        }
        carrier = await client.post("/api/v1/compliance/carrier-appointments", json=carrier_payload)
        require(carrier.status_code == 201, "carrier appointment create", carrier.text)

        bad_license = await client.post("/api/v1/compliance/state-licenses", json={**license_payload, "state_code": "XX"})
        require(bad_license.status_code == 422, "invalid state rejected", bad_license.text)

        eligible = await client.get("/api/v1/compliance/eligibility", params={"carrier": "Cigna", "state": "NV"})
        require(
            eligible.status_code == 200 and any(a["id"] == identity["agent_id"] for a in eligible.json()["items"]),
            "eligible agent list",
            eligible.text,
        )

        approved = await client.post(
            "/api/v1/compliance/deals/submit",
            json={"agent_id": identity["agent_id"], "lead_id": lead_id, "customer_name": "Runtime Lead", "carrier": "Cigna", "state": "NV", "premium": "123.45"},
        )
        require(approved.status_code == 201 and approved.json()["decision"] == "APPROVED", "approved deal decision", approved.text)
        require(verify_db_row(DealApprovalLog, identity["tenant_id"], deal_id=approved.json()["deal"]["id"]), "approval log DB persistence")
        await asyncio.sleep(0.3)

        rejected = await client.post(
            "/api/v1/compliance/deals/submit",
            json={"agent_id": identity["agent_id"], "customer_name": "Runtime Reject", "carrier": "Aetna", "state": "TX"},
        )
        require(rejected.status_code == 201 and rejected.json()["decision"] == "NOT_APPROVED", "rejected deal decision", rejected.text)
        require(verify_db_row(ComplianceEvent, identity["tenant_id"], deal_id=rejected.json()["deal"]["id"]), "rejection compliance event")
        await asyncio.sleep(0.3)

        revalidate = await client.patch(f"/api/v1/compliance/deals/{approved.json()['deal']['id']}/revalidate", json={})
        require(revalidate.status_code == 200 and revalidate.json()["decision"] == "APPROVED", "deal revalidation", revalidate.text)

        dash = await client.get("/api/v1/compliance/dashboard")
        analytics = await client.get("/api/v1/compliance/analytics")
        require(dash.status_code == 200 and "approval_rate" in dash.json(), "compliance dashboard", dash.text)
        require(analytics.status_code == 200 and analytics.json()["total_decisions"] >= 2, "compliance analytics", analytics.text)

        scan_exp = await client.post("/api/v1/compliance/scan/expirations")
        scan_risk = await client.post("/api/v1/compliance/scan/risk")
        require(scan_exp.status_code == 200, "expiration scanner", scan_exp.text)
        require(scan_risk.status_code == 200, "risk scanner", scan_risk.text)

        noshow = await client.get("/api/v1/booking/no-show/batch")
        require(noshow.status_code == 200 and "predictions" in noshow.json(), "booking no-show batch", noshow.text)

        reminders = await client.get("/api/v1/booking/reminders")
        require(reminders.status_code == 200 and "total" in reminders.json(), "reminder listing", reminders.text)

        audit = await client.get("/api/v1/audit")
        require(audit.status_code == 200, "audit endpoint", audit.text)

        realtime_status = await client.get("/api/v1/realtime/status")
        require(
            realtime_status.status_code == 200
            and realtime_status.json().get("status") == "ok"
            and realtime_status.json().get("websocket") == "active",
            "realtime status",
            realtime_status.text,
        )

        after = db_counts(identity["tenant_id"])
        require(after["leads"] > before["leads"], "lead count increased", {"before": before, "after": after})
        require(after["messages"] > before["messages"], "message count increased", {"before": before, "after": after})
        require(after["appointments"] > before["appointments"], "appointment count increased", {"before": before, "after": after})
        require(after["dispositions"] > before["dispositions"], "disposition count increased", {"before": before, "after": after})
        require(after["approval_logs"] >= 3, "approval logs created", after)

        expected_events = {"connected", "conversation_message_created", "appointment_created", "appointment_updated", "appointment_disposition_saved", "deal_approved", "deal_not_approved"}
        seen = {name for name, _ in events}
        require(expected_events.issubset(seen), "Socket.IO event delivery", {"expected": sorted(expected_events), "seen": sorted(seen)})

        sio.disconnect()

    result = {
        "identity": {k: v for k, v in identity.items() if k != "token"},
        "db_counts": db_counts(identity["tenant_id"]),
        "socket_events": sorted({name for name, _ in events}),
        "checks": "passed",
    }
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception as exc:
        print(f"VALIDATION_FAILED: {exc}", file=sys.stderr)
        raise
