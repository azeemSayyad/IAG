"""HealthSherpa text campaigns — the HealthSherpa page's own texting flow.

COMPLETELY separate from the Auto Dialer's text campaigns (voice_dialer/
campaigns.py): its own tables (hs_text_campaigns / hs_campaign_texts + the
conversation columns on hs_auth_requests), its own page section, and replies
to it NEVER reach the dialer or the SMS pool. Only the SENDER is shared: the
same Telnyx numbers, per-number pacing, brand-wide daily cap, client-timezone
texting window, Do-Not-Call checks, STOP footer and auto-pause rules
(voice_dialer.campaigns: senders / sender_for / claim_number / in_hours /
dnc_digits / sent_today / failure_action).

The flow, per client (`convo`):
  QUEUED         uploaded; the blast goes out in the client's texting hours
  BLAST_SENT     blast sent — waiting for a reply
  REPLIED        any NON-negative reply -> the approved follow-up is queued
                 (sent at once, any hour — it answers their text)
  FOLLOWUP_SENT  follow-up sent — waiting answer_wait_minutes (10) for the
                 company they're renewing
  NO_ANSWER      nothing in 10 min; a LATE answer still counts
  ANSWERED       any non-negative answer = "Company they're renewing"; the
                 HealthSherpa job is queued (status PENDING) for the
                 campaign's agent, whose portal tab in AUTO MODE runs it
  DNC            STOP / no / abuse / wrong number at ANY stage: Do-Not-Call
                 (the dialer's own list and reasons), queued texts cancelled,
                 a running HealthSherpa job stopped
  INVALID        bad name / DOB / state — "Fix data"; not texted until fixed
  SKIPPED        on Do-Not-Call by send time
  BLAST_FAILED   the carrier refused the blast
  CANCELLED      campaign stopped before their blast went out

After the HealthSherpa link is sent (status LINK_SENT): at reminder_minutes (7)
ONE reminder text (only when reminder_enabled and in texting hours); at
expire_minutes (15) EXPIRED and the agent moves on (no resend unless
resend_on_expiry). Timers run in tick() — Celery beat `hs-campaign-tick`, and
locally the page's own poll. All of this is synchronous DB code: routes run it
in the threadpool (never on the event loop).

The follow-up wording is Stan's approved 10DLC exception to "one text per
person per campaign" and is sent VERBATIM (FOLLOWUP_TEXT).
"""
from __future__ import annotations

import csv
import io
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, text as sql_text
from sqlalchemy.orm import Session

from app.core.active_agents import user_is_active
from app.hs_auth import service
from app.hs_auth.sheet import clean_rows, read_rows
from app.models.hs_auth import HsAuthBatch, HsAuthRequest, HsAuthRun, HsCampaignText, HsTextCampaign
from app.models.sms import SmsMessage, TextCampaignRecipient
from app.models.user import User
from app.voice_dialer import campaigns as vc
from app.voice_dialer import replies, telnyx

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

FOLLOWUP_TEXT = (
    "Insurance Alliance Group: You will get a text link to authorize help with your plan. "
    "What company are you renewing?Msg&data rates apply. Reply STOP to opt out"
)
# Stan's approved first text (2026-11-13) — the New campaign form starts with it.
BLAST_DEFAULT = (
    "Insurance Alliance Group: Hi {name}, this is Stan following up on your health plan for 2027. "
    "Reply YES to renew. Reply STOP to opt out."
)
REMINDER_DRAFT = (
    "Insurance Alliance Group: Did you receive the HealthSherpa link? Tap it to authorize "
    "help with your plan. Reply STOP to opt out"
)

REPLY_WINDOW = vc.REPLY_WINDOW
STALE_SENDING = vc.STALE_SENDING
CANDIDATES = 400
RESEND_GRACE = timedelta(minutes=3)   # resend_on_expiry: let the extension resend first
EDITABLE = ("DRAFT", "PAUSED")
CAMPAIGN_ROLES = ("super_admin", "dev")  # create / start / pause / stop / settings

# Rows the conversation still owns (a reply is matched to them).
NOT_TEXTED = ("QUEUED", "INVALID", "SKIPPED", "BLAST_FAILED", "CANCELLED")


class CampaignError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def e164(phone10: Optional[str]) -> Optional[str]:
    return replies.normalize_phone(phone10) if phone10 else None


def can_manage(user: User) -> bool:
    return (user.role or "") in CAMPAIGN_ROLES


def can_view(user: User) -> bool:
    return service.is_manager(user)


def _et(dt: Optional[datetime]) -> str:
    return dt.astimezone(ET).strftime("%m/%d/%Y %I:%M %p ET") if dt else ""


# ---- Campaign lifecycle ---------------------------------------------------------------

def create(db: Session, tenant_id, user: User, *, name: str, template: str, file_name: str,
           raw: bytes, hs_agent_id: Optional[str]) -> HsTextCampaign:
    template = vc.check_template(template)
    agent_id = hs_agent_id or str(user.id)
    agent = db.query(User).filter(User.id == agent_id, User.tenant_id == tenant_id).first()
    if not agent or not user_is_active(agent):
        raise CampaignError("The HealthSherpa agent wasn't found or is disabled.")

    cleaned = clean_rows(read_rows(file_name or "clients.csv", raw))
    if not cleaned:
        raise CampaignError("No client rows found under the header row.")

    keep, no_phone, dupes = [], 0, 0
    seen: set[str] = set()
    for c in cleaned:
        phone = e164(c["phone"])
        if not phone:
            no_phone += 1
            continue
        if phone in seen:
            dupes += 1
            continue
        seen.add(phone)
        keep.append((c, phone))
    dnc = vc.dnc_digits(db, tenant_id, [p for _c, p in keep])
    texted = [(c, p) for c, p in keep if vc.digits(p) not in dnc]
    if not texted:
        raise CampaignError("Nobody left to text — every row had no valid mobile number, was a duplicate or is on Do-Not-Call.")

    batch = HsAuthBatch(tenant_id=tenant_id, name=f"Campaign: {(name or file_name or 'Upload')}"[:200],
                        uploaded_by=user.id)
    db.add(batch)
    db.flush()
    camp = HsTextCampaign(
        tenant_id=tenant_id, name=(name or "").strip()[:120] or (file_name or "Campaign")[:120],
        template=template, status="DRAFT", file_name=(file_name or "")[:255], hs_agent_id=agent.id,
        batch_id=batch.id, rows_in_file=len(cleaned), skipped_no_phone=no_phone, skipped_duplicate=dupes,
        skipped_dnc=len(keep) - len(texted), reminder_text=REMINDER_DRAFT, created_by=user.id,
    )
    db.add(camp)
    db.flush()
    invalid = 0
    for c, _phone in texted:
        bad = bool(c["errors"])
        invalid += bad
        db.add(HsAuthRequest(
            tenant_id=tenant_id, batch_id=batch.id, campaign_id=camp.id, row_number=c["row_number"],
            first_name=c["first_name"], last_name=c["last_name"], dob=c["dob"], state=c["state"],
            phone=c["phone"], email=c["email"], assigned_to=agent.id,
            status="INVALID" if bad else "TEXTING",
            status_detail="; ".join(c["errors"]) or None,
            convo="INVALID" if bad else "QUEUED",
            convo_detail=("Fix data — not texted until fixed: " + "; ".join(c["errors"])) if bad else None,
            events=[],
        ))
    camp.skipped_invalid = invalid
    batch.total_rows = len(texted)
    batch.invalid_rows = invalid
    db.commit()
    return camp


