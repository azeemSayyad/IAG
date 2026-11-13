"""HealthSherpa text campaigns (app/hs_auth/campaigns.py).

The first tests need nothing. The flow tests run the REAL code against the
local Postgres (skipped when it isn't up), each inside one outer transaction
that is rolled back, with the Telnyx sender faked and the clock movable:
blast -> YES -> approved follow-up -> answer -> auto job; no answer in 10 min
then a late answer; 7-min reminder; 15-min expiry; STOP at every stage; the
dialer never sees these replies; a dialer text sent later takes the number back.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.hs_auth import campaigns as hc
from app.hs_auth import service
from app.voice_dialer import campaigns as vc
from app.voice_dialer import replies


# ---------------------------------------------------------------- no database

def test_followup_is_the_approved_wording_verbatim():
    assert hc.FOLLOWUP_TEXT == (
        "Insurance Alliance Group: You will get a text link to authorize help with your plan. "
        "What company are you renewing?Msg&data rates apply. Reply STOP to opt out"
    )
    # render() would add a STOP line if it were missing — it isn't, so the text is untouched.
    assert vc.render(hc.FOLLOWUP_TEXT, "x") == hc.FOLLOWUP_TEXT


def test_failure_actions_shared_with_the_dialer():
    assert vc.failure_action("40300", "+1") == ("dnc", None)
    assert vc.failure_action("40002", "+1")[0] == "pause"
    assert vc.failure_action("40010", "+15550000000")[1].startswith("+15550000000")
    assert vc.failure_action("30008", "+1") == (None, None)


def test_stage_names():
    R = lambda **k: SimpleNamespace(**{"status": "TEXTING", "convo": None, "needs_reply": False, **k})  # noqa: E731
    assert hc.stage_of(R(convo="QUEUED")) == "QUEUED"
    assert hc.stage_of(R(convo="BLAST_SENT")) == "TEXTED"
    assert hc.stage_of(R(convo="REPLIED")) == "ASKED"
    assert hc.stage_of(R(convo="FOLLOWUP_SENT")) == "ASKED"
    assert hc.stage_of(R(convo="NO_ANSWER")) == "NO_ANSWER"
    assert hc.stage_of(R(convo="ANSWERED", status="PENDING")) == "HS_QUEUED"
    assert hc.stage_of(R(convo="ANSWERED", status="LINK_SENT")) == "LINK_SENT"
    assert hc.stage_of(R(convo="ANSWERED", status="AUTHORIZED")) == "AUTHORIZED"
    assert hc.stage_of(R(convo="ANSWERED", status="NOT_FOUND")) == "HS_REVIEW"
    assert hc.stage_of(R(convo="DNC", status="CANCELLED")) == "DNC"
    assert hc.stage_of(R(convo="FOLLOWUP_SENT", needs_reply=True)) == "NEEDS_REPLY"
    assert hc.stage_of(R(convo="DNC", needs_reply=True)) == "DNC"


# ---------------------------------------------------------------- with the database

def _db_or_skip():
    try:
        from sqlalchemy import text
        from app.core.database import engine

        conn = engine.connect()
        conn.execute(text("select 1 from hs_campaign_texts limit 1"))
        conn.rollback()
        return conn
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"local Postgres with migration 068 not available: {exc}")


class Clock:
    def __init__(self):
        self.t = datetime.now(timezone.utc).replace(microsecond=0)

    def __call__(self):
        return self.t

    def add(self, minutes):
        self.t += timedelta(minutes=minutes)


@pytest.fixture
def env(monkeypatch):
    from sqlalchemy.orm import Session

    from app.models.tenant import Tenant
    from app.models.user import User

    conn = _db_or_skip()
    trans = conn.begin()
    db = Session(bind=conn, join_transaction_mode="create_savepoint")

    clock = Clock()
    for mod in (hc, service, vc):
        monkeypatch.setattr(mod, "_now", clock)
    sent = []

    def fake_send(*, from_, to, text, webhook_url=None):
        sent.append({"from": from_, "to": to, "text": text})
        return "sim-" + uuid.uuid4().hex

    monkeypatch.setattr(hc.telnyx, "send_sms", fake_send)
    monkeypatch.setattr(hc.telnyx, "simulating", lambda: True)
    monkeypatch.setattr(vc, "senders", lambda: ["+15550001111", "+15550002222"])
    monkeypatch.setattr(vc, "_redis", lambda: None)
    monkeypatch.setattr(vc, "hours_settings", lambda db, tid: None)  # hours tested in the dialer's own tests
    monkeypatch.setattr(vc, "daily_cap", lambda: 2000)

    tenant = Tenant(name="hs-campaign-test")
    db.add(tenant)
    db.flush()
    stan = User(tenant_id=tenant.id, email=f"stan-{uuid.uuid4().hex[:8]}@t.test", password_hash="x",
                first_name="Stan", last_name="L", role="super_admin", status="active")
    db.add(stan)
    db.flush()
    ns = SimpleNamespace(db=db, tenant=str(tenant.id), stan=stan, clock=clock, sent=sent)
    try:
        yield ns
    finally:
        db.close()
        trans.rollback()
        conn.close()


CSV = (
    "First Name,Last Name,DOB,State,Phone\n"
    "Ana,Diaz,01/31/1985,FL,(305) 555-0101\n"
    "Bo,Li,02/02/1970,TX,3055550102\n"
    "Cy,Ng,03/03/1990,GA,3055550103\n"
    "Di,Oz,04/04/1980,OH,3055550104\n"
    "Ed,Bad,13/45/1980,FL,3055550105\n"     # bad DOB -> Fix data, not texted
    "Fy,Nophone,05/05/1975,FL,\n"           # no phone -> skipped
    "Gus,Dup,06/06/1966,FL,305-555-0101\n"  # same phone as Ana -> skipped
).encode()


def _campaign(e):
    c = hc.create(e.db, e.tenant, e.stan, name="Renewals", template="Hi {name}, want help renewing? Reply YES",
                  file_name="c.csv", raw=CSV, hs_agent_id=None)
    hc.start(e.db, c)
    return c


def _row(e, first):
    from app.models.hs_auth import HsAuthRequest

    e.db.expire_all()
    return e.db.query(HsAuthRequest).filter(HsAuthRequest.tenant_id == e.tenant, HsAuthRequest.first_name == first).one()


def _reply(e, phone, text):
    """Through the dialer's own inbound handler — the split must happen there."""
    from app.voice_dialer import service as vs

    result, events = vs.on_inbound_sms(e.db, e.tenant, phone, "+15550001111", text, "m-" + uuid.uuid4().hex)
    return result


