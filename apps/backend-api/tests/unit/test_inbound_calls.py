"""Inbound calls (Auto Dialer) — who a client calling our number reaches.

Against the local Postgres (rolled back), the real routing code
(app/voice_dialer/inbound.py), with Telnyx replaced by a recorder:
closed hours, owner free / busy / away, nobody free, nobody taking calls,
Do-Not-Call, a held caller served when an agent frees up, press 1 = callback.
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.voice_dialer import inbound, service


def _db_or_skip():
    try:
        from sqlalchemy import text
        from app.core.database import engine

        conn = engine.connect()
        conn.execute(text("select 1 from voice_client_owners limit 1"))
        conn.rollback()
        return conn
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"local Postgres with the dialer tables not available: {exc}")


@pytest.fixture
def env(monkeypatch):
    from sqlalchemy.orm import Session

    from app.models.tenant import Tenant
    from app.models.user import User

    conn = _db_or_skip()
    trans = conn.begin()
    db = Session(bind=conn, join_transaction_mode="create_savepoint")

    transfers, hangups = [], []
    monkeypatch.setattr(inbound.telnyx, "transfer", lambda leg, script: transfers.append(leg) or True)
    monkeypatch.setattr(inbound.telnyx, "hangup", lambda leg: hangups.append(leg) or True)

    tenant = Tenant(name="inbound-call-test")
    db.add(tenant)
    db.flush()
    tid = str(tenant.id)

    def agent(name, *, ready=True, status="AVAILABLE", line=True, role="agent"):
        from app.models.sms import SmsQueueAgent

        u = User(tenant_id=tenant.id, email=f"{name.lower()}-{uuid.uuid4().hex[:6]}@t.test", password_hash="x",
                 first_name=name, last_name="T", role=role, status="active")
        db.add(u)
        db.flush()
        a = SmsQueueAgent(tenant_id=tenant.id, user_id=u.id, status=status, voice_ready=ready,
                          voice_leg_uuid=("leg-" + name) if line else None,
                          voice_line_at=datetime.now(timezone.utc) if line else None,
                          last_active_at=datetime.now(timezone.utc))
        db.add(a)
        db.flush()
        return u, a

    s = service.get_settings(db, tid)
    s.hours_enabled = False
    db.flush()
    ns = SimpleNamespace(db=db, tenant=tid, agent=agent, settings=s, transfers=transfers, hangups=hangups)
    try:
        yield ns
    finally:
        db.close()
        trans.rollback()
        conn.close()


PHONE = "+13055550777"


def _call(e, phone=PHONE):
    script, events = inbound.on_call(e.db, e.tenant, phone, "+15610000001", "leg-caller-" + uuid.uuid4().hex[:6])
    from app.models.sms import VoiceCallAttempt

    att = (e.db.query(VoiceCallAttempt).filter(VoiceCallAttempt.tenant_id == e.tenant, VoiceCallAttempt.phone_number == phone)
           .order_by(VoiceCallAttempt.created_at.desc()).first())
    return script, events, att


def _said(script) -> str:
    return " ".join(str(step.get("text", "")) for step in script).lower()


def test_closed_hours_play_the_closed_message(env):
    env.settings.hours_enabled = True
    env.settings.open_days = ""  # closed every day
    env.agent("Amy")
    script, events, att = _call(env)
    assert att.status == "MISSED" and att.error == "closed"
    assert events == [] and script


def test_owner_free_gets_the_call(env):
    amy, _ = env.agent("Amy")
    bob, _ = env.agent("Bob")
    inbound.set_owner(env.db, env.tenant, PHONE, bob.id, source="admin")
    env.db.commit()
    script, events, att = _call(env)
    assert att.status == "CONNECTED" and str(att.agent_user_id) == str(bob.id)
    assert any(e["event"] == "voice:connected" and e["to"] == "user" and e["id"] == str(bob.id) for e in events)
    assert "leg-Bob" in env.transfers and script  # Bob's line joins, the caller's script bridges


def test_owner_busy_caller_holds_for_them(env):
    amy, _ = env.agent("Amy", status="ON_CALL")          # taking calls, on a call
    inbound.set_owner(env.db, env.tenant, PHONE, amy.id, source="admin")
    env.agent("Bob")                                       # free, but the owner wins while she's only busy
    env.db.commit()
    assert inbound.owner_state(env.db, env.tenant, amy.id) == "busy"
    script, events, att = _call(env)
    assert att.status == "HOLDING" and str(att.hold_for_user_id) == str(amy.id) and script


def test_owner_away_next_free_agent_takes_it(env):
    amy, _ = env.agent("Amy", ready=False)                # not taking calls
    bob, _ = env.agent("Bob")
    inbound.set_owner(env.db, env.tenant, PHONE, amy.id, source="admin")
    env.db.commit()
    _, _, att = _call(env)
    assert att.status == "CONNECTED" and str(att.agent_user_id) == str(bob.id)
    # Whoever talked to them becomes the owner.
    assert str(inbound.owner_for(env.db, env.tenant, PHONE)) == str(bob.id)


def test_nobody_taking_calls_schedules_a_callback(env):
    env.agent("Amy", ready=False)
    script, _, att = _call(env)
    assert att.status != "HOLDING" and att.status != "CONNECTED"
    assert "call you back" in _said(script) or "call back" in _said(script)


def test_do_not_call_caller_is_not_connected(env):
    from app.sms_queue.services import queue_service as qs

    env.agent("Amy")
    qs.add_to_dnc(env.db, env.tenant, PHONE, "OPTED_OUT")
    env.db.commit()
    _, _, att = _call(env)
    assert att.status == "MISSED" and env.transfers == []


def test_held_caller_is_served_when_an_agent_frees_up(env):
    amy, a = env.agent("Amy", status="ON_CALL")
    env.db.commit()
    _, _, att = _call(env)
    assert att.status == "HOLDING" and att.hold_for_user_id is None  # nobody owns them, nobody free
    a.status = "AVAILABLE"
    env.db.commit()
    inbound.serve_holding(env.db, env.tenant, env.settings)
    env.db.refresh(att)
    assert att.status == "CONNECTED" and str(att.agent_user_id) == str(amy.id)
    assert att.call_uuid in env.transfers  # the caller is transferred into the call


def test_press_1_on_hold_means_call_me_back(env):
    env.agent("Amy", status="ON_CALL")
    env.db.commit()
    _, _, att = _call(env)
    assert att.status == "HOLDING"
    script, _ = inbound.on_hold_input(env.db, att, "1")
    env.db.refresh(att)
    assert att.status != "HOLDING" and script


def test_auto_dialer_mode_never_offered_a_pool_lead(env):
    """One system at a time (owner, 2026-11-17): an agent in "Auto dialer" mode
    (voice_ready) is never offered a pool lead — even with the dialer OFF — and
    joining "SMS leads" switches them back to the pool."""
    from app.models.sms import SmsLead
    from app.sms_queue.services import queue_service as qs

    env.settings.enabled = False
    u, a = env.agent("Dina", ready=True, line=False)
    lead = SmsLead(tenant_id=env.tenant, phone_number="+13055550901", status="QUEUED", priority="HOT")
    env.db.add(lead)
    env.db.flush()

    qs._try_assign(env.db, env.tenant)
    assert lead.status == "QUEUED" and lead.assigned_agent_id is None

    qs.join(env.db, env.tenant, str(u.id))  # picks "SMS leads"
    assert a.voice_ready is False
    assert lead.status == "ASSIGNED" and str(lead.assigned_agent_id) == str(u.id)