def find(db: Session, tenant_id, campaign_id) -> HsTextCampaign:
    try:
        cid = uuid.UUID(str(campaign_id))
    except (ValueError, TypeError):
        cid = None
    c = db.query(HsTextCampaign).filter(HsTextCampaign.id == cid, HsTextCampaign.tenant_id == tenant_id).first() if cid else None
    if c is None:
        raise CampaignError("Campaign not found", 404)
    return c


def _int(v, lo: int, hi: int, label: str) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise CampaignError(f"{label} must be a number")
    if not lo <= n <= hi:
        raise CampaignError(f"{label} must be between {lo} and {hi}")
    return n


def update(db: Session, c: HsTextCampaign, data: dict) -> None:
    """Name / blast while DRAFT or PAUSED; timers and the reminder any time."""
    if "template" in data and data["template"] is not None and data["template"] != c.template:
        if c.status not in EDITABLE:
            raise CampaignError("Pause the campaign to change the message", 409)
        c.template = vc.check_template(data["template"])
    if data.get("name") and str(data["name"]).strip():
        c.name = str(data["name"]).strip()[:120]
    if "reminder_text" in data:
        t = str(data["reminder_text"] or "").strip()
        if len(t) > vc.MAX_TEMPLATE:
            raise CampaignError(f"Keep the reminder under {vc.MAX_TEMPLATE} characters")
        c.reminder_text = t or None
    if "reminder_enabled" in data:
        c.reminder_enabled = bool(data["reminder_enabled"])
        if c.reminder_enabled and not (c.reminder_text or "").strip():
            raise CampaignError("Write the reminder text before turning it on")
    if "reminder_minutes" in data:
        c.reminder_minutes = _int(data["reminder_minutes"], 1, 14, "Reminder minute")
    if "answer_wait_minutes" in data:
        c.answer_wait_minutes = _int(data["answer_wait_minutes"], 1, 120, "Wait for their answer")
    if "expire_minutes" in data:
        c.expire_minutes = _int(data["expire_minutes"], 5, 60, "Link wait")
    if "resend_on_expiry" in data:
        c.resend_on_expiry = bool(data["resend_on_expiry"])
    if c.reminder_minutes >= c.expire_minutes:
        raise CampaignError("The reminder must go out before the link wait ends")
    db.commit()


def start(db: Session, c: HsTextCampaign) -> None:
    if c.status not in EDITABLE:
        raise CampaignError("This campaign can't be started", 409)
    if not telnyx.simulating():
        if not telnyx.available():
            raise CampaignError("Texting is not set up (Telnyx is not configured)", 503)
        if not vc.senders():
            raise CampaignError("No sending numbers — set TELNYX_FROM_NUMBERS", 503)
    c.status = "RUNNING"
    c.pause_reason = None
    c.started_at = c.started_at or _now()
    db.commit()


def pause(db: Session, c: HsTextCampaign, reason: Optional[str] = None) -> None:
    if c.status != "RUNNING":
        raise CampaignError("Only a running campaign can be paused", 409)
    c.status = "PAUSED"
    c.pause_reason = reason
    db.commit()


def stop(db: Session, c: HsTextCampaign) -> None:
    """No more blasts. Clients already talking to us still finish (they asked)."""
    if c.status in ("COMPLETED", "STOPPED"):
        return
    now = _now()
    db.query(HsAuthRequest).filter(HsAuthRequest.campaign_id == c.id, HsAuthRequest.convo == "QUEUED").update(
        {"convo": "CANCELLED", "convo_detail": "Campaign stopped before the text went out", "updated_at": now},
        synchronize_session=False)
    db.query(HsCampaignText).filter(HsCampaignText.campaign_id == c.id, HsCampaignText.kind == "BLAST",
                                    HsCampaignText.status == "QUEUED").update({"status": "CANCELLED"}, synchronize_session=False)
    c.status = "STOPPED"
    c.finished_at = now
    db.commit()


def delete(db: Session, c: HsTextCampaign) -> None:
    if c.status != "DRAFT":
        raise CampaignError("Only a campaign that never sent can be deleted — stop it instead", 409)
    rows = db.query(HsAuthRequest.id).filter(HsAuthRequest.campaign_id == c.id)
    db.query(HsAuthRun).filter(HsAuthRun.request_id.in_(rows.subquery())).delete(synchronize_session=False)
    db.query(HsAuthRequest).filter(HsAuthRequest.campaign_id == c.id).delete(synchronize_session=False)
    batch_id = c.batch_id
    db.delete(c)
    db.flush()
    if batch_id:
        db.query(HsAuthBatch).filter(HsAuthBatch.id == batch_id).delete(synchronize_session=False)
    db.commit()


# ---- Sending (the shared Telnyx sender) -------------------------------------------------

def _lock(tenant_id) -> bool:
    r = vc._redis()
    if r is None:
        return True
    try:
        return bool(r.set(f"hs:text:tick:{tenant_id}", "1", nx=True, px=15000))
    except Exception:
        return True


def _unlock(tenant_id) -> None:
    r = vc._redis()
    try:
        if r is not None:
            r.delete(f"hs:text:tick:{tenant_id}")
    except Exception:
        pass