def _blast_all(e):
    for _ in range(5):
        hc.tick(e.db, e.tenant)


def test_upload_cleans_and_skips(env):
    c = _campaign(env)
    assert (c.rows_in_file, c.skipped_no_phone, c.skipped_duplicate, c.skipped_invalid) == (7, 1, 1, 1)
    assert _row(env, "Ed").convo == "INVALID"
    _blast_all(env)
    texted = {s["to"] for s in env.sent}
    assert texted == {"+13055550101", "+13055550102", "+13055550103", "+13055550104"}
    assert all("Reply STOP" in s["text"] or "STOP" in s["text"].upper() for s in env.sent)
    assert _row(env, "Ana").convo == "BLAST_SENT"
    # Each client hears from one number.
    assert len({s["from"] for s in env.sent if s["to"] == "+13055550101"}) == 1


def test_yes_followup_answer_auto_job_authorized(env):
    from app.models.sms import SmsLead

    _campaign(env)
    _blast_all(env)
    n = len(env.sent)
    r = _reply(env, "+13055550101", "Yes please")
    assert r["result"] == "hs_followup"
    assert env.sent[n]["text"] == hc.FOLLOWUP_TEXT and env.sent[n]["to"] == "+13055550101"
    ana = _row(env, "Ana")
    assert ana.convo == "FOLLOWUP_SENT" and ana.yes_text == "Yes please"

    r = _reply(env, "+13055550101", "Ambetter")
    assert r["result"] == "hs_answered"
    ana = _row(env, "Ana")
    assert (ana.convo, ana.status, ana.renewing_company) == ("ANSWERED", "PENDING", "Ambetter")

    # Never a dialer lead / pool lead.
    assert env.db.query(SmsLead).filter(SmsLead.tenant_id == env.tenant).count() == 0

    out = hc.next_job(env.db, env.tenant, env.stan)
    job = out["job"]
    assert job["auto"] is True and job["max_resends"] == 0 and job["first_name"] == "Ana"
    ana = _row(env, "Ana")
    assert ana.status == "IN_PROGRESS"
    consent = [e for e in ana.events if e["step"] == "consent"][0]["detail"]
    assert "Yes please" in consent and "Ambetter" in consent
    # One at a time.
    assert hc.next_job(env.db, env.tenant, env.stan)["reason"] == "running"

    service.report(env.db, job["id"], job["token"], {"step": "link", "status": "LINK_SENT", "link_sent": True})
    service.report(env.db, job["id"], job["token"], {"step": "done", "status": "AUTHORIZED", "hs_app_id": "123"})
    ana = _row(env, "Ana")
    assert ana.status == "AUTHORIZED" and hc.stage_of(ana) == "AUTHORIZED"
    assert hc.next_job(env.db, env.tenant, env.stan)["reason"] == "idle"


