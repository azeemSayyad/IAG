#!/usr/bin/env python3
"""Full business-runtime validation for Launchpad Call Center.

This complements local-e2e-validation.py with end-to-end business assertions:
lead scoring/outreach queues, provider failure honesty, signed webhook state,
delivery callback updates, appointment realtime/reminders, followups, analytics,
coaching, frontend page API contracts, and Celery worker execution.

Real Engage Clouds delivery depends on valid provider credentials; the script
validates that outbound success is never faked when the provider rejects a send.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import psycopg2
import redis
import socketio


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "workers"))
sys.path.insert(0, str(ROOT / "apps" / "backend-api"))

API = os.getenv("LOCAL_E2E_API_URL", "http://127.0.0.1:8000")
FRONTEND = os.getenv("LOCAL_E2E_FRONTEND_URL", "http://127.0.0.1:5500")
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://postgres:postgres@127.0.0.1:5432/launchpad")
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
WEBHOOK_SECRET = os.getenv("ENGAGE_CLOUD_WEBHOOK_SECRET", "local-webhook-secret")


class CheckRunner:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.results.append((name, True, detail))
        print(f"PASS {name}" + (f" - {detail}" if detail else ""))

    def fail(self, name: str, detail: str) -> None:
        self.results.append((name, False, detail))
        print(f"FAIL {name} - {detail}")
        raise AssertionError(f"{name}: {detail}")

    def assert_true(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.ok(name, detail)
        else:
            self.fail(name, detail or "condition was false")


def db_fetchone(sql: str, params: tuple = ()) -> tuple | None:
    with psycopg2.connect(POSTGRES_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def db_fetchall(sql: str, params: tuple = ()) -> list[tuple]:
    with psycopg2.connect(POSTGRES_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def db_execute(sql: str, params: tuple = ()) -> tuple | None:
    with psycopg2.connect(POSTGRES_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            try:
                row = cur.fetchone()
            except psycopg2.ProgrammingError:
                row = None
            conn.commit()
            return row


def redis_json_items(client: redis.Redis, key: str, limit: int = 200) -> list[dict[str, Any]]:
    items = []
    for raw in client.lrange(key, 0, limit - 1):
        try:
            items.append(json.loads(raw))
        except Exception:
            pass
    return items


def wait_for(predicate, timeout: float = 8.0, interval: float = 0.25) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def main() -> None:
    runner = CheckRunner()
    client = httpx.Client(timeout=120)
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    unique = int(time.time())

    # Phase 1: service boot validation.
    health = client.get(f"{API}/health")
    runner.assert_true("backend health", health.status_code == 200 and health.json().get("status") == "ok", health.text)
    runner.assert_true("postgres select", db_fetchone("select 1") == (1,))
    runner.assert_true("redis ping", redis_client.ping() is True)
    ollama = client.get("http://127.0.0.1:11434/api/tags")
    runner.assert_true("ollama tags", ollama.status_code == 200 and "models" in ollama.json(), ollama.text)

    # Phase 2: auth and frontend runtime contracts.
    email = f"business-runtime-{unique}@example.com"
    password = "LocalTest1"
    register = client.post(f"{API}/api/v1/auth/register", json={
        "email": email,
        "password": password,
        "first_name": "Runtime",
        "last_name": "Validator",
        "company_name": f"Runtime Validation {unique}",
    })
    runner.assert_true("auth register business tenant", register.status_code == 201, register.text)
    token = register.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get(f"{API}/api/v1/auth/me", headers=headers)
    runner.assert_true("auth me business tenant", me.status_code == 200 and me.json()["email"] == email, me.text)
    tenant_id = me.json()["tenant_id"]
    user_id = me.json()["id"]

    frontend_pages = [
        "login.html",
        "dashboard.html",
        "inbox.html",
        "appointments.html",
        "analytics.html",
        "upload-leads.html",
        "qa-review.html",
        "notifications.html",
        "ask-the-brain.html",
    ]
    for page in frontend_pages:
        res = client.get(f"{FRONTEND}/{page}")
        runner.assert_true(f"frontend page loads {page}", res.status_code == 200 and "<html" in res.text.lower(), page)
    api_js = client.get(f"{FRONTEND}/services/api.js")
    runner.assert_true(
        "frontend realtime event bridge covers backend events",
        api_js.status_code == 200
        and "conversation_message_created" in api_js.text
        and "appointment_created" in api_js.text
        and "lead_created" in api_js.text,
        "api.js missing emitted backend event names",
    )

    # Phase 3: realtime connection and event capture.
    events: dict[str, list[dict]] = {
        "lead_created": [],
        "conversation_message_created": [],
        "engage_cloud_inbound_processed": [],
        "appointment_created": [],
        "appointment_updated": [],
        "message_delivery_updated": [],
        "notification": [],
    }
    sio = socketio.Client(logger=False, engineio_logger=False)
    for event_name in events:
        sio.on(event_name, lambda data, name=event_name: events[name].append(data))
    sio.connect(API, auth={"token": token}, transports=["polling"])
    online = client.get(f"{API}/api/v1/realtime/online", headers=headers)
    runner.assert_true("socket authenticated online user", online.status_code == 200 and online.json()["total"] >= 1, online.text)

    # Phase 4: lead creation, scoring, outreach queue, and frontend-visible list.
    phone = f"+1556{str(unique)[-7:]}"
    lead_payload = {
        "source": "business_runtime_manual",
        "first_name": "Maya",
        "last_name": "Qualified",
        "phone": phone,
        "email": f"maya-{unique}@example.com",
        "state": "NY",
        "city": "New York",
        "zip_code": "10001",
        # Keep this runtime lead inside TCPA sending hours so the provider
        # rejection path is exercised instead of stopping at compliance.
        "timezone": "Asia/Kolkata",
        "tags": ["business-runtime"],
        "custom_fields": {"household_size": 3},
    }
    lead = client.post(f"{API}/api/v1/leads", headers=headers, json=lead_payload)
    runner.assert_true("manual lead creates through API", lead.status_code == 201, lead.text)
    lead_body = lead.json()
    lead_id = lead_body["id"]
    runner.assert_true("manual lead scoring triggered", float(lead_body.get("lead_score") or 0) > 0, json.dumps(lead_body))
    lead_row = db_fetchone(
        "select lead_score, phone_normalized, email_normalized from leads where id = %s",
        (lead_id,),
    )
    runner.assert_true(
        "manual lead persisted normalized/scored",
        lead_row and float(lead_row[0]) > 0 and lead_row[1] and lead_row[2] == lead_payload["email"],
        str(lead_row),
    )
    runner.assert_true(
        "manual lead realtime emitted",
        wait_for(lambda: any(e.get("lead_id") == lead_id for e in events["lead_created"])),
        json.dumps(events["lead_created"][-3:]),
    )
    runner.assert_true(
        "manual lead queued outbound outreach",
        any(item.get("lead_id") == lead_id for item in redis_json_items(redis_client, "queue:outbound_sms")),
        "lead-created hook did not enqueue outbound SMS",
    )
    list_leads = client.get(f"{API}/api/v1/leads", headers=headers, params={"search": "Maya", "size": 10})
    runner.assert_true("frontend lead list API sees manual lead", list_leads.status_code == 200 and list_leads.json()["total"] >= 1, list_leads.text)

    # CSV ingestion should deduplicate and score, not just parse.
    csv_phone = f"+1557{str(unique)[-7:]}"
    csv_content = (
        "first_name,last_name,phone,email,state,city,zip_code\n"
        f"Chris,CSV,{csv_phone},chris-{unique}@example.com,TX,Austin,73301\n"
        f"Chris,CSV,{csv_phone},chris-dup-{unique}@example.com,TX,Austin,73301\n"
    )
    csv_import = client.post(
        f"{API}/api/v1/ingestion/csv",
        headers=headers,
        data={"source": "business_runtime_csv", "dedup_mode": "skip"},
        files={"file": ("business-runtime.csv", csv_content, "text/csv")},
    )
    csv_body = csv_import.json() if csv_import.status_code == 200 else {}
    runner.assert_true("csv ingestion business summary", csv_import.status_code == 200 and csv_body.get("summary"), csv_import.text)
    csv_leads = db_fetchall(
        "select id, lead_score from leads where tenant_id = %s and phone = %s",
        (tenant_id, csv_phone),
    )
    runner.assert_true("csv ingestion dedupe and score", len(csv_leads) == 1 and float(csv_leads[0][1]) > 0, str(csv_leads))

    # Phase 5: outbound provider path must fail honestly if Engage rejects it.
    from workers.celery_app import celery_app

    redis_client.lpush("queue:outbound_sms", json.dumps({
        "lead_id": lead_id,
        "tenant_id": tenant_id,
        "lead_name": "Maya Qualified",
        "phone": phone,
        "source": "business_runtime_manual",
        "score": lead_body.get("lead_score"),
        "tier": "warm",
        "attempts": 0,
    }))
    from workers.tasks.sms import process_sms_queue

    sms_result = process_sms_queue.apply().get()
    runner.assert_true(
        "sms queue worker executes without fake provider success",
        sms_result.get("failed", 0) >= 1 and sms_result.get("processed", 0) == 0,
        json.dumps(sms_result),
    )
    runner.assert_true(
        "failed outbound moves to retry queue",
        any(item.get("lead_id") == lead_id and item.get("last_error") for item in redis_json_items(redis_client, "queue:retries")),
        "no retry job found for failed Engage outbound",
    )
    health_task = celery_app.send_task("workers.tasks.system.health_check", queue="analytics")
    health_result = health_task.get(timeout=30)
    runner.assert_true("distributed celery worker executes task", health_result.get("success") is True, json.dumps(health_result))

    # Phase 6: conversation, AI, inbound webhook, delivery webhook.
    agent_id = db_execute(
        """
        insert into agents (id, tenant_id, user_id, timezone, daily_capacity, max_concurrent, skills, weight, status, created_at, updated_at)
        values (gen_random_uuid(), %s, %s, 'UTC', 8, 1, '[]'::jsonb, 100, 'active', now(), now())
        on conflict (user_id) do update set status='active', timezone='UTC'
        returning id
        """,
        (tenant_id, user_id),
    )[0]
    conversation = client.post(f"{API}/api/v1/conversations", headers=headers, json={"lead_id": lead_id, "status": "initiated"})
    runner.assert_true("conversation create business flow", conversation.status_code == 201, conversation.text)
    conversation_id = conversation.json()["id"]
    msg = client.post(f"{API}/api/v1/conversations/{conversation_id}/messages", headers=headers, json={
        "content": "Hi Maya, this is your local runtime validation message.",
        "sender": "agent",
        "message_type": "sms",
        "send_sms": False,
    })
    runner.assert_true("conversation message persisted without provider", msg.status_code == 201, msg.text)
    message_id = msg.json()["id"]
    runner.assert_true(
        "conversation message realtime delivered",
        wait_for(lambda: any(e.get("id") == message_id for e in events["conversation_message_created"])),
        json.dumps(events["conversation_message_created"][-3:]),
    )

    blocked_send = client.post(f"{API}/api/v1/conversations/{conversation_id}/messages", headers=headers, json={
        "content": "This should try Engage and fail honestly if the provider rejects it.",
        "sender": "agent",
        "message_type": "sms",
        "send_sms": True,
    })
    runner.assert_true("outbound Engage rejection blocks fake message", blocked_send.status_code == 502, blocked_send.text)

    intent = client.post(f"{API}/api/v1/internal/intent-detect", headers=headers, json={
        "lead_id": lead_id,
        "conversation_id": conversation_id,
        "text": "Yes, I want to book an appointment tomorrow afternoon.",
    })
    runner.assert_true("intent detection booking", intent.status_code == 200 and intent.json()["intent"], intent.text)

    objection = client.post(f"{API}/api/v1/internal/handle-objection", headers=headers, json={
        "message": "I am worried this will be too expensive.",
        "lead_name": "Maya",
    })
    runner.assert_true("objection handler returns response", objection.status_code == 200 and objection.json()["response"], objection.text)

    ai = client.post(f"{API}/api/v1/ai/conversation/message", headers=headers, json={
        "lead_id": lead_id,
        "conversation_id": conversation_id,
        "message": "I qualify and want to book a call this week. What openings do you have?",
    })
    ai_body = ai.json() if ai.status_code == 200 else {}
    runner.assert_true(
        "ai qualification/booking response real model",
        ai.status_code == 200
        and ai_body.get("message")
        and ai_body.get("generation", {}).get("model_used") != "template_fallback",
        ai.text,
    )
    today = datetime.now(timezone.utc).date().isoformat()
    for call in ai_body.get("tool_calls", []):
        if call.get("tool") == "search_slots" and call.get("result", {}).get("date"):
            runner.assert_true("ai does not offer past slots", call["result"]["date"] >= today, json.dumps(call))

    webhook_payload = {
        "event": "message.received",
        "id": f"business-runtime-inbound-{unique}",
        "data": {
            "from": phone,
            "to": "+15557654321",
            "body": "Yes, please book me for the soonest available appointment.",
            "message_id": f"engage-business-inbound-{unique}",
        },
    }
    inbound = client.post(
        f"{API}/api/v1/webhooks/engage-clouds",
        headers={"X-EngageCloud-Webhook-Secret": WEBHOOK_SECRET},
        json=webhook_payload,
    )
    runner.assert_true("engage inbound webhook processed", inbound.status_code == 200 and inbound.json()["status"] == "processed", inbound.text)
    inbound_row = db_fetchone(
        "select sender, content from messages where provider_message_sid = %s",
        (f"engage-business-inbound-{unique}",),
    )
    runner.assert_true("engage inbound persisted customer message", inbound_row == ("customer", webhook_payload["data"]["body"]), str(inbound_row))
    lead_status = db_fetchone("select status from leads where id = %s", (lead_id,))
    runner.assert_true("lead reply updates lifecycle", lead_status == ("replied",), str(lead_status))
    runner.assert_true(
        "engage inbound realtime emitted",
        wait_for(lambda: any(e.get("lead_id") == lead_id for e in events["engage_cloud_inbound_processed"])),
        json.dumps(events["engage_cloud_inbound_processed"][-3:]),
    )
    replay = client.post(
        f"{API}/api/v1/webhooks/engage-clouds",
        headers={"X-EngageCloud-Webhook-Secret": WEBHOOK_SECRET},
        json=webhook_payload,
    )
    runner.assert_true("engage webhook replay protection", replay.status_code == 200 and replay.json()["status"] == "duplicate", replay.text)

    provider_sid = f"engage-delivery-{unique}"
    db_execute(
        "update messages set provider='engage_cloud', provider_message_sid=%s, delivery_status='queued' where id=%s",
        (provider_sid, message_id),
    )
    delivery = client.post(
        f"{API}/api/v1/webhooks/engage-clouds",
        headers={"X-EngageCloud-Webhook-Secret": WEBHOOK_SECRET},
        json={
            "event": "message.delivered",
            "id": f"business-runtime-delivery-{unique}",
            "data": {"message_id": provider_sid, "status": "delivered"},
        },
    )
    runner.assert_true("engage delivery webhook processed", delivery.status_code == 200 and delivery.json()["status"] == "ok", delivery.text)
    delivered = db_fetchone("select delivery_status, delivered_at is not null from messages where id=%s", (message_id,))
    runner.assert_true("engage delivery persisted", delivered == ("delivered", True), str(delivered))
    runner.assert_true(
        "engage delivery realtime emitted",
        wait_for(lambda: any(e.get("message_id") == message_id for e in events["message_delivery_updated"])),
        json.dumps(events["message_delivery_updated"][-3:]),
    )

    # Phase 7: appointment, conflict, reminders, update realtime.
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=24)
    appt = client.post(f"{API}/api/v1/appointments", headers=headers, json={
        "lead_id": lead_id,
        "agent_id": str(agent_id),
        "conversation_id": conversation_id,
        "start_time": start.isoformat().replace("+00:00", "Z"),
        "end_time": (start + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
    })
    runner.assert_true("appointment create confirmed", appt.status_code == 201 and appt.json()["status"] == "confirmed", appt.text)
    appt_id = appt.json()["id"]
    runner.assert_true("appointment db persisted", db_fetchone("select status from appointments where id=%s", (appt_id,)) == ("confirmed",))
    runner.assert_true(
        "appointment create realtime emitted",
        wait_for(lambda: any(e.get("appointment_id") == appt_id for e in events["appointment_created"])),
        json.dumps(events["appointment_created"][-3:]),
    )
    conflict = client.post(f"{API}/api/v1/appointments", headers=headers, json={
        "lead_id": lead_id,
        "agent_id": str(agent_id),
        "conversation_id": conversation_id,
        "start_time": (start + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "end_time": (start + timedelta(minutes=35)).isoformat().replace("+00:00", "Z"),
    })
    runner.assert_true("appointment overlap rejected", conflict.status_code == 409, conflict.text)
    reminder_jobs = [item for item in redis_json_items(redis_client, "queue:reminders") if item.get("appointment_id") == appt_id]
    runner.assert_true("appointment reminder jobs scheduled", len(reminder_jobs) >= 3, str(reminder_jobs))
    reminder_task = celery_app.send_task("workers.tasks.reminders.process_pending_reminders", queue="reminders")
    reminder_result = reminder_task.get(timeout=30)
    runner.assert_true(
        "reminder worker executes honestly with provider rejection",
        reminder_result.get("total_pending", 0) >= 1 and reminder_result.get("failed", 0) >= 1,
        json.dumps(reminder_result),
    )
    appt_update = client.patch(f"{API}/api/v1/appointments/{appt_id}", headers=headers, json={"status": "completed", "disposition": "won"})
    runner.assert_true("appointment update completed", appt_update.status_code == 200 and appt_update.json()["status"] == "completed", appt_update.text)
    runner.assert_true(
        "appointment update realtime emitted",
        wait_for(lambda: any(e.get("appointment_id") == appt_id and e.get("status") == "completed" for e in events["appointment_updated"])),
        json.dumps(events["appointment_updated"][-3:]),
    )

    # Phase 8: followup worker failure path with no fake provider success.
    follow_phone = f"+1558{str(unique)[-7:]}"
    follow_lead = client.post(f"{API}/api/v1/leads", headers=headers, json={
        "source": "business_runtime_followup",
        "first_name": "NoReply",
        "last_name": "Lead",
        "phone": follow_phone,
        "email": f"noreply-{unique}@example.com",
        "state": "FL",
        "timezone": "UTC",
    })
    runner.assert_true("followup test lead created", follow_lead.status_code == 201, follow_lead.text)
    follow_id = follow_lead.json()["id"]
    follow_conv = client.post(f"{API}/api/v1/conversations", headers=headers, json={"lead_id": follow_id, "status": "active"})
    runner.assert_true("followup conversation created", follow_conv.status_code == 201, follow_conv.text)
    client.post(f"{API}/api/v1/conversations/{follow_conv.json()['id']}/messages", headers=headers, json={
        "content": "Initial outreach",
        "sender": "ai",
        "message_type": "sms",
        "send_sms": False,
    })
    db_execute(
        "update leads set status='contacted', last_contacted_at=now() - interval '25 hours' where id=%s",
        (follow_id,),
    )
    followup = client.post(f"{API}/api/v1/followup/no-reply/process", headers=headers)
    runner.assert_true(
        "no-reply followup worker path executes honestly",
        followup.status_code == 200 and followup.json().get("total_checked", 0) >= 1 and followup.json().get("failed", 0) >= 1,
        followup.text,
    )
    follow_status = client.get(f"{API}/api/v1/followup/no-reply/status/{follow_id}", headers=headers)
    runner.assert_true("no-reply followup status endpoint", follow_status.status_code == 200 and "next_step" in follow_status.json(), follow_status.text)

    # Phase 9: analytics, coaching, notifications, and page API endpoints.
    analytics_overview = client.get(f"{API}/api/v1/admin/analytics/overview", headers=headers)
    runner.assert_true("analytics overview reflects DB", analytics_overview.status_code == 200 and analytics_overview.json().get("leads", {}).get("total", 0) >= 2, analytics_overview.text)
    analytics_trends = client.get(f"{API}/api/v1/admin/analytics/trends", headers=headers, params={"days": 14})
    runner.assert_true(
        "analytics trends available",
        analytics_trends.status_code == 200 and isinstance(analytics_trends.json().get("trends"), list),
        analytics_trends.text,
    )
    ai_analytics = client.get(f"{API}/api/v1/admin/analytics/ai", headers=headers)
    runner.assert_true("ai analytics available", ai_analytics.status_code == 200 and isinstance(ai_analytics.json(), dict), ai_analytics.text)

    coaching = client.get(f"{API}/api/v1/coaching/performance/{agent_id}", headers=headers)
    runner.assert_true("coaching performance endpoint", coaching.status_code == 200 and isinstance(coaching.json(), dict), coaching.text)
    realtime_coach = client.post(
        f"{API}/api/v1/coaching/realtime/coach/{agent_id}",
        headers=headers,
        params={"segment_text": "I need to think about the price before I decide.", "speaker": "customer"},
    )
    runner.assert_true("realtime coaching endpoint", realtime_coach.status_code == 200 and "cues" in realtime_coach.json(), realtime_coach.text)
    call_summary = client.get(f"{API}/api/v1/calls/summary/{appt_id}/quick", headers=headers)
    runner.assert_true("qa/call summary endpoint handles missing transcript honestly", call_summary.status_code in (200, 404), call_summary.text)

    notify = client.post(f"{API}/api/v1/realtime/notify", headers=headers, json={
        "type": "business_runtime",
        "title": "Runtime validation",
        "message": "Runtime validation notification",
        "data": {"lead_id": lead_id},
    })
    runner.assert_true("notification endpoint sends realtime", notify.status_code == 200 and notify.json()["success"] is True, notify.text)
    runner.assert_true("notification realtime emitted", wait_for(lambda: len(events["notification"]) > 0), json.dumps(events["notification"][-3:]))

    page_api_checks = [
        ("dashboard leads", f"{API}/api/v1/leads", {"page": 1, "size": 5}),
        ("dashboard conversations", f"{API}/api/v1/conversations", {"page": 1, "size": 5}),
        ("appointments page", f"{API}/api/v1/appointments", {"page": 1, "size": 5}),
        ("analytics campaigns", f"{API}/api/v1/admin/analytics/campaigns", {}),
        ("notifications audit", f"{API}/api/v1/audit", {"page": 1, "size": 5}),
        ("qa transcripts", f"{API}/api/v1/calls/transcripts", {"page": 1, "size": 5}),
    ]
    for name, url, params in page_api_checks:
        page_res = client.get(url, headers=headers, params=params)
        runner.assert_true(f"frontend API contract {name}", page_res.status_code == 200 and isinstance(page_res.json(), (dict, list)), page_res.text)

    sio.disconnect()
    passed = sum(1 for _, ok, _ in runner.results if ok)
    print(json.dumps({
        "passed": passed,
        "failed": 0,
        "blocked_external_live": [
            "real Engage Clouds outbound SMS accepted by provider",
            "customer receipt and real provider delivery callback",
        ],
        "checks": [name for name, _, _ in runner.results],
    }, indent=2))


if __name__ == "__main__":
    main()