def tick(db: Session, tenant_id) -> int:
    """Timers + the next batch of texts. Safe to call often (Celery beat, the
    page's poll, right after a reply). Returns texts sent."""
    if not _lock(tenant_id):
        return 0
    try:
        now = _now()
        _timers(db, tenant_id, now)
        return _send(db, tenant_id, now)
    except Exception:
        db.rollback()
        logger.exception("hs text campaigns: tick failed for tenant %s", tenant_id)
        return 0
    finally:
        _unlock(tenant_id)


def _cancel_queued(db: Session, row: HsAuthRequest, kinds: tuple = ("FOLLOWUP", "REMINDER", "BLAST")) -> None:
    db.query(HsCampaignText).filter(HsCampaignText.request_id == row.id, HsCampaignText.status == "QUEUED",
                                    HsCampaignText.kind.in_(kinds)).update({"status": "CANCELLED"}, synchronize_session=False)


def _timers(db: Session, tenant_id, now: datetime) -> None:
    # A send that never came back: failed, never re-sent (as the dialer's campaigns).
    db.query(HsCampaignText).filter(
        HsCampaignText.tenant_id == tenant_id, HsCampaignText.status == "SENDING",
        HsCampaignText.sent_at < now - STALE_SENDING,
    ).update({"status": "FAILED", "error": "No answer from the carrier — not retried"}, synchronize_session=False)

    camps = {c.id: c for c in db.query(HsTextCampaign).filter(HsTextCampaign.tenant_id == tenant_id).all()}
    if not camps:
        db.commit()
        return

    # 1) Follow-up sent, no answer within answer_wait_minutes -> "No answer yet".
    for r in db.query(HsAuthRequest).filter(HsAuthRequest.tenant_id == tenant_id,
                                            HsAuthRequest.convo == "FOLLOWUP_SENT",
                                            HsAuthRequest.deleted_at.is_(None)).all():
        c = camps.get(r.campaign_id)
        if c and r.followup_sent_at and r.followup_sent_at <= now - timedelta(minutes=c.answer_wait_minutes):
            r.convo = "NO_ANSWER"
            r.convo_detail = f"No answer in {c.answer_wait_minutes} min — a later answer still queues them."
            r.updated_at = now
            service._event(r, "no_answer", None, r.convo_detail)

    # 2) Link sent: one reminder, then expiry.
    for r in db.query(HsAuthRequest).filter(HsAuthRequest.tenant_id == tenant_id,
                                            HsAuthRequest.campaign_id.isnot(None),
                                            HsAuthRequest.status == "LINK_SENT",
                                            HsAuthRequest.deleted_at.is_(None)).all():
        c = camps.get(r.campaign_id)
        if not c or not r.link_sent_at:
            continue
        deadline = r.link_sent_at + timedelta(minutes=c.expire_minutes)
        if c.resend_on_expiry and (r.link_sends or 0) < 2:
            deadline += RESEND_GRACE  # the extension resends when HealthSherpa's link expires
        if now >= deadline:
            service._set_status(r, "EXPIRED", f"Not authorized within {c.expire_minutes} minutes — moved on.", "expired")
            _cancel_queued(db, r, ("REMINDER",))
            continue
        due = r.link_sent_at + timedelta(minutes=c.reminder_minutes)
        if (c.reminder_enabled and (c.reminder_text or "").strip() and r.reminder_sent_at is None
                and now >= due and c.status != "PAUSED"):
            already = db.query(HsCampaignText.id).filter(
                HsCampaignText.request_id == r.id, HsCampaignText.kind == "REMINDER",
                HsCampaignText.created_at >= (r.started_at or r.link_sent_at)).first()
            if already is None:
                db.add(HsCampaignText(
                    tenant_id=tenant_id, campaign_id=c.id, request_id=r.id, phone_number=e164(r.phone),
                    direction="OUT", kind="REMINDER", body=vc.render(c.reminder_text, r.first_name),
                    status="QUEUED", from_number=_last_from(db, r.id)))

    # 3) Blast done -> COMPLETED (conversations carry on).
    for c in camps.values():
        if c.status == "RUNNING" and db.query(HsAuthRequest.id).filter(
                HsAuthRequest.campaign_id == c.id, HsAuthRequest.convo == "QUEUED").first() is None:
            c.status = "COMPLETED"
            c.finished_at = now
    db.commit()


def _last_from(db: Session, request_id) -> Optional[str]:
    """The number this client already hears from (sticky sender)."""
    row = (db.query(HsCampaignText.from_number)
           .filter(HsCampaignText.request_id == request_id, HsCampaignText.direction == "OUT",
                   HsCampaignText.from_number.isnot(None))
           .order_by(HsCampaignText.created_at.desc()).first())
    return row[0] if row else None