def test_no_answer_then_late_answer(env):
    _campaign(env)
    _blast_all(env)
    _reply(env, "+13055550102", "ok")
    env.clock.add(9)
    hc.tick(env.db, env.tenant)
    assert _row(env, "Bo").convo == "FOLLOWUP_SENT"
    env.clock.add(2)
    hc.tick(env.db, env.tenant)
    assert _row(env, "Bo").convo == "NO_ANSWER"
    assert hc.next_job(env.db, env.tenant, env.stan)["reason"] == "idle"
    env.clock.add(60 * 5)
    assert _reply(env, "+13055550102", "Oscar Health")["result"] == "hs_answered"
    bo = _row(env, "Bo")
    assert bo.status == "PENDING" and bo.renewing_company == "Oscar Health"


def _to_link_sent(e, phone, first):
    _reply(e, phone, "yes")
    _reply(e, phone, "Blue Cross")
    job = hc.next_job(e.db, e.tenant, e.stan)["job"]
    assert job and job["first_name"] == first
    service.report(e.db, job["id"], job["token"], {"step": "link", "status": "LINK_SENT", "link_sent": True})
    return job


def test_reminder_once_at_7_and_expiry_at_15(env):
    c = _campaign(env)
    hc.update(env.db, c, {"reminder_enabled": True})
    _blast_all(env)
    _to_link_sent(env, "+13055550103", "Cy")
    n = len(env.sent)
    env.clock.add(6)
    hc.tick(env.db, env.tenant)
    assert len(env.sent) == n
    env.clock.add(1.5)
    hc.tick(env.db, env.tenant)
    hc.tick(env.db, env.tenant)
    assert len(env.sent) == n + 1 and env.sent[n]["text"] == hc.REMINDER_DRAFT
    env.clock.add(3)
    hc.tick(env.db, env.tenant)
    assert len(env.sent) == n + 1  # only ONE reminder
    assert _row(env, "Cy").status == "LINK_SENT"
    env.clock.add(5)
    hc.tick(env.db, env.tenant)
    cy = _row(env, "Cy")
    assert cy.status == "EXPIRED" and cy.job_token_hash is None


