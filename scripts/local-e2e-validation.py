#!/usr/bin/env python3
"""Local end-to-end validation for Launchpad Call Center.

Requires local backend, frontend, PostgreSQL, Redis, Ollama, Celery worker,
and Celery beat to be running. This script validates behavior, persistence,
queues, and realtime events. It intentionally does not fake Engage outbound
success; real outbound SMS remains blocked until real Engage credentials are
entered.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def main() -> None:
    runner = CheckRunner()
    client = httpx.Client(timeout=30)
    unique = int(time.time())
    email = f"local-e2e-{unique}@example.com"
    password = "LocalTest1"
    phone = f"+1555{str(unique)[-7:]}"

    health = client.get(f"{API}/health")
    runner.assert_true("backend health", health.status_code == 200 and health.json()["status"] == "ok", health.text)

    login_page = client.get(f"{FRONTEND}/login.html")
    runner.assert_true("frontend login html", login_page.status_code == 200 and "__ebAPI" in login_page.text)

    api_js = client.get(f"{FRONTEND}/services/api.js")
    runner.assert_true("frontend realtime client", api_js.status_code == 200 and "/socket.io" in api_js.text)

    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    runner.assert_true("redis ping", redis_client.ping() is True)

    db_check = db_fetchone("select 1")
    runner.assert_true("postgres select", db_check == (1,))

    bad_register = client.post(f"{API}/api/v1/auth/register", json={
        "email": f"bad-{unique}@example.com",
        "password": "short",
        "first_name": "Bad",
        "last_name": "Password",
        "company_name": "Launchpad Local",
    })
    runner.assert_true("auth schema validation", bad_register.status_code == 422, bad_register.text)

    register = client.post(f"{API}/api/v1/auth/register", json={
        "email": email,
        "password": password,
        "first_name": "Local",
        "last_name": "E2E",
        "company_name": f"Launchpad Local {unique}",
    })
    runner.assert_true("auth register", register.status_code == 201, register.text)
    token = register.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get(f"{API}/api/v1/auth/me", headers=headers)
    runner.assert_true("auth me", me.status_code == 200 and me.json()["email"] == email, me.text)
    user_id = me.json()["id"]
    tenant_id = me.json()["tenant_id"]

    unauth = client.get(f"{API}/api/v1/leads")
    runner.assert_true("auth required on leads", unauth.status_code in (401, 403), unauth.text)

    bad_lead = client.post(f"{API}/api/v1/leads", headers=headers, json={"first_name": "NoPhone"})
    runner.assert_true("lead schema validation", bad_lead.status_code == 422, bad_lead.text)

    lead_payload = {
        "source": "local_e2e",
        "first_name": "Ada",
        "last_name": "Flow",
        "phone": phone,
        "email": f"ada-{unique}@example.com",
        "state": "NY",
        "city": "New York",
        "zip_code": "10001",
        "tags": ["local-e2e"],
        "custom_fields": {"timezone": "America/New_York"},
    }
    lead = client.post(f"{API}/api/v1/leads", headers=headers, json=lead_payload)
    runner.assert_true("lead create", lead.status_code == 201, lead.text)
    lead_id = lead.json()["id"]
    persisted_lead = db_fetchone("select phone, email from leads where id = %s", (lead_id,))
    runner.assert_true("lead db persistence", persisted_lead == (phone, lead_payload["email"]))

    duplicate = client.post(f"{API}/api/v1/ingestion/api?dedup_mode=skip", headers=headers, json=lead_payload)
    runner.assert_true("ingestion duplicate protection", duplicate.status_code == 409, duplicate.text)

    csv_content = "first_name,last_name,phone,email,state\nGrace,Hopper,+15550001111,grace@example.com,NY\nGrace,Hopper,+15550001111,grace2@example.com,NY\n"
    csv_import = client.post(
        f"{API}/api/v1/ingestion/csv",
        headers=headers,
        data={"source": "local_csv", "dedup_mode": "skip"},
        files={"file": ("leads.csv", csv_content, "text/csv")},
    )
    runner.assert_true("csv ingestion", csv_import.status_code == 200 and "summary" in csv_import.json(), csv_import.text)

    agent_id = db_execute(
        """
        insert into agents (id, tenant_id, user_id, timezone, daily_capacity, max_concurrent, skills, weight, status, created_at, updated_at)
        values (gen_random_uuid(), %s, %s, 'America/New_York', 8, 1, '[]'::jsonb, 100, 'active', now(), now())
        on conflict (user_id) do update set status='active'
        returning id
        """,
        (tenant_id, user_id),
    )[0]
    runner.ok("agent row", str(agent_id))

    conversation = client.post(f"{API}/api/v1/conversations", headers=headers, json={
        "lead_id": lead_id,
        "status": "initiated",
    })
    runner.assert_true("conversation create", conversation.status_code == 201, conversation.text)
    conversation_id = conversation.json()["id"]

    received: list[dict] = []
    sio = socketio.Client(logger=False, engineio_logger=False)

    @sio.on("conversation_message_created")
    def on_message(data: dict) -> None:
        received.append(data)

    sio.connect(API, auth={"token": token}, transports=["polling"])
    msg = client.post(f"{API}/api/v1/conversations/{conversation_id}/messages", headers=headers, json={
        "content": "Local realtime validation",
        "sender": "agent",
        "message_type": "sms",
        "send_sms": False,
    })
    runner.assert_true("message create no provider", msg.status_code == 201, msg.text)
    for _ in range(20):
        if received:
            break
        time.sleep(0.25)
    sio.disconnect()
    runner.assert_true("socket conversation event", bool(received) and received[0]["content"] == "Local realtime validation")

    invalid_webhook = client.post(
        f"{API}/api/v1/webhooks/engage-clouds",
        headers={"X-EngageCloud-Webhook-Secret": "wrong-secret"},
        json={"event": "message.received", "id": f"bad-{unique}", "data": {"from": phone, "body": "bad"}},
    )
    runner.assert_true("webhook invalid signature", invalid_webhook.status_code == 403, invalid_webhook.text)

    webhook_payload = {
        "event": "message.received",
        "id": f"local-e2e-webhook-{unique}",
        "data": {
            "from": phone,
            "to": "+15557654321",
            "body": "I want to book an appointment",
            "message_id": f"engage-inbound-{unique}",
        },
    }
    webhook = client.post(
        f"{API}/api/v1/webhooks/engage-clouds",
        headers={"X-EngageCloud-Webhook-Secret": WEBHOOK_SECRET},
        json=webhook_payload,
    )
    runner.assert_true("webhook valid persistence", webhook.status_code == 200 and webhook.json()["status"] == "processed", webhook.text)
    inbound_row = db_fetchone(
        "select content from messages where provider_message_sid = %s",
        (f"engage-inbound-{unique}",),
    )
    runner.assert_true("webhook db message", inbound_row == ("I want to book an appointment",))

    replay = client.post(
        f"{API}/api/v1/webhooks/engage-clouds",
        headers={"X-EngageCloud-Webhook-Secret": WEBHOOK_SECRET},
        json=webhook_payload,
    )
    runner.assert_true("webhook replay protection", replay.status_code == 200 and replay.json()["status"] == "duplicate", replay.text)

    ai = client.post(f"{API}/api/v1/ai/conversation/message", headers=headers, json={
        "lead_id": lead_id,
        "conversation_id": conversation_id,
        "message": "Can you help me choose a plan?",
    })
    ai_body = ai.json() if ai.status_code == 200 else {}
    runner.assert_true(
        "ai conversation",
        ai.status_code == 200
        and ai_body.get("message")
        and ai_body.get("generation", {}).get("model_used") != "template_fallback",
        ai.text,
    )
    for tool_call in ai_body.get("tool_calls", []):
        if tool_call.get("tool") == "search_slots" and tool_call.get("result", {}).get("date"):
            runner.assert_true(
                "ai booking slots not past dated",
                tool_call["result"]["date"] >= datetime.now(timezone.utc).date().isoformat(),
                json.dumps(tool_call),
            )

    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)
    appt = client.post(f"{API}/api/v1/appointments", headers=headers, json={
        "lead_id": lead_id,
        "agent_id": str(agent_id),
        "conversation_id": conversation_id,
        "start_time": start.isoformat().replace("+00:00", "Z"),
        "end_time": (start + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
    })
    runner.assert_true("appointment create", appt.status_code == 201 and appt.json()["status"] == "confirmed", appt.text)
    appt_id = appt.json()["id"]
    appt_row = db_fetchone("select status from appointments where id = %s", (appt_id,))
    runner.assert_true("appointment db persistence", appt_row == ("confirmed",))

    queues = client.get(f"{API}/api/v1/ai/queues", headers=headers)
    runner.assert_true("reminder queue scheduled", queues.status_code == 200 and queues.json().get("reminders", 0) >= 3, queues.text)

    from workers.celery_app import celery_app

    task = celery_app.send_task("workers.tasks.system.health_check", queue="analytics")
    task_result = task.get(timeout=10)
    runner.assert_true("celery task execution", task_result.get("success") is True and "queues" in task_result, json.dumps(task_result))

    overview = client.get(f"{API}/api/v1/admin/analytics/overview", headers=headers)
    runner.assert_true("analytics overview", overview.status_code == 200 and isinstance(overview.json(), dict), overview.text)

    audit = client.get(f"{API}/api/v1/audit", headers=headers)
    runner.assert_true("audit endpoint", audit.status_code == 200 and "items" in audit.json(), audit.text)

    passed = sum(1 for _, ok, _ in runner.results if ok)
    print(json.dumps({"passed": passed, "failed": 0, "checks": [name for name, _, _ in runner.results]}, indent=2))


if __name__ == "__main__":
    main()