def _send(db: Session, tenant_id, now: datetime) -> int:
    numbers = vc.senders()
    if not numbers:
        return 0
    budget = min(len(numbers), vc.daily_cap() - vc.sent_today(db, tenant_id))
    if budget <= 0:
        return 0
    s = vc.hours_settings(db, tenant_id)
    camps = {c.id: c for c in db.query(HsTextCampaign).filter(HsTextCampaign.tenant_id == tenant_id).all()}

    # Replies to a client first (follow-ups, then reminders), then the blast.
    queued = (db.query(HsCampaignText)
              .filter(HsCampaignText.tenant_id == tenant_id, HsCampaignText.status == "QUEUED",
                      HsCampaignText.direction == "OUT")
              .order_by((HsCampaignText.kind == "FOLLOWUP").desc(), HsCampaignText.created_at)
              .limit(CANDIDATES).all())
    running = sorted((c for c in camps.values() if c.status == "RUNNING"), key=lambda c: c.started_at or now)
    blast_rows: list[HsAuthRequest] = []
    for c in running:
        if len(blast_rows) >= CANDIDATES:
            break
        blast_rows += (db.query(HsAuthRequest)
                       .filter(HsAuthRequest.campaign_id == c.id, HsAuthRequest.convo == "QUEUED",
                               HsAuthRequest.deleted_at.is_(None))
                       .order_by(HsAuthRequest.row_number)
                       .limit(CANDIDATES - len(blast_rows)).all())
    if not queued and not blast_rows:
        return 0

    rows_by_id = {r.id: r for r in db.query(HsAuthRequest).filter(
        HsAuthRequest.id.in_([t.request_id for t in queued])).all()} if queued else {}
    phones = [t.phone_number for t in queued] + [e164(r.phone) for r in blast_rows]
    dnc = vc.dnc_digits(db, tenant_id, [p for p in phones if p])

    picks: list[HsCampaignText] = []
    used: set[str] = set()

    def take(num: str) -> bool:
        if num in used or not vc.claim_number(num):
            used.add(num)
            return False
        used.add(num)
        return True

    for t in queued:
        if len(picks) >= budget or len(used) == len(numbers):
            break
        row = rows_by_id.get(t.request_id)
        c = camps.get(t.campaign_id)
        if row is None or c is None or row.convo == "DNC":
            t.status = "CANCELLED"
            continue
        if vc.digits(t.phone_number) in dnc:
            t.status, t.error = "SKIPPED", "On Do-Not-Call by send time"
            continue
        if t.kind == "REMINDER":
            if row.status != "LINK_SENT":
                t.status = "CANCELLED"  # authorized / expired before it could go
                continue
            if c.status == "PAUSED":
                continue
            if not (replies.is_test_number(t.phone_number) or vc.in_hours(s, now, row.state)):
                continue
        # FOLLOWUP: any hour — it answers the text they just sent us.
        num = t.from_number if t.from_number in numbers else vc.sender_for(t.phone_number, numbers, _last_from(db, row.id))
        if not take(num):
            continue
        t.from_number, t.status, t.sent_at = num, "SENDING", now
        picks.append(t)

    for r in blast_rows:
        if len(picks) >= budget or len(used) == len(numbers):
            break
        phone = e164(r.phone)
        if vc.digits(phone) in dnc:
            r.convo, r.convo_detail = "SKIPPED", "On Do-Not-Call by send time"
            continue
        if not (replies.is_test_number(phone) or vc.in_hours(s, now, r.state)):
            continue  # outside their texting hours — stays queued
        num = vc.sender_for(phone, numbers, None)
        if not take(num):
            continue
        t = HsCampaignText(tenant_id=tenant_id, campaign_id=r.campaign_id, request_id=r.id, phone_number=phone,
                           direction="OUT", kind="BLAST", body=vc.render(camps[r.campaign_id].template, r.first_name),
                           from_number=num, status="SENDING", sent_at=now)
        db.add(t)
        r.convo, r.convo_detail = "BLAST_SENT", None  # marked now so a parallel tick can't pick it again
        picks.append(t)
    db.commit()

    hook = _status_url()
    sent = 0
    for t in picks:
        row = db.query(HsAuthRequest).filter(HsAuthRequest.id == t.request_id).first()
        try:
            t.provider_message_id = telnyx.send_sms(from_=t.from_number, to=t.phone_number, text=t.body, webhook_url=hook)
            t.status = "DELIVERED" if telnyx.simulating() else "SENT"
            if t.status == "DELIVERED":
                t.delivered_at = _now()
            sent += 1
            _after_sent(row, t)
        except telnyx.SendError as exc:
            _failed(db, t, row, exc.code, str(exc))
        db.commit()
    return sent


def _status_url() -> str:
    from app.voice_dialer.service import _url

    return _url("sms-status")


def _after_sent(row: Optional[HsAuthRequest], t: HsCampaignText) -> None:
    if row is None:
        return
    now = _now()
    row.last_text_at = now
    row.updated_at = now
    if t.kind == "BLAST":
        row.convo = "BLAST_SENT"
        service._event(row, "blast", None, "Campaign text sent")
    elif t.kind == "FOLLOWUP" and row.convo == "REPLIED":
        row.convo = "FOLLOWUP_SENT"
        row.followup_sent_at = now
        row.convo_detail = None
        service._event(row, "followup", None, "Approved follow-up sent — waiting for the company they're renewing")
    elif t.kind == "REMINDER":
        row.reminder_sent_at = now
        service._event(row, "reminder", None, "Reminder text sent")


def _failed(db: Session, t: HsCampaignText, row: Optional[HsAuthRequest], code: Optional[str], message: str) -> None:
    t.status = "FAILED"
    t.error = f"{code}: {message}" if code else message
    action, why = vc.failure_action(code, t.from_number)
    if action == "dnc":
        from app.sms_queue.services import queue_service as qs

        qs.add_to_dnc(db, str(t.tenant_id), t.phone_number, "OPTED_OUT")
        t.status = "SKIPPED"
        if row is not None:
            _to_dnc(db, row, "They texted STOP to us earlier (carrier refused the text)")
        return
    if row is not None and t.kind == "BLAST":
        row.convo, row.convo_detail = "BLAST_FAILED", t.error
    elif row is not None:
        row.convo_detail = f"{t.kind.title()} text failed: {t.error}"
    c = db.query(HsTextCampaign).filter(HsTextCampaign.id == t.campaign_id).first()
    if c is None or c.status != "RUNNING":
        return
    if action == "pause":
        c.status, c.pause_reason = "PAUSED", why
        return
    db.flush()
    last = [s for (s,) in db.query(HsCampaignText.status).filter(
        HsCampaignText.campaign_id == c.id, HsCampaignText.direction == "OUT", HsCampaignText.sent_at.isnot(None),
    ).order_by(HsCampaignText.sent_at.desc()).limit(vc.FAIL_STREAK).all()]
    if len(last) == vc.FAIL_STREAK and all(s == "FAILED" for s in last):
        c.status, c.pause_reason = "PAUSED", f"{vc.FAIL_STREAK} texts in a row failed — paused. Last error: {t.error}"


def on_status(db: Session, st: dict) -> bool:
    """A Telnyx delivery report (already parsed by telnyx.message_status).
    True when it was one of ours."""
    t = db.query(HsCampaignText).filter(HsCampaignText.provider_message_id == st["id"]).first()
    if t is None:
        return False
    if st["status"] == "delivered":
        t.status = "DELIVERED"
        t.delivered_at = t.delivered_at or _now()
    elif st["status"] == "failed" and t.status != "DELIVERED":
        row = db.query(HsAuthRequest).filter(HsAuthRequest.id == t.request_id).first()
        _failed(db, t, row, st.get("error_code"), st.get("error") or "Not delivered")
    db.commit()
    return True