def test_reminder_off_by_default(env):
    _campaign(env)
    _blast_all(env)
    _to_link_sent(env, "+13055550103", "Cy")
    n = len(env.sent)
    env.clock.add(8)
    hc.tick(env.db, env.tenant)
    assert len(env.sent) == n


@pytest.mark.parametrize("stage", ["blast", "followup", "no_answer", "queued", "link_sent"])
def test_stop_at_every_stage_goes_to_dnc(env, stage):
    from app.sms_queue.services import queue_service as qs

    _campaign(env)
    _blast_all(env)
    phone = "+13055550104"
    if stage in ("followup", "no_answer", "queued", "link_sent"):
        _reply(env, phone, "sure")
    if stage == "no_answer":
        env.clock.add(11)
        hc.tick(env.db, env.tenant)
    if stage == "queued":
        _reply(env, phone, "UnitedHealthcare")
    if stage == "link_sent":
        _reply(env, phone, "UnitedHealthcare")
        job = hc.next_job(env.db, env.tenant, env.stan)["job"]
        service.report(env.db, job["id"], job["token"], {"step": "link", "status": "LINK_SENT", "link_sent": True})
    n = len(env.sent)
    assert _reply(env, phone, "STOP")["result"] == "hs_dnc"
    di = _row(env, "Di")
    assert di.convo == "DNC" and qs.is_dnc(env.db, env.tenant, phone)
    if stage in ("queued", "link_sent"):
        assert di.status == "CANCELLED" and di.job_token_hash is None
    # Nothing more is ever texted to them.
    env.clock.add(30)
    hc.tick(env.db, env.tenant)
    assert len(env.sent) == n
    assert hc.next_job(env.db, env.tenant, env.stan)["job"] is None


def test_negative_words_also_dnc(env):
    _campaign(env)
    _blast_all(env)
    assert _reply(env, "+13055550101", "not interested")["result"] == "hs_dnc"
    assert _row(env, "Ana").convo_detail.startswith("Opted out")


def test_daily_limit_holds_auto_jobs(env, monkeypatch):
    monkeypatch.setattr(service, "daily_limit", lambda: 1)
    _campaign(env)
    _blast_all(env)
    job = _to_link_sent(env, "+13055550101", "Ana")
    service.report(env.db, job["id"], job["token"], {"step": "done", "status": "AUTHORIZED"})
    _reply(env, "+13055550102", "yes")
    _reply(env, "+13055550102", "Aetna")
    assert hc.next_job(env.db, env.tenant, env.stan)["reason"] == "limit"


def test_release_gives_the_slot_back(env):
    _campaign(env)
    _blast_all(env)
    _reply(env, "+13055550101", "yes")
    _reply(env, "+13055550101", "Aetna")
    used = service.usage(env.db, env.tenant, env.stan.id)["used"]
    job = hc.next_job(env.db, env.tenant, env.stan)["job"]
    assert service.usage(env.db, env.tenant, env.stan.id)["used"] == used + 1
    hc.release(env.db, env.tenant, env.stan, job["id"])
    assert service.usage(env.db, env.tenant, env.stan.id)["used"] == used
    assert _row(env, "Ana").status == "PENDING"


def test_a_newer_dialer_text_takes_the_number_back(env):
    """Newest sender wins: once the dialer texted the number after us, replies are the dialer's."""
    from app.models.sms import TextCampaign, TextCampaignRecipient

    _campaign(env)
    _blast_all(env)
    tc = TextCampaign(tenant_id=env.tenant, name="dialer", template="x", status="RUNNING")
    env.db.add(tc)
    env.db.flush()
    env.clock.add(1)
    env.db.add(TextCampaignRecipient(campaign_id=tc.id, tenant_id=env.tenant, phone_number="+13055550101",
                                     status="DELIVERED", sent_at=env.clock()))
    env.db.flush()
    assert hc.owner_text(env.db, env.tenant, "+13055550101") is None
    assert hc.owner_text(env.db, env.tenant, "+13055550102") is not None


