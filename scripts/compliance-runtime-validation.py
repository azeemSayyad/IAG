#!/usr/bin/env python3
import json
import os
import time
from datetime import date, timedelta
from typing import Any

import httpx
import psycopg2
import socketio


API = os.getenv("LOCAL_E2E_API_URL", "http://127.0.0.1:18000")
FRONTEND = os.getenv("LOCAL_E2E_FRONTEND_URL", "http://127.0.0.1:13000")
POSTGRES_URL = os.getenv(
    "POSTGRES_URL",
    "postgresql://launchpad:launchpad-local-compose-password@127.0.0.1:15432/launchpad",
)


class Runner:
    def __init__(self):
        self.passed = []

    def ok(self, name: str, detail: Any = ""):
        self.passed.append(name)
        print(f"PASS {name} - {detail}")

    def assert_true(self, name: str, condition: bool, detail: Any = ""):
        if not condition:
            print(f"FAIL {name} - {detail}")
            raise AssertionError(f"{name}: {detail}")
        self.ok(name, detail)


def db_fetchone(sql: str, params: tuple = ()):
    with psycopg2.connect(POSTGRES_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        conn.commit()
        return row


def db_execute(sql: str, params: tuple = ()):
    with psycopg2.connect(POSTGRES_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            try:
                row = cur.fetchone()
            except psycopg2.ProgrammingError:
                row = None
        conn.commit()
        return row


def wait_for(predicate, timeout: float = 8.0, interval: float = 0.25) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def main():
    runner = Runner()
    client = httpx.Client(timeout=60)
    unique = int(time.time())

    health = client.get(f"{API}/health")
    runner.assert_true("backend health", health.status_code == 200, health.text)
    runner.assert_true("frontend compliance page loads", client.get(f"{FRONTEND}/compliance.html").status_code == 200)
    runner.assert_true("frontend add-deal guard page loads", client.get(f"{FRONTEND}/add-deal-4.html").status_code == 200)
    runner.assert_true("frontend deals page loads", client.get(f"{FRONTEND}/deals.html").status_code == 200)
    runner.assert_true("frontend agent profile page loads", client.get(f"{FRONTEND}/agent-performance.html").status_code == 200)
    runner.assert_true("frontend dashboard page loads", client.get(f"{FRONTEND}/dashboard.html").status_code == 200)
    runner.assert_true("compliance tables migrated", db_fetchone("select to_regclass('deal_approval_logs')")[0] == "deal_approval_logs")

    register = client.post(f"{API}/api/v1/auth/register", json={
        "email": f"compliance-runtime-{unique}@example.com",
        "password": "LocalTest1",
        "first_name": "Sarah",
        "last_name": "Chen",
        "company_name": f"Compliance Runtime {unique}",
    })
    runner.assert_true("auth register tenant admin", register.status_code == 201, register.text)
    token = register.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get(f"{API}/api/v1/auth/me", headers=headers).json()
    tenant_id = me["tenant_id"]
    user_id = me["id"]
    agent_id = db_execute(
        """
        insert into agents (tenant_id, user_id, timezone, daily_capacity, max_concurrent, skills, weight, status)
        values (%s, %s, 'America/New_York', 8, 1, '[]'::jsonb, 100, 'active')
        returning id
        """,
        (tenant_id, user_id),
    )[0]
    runner.ok("agent profile created", str(agent_id))

    events = {name: [] for name in [
        "state_license_created",
        "carrier_appointment_created",
        "deal_approved",
        "deal_not_approved",
        "appointment_expiring",
        "compliance_event_created",
        "notification",
    ]}
    sio = socketio.Client(logger=False, engineio_logger=False)
    for name in events:
        sio.on(name, lambda data, event_name=name: events[event_name].append(data))
    sio.connect(API, auth={"token": token}, transports=["polling"])

    today = date.today()
    license_res = client.post(f"{API}/api/v1/compliance/state-licenses", headers=headers, json={
        "agent_id": str(agent_id),
        "state_code": "NV",
        "license_number": f"NV-{unique}",
        "effective_date": str(today - timedelta(days=10)),
        "expiration_date": str(today + timedelta(days=365)),
        "status": "ACTIVE",
    })
    runner.assert_true("state license created through API", license_res.status_code == 201, license_res.text)

    appointment_res = client.post(f"{API}/api/v1/compliance/carrier-appointments", headers=headers, json={
        "agent_id": str(agent_id),
        "carrier_name": "Cigna",
        "state_code": "NV",
        "appointment_number": f"CIG-{unique}",
        "effective_date": str(today - timedelta(days=10)),
        "expiration_date": str(today + timedelta(days=30)),
        "status": "ACTIVE",
    })
    runner.assert_true("carrier appointment created through API", appointment_res.status_code == 201, appointment_res.text)
    appointment_id = appointment_res.json()["id"]

    agents_res = client.get(f"{API}/api/v1/compliance/agents", headers=headers)
    runner.assert_true("compliance agents endpoint exposes agent", agents_res.status_code == 200 and any(a["id"] == str(agent_id) for a in agents_res.json()["items"]), agents_res.text)

    profile_res = client.get(f"{API}/api/v1/compliance/agents/{agent_id}/profile", headers=headers)
    runner.assert_true(
        "agent compliance profile includes appointments and licenses",
        profile_res.status_code == 200
        and profile_res.json()["summary"]["active_state_licenses"] >= 1
        and profile_res.json()["summary"]["active_carrier_appointments"] >= 1,
        profile_res.text,
    )

    eligible_res = client.get(f"{API}/api/v1/compliance/eligibility", headers=headers, params={"carrier": "Cigna", "state": "NV"})
    runner.assert_true("eligible agent list includes compliant agent", eligible_res.status_code == 200 and eligible_res.json()["total"] >= 1, eligible_res.text)

    ineligible_res = client.get(f"{API}/api/v1/compliance/eligibility", headers=headers, params={"carrier": "Aetna", "state": "TX"})
    runner.assert_true("eligible agent list excludes ineligible state/carrier", ineligible_res.status_code == 200 and ineligible_res.json()["total"] == 0, ineligible_res.text)

    approved = client.post(f"{API}/api/v1/compliance/deals/submit", headers=headers, json={
        "agent_id": str(agent_id),
        "customer_name": "Approved Customer",
        "carrier": "Cigna",
        "state": "NV",
        "plan_type": "ACA",
        "premium": "275.50",
    })
    runner.assert_true("deal approved automatically", approved.status_code == 201 and approved.json()["decision"] == "APPROVED", approved.text)
    approved_deal_id = approved.json()["deal"]["id"]

    blocked = client.post(f"{API}/api/v1/compliance/deals/submit", headers=headers, json={
        "agent_id": str(agent_id),
        "customer_name": "Blocked Customer",
        "carrier": "Aetna",
        "state": "TX",
        "plan_type": "ACA",
        "premium": "300.00",
    })
    runner.assert_true("deal blocked automatically", blocked.status_code == 201 and blocked.json()["decision"] == "NOT_APPROVED", blocked.text)

    deals_res = client.get(f"{API}/api/v1/compliance/deals", headers=headers)
    runner.assert_true("deals workspace API returns approval decisions", deals_res.status_code == 200 and deals_res.json()["total"] >= 2, deals_res.text)

    revalidate_res = client.patch(f"{API}/api/v1/compliance/deals/{approved_deal_id}/revalidate", headers=headers, json={})
    runner.assert_true("deal edit/reopen revalidation uses compliance engine", revalidate_res.status_code == 200 and revalidate_res.json()["decision"] == "APPROVED", revalidate_res.text)

    log_count = db_fetchone(
        "select count(*) from deal_approval_logs where tenant_id=%s and agent_id=%s",
        (tenant_id, str(agent_id)),
    )[0]
    runner.assert_true("approval logs persisted", log_count >= 2, log_count)

    scan = client.post(f"{API}/api/v1/compliance/scan/expirations", headers=headers)
    runner.assert_true("expiration scan creates event", scan.status_code == 200 and scan.json()["events_created"] >= 1, scan.text)

    revoked = client.patch(
        f"{API}/api/v1/compliance/carrier-appointments/{appointment_id}",
        headers=headers,
        json={"status": "REVOKED"},
    )
    runner.assert_true("lost appointment rule update accepted", revoked.status_code == 200, revoked.text)
    eligible_after_revoked = client.get(f"{API}/api/v1/compliance/eligibility", headers=headers, params={"carrier": "Cigna", "state": "NV"})
    runner.assert_true("revoked appointment removes agent eligibility", eligible_after_revoked.status_code == 200 and eligible_after_revoked.json()["total"] == 0, eligible_after_revoked.text)
    lost_event = db_fetchone(
        "select count(*) from compliance_events where tenant_id=%s and event_type='COMPLIANCE_REVOKED'",
        (tenant_id,),
    )[0]
    runner.assert_true("lost appointment event persisted", lost_event >= 1, lost_event)

    csv_body = (
        "agent_email,carrier,state,effective_date,expiration_date,appointment_number,status\n"
        f"{me['email']},Humana,FL,{today - timedelta(days=1)},{today + timedelta(days=120)},HUM-{unique},ACTIVE\n"
    )
    csv_res = client.post(
        f"{API}/api/v1/compliance/carrier-appointments/import-csv",
        headers=headers,
        files={"file": ("appointments.csv", csv_body, "text/csv")},
    )
    runner.assert_true("carrier appointment CSV import", csv_res.status_code == 200 and csv_res.json()["created"] == 1, csv_res.text)

    dashboard = client.get(f"{API}/api/v1/compliance/dashboard", headers=headers)
    runner.assert_true("compliance dashboard reflects DB", dashboard.status_code == 200 and dashboard.json()["compliance_alerts"] >= 1, dashboard.text)

    analytics = client.get(f"{API}/api/v1/compliance/analytics", headers=headers)
    runner.assert_true("compliance analytics reflect decisions", analytics.status_code == 200 and analytics.json()["total_decisions"] >= 2, analytics.text)

    runner.assert_true("realtime deal approved emitted", wait_for(lambda: len(events["deal_approved"]) >= 1), events)
    runner.assert_true("realtime deal not approved emitted", wait_for(lambda: len(events["deal_not_approved"]) >= 1), events)
    runner.assert_true("realtime compliance event emitted", wait_for(lambda: len(events["compliance_event_created"]) >= 1 or len(events["appointment_expiring"]) >= 1), events)

    sio.disconnect()
    print(json.dumps({"passed": len(runner.passed), "checks": runner.passed}, indent=2))


if __name__ == "__main__":
    main()