# ---- Replies --------------------------------------------------------------------------

def owner_text(db: Session, tenant_id, phone: str) -> Optional[HsCampaignText]:
    """The HealthSherpa text this reply answers — or None when the number's
    NEWEST text from us was the dialer's (a dialer campaign / inbox text), or
    we never texted it in the last REPLY_WINDOW. Newest sender wins."""
    since = _now() - REPLY_WINDOW
    hs = (db.query(HsCampaignText)
          .filter(HsCampaignText.tenant_id == tenant_id, HsCampaignText.phone_number == phone,
                  HsCampaignText.direction == "OUT", HsCampaignText.sent_at >= since,
                  HsCampaignText.status.in_(("SENDING", "SENT", "DELIVERED")))
          .order_by(HsCampaignText.sent_at.desc()).first())
    if hs is None:
        return None
    dialer_campaign = db.query(func.max(TextCampaignRecipient.sent_at)).filter(
        TextCampaignRecipient.tenant_id == tenant_id, TextCampaignRecipient.phone_number == phone).scalar()
    dialer_single = db.query(func.max(SmsMessage.created_at)).filter(
        SmsMessage.tenant_id == tenant_id, SmsMessage.phone_number == phone,
        SmsMessage.direction == "OUTBOUND").scalar()
    newest_dialer = max([d for d in (dialer_campaign, dialer_single) if d is not None], default=None)
    if newest_dialer is not None and newest_dialer > hs.sent_at:
        return None
    return hs


def _to_dnc(db: Session, row: HsAuthRequest, why: str) -> None:
    now = _now()
    row.convo, row.convo_detail = "DNC", why
    row.updated_at = now
    _cancel_queued(db, row)
    if row.status in service.ACTIVE:
        service._set_status(row, "CANCELLED", "Client opted out by text — stopped.", "opted_out")
    elif row.status in ("PENDING", "TEXTING"):
        service._set_status(row, "CANCELLED", "Client opted out by text.", "opted_out")
    else:
        service._event(row, "opted_out", None, why)


def on_inbound(db: Session, tenant_id, phone: str, to_raw: Optional[str], text: Optional[str],
               message_id: Optional[str], kind: str) -> Optional[dict]:
    """A text to our numbers, BEFORE the dialer sees it (service.on_inbound_sms).
    None = not a HealthSherpa conversation (the dialer handles it as always).
    Otherwise it is handled here completely and the dialer never runs."""
    last = owner_text(db, tenant_id, phone)
    if last is None:
        return None
    if message_id and db.query(HsCampaignText.id).filter(HsCampaignText.provider_message_id == message_id,
                                                         HsCampaignText.direction == "IN").first():
        return {"ok": True, "result": "duplicate"}
    row = db.query(HsAuthRequest).filter(HsAuthRequest.id == last.request_id).first()
    c = db.query(HsTextCampaign).filter(HsTextCampaign.id == last.campaign_id).first()
    if row is None or c is None:
        return None
    now = _now()
    body = (text or "")[:1600]
    db.add(HsCampaignText(tenant_id=tenant_id, campaign_id=c.id, request_id=row.id, phone_number=phone,
                          direction="IN", kind="REPLY", body=body, status="RECEIVED", reply_kind=kind,
                          provider_message_id=message_id, sent_at=now))
    row.last_text_at = now
    row.updated_at = now

    from app.sms_queue.services import queue_service as qs

    send_now = False
    if kind in replies.NEGATIVE_KINDS:
        # Exactly the dialer's rule: every negative goes on Do-Not-Call forever.
        qs.add_to_dnc(db, tenant_id, phone, replies.DNC_REASON[kind])
        _to_dnc(db, row, f"Opted out by text: “{body[:120]}”")
        result = "hs_dnc"
    elif row.convo == "DNC" or qs.is_dnc(db, tenant_id, phone):
        service._event(row, "reply", None, f"Text saved (number is on Do-Not-Call): “{body[:200]}”")
        result = "hs_saved_dnc"
    elif row.convo in ("BLAST_SENT", "BLAST_FAILED"):
        row.yes_text, row.yes_at = body, now
        row.convo, row.convo_detail = "REPLIED", None
        to = replies.normalize_phone(to_raw) or last.from_number
        db.add(HsCampaignText(tenant_id=tenant_id, campaign_id=c.id, request_id=row.id, phone_number=phone,
                              direction="OUT", kind="FOLLOWUP", body=FOLLOWUP_TEXT, from_number=to, status="QUEUED"))
        service._event(row, "replied", None, f"Replied “{body[:200]}” — sending the follow-up")
        send_now = True
        result = "hs_followup"
    elif row.convo in ("FOLLOWUP_SENT", "NO_ANSWER"):
        late = row.convo == "NO_ANSWER"
        row.renewing_company, row.answered_at = body, now
        row.convo, row.convo_detail = "ANSWERED", None
        service._set_status(row, "PENDING", "Queued for HealthSherpa (auto mode).", "answered")
        service._event(row, "answered", None,
                       f"{'Late answer' if late else 'Answered'}: “{body[:200]}” — queued for HealthSherpa")
        result = "hs_answered"
    else:
        service._event(row, "reply", None, f"Text: “{body[:200]}”")
        result = "hs_saved"
    db.commit()
    if send_now:
        tick(db, tenant_id)  # the follow-up goes now, not on the next beat
    return {"ok": True, "result": result, "kind": kind, "hs_request_id": str(row.id)}


# ---- Auto mode (the agent's portal tab asks for the next job) ---------------------------

def consent_note(row: HsAuthRequest) -> str:
    return (f"Consent by text (no agent tick): replied “{(row.yes_text or '')[:200]}” at {_et(row.yes_at)}; "
            f"answered “{(row.renewing_company or '')[:200]}” at {_et(row.answered_at)}.")