def test_dnc_at_upload_and_before_send(env):
    from app.sms_queue.services import queue_service as qs

    qs.add_to_dnc(env.db, env.tenant, "+13055550103", "OPTED_OUT")
    env.db.commit()
    c = _campaign(env)
    assert c.skipped_dnc == 1
    qs.add_to_dnc(env.db, env.tenant, "+13055550104", "OPTED_OUT")  # after upload
    env.db.commit()
    _blast_all(env)
    assert "+13055550104" not in {s["to"] for s in env.sent}
    assert _row(env, "Di").convo == "SKIPPED"


def test_paused_campaign_still_answers_but_does_not_blast(env):
    c = _campaign(env)
    hc.tick(env.db, env.tenant)  # one batch (2 numbers -> 2 texts)
    hc.pause(env.db, c)
    first = {s["to"] for s in env.sent}
    for _ in range(3):
        hc.tick(env.db, env.tenant)
    assert {s["to"] for s in env.sent} == first
    phone = sorted(first)[0]
    assert _reply(env, phone, "yes")["result"] == "hs_followup"
    assert env.sent[-1]["text"] == hc.FOLLOWUP_TEXT


def test_campaign_rows_are_not_in_the_manual_list(env):
    _campaign(env)
    out = service.list_requests(env.db, env.tenant, env.stan, status=None, agent=None, batch=None, q=None,
                                limit=100, offset=0)
    assert out["rows"] == []
    with pytest.raises(service.HsAuthError):
        service.start(env.db, env.tenant, env.stan, str(_row(env, "Ana").id))


def test_export_has_everything_for_the_closer(env):
    c = _campaign(env)
    _blast_all(env)
    _reply(env, "+13055550101", "Yes")
    _reply(env, "+13055550101", "Ambetter")
    body = hc.export_csv(env.db, env.tenant, campaign=str(c.id), stage=None, q=None)
    assert body.startswith("﻿")
    import csv
    import io

    rows = list(csv.DictReader(io.StringIO(body.lstrip("﻿"))))
    ana = next(r for r in rows if r["First name"] == "Ana")
    assert ana["Company they're renewing"] == "Ambetter" and ana["Their reply"] == "Yes"
    assert ana["Stage"] == "Queued for HealthSherpa" and ana["DOB"] == "01/31/1985"
    assert "Follow-up: " + hc.FOLLOWUP_TEXT in ana["All texts"]
    assert {r["First name"] for r in rows} == {"Ana", "Bo", "Cy", "Di", "Ed"}


def test_fixing_an_invalid_row_queues_its_text(env):
    _campaign(env)
    ed = _row(env, "Ed")
    service.update(env.db, env.tenant, env.stan, str(ed.id), {"dob": "12/12/1980"})
    ed = _row(env, "Ed")
    assert (ed.convo, ed.status, ed.dob) == ("QUEUED", "TEXTING", date(1980, 12, 12))


def test_extension_diagnostic_is_kept_in_history(env):
    """A page that didn't look as expected: the extension's value-free field
    description is stored (longer than a normal step) so it can be fixed."""
    _campaign(env)
    _blast_all(env)
    _reply(env, "+13055550101", "yes")
    _reply(env, "+13055550101", "Aetna")
    job = hc.next_job(env.db, env.tenant, env.stan)["job"]
    diag = "url /person_search\n" + "input type=text name=person[first_name] filled=no\n" * 40
    service.report(env.db, job["id"], job["token"], {"step": "fill", "detail": "Couldn't fill the first name", "diag": diag})
    ev = [e for e in _row(env, "Ana").events if e["step"] == "diagnostic"]
    assert len(ev) == 1 and len(ev[0]["detail"]) > 500 and "person[first_name]" in ev[0]["detail"]