def next_job(db: Session, tenant_id, user: User) -> dict:
    """Start the next answered client for THIS agent, or say why not. The
    server still enforces the daily limit and one client at a time."""
    tick(db, tenant_id)
    service.sweep_stale(db, tenant_id)
    # Two auto tabs for one agent must never both start a job.
    db.execute(sql_text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"hs-auto:{user.id}"})
    mine = (db.query(HsAuthRequest)
            .filter(HsAuthRequest.tenant_id == tenant_id, HsAuthRequest.started_by == user.id,
                    HsAuthRequest.status.in_(service.ACTIVE), HsAuthRequest.deleted_at.is_(None)).first())
    if mine:
        db.commit()
        return {"job": None, "reason": "running", "running_id": str(mine.id)}
    waiting = (db.query(HsAuthRequest, HsTextCampaign)
               .join(HsTextCampaign, HsTextCampaign.id == HsAuthRequest.campaign_id)
               .filter(HsAuthRequest.tenant_id == tenant_id, HsAuthRequest.status == "PENDING",
                       HsAuthRequest.convo == "ANSWERED", HsAuthRequest.deleted_at.is_(None),
                       HsTextCampaign.hs_agent_id == user.id)
               .order_by(HsAuthRequest.answered_at.asc()).limit(50).all())
    if not waiting:
        db.commit()
        return {"job": None, "reason": "idle", "waiting": 0}
    u = service.usage(db, tenant_id, user.id)
    if u["left"] <= 0:
        db.commit()
        return {"job": None, "reason": "limit", "waiting": len(waiting), "usage": u}
    s = vc.hours_settings(db, tenant_id)
    now = _now()
    pick = next(((r, c) for r, c in waiting
                 if replies.is_test_number(e164(r.phone)) or vc.in_hours(s, now, r.state)), None)
    if pick is None:
        db.commit()
        return {"job": None, "reason": "hours", "waiting": len(waiting)}
    r, c = pick
    out = service.start(db, tenant_id, user, str(r.id), auto=True,
                        resends=1 if c.resend_on_expiry else 0, consent=consent_note(r))
    out["waiting"] = len(waiting) - 1
    return out


def release(db: Session, tenant_id, user: User, request_id: str) -> dict:
    """The extension never took the job (not installed / busy): put the client
    back in the queue and give the daily slot back."""
    row = service.get_request(db, tenant_id, user, request_id)
    if row.campaign_id is None or row.status != "IN_PROGRESS" or str(row.started_by) != str(user.id) \
            or row.step != "start" or not row.started_at or row.started_at < _now() - timedelta(minutes=2):
        raise CampaignError("This job can't be put back.", 409)
    run = (db.query(HsAuthRun).filter(HsAuthRun.request_id == row.id, HsAuthRun.user_id == user.id)
           .order_by(HsAuthRun.created_at.desc()).first())
    if run is not None:
        db.delete(run)
    row.runs = max(0, (row.runs or 1) - 1)
    row.job_token_hash = None
    service._set_status(row, "PENDING", "The extension didn't take it — back in the queue.", "released")
    db.commit()
    return {"ok": True}


# ---- Read models ----------------------------------------------------------------------

def _stage_conditions():
    """One "stage" per client = text conversation + HealthSherpa status, for
    the table's filter chips and the export."""
    R = HsAuthRequest
    return {
        "QUEUED": ("Not texted yet", R.convo == "QUEUED"),
        "TEXTED": ("Texted — no reply", R.convo == "BLAST_SENT"),
        "ASKED": ("Asked company", R.convo.in_(("REPLIED", "FOLLOWUP_SENT"))),
        "NO_ANSWER": ("No answer yet", R.convo == "NO_ANSWER"),
        "HS_QUEUED": ("Queued for HealthSherpa", and_(R.convo == "ANSWERED", R.status == "PENDING")),
        "HS_RUNNING": ("Running in HealthSherpa", and_(R.convo == "ANSWERED", R.status == "IN_PROGRESS")),
        "LINK_SENT": ("Link sent", and_(R.convo == "ANSWERED", R.status == "LINK_SENT")),
        "AUTHORIZED": ("Authorized", R.status == "AUTHORIZED"),
        "EXPIRED": ("Expired", and_(R.convo == "ANSWERED", R.status == "EXPIRED")),
        "HS_REVIEW": ("Needs a look", and_(R.convo == "ANSWERED",
                                           R.status.in_(("NOT_FOUND", "NEEDS_REVIEW", "FAILED", "CANCELLED", "INVALID")))),
        "DNC": ("Opted out (DNC)", R.convo == "DNC"),
        "NOT_SENT": ("Not sent", R.convo.in_(("SKIPPED", "BLAST_FAILED", "CANCELLED"))),
        "INVALID": ("Fix data", R.convo == "INVALID"),
    }


def stage_of(r: HsAuthRequest) -> str:
    if r.status == "AUTHORIZED":
        return "AUTHORIZED"
    cv = r.convo
    if cv == "ANSWERED":
        return {"PENDING": "HS_QUEUED", "IN_PROGRESS": "HS_RUNNING", "LINK_SENT": "LINK_SENT",
                "EXPIRED": "EXPIRED"}.get(r.status, "HS_REVIEW")
    return {"QUEUED": "QUEUED", "BLAST_SENT": "TEXTED", "REPLIED": "ASKED", "FOLLOWUP_SENT": "ASKED",
            "NO_ANSWER": "NO_ANSWER", "DNC": "DNC", "INVALID": "INVALID"}.get(cv or "", "NOT_SENT")


def _counts(db: Session, ids: list) -> dict:
    R = HsAuthRequest
    out = {i: {} for i in ids}
    if not ids:
        return out
    cols = [
        func.count(R.id).label("total"),
        func.count(R.id).filter(R.convo == "QUEUED").label("queued"),
        func.count(R.id).filter(R.convo.notin_(NOT_TEXTED)).label("sent"),
        func.count(R.id).filter(R.yes_at.isnot(None)).label("positive"),
        func.count(R.id).filter(R.answered_at.isnot(None)).label("answered"),
        func.count(R.id).filter(R.convo == "NO_ANSWER").label("no_answer"),
        func.count(R.id).filter(R.link_sent_at.isnot(None)).label("link_sent"),
        func.count(R.id).filter(R.status == "AUTHORIZED").label("authorized"),
        func.count(R.id).filter(and_(R.convo == "ANSWERED", R.status == "EXPIRED")).label("expired"),
        func.count(R.id).filter(R.convo == "DNC").label("dnc"),
        func.count(R.id).filter(R.convo == "INVALID").label("invalid"),
        func.count(R.id).filter(R.convo.in_(("SKIPPED", "BLAST_FAILED"))).label("failed"),
    ]
    for row in db.query(R.campaign_id, *cols).filter(R.campaign_id.in_(ids), R.deleted_at.is_(None)).group_by(R.campaign_id):
        out[row[0]] = {k: int(v) for k, v in row._asdict().items() if k != "campaign_id"}
    replied = dict(db.query(HsCampaignText.campaign_id, func.count(func.distinct(HsCampaignText.request_id)))
                   .filter(HsCampaignText.campaign_id.in_(ids), HsCampaignText.direction == "IN")
                   .group_by(HsCampaignText.campaign_id).all())
    for i in ids:
        out[i]["replied"] = int(replied.get(i, 0))
    return out