def test_questions_are_not_answers():
    for q in ("Is this a autonresponder?", "who is this", "How much does it cost", "are you a real person",
              "is this automated", "what is this about?"):
        assert hc.looks_like_question(q), q
    for a in ("Oscar", "Ambetter", "ok", "Blue Cross Blue Shield", "yes"):
        assert not hc.looks_like_question(a), a


def test_hesitation_detection():
    for h in ("How did you get my number?", "not right now", "maybe later", "I already have an agent",
              "why do you need this", "I'm busy", "let me think about it", "I didn't sign up for this"):
        assert hc.looks_hesitant(h), h
    for ok in ("Oscar", "Is this a real person?", "who is this", "Ambetter, is this free?", "yes"):
        assert not hc.looks_hesitant(ok), ok
    assert hc.company_from("Oscar, is this free?") == "Oscar"
    assert hc.company_from("Is this a real person?") is None
    assert hc.company_from("Ambetter") == "Ambetter"


def test_question_with_answer_moves_forward_and_is_flagged(env):
    """Owner's rule: a question doesn't stop the automation — HealthSherpa runs,
    the question waits for a person."""
    _campaign(env)
    _blast_all(env)
    _reply(env, "+13055550102", "yes")
    r = _reply(env, "+13055550102", "Oscar, is this free?")
    bo = _row(env, "Bo")
    assert r["result"] == "hs_answered" and bo.status == "PENDING" and bo.renewing_company == "Oscar"
    assert bo.needs_reply and hc.stage_of(bo) == "NEEDS_REPLY"
    job = hc.next_job(env.db, env.tenant, env.stan)["job"]
    assert job and job["first_name"] == "Bo"      # automation keeps going
    # A person answers later from the client window -> flag cleared, same number.
    n = len(env.sent)
    d = hc.send_manual(env.db, env.tenant, env.stan, str(bo.id), "Yes, it's free to renew. The link is on its way.")
    assert len(env.sent) == n + 1
    assert env.sent[n]["from"] == next(s["from"] for s in env.sent if s["to"] == "+13055550102")
    manual = [t for t in d["texts"] if t["kind"] == "MANUAL"][0]
    assert manual["sent_by_name"] == "Stan L" and not d["needs_reply"]


def test_pure_question_also_moves_forward(env):
    _campaign(env)
    _blast_all(env)
    _reply(env, "+13055550102", "yes")
    _reply(env, "+13055550102", "Is this a autonresponder?")
    bo = _row(env, "Bo")
    assert bo.status == "PENDING" and bo.renewing_company is None and bo.needs_reply


def test_hesitant_reply_pauses_for_a_person(env):
    _campaign(env)
    _blast_all(env)
    _reply(env, "+13055550102", "yes")
    r = _reply(env, "+13055550102", "how did you get my number?")
    bo = _row(env, "Bo")
    assert r["result"] == "hs_hesitant" and bo.needs_reply and bo.status == "TEXTING"
    assert hc.next_job(env.db, env.tenant, env.stan)["job"] is None   # paused
    hc.send_manual(env.db, env.tenant, env.stan, str(bo.id), "You filled out a form with us last year. Which company are you renewing?")
    assert _reply(env, "+13055550102", "Ambetter")["result"] == "hs_answered"
    assert _row(env, "Bo").status == "PENDING"


def test_hesitant_first_reply_gets_no_follow_up(env):
    _campaign(env)
    _blast_all(env)
    n = len(env.sent)
    assert _reply(env, "+13055550103", "who gave you my number")["result"] == "hs_hesitant"
    hc.tick(env.db, env.tenant)
    assert len(env.sent) == n and _row(env, "Cy").needs_reply   # no automatic follow-up
    assert _reply(env, "+13055550103", "ok yes")["result"] == "hs_followup"


def test_regular_admins_run_campaigns():
    for role in ("super_admin", "tenant_admin", "admin", "head", "dev"):
        assert hc.can_manage(SimpleNamespace(role=role)), role
    for role in ("agent", "manager", "lead"):
        assert not hc.can_manage(SimpleNamespace(role=role)), role


def test_text_after_queued_needs_reply_but_thanks_does_not(env):
    _campaign(env)
    _blast_all(env)
    _reply(env, "+13055550101", "yes")
    _reply(env, "+13055550101", "Aetna")
    _reply(env, "+13055550101", "thanks!")
    assert not _row(env, "Ana").needs_reply
    _reply(env, "+13055550101", "I didn't get any link")
    ana = _row(env, "Ana")
    assert ana.needs_reply and hc.stage_of(ana) == "NEEDS_REPLY"
    hc.mark_handled(env.db, env.tenant, env.stan, str(ana.id))
    assert hc.stage_of(_row(env, "Ana")) == "HS_QUEUED"


def test_manual_text_blocked_for_opted_out(env):
    _campaign(env)
    _blast_all(env)
    _reply(env, "+13055550101", "STOP")
    with pytest.raises(hc.CampaignError):
        hc.send_manual(env.db, env.tenant, env.stan, str(_row(env, "Ana").id), "hello?")


def test_needs_reply_counts_once(env):
    c = _campaign(env)
    _blast_all(env)
    _reply(env, "+13055550101", "yes")
    _reply(env, "+13055550101", "Oscar, who is this?")
    out = hc.clients(env.db, env.tenant, campaign=str(c.id), stage=None, q=None, limit=50, offset=0)
    counts = out["counts"]
    assert counts["NEEDS_REPLY"] == 1
    assert sum(v for k, v in counts.items() if k != "ALL") == counts["ALL"]


def test_after_a_reply_nothing_waits_for_texting_hours(env, monkeypatch):
    """Owner's rule: only the blast is limited to 8am-8pm. Once they reply, the
    lookup (and the reminder) go right away, any hour."""
    closed = lambda *a, **k: False  # noqa: E731 — it's 10 pm everywhere
    monkeypatch.setattr(vc, "in_hours", closed)
    c = _campaign(env)
    hc.update(env.db, c, {"reminder_enabled": True})
    monkeypatch.setattr(vc, "in_hours", lambda *a, **k: True)
    _blast_all(env)                      # blast was in hours
    monkeypatch.setattr(vc, "in_hours", closed)
    _reply(env, "+13055550101", "yes")
    _reply(env, "+13055550101", "Oscar")
    job = hc.next_job(env.db, env.tenant, env.stan)["job"]
    assert job and job["first_name"] == "Ana"   # not held until morning
    service.report(env.db, job["id"], job["token"], {"step": "link", "status": "LINK_SENT", "link_sent": True})
    n = len(env.sent)
    env.clock.add(7.5)
    hc.tick(env.db, env.tenant)
    hc.tick(env.db, env.tenant)
    assert len(env.sent) == n + 1 and env.sent[n]["text"] == hc.REMINDER_DRAFT
    # ...but the BLAST (us reaching out first) still waits for texting hours.
    before = len(env.sent)
    _campaign(env)                       # a second list, started at "10 pm"
    for _ in range(3):
        hc.tick(env.db, env.tenant)
    assert len(env.sent) == before


# ---------------------------------------------------------------- state numbers + re-text

def test_area_code_states():
    from app.core.area_codes import state_of_number

    assert state_of_number("+12145550001") == "TX"
    assert state_of_number("(305) 555-0101") == "FL"
    assert state_of_number("+15550001111") is None
    nums = ["+12145550001", "+13055550002", "+17135550003"]
    assert vc.numbers_by_state(nums) == {"FL": 1, "TX": 2}
    # A Texas client gets a Texas number; a state we have no number for gets any.
    for p in ("+19725550000", "+18175550001", "+12105550002"):
        assert vc.sender_for(p, nums, None, "TX") in ("+12145550001", "+17135550003")
    assert vc.sender_for("+19725550000", nums, None, "OH") in nums
    # The number a client already knows always wins.
    assert vc.sender_for("+19725550000", nums, "+13055550002", "TX") == "+13055550002"