def campaign_dict(c: HsTextCampaign, counts: dict, names: dict) -> dict:
    return {
        "id": str(c.id), "name": c.name, "template": c.template, "status": c.status,
        "pause_reason": c.pause_reason, "file_name": c.file_name,
        "hs_agent_id": str(c.hs_agent_id), "hs_agent_name": names.get(c.hs_agent_id),
        "rows_in_file": c.rows_in_file, "skipped_invalid": c.skipped_invalid,
        "skipped_no_phone": c.skipped_no_phone, "skipped_duplicate": c.skipped_duplicate,
        "skipped_dnc": c.skipped_dnc,
        "answer_wait_minutes": c.answer_wait_minutes, "reminder_enabled": c.reminder_enabled,
        "reminder_text": c.reminder_text, "reminder_minutes": c.reminder_minutes,
        "expire_minutes": c.expire_minutes, "resend_on_expiry": c.resend_on_expiry,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "started_at": c.started_at.isoformat() if c.started_at else None,
        "finished_at": c.finished_at.isoformat() if c.finished_at else None,
        "counts": counts or {},
        "preview": vc.render(c.template, "Jane"),
    }


def overview(db: Session, tenant_id, user: User) -> dict:
    camps = (db.query(HsTextCampaign).filter(HsTextCampaign.tenant_id == tenant_id)
             .order_by(HsTextCampaign.created_at.desc()).limit(100).all())
    counts = _counts(db, [c.id for c in camps])
    names = service._names(db, {c.hs_agent_id for c in camps})
    mine_waiting = (db.query(func.count(HsAuthRequest.id))
                    .join(HsTextCampaign, HsTextCampaign.id == HsAuthRequest.campaign_id)
                    .filter(HsAuthRequest.tenant_id == tenant_id, HsAuthRequest.status == "PENDING",
                            HsAuthRequest.convo == "ANSWERED", HsAuthRequest.deleted_at.is_(None),
                            HsTextCampaign.hs_agent_id == user.id).scalar()) or 0
    return {
        "campaigns": [campaign_dict(c, counts.get(c.id), names) for c in camps],
        "can_manage": can_manage(user),
        "is_hs_agent": any(str(c.hs_agent_id) == str(user.id) for c in camps),
        "my_queue": int(mine_waiting),
        "usage": service.usage(db, tenant_id, user.id),
        "followup_text": FOLLOWUP_TEXT,
        "blast_default": BLAST_DEFAULT,
        "me": str(user.id),
        "reminder_draft": REMINDER_DRAFT,
        "stages": {k: v[0] for k, v in _stage_conditions().items()},
        "sent_today": vc.sent_today(db, tenant_id),
        "daily_cap": vc.daily_cap(),
        "simulating": telnyx.simulating(),
    }


def _client_query(db: Session, tenant_id, *, campaign: Optional[str], stage: Optional[str], q: Optional[str]):
    R = HsAuthRequest
    base = db.query(R).filter(R.tenant_id == tenant_id, R.campaign_id.isnot(None), R.deleted_at.is_(None))
    if campaign:
        base = base.filter(R.campaign_id == campaign)
    if q and q.strip():
        like = f"%{q.strip()}%"
        digits = re.sub(r"\D", "", q)
        conds = [R.first_name.ilike(like), R.last_name.ilike(like), (R.first_name + " " + R.last_name).ilike(like),
                 R.hs_app_id.ilike(like), R.renewing_company.ilike(like)]
        if len(digits) >= 3:
            conds.append(R.phone.ilike(f"%{digits}%"))
        base = base.filter(or_(*conds))
    conds = _stage_conditions()
    rows_q = base.filter(conds[stage][1]) if stage and stage in conds else base
    return base, rows_q


def _stage_counts(db: Session, base) -> dict:
    cols = [func.count(HsAuthRequest.id).filter(cond).label(key) for key, (_l, cond) in _stage_conditions().items()]
    row = base.with_entities(func.count(HsAuthRequest.id).label("ALL"), *cols).one()
    return {k: int(v or 0) for k, v in row._asdict().items()}


def client_dict(r: HsAuthRequest, camps: dict, names: dict, *, last_texts: dict | None = None) -> dict:
    d = service.serialize(r, names)
    c = camps.get(r.campaign_id)
    d.update({
        "campaign_id": str(r.campaign_id) if r.campaign_id else None,
        "campaign_name": c.name if c else None,
        "convo": r.convo, "convo_detail": r.convo_detail, "stage": stage_of(r),
        "yes_text": r.yes_text, "yes_at": r.yes_at.isoformat() if r.yes_at else None,
        "followup_sent_at": r.followup_sent_at.isoformat() if r.followup_sent_at else None,
        "renewing_company": r.renewing_company,
        "answered_at": r.answered_at.isoformat() if r.answered_at else None,
        "reminder_sent_at": r.reminder_sent_at.isoformat() if r.reminder_sent_at else None,
        "last_text_at": r.last_text_at.isoformat() if r.last_text_at else None,
    })
    if last_texts is not None:
        t = last_texts.get(r.id)
        d["last_text"] = ({"direction": t.direction, "kind": t.kind, "body": t.body,
                           "at": (t.sent_at or t.created_at).isoformat()} if t else None)
    return d