def test_blast_texts_from_a_same_state_number(env, monkeypatch):
    monkeypatch.setattr(vc, "senders", lambda: ["+12145550001", "+13055550002", "+14045550003"])
    _campaign(env)
    _blast_all(env)
    by_to = {s["to"]: s["from"] for s in env.sent}
    assert by_to["+13055550101"] == "+13055550002"   # Ana FL
    assert by_to["+13055550102"] == "+12145550001"   # Bo TX
    assert by_to["+13055550103"] == "+14045550003"   # Cy GA
    assert by_to["+13055550104"] in ("+12145550001", "+13055550002", "+14045550003")  # Di OH: any
    # Her follow-up comes from the same number she already knows.
    n = len(env.sent)
    _reply(env, "+13055550101", "yes")
    assert env.sent[n]["from"] == "+13055550002"


def test_retext_goes_only_to_clients_who_never_replied(env):
    c = _campaign(env)
    _blast_all(env)
    first_from = {s["to"]: s["from"] for s in env.sent}
    _reply(env, "+13055550101", "yes")                 # Ana replied
    _reply(env, "+13055550102", "STOP")                # Bo opted out
    with pytest.raises(hc.CampaignError):              # texted less than an hour ago
        hc.retext(env.db, c, "")
    env.clock.add(61)
    assert hc.retext(env.db, c, "Hi {name}, just checking in about your 2027 plan. Reply YES") == 2
    assert c.status == "RUNNING"
    n = len(env.sent)
    _blast_all(env)
    new = env.sent[n:]
    assert {s["to"] for s in new} == {"+13055550103", "+13055550104"}    # Cy, Di only
    assert all(s["from"] == first_from[s["to"]] for s in new)          # same number as before
    assert all(s["text"].startswith("Hi Cy") or s["text"].startswith("Hi Di") for s in new)
    assert c.status == "COMPLETED"
    counts = hc._counts(env.db, [c.id])[c.id]
    assert counts["retexted"] == 2 and counts["retext_ready"] == 0
    # A reply to the re-text runs the normal flow.
    assert _reply(env, "+13055550103", "yes")["result"] == "hs_followup"
    # At most 3 re-texts per client in 30 days (Di has had 1)...
    assert hc.RETEXT_MAX == 3
    for _ in range(2):
        env.clock.add(61)
        assert hc.retext(env.db, c, "") == 1            # Di
        _blast_all(env)
    env.clock.add(61)
    with pytest.raises(hc.CampaignError):
        hc.retext(env.db, c, "")
    # ...then the count resets: 30 days later she can be re-texted again.
    env.clock.add(60 * 24 * 30)
    assert hc.retext(env.db, c, "") == 1
    _blast_all(env)
    # Opting out ends it for good.
    _reply(env, "+13055550104", "STOP")
    env.clock.add(60 * 24 * 31)
    with pytest.raises(hc.CampaignError):
        hc.retext(env.db, c, "")


def test_retext_waits_for_pause_and_texting_hours(env, monkeypatch):
    c = _campaign(env)
    _blast_all(env)
    env.clock.add(61)
    assert hc.retext(env.db, c, "") == 4
    hc.pause(env.db, c)
    n = len(env.sent)
    _blast_all(env)
    assert len(env.sent) == n
    monkeypatch.setattr(vc, "in_hours", lambda *a, **k: False)
    hc.start(env.db, c)
    _blast_all(env)
    assert len(env.sent) == n                           # 10 pm for them: waits
    monkeypatch.setattr(vc, "in_hours", lambda *a, **k: True)
    _blast_all(env)
    assert len(env.sent) == n + 4
    # Stop cancels re-texts that haven't gone out.
    env.clock.add(61)
    hc.retext(env.db, c, "")
    hc.stop(env.db, c)
    _blast_all(env)
    assert len(env.sent) == n + 4