def clients(db: Session, tenant_id, *, campaign: Optional[str], stage: Optional[str], q: Optional[str],
            limit: int, offset: int) -> dict:
    base, rows_q = _client_query(db, tenant_id, campaign=campaign, stage=stage, q=q)
    total = rows_q.count()
    rows = (rows_q.order_by(func.coalesce(HsAuthRequest.last_text_at, HsAuthRequest.created_at).desc(),
                            HsAuthRequest.row_number).limit(limit).offset(offset).all())
    camps = {c.id: c for c in db.query(HsTextCampaign).filter(HsTextCampaign.id.in_({r.campaign_id for r in rows})).all()} if rows else {}
    names = service._names(db, {r.assigned_to for r in rows} | {r.started_by for r in rows})
    last = {}
    if rows:
        sub = (db.query(HsCampaignText.request_id, func.max(HsCampaignText.created_at).label("m"))
               .filter(HsCampaignText.request_id.in_([r.id for r in rows]),
                       HsCampaignText.status.notin_(("QUEUED", "CANCELLED")))
               .group_by(HsCampaignText.request_id).subquery())
        for t in db.query(HsCampaignText).join(sub, and_(HsCampaignText.request_id == sub.c.request_id,
                                                          HsCampaignText.created_at == sub.c.m)).all():
            last[t.request_id] = t
    return {
        "rows": [client_dict(r, camps, names, last_texts=last) for r in rows],
        "total": total,
        "counts": _stage_counts(db, base),
    }


def text_dict(t: HsCampaignText) -> dict:
    return {
        "id": str(t.id), "direction": t.direction, "kind": t.kind, "body": t.body, "status": t.status,
        "reply_kind": t.reply_kind, "from_number": t.from_number, "error": t.error,
        "at": (t.sent_at or t.created_at).isoformat() if (t.sent_at or t.created_at) else None,
        "delivered_at": t.delivered_at.isoformat() if t.delivered_at else None,
    }


def client_detail(db: Session, tenant_id, request_id: str) -> dict:
    try:
        rid = uuid.UUID(str(request_id))
    except (ValueError, TypeError):
        raise CampaignError("Client not found", 404)
    r = db.query(HsAuthRequest).filter(HsAuthRequest.id == rid, HsAuthRequest.tenant_id == tenant_id,
                                       HsAuthRequest.campaign_id.isnot(None)).first()
    if r is None:
        raise CampaignError("Client not found", 404)
    camps = {c.id: c for c in db.query(HsTextCampaign).filter(HsTextCampaign.id == r.campaign_id).all()}
    names = service._names(db, {r.assigned_to, r.started_by})
    d = client_dict(r, camps, names)
    d["events"] = r.events or []
    d["texts"] = [text_dict(t) for t in db.query(HsCampaignText).filter(HsCampaignText.request_id == r.id)
                  .order_by(func.coalesce(HsCampaignText.sent_at, HsCampaignText.created_at)).all()]
    return d


EXPORT_COLUMNS = [
    "First name", "Last name", "Phone", "DOB", "State", "Campaign", "Stage", "Text detail",
    "Their reply", "Replied at (ET)", "Company they're renewing", "Answered at (ET)",
    "HealthSherpa status", "HealthSherpa detail", "App ID", "Link sent at (ET)", "Reminder sent at (ET)",
    "Authorized at (ET)", "Agent", "All texts",
]
EXPORT_MAX = 20_000


def export_csv(db: Session, tenant_id, *, campaign: Optional[str], stage: Optional[str], q: Optional[str]) -> str:
    """Everything the closer needs, one row per client. UTF-8 with a BOM so
    Excel opens it with the right characters."""
    _base, rows_q = _client_query(db, tenant_id, campaign=campaign, stage=stage, q=q)
    rows = rows_q.order_by(HsAuthRequest.campaign_id, HsAuthRequest.row_number).limit(EXPORT_MAX).all()
    camps = {c.id: c for c in db.query(HsTextCampaign).filter(HsTextCampaign.tenant_id == tenant_id).all()}
    names = service._names(db, {r.assigned_to for r in rows})
    labels = {k: v[0] for k, v in _stage_conditions().items()}
    texts: dict = {}
    ids = [r.id for r in rows]
    for i in range(0, len(ids), 1000):
        for t in (db.query(HsCampaignText).filter(HsCampaignText.request_id.in_(ids[i:i + 1000]),
                                                  HsCampaignText.status.notin_(("QUEUED", "CANCELLED")))
                  .order_by(func.coalesce(HsCampaignText.sent_at, HsCampaignText.created_at))):
            who = "Client" if t.direction == "IN" else {"BLAST": "Campaign", "FOLLOWUP": "Follow-up",
                                                         "REMINDER": "Reminder"}.get(t.kind, "Us")
            texts.setdefault(t.request_id, []).append(f"[{_et(t.sent_at or t.created_at)}] {who}: {t.body or ''}")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EXPORT_COLUMNS)
    for r in rows:
        c = camps.get(r.campaign_id)
        st = service.STATUS_LABELS.get(r.status, r.status) if r.convo == "ANSWERED" or r.status == "AUTHORIZED" else ""
        w.writerow([
            r.first_name, r.last_name,
            f"({r.phone[:3]}) {r.phone[3:6]}-{r.phone[6:]}" if r.phone and len(r.phone) == 10 else (r.phone or ""),
            r.dob.strftime("%m/%d/%Y") if r.dob else "", r.state or "", c.name if c else "",
            labels.get(stage_of(r), ""), r.convo_detail or "",
            r.yes_text or "", _et(r.yes_at), r.renewing_company or "", _et(r.answered_at),
            st, (r.status_detail or "") if st else "", r.hs_app_id or "", _et(r.link_sent_at),
            _et(r.reminder_sent_at), _et(r.authorized_at), names.get(r.assigned_to, "") if r.assigned_to else "",
            "\n".join(texts.get(r.id, [])),
        ])
    return "﻿" + buf.getvalue()


# ---- Simulator (local testing only) ------------------------------------------------------

def sim_age(db: Session, tenant_id, request_id: str, minutes: float) -> dict:
    """Move this client's conversation clocks back by `minutes`, so the 10-min
    answer wait / 7-min reminder / 15-min expiry can be tested in seconds.
    Only while the Telnyx simulator is on — never in production."""
    if not telnyx.simulating():
        raise CampaignError("simulator is off", 404)
    r = db.query(HsAuthRequest).filter(HsAuthRequest.id == request_id, HsAuthRequest.tenant_id == tenant_id).first()
    if r is None:
        raise CampaignError("Client not found", 404)
    d = timedelta(minutes=float(minutes))
    for col in ("yes_at", "followup_sent_at", "link_sent_at", "answered_at"):
        v = getattr(r, col)
        if v is not None:
            setattr(r, col, v - d)
    db.commit()
    tick(db, tenant_id)
    return {"ok": True}
