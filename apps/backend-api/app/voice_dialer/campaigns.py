"""Text campaigns — Auto Dialer page: upload a CSV, write ONE message, and the
portal texts everyone through Telnyx, in paced batches, until the list is done
or an admin pauses it.

Built from scratch and kept apart from the SMS Manager's (Sinch) campaigns:
its own tables (text_campaigns / text_campaign_recipients), its own sender.

Replies need nothing new: they arrive at /voice/telnyx/inbound-sms like every
text to our numbers, so service.on_inbound_sms sorts them exactly as before —
positive -> dial list, negative -> Do-Not-Call. on_reply() only adds the
campaign's side: who replied, and the CSV row (name + details) on the lead.

Keeping the numbers from being blocked (carrier limits read from our 10DLC
registration — AT&T 240 texts/min per campaign, T-Mobile 2,000/day for the
brand, tier LOW):
  * each sending number sends at most one text every SEND_GAP_SECONDS;
  * at most settings.TEXT_DAILY_CAP texts per Eastern day, all campaigns together;
  * only 8 am – 8 pm in the CLIENT's zone (their CSV state; unknown = the
    strict 11 am – 8 pm Eastern, as for calls);
  * Do-Not-Call is checked at upload AND again right before each text;
  * every text carries "Reply STOP to opt out" (added if the message lacks it);
  * a client always gets texts from the same one of our numbers;
  * spam blocks or an unregistered number pause the campaign at once, and
    FAIL_STREAK texts failing in a row pause it too — a person looks first.
One text per person per campaign, no follow-ups (as the SMS Manager's
first-template-only rule). The same number in a later campaign IS texted again.

The safety pieces above (senders / sender_for / claim_number / in_hours /
dnc_digits / sent_today / daily_cap / failure_action) are SHARED with the
HealthSherpa text campaigns (app/hs_auth/campaigns.py) — one sender, one daily
cap for the whole brand. Change them here, for both.
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.sms import SmsDoNotCall, SmsLead, SmsMessage, TextCampaign, TextCampaignRecipient
from app.voice_dialer import replies, rules, telnyx

logger = logging.getLogger(__name__)

MAX_ROWS = 50_000
MAX_TEMPLATE = 480                 # three SMS segments
SEND_GAP_SECONDS = 4               # per number: ≤15/min each, 75/min for five (AT&T allows 240)
CANDIDATES = 400                   # rows looked at per tick (texting hours may skip some)
STALE_SENDING = timedelta(minutes=5)
REPLY_WINDOW = timedelta(days=30)  # a reply this soon after our text counts for the campaign
FAIL_STREAK = 10
STOP_LINE = "Reply STOP to opt out."
SPAM_CODES = {"40002", "40003"}    # carrier flagged us as spam
UNREGISTERED_CODES = {"40010"}     # number not on an approved 10DLC campaign
OPTED_OUT_CODES = {"40300"}        # they texted STOP to us earlier
EDITABLE = ("DRAFT", "PAUSED")
ET = ZoneInfo("America/New_York")


class CampaignError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- The message ------------------------------------------------------------------

_NAME = re.compile(r"\{\s*(first_?name|name)\s*\}", re.IGNORECASE)


def render(template: str, first_name: str | None) -> str:
    """{name} -> their first name ("there" when the file has none), and the
    opt-out line if the message doesn't mention STOP."""
    name = (first_name or "").strip().split(" ")[0].title() or "there"
    text = _NAME.sub(name, template or "").strip()
    if "STOP" not in text.upper():
        text = f"{text} {STOP_LINE}"
    return text


def check_template(template: str | None) -> str:
    t = (template or "").strip()
    if not t:
        raise CampaignError("Write the message first")
    if len(t) > MAX_TEMPLATE:
        raise CampaignError(f"Keep the message under {MAX_TEMPLATE} characters")
    return t


# ---- The file ---------------------------------------------------------------------

def _find(header: list[str], *keys: str, avoid: tuple[str, ...] = ()) -> int | None:
    for i, h in enumerate(header):
        if any(k in h for k in keys) and not any(a in h for a in avoid):
            return i
    return None


def parse_csv(raw: bytes) -> tuple[list[dict], dict]:
    """CSV -> recipients [{phone, first_name, state, fields}] + what was dropped.
    Finds the phone / first name / state columns by their headers; with no
    recognisable header, the column that holds phone numbers."""
    text = raw.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if not rows:
        raise CampaignError("The file is empty")
    head = [h.strip().lower() for h in rows[0]]
    phone_i = _find(head, "phone", "mobile", "cell", "tel", "number",
                    avoid=("policy", "member", "account", "zip", "ssn", "id number", "case"))
    has_header = phone_i is not None or not any(replies.normalize_phone(c) for c in rows[0])
    if phone_i is None:
        # No phone header: the column where phone numbers actually are.
        sample = rows[1:51] if has_header else rows[:50]
        best = max(range(len(rows[0])), key=lambda i: sum(1 for r in sample if i < len(r) and replies.normalize_phone(r[i])))
        if not any(best < len(r) and replies.normalize_phone(r[best]) for r in sample):
            raise CampaignError("No phone number column found")
        phone_i = best
    first_i = _find(head, "first") if has_header else None
    name_i = None
    if has_header and first_i is None:
        name_i = _find(head, "name", avoid=("last", "company", "agent", "user", "file", "business"))
    state_i = None
    if has_header:
        state_i = next((i for i, h in enumerate(head) if h in ("state", "st", "state code", "state_code")), None)
        if state_i is None:
            state_i = _find(head, "state", avoid=("statement", "estate"))
    labels = [h.strip() or f"Column {i + 1}" for i, h in enumerate(rows[0])] if has_header else \
        [f"Column {i + 1}" for i in range(len(rows[0]))]
    body = rows[1:] if has_header else rows
    if len(body) > MAX_ROWS:
        raise CampaignError(f"Too many rows — at most {MAX_ROWS:,} per campaign")

    out: list[dict] = []
    seen: set[str] = set()
    invalid = dupes = 0
    for r in body:
        cell = lambda i: (r[i].strip() if i is not None and i < len(r) else "")  # noqa: E731
        phone = replies.normalize_phone(cell(phone_i))
        if not phone or not phone.startswith("+1"):
            invalid += 1
            continue
        if phone in seen:
            dupes += 1
            continue
        seen.add(phone)
        first = cell(first_i) or cell(name_i).split(" ")[0]
        out.append({
            "phone": phone,
            "first_name": first[:120] or None,
            "state": rules.state_code(cell(state_i)),
            "fields": [[labels[i] if i < len(labels) else f"Column {i + 1}", v.strip()]
                       for i, v in enumerate(r) if v.strip()],
        })
    return out, {"rows": len(body), "invalid": invalid, "duplicate": dupes}


def dnc_digits(db: Session, tenant_id, phones: list[str]) -> set[str]:
    """Which of these numbers are on Do-Not-Call (digits-only keys, as the list stores them)."""
    digits = {"".join(c for c in p if c.isdigit()) for p in phones}
    out: set[str] = set()
    chunk = list(digits)
    for i in range(0, len(chunk), 1000):
        out |= {d for (d,) in db.query(SmsDoNotCall.phone_number).filter(
            SmsDoNotCall.tenant_id == tenant_id, SmsDoNotCall.phone_number.in_(chunk[i:i + 1000])).all()}
    return out


def digits(phone: str) -> str:
    return "".join(c for c in phone if c.isdigit())


_dnc_digits = dnc_digits  # old private names, kept for callers
_digits = digits


# ---- Campaign lifecycle -------------------------------------------------------------

def create(db: Session, tenant_id, user, name: str, template: str, file_name: str | None,
           raw: bytes) -> TextCampaign:
    name = (name or "").strip()[:120] or (file_name or "Campaign")
    template = check_template(template)
    people, stats = parse_csv(raw)
    dnc = _dnc_digits(db, tenant_id, [p["phone"] for p in people])
    keep = [p for p in people if _digits(p["phone"]) not in dnc]
    if not keep:
        raise CampaignError("Nobody left to text — every number was invalid, a duplicate or on Do-Not-Call")
    c = TextCampaign(
        tenant_id=tenant_id, name=name, template=template, status="DRAFT", file_name=(file_name or "")[:255],
        rows_in_file=stats["rows"], skipped_invalid=stats["invalid"], skipped_duplicate=stats["duplicate"],
        skipped_dnc=len(people) - len(keep), created_by=getattr(user, "id", None),
    )
    db.add(c)
    db.flush()
    db.bulk_insert_mappings(TextCampaignRecipient, [
        {"id": uuid.uuid4(), "campaign_id": c.id, "tenant_id": tenant_id, "position": i,
         "phone_number": p["phone"], "first_name": p["first_name"], "state": p["state"],
         "fields": p["fields"], "status": "QUEUED"}
        for i, p in enumerate(keep)
    ])
    db.commit()
    return c


def find(db: Session, tenant_id, campaign_id) -> TextCampaign:
    try:
        cid = uuid.UUID(str(campaign_id))
    except (ValueError, TypeError):
        cid = None
    c = db.query(TextCampaign).filter(TextCampaign.id == cid, TextCampaign.tenant_id == tenant_id).first() if cid else None
    if c is None:
        raise CampaignError("Campaign not found", 404)
    return c


def update(db: Session, c: TextCampaign, name: str | None, template: str | None) -> None:
    if c.status not in EDITABLE:
        raise CampaignError("Pause the campaign to change it", 409)
    if name is not None and name.strip():
        c.name = name.strip()[:120]
    if template is not None:
        c.template = check_template(template)
    db.commit()


def start(db: Session, c: TextCampaign) -> None:
    if c.status not in EDITABLE:
        raise CampaignError("This campaign can't be started", 409)
    if not telnyx.simulating():
        if not telnyx.available():
            raise CampaignError("Texting is not set up (Telnyx is not configured)", 503)
        if not senders():
            raise CampaignError("No sending numbers — set TELNYX_FROM_NUMBERS", 503)
    c.status = "RUNNING"
    c.pause_reason = None
    c.started_at = c.started_at or _now()
    db.commit()


def pause(db: Session, c: TextCampaign, reason: str | None = None) -> None:
    if c.status != "RUNNING":
        raise CampaignError("Only a running campaign can be paused", 409)
    c.status = "PAUSED"
    c.pause_reason = reason
    db.commit()


def stop(db: Session, c: TextCampaign) -> None:
    """End it for good: nobody still waiting is texted."""
    if c.status in ("COMPLETED", "STOPPED"):
        return
    db.query(TextCampaignRecipient).filter(
        TextCampaignRecipient.campaign_id == c.id, TextCampaignRecipient.status == "QUEUED",
    ).update({"status": "CANCELLED"}, synchronize_session=False)
    c.status = "STOPPED"
    c.finished_at = _now()
    db.commit()


def delete(db: Session, c: TextCampaign) -> None:
    if c.status != "DRAFT":
        raise CampaignError("Only a campaign that never sent can be deleted — stop it instead", 409)
    db.delete(c)
    db.commit()


# ---- Sending ------------------------------------------------------------------------

def senders() -> list[str]:
    from app.voice_dialer.service import fleet_numbers

    nums = fleet_numbers()
    if not nums and telnyx.simulating():
        return ["+15550000000"]
    return nums


def sender_for(phone: str, numbers: list[str], known: str | None = None) -> str:
    """The same client always hears from the same number: the one they already
    know (their lead's dial_from), else a stable pick by their phone."""
    if known and known in numbers:
        return known
    h = int(hashlib.sha1(_digits(phone)[-10:].encode()).hexdigest(), 16)
    return numbers[h % len(numbers)]


def day_start(now: datetime | None = None) -> datetime:
    local = (now or _now()).astimezone(ET)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def sent_today(db: Session, tenant_id) -> int:
    """Texts that left us today: campaign texts + one-to-one texts from the
    inbox + HealthSherpa campaign texts (the carrier's daily cap counts every
    text from the brand)."""
    from app.models.hs_auth import HsCampaignText

    start = day_start()
    campaign = db.query(func.count(TextCampaignRecipient.id)).filter(
        TextCampaignRecipient.tenant_id == tenant_id, TextCampaignRecipient.sent_at >= start,
    ).scalar() or 0
    single = db.query(func.count(SmsMessage.id)).filter(
        SmsMessage.tenant_id == tenant_id, SmsMessage.provider == telnyx.SLUG,
        SmsMessage.direction == "OUTBOUND", SmsMessage.created_at >= start,
    ).scalar() or 0
    hs = db.query(func.count(HsCampaignText.id)).filter(
        HsCampaignText.tenant_id == tenant_id, HsCampaignText.direction == "OUT",
        HsCampaignText.sent_at >= start,
    ).scalar() or 0
    return int(campaign) + int(single) + int(hs)


def daily_cap() -> int:
    return max(0, int(settings.TEXT_DAILY_CAP or 0))


def _redis():
    try:
        from app.core.redis import redis_service

        return redis_service.client
    except Exception:
        return None


def _lock(tenant_id) -> bool:
    r = _redis()
    if r is None:
        return True
    try:
        return bool(r.set(f"text:tick:{tenant_id}", "1", nx=True, px=15000))
    except Exception:
        return True


def _unlock(tenant_id) -> None:
    r = _redis()
    try:
        if r is not None:
            r.delete(f"text:tick:{tenant_id}")
    except Exception:
        pass


def claim_number(number: str) -> bool:
    """One text per number per SEND_GAP_SECONDS, however many ticks run."""
    r = _redis()
    if r is None:
        return True  # no Redis: the tick's own one-per-number rule still holds
    try:
        return bool(r.set(f"text:num:{number}", "1", nx=True, px=SEND_GAP_SECONDS * 1000))
    except Exception:
        return True


_claim_number = claim_number


def tick(db: Session, tenant_id) -> int:
    """Send the next batch (at most one text per sending number). Safe to call
    often — from Celery beat, and from the page's own refresh. Returns texts sent."""
    if not _lock(tenant_id):
        return 0
    try:
        return _tick(db, tenant_id)
    except Exception:
        db.rollback()
        logger.exception("text campaigns: tick failed for tenant %s", tenant_id)
        return 0
    finally:
        _unlock(tenant_id)


def _tick(db: Session, tenant_id) -> int:
    now = _now()
    # A send that never came back (crash mid-request): failed, never re-sent —
    # a second copy of the same text is worse than a missing one.
    db.query(TextCampaignRecipient).filter(
        TextCampaignRecipient.tenant_id == tenant_id, TextCampaignRecipient.status == "SENDING",
        TextCampaignRecipient.sent_at < now - STALE_SENDING,
    ).update({"status": "FAILED", "error": "No answer from the carrier — not retried"}, synchronize_session=False)
    db.commit()

    running = (
        db.query(TextCampaign)
        .filter(TextCampaign.tenant_id == tenant_id, TextCampaign.status == "RUNNING")
        .order_by(TextCampaign.started_at.asc())
        .all()
    )
    if not running:
        return 0
    numbers = senders()
    if not numbers:
        return 0
    budget = min(len(numbers), daily_cap() - sent_today(db, tenant_id))
    if budget <= 0:
        return 0

    s = hours_settings(db, tenant_id)
    candidates: list[TextCampaignRecipient] = []
    for c in running:  # oldest campaign first, file order within it
        if len(candidates) >= CANDIDATES:
            break
        candidates += (
            db.query(TextCampaignRecipient)
            .filter(TextCampaignRecipient.campaign_id == c.id, TextCampaignRecipient.status == "QUEUED")
            .order_by(TextCampaignRecipient.position)
            .limit(CANDIDATES - len(candidates))
            .all()
        )
    if not candidates:
        _finish_done(db, running)
        return 0
    known = dict(
        db.query(SmsLead.phone_number, SmsLead.dial_from)
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.phone_number.in_([r.phone_number for r in candidates]),
                SmsLead.dial_from.isnot(None))
        .all()
    )
    dnc = _dnc_digits(db, tenant_id, [r.phone_number for r in candidates])
    campaigns = {c.id: c for c in running}

    picks: list[tuple[TextCampaignRecipient, str]] = []
    used: set[str] = set()
    for r in candidates:
        if len(picks) >= budget or len(used) == len(numbers):
            break
        if _digits(r.phone_number) in dnc:
            r.status = "SKIPPED"
            r.error = "On Do-Not-Call by send time"
            continue
        if not (replies.is_test_number(r.phone_number) or _in_hours(s, now, r.state)):
            continue  # outside their texting hours — they stay queued
        num = sender_for(r.phone_number, numbers, known.get(r.phone_number))
        if num in used or not _claim_number(num):
            used.add(num)
            continue
        used.add(num)
        r.status = "SENDING"
        r.from_number = num
        r.body = render(campaigns[r.campaign_id].template, r.first_name)
        r.sent_at = now
        picks.append((r, num))
    db.commit()

    from app.voice_dialer.service import _url

    hook = _url("sms-status")
    sent = 0
    for r, num in picks:
        try:
            r.provider_message_id = telnyx.send_sms(from_=num, to=r.phone_number, text=r.body, webhook_url=hook)
            r.status = "DELIVERED" if telnyx.simulating() else "SENT"
            if r.status == "DELIVERED":
                r.delivered_at = _now()
            sent += 1
        except telnyx.SendError as exc:
            _failed(db, r, exc.code, str(exc))
        db.commit()
    _finish_done(db, running)
    return sent


def in_hours(s, now: datetime, state: str | None) -> bool:
    """The dialer's own calling window, in the client's zone (Dialing rules).
    s=None: the simulator is ignoring hours."""
    if s is None:
        return True
    return rules.in_call_window(now, state, int(s.window_start_hour or 8), int(s.window_end_hour or 20))


_in_hours = in_hours


def hours_settings(db: Session, tenant_id):
    """The dialer's settings row for in_hours(), or None while the simulator's
    "Ignore calling hours while testing" is on."""
    from app.voice_dialer.service import get_settings

    s = get_settings(db, tenant_id)
    if telnyx.simulating():
        from app.voice_dialer.service import _sim_ignore_hours

        if _sim_ignore_hours(tenant_id):
            return None
    return s


def failure_action(code: str | None, from_number: str | None) -> tuple[str | None, str | None]:
    """What a failed text means for the campaign: ("dnc", None) they texted
    STOP earlier; ("pause", why) our numbers are at risk; (None, None) a plain
    failure (the caller still watches for FAIL_STREAK)."""
    if code in OPTED_OUT_CODES:
        return "dnc", None
    if code in SPAM_CODES:
        return "pause", "Carriers flagged texts as spam — paused to protect our numbers. Check the message before resuming."
    if code in UNREGISTERED_CODES:
        return "pause", f"{from_number} is not approved for texting (10DLC) — paused."
    return None, None


def _failed(db: Session, r: TextCampaignRecipient, code: str | None, message: str) -> None:
    """Record a failed text; pause the campaign when the failure says our
    numbers are at risk (the caller commits)."""
    r.status = "FAILED"
    r.error = f"{code}: {message}" if code else message
    c = db.query(TextCampaign).filter(TextCampaign.id == r.campaign_id).first()
    action, why = failure_action(code, r.from_number)
    if action == "dnc":
        from app.sms_queue.services import queue_service as qs

        qs.add_to_dnc(db, str(r.tenant_id), r.phone_number, "OPTED_OUT")
        r.status = "SKIPPED"
        return
    if c is None or c.status != "RUNNING":
        return
    if action == "pause":
        c.status, c.pause_reason = "PAUSED", why
    else:
        # The latest FAIL_STREAK texts, this one included (autoflush writes it first).
        last = [s for (s,) in db.query(TextCampaignRecipient.status).filter(
            TextCampaignRecipient.campaign_id == c.id, TextCampaignRecipient.sent_at.isnot(None),
        ).order_by(TextCampaignRecipient.sent_at.desc()).limit(FAIL_STREAK).all()]
        if len(last) == FAIL_STREAK and all(s == "FAILED" for s in last):
            c.status, c.pause_reason = "PAUSED", f"{FAIL_STREAK} texts in a row failed — paused. Last error: {r.error}"


def _finish_done(db: Session, running: list[TextCampaign]) -> None:
    for c in running:
        left = db.query(TextCampaignRecipient.id).filter(
            TextCampaignRecipient.campaign_id == c.id,
            TextCampaignRecipient.status.in_(("QUEUED", "SENDING")),
        ).first()
        if left is None and c.status == "RUNNING":
            c.status = "COMPLETED"
            c.finished_at = _now()
    db.commit()


# ---- Carrier reports and replies --------------------------------------------------------

def on_status(db: Session, data: dict) -> dict:
    """Telnyx delivery report (message.sent / message.finalized)."""
    st = telnyx.message_status(data)
    if not st:
        return {"ok": True, "ignored": True}
    r = db.query(TextCampaignRecipient).filter(TextCampaignRecipient.provider_message_id == st["id"]).first()
    if r is None:
        # A one-to-one text from the inbox?
        m = db.query(SmsMessage).filter(SmsMessage.provider_message_id == st["id"],
                                        SmsMessage.direction == "OUTBOUND").first()
        if m is None:
            # A HealthSherpa campaign text? (its own tables, its own bookkeeping)
            from app.hs_auth import campaigns as hs_campaigns

            if hs_campaigns.on_status(db, st):
                return {"ok": True, "hs": True}
            return {"ok": True, "unknown": True}
        if st["status"] == "delivered":
            m.status, m.delivered_at = "DELIVERED", m.delivered_at or _now()
        elif st["status"] == "failed" and m.status != "DELIVERED":
            m.status, m.error_code, m.error_message = "FAILED", st.get("error_code"), st.get("error")
        db.commit()
        return {"ok": True}
    if st["status"] == "delivered":
        r.status = "DELIVERED"
        r.delivered_at = r.delivered_at or _now()
    elif st["status"] == "failed" and r.status != "DELIVERED":
        _failed(db, r, st.get("error_code"), st.get("error") or "Not delivered")
    db.commit()
    return {"ok": True}


def on_reply(db: Session, tenant_id, phone: str, text: str | None, kind: str, lead: SmsLead) -> None:
    """A text came in (service.on_inbound_sms has the lead). If we texted this
    number in a campaign lately: mark the reply, and give an unnamed lead the
    name and CSV row we have, so the agent's card isn't "Unknown"."""
    r = (
        db.query(TextCampaignRecipient)
        .filter(TextCampaignRecipient.tenant_id == tenant_id, TextCampaignRecipient.phone_number == phone,
                TextCampaignRecipient.sent_at >= _now() - REPLY_WINDOW,
                TextCampaignRecipient.status.in_(("SENT", "DELIVERED")))
        .order_by(TextCampaignRecipient.sent_at.desc())
        .first()
    )
    if r is None:
        return
    if r.replied_at is None:
        r.replied_at = _now()
        r.reply_text = (text or "")[:1000]
        r.reply_kind = kind
    if r.first_name and (not lead.customer_name or lead.customer_name.startswith("Unknown")):
        full = next((v for k, v in (r.fields or []) if re.search(r"full\s*name|^name$", str(k), re.I)), None)
        last = next((v for k, v in (r.fields or []) if re.search(r"last", str(k), re.I)), None)
        lead.customer_name = (full or f"{r.first_name} {last or ''}").strip()[:255]
    if not lead.details and r.fields:
        lead.details = {"fields": r.fields}
    if not lead.dial_from and r.from_number:
        lead.dial_from = r.from_number


# ---- Read models -------------------------------------------------------------------------

POSITIVE = (replies.YES, replies.REPLY)


def _counts(db: Session, ids: list) -> dict:
    out: dict = {i: {"total": 0, "queued": 0, "sent": 0, "delivered": 0, "failed": 0, "skipped": 0,
                     "cancelled": 0, "replied": 0, "positive": 0, "opted_out": 0} for i in ids}
    if not ids:
        return out
    for cid, status, n in (
        db.query(TextCampaignRecipient.campaign_id, TextCampaignRecipient.status, func.count())
        .filter(TextCampaignRecipient.campaign_id.in_(ids))
        .group_by(TextCampaignRecipient.campaign_id, TextCampaignRecipient.status).all()
    ):
        c = out[cid]
        c["total"] += n
        key = {"QUEUED": "queued", "SENDING": "queued", "SENT": "sent", "DELIVERED": "delivered",
               "FAILED": "failed", "SKIPPED": "skipped", "CANCELLED": "cancelled"}.get(status)
        if key:
            c[key] += n
    for cid, kind, n in (
        db.query(TextCampaignRecipient.campaign_id, TextCampaignRecipient.reply_kind, func.count())
        .filter(TextCampaignRecipient.campaign_id.in_(ids), TextCampaignRecipient.replied_at.isnot(None))
        .group_by(TextCampaignRecipient.campaign_id, TextCampaignRecipient.reply_kind).all()
    ):
        c = out[cid]
        c["replied"] += n
        if kind in POSITIVE:
            c["positive"] += n
        elif kind in replies.NEGATIVE_KINDS:
            c["opted_out"] += n
    for c in out.values():
        c["sent"] += c["delivered"]  # "sent" = left us (delivered is a subset)
    return out


def campaign_dict(c: TextCampaign, counts: dict) -> dict:
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {
        "id": str(c.id), "name": c.name, "template": c.template, "status": c.status,
        "pause_reason": c.pause_reason, "file_name": c.file_name,
        "skipped_at_upload": {"rows": c.rows_in_file, "invalid": c.skipped_invalid,
                              "duplicate": c.skipped_duplicate, "dnc": c.skipped_dnc},
        "created_at": iso(c.created_at), "started_at": iso(c.started_at), "finished_at": iso(c.finished_at),
        "counts": counts,
    }


def overview(db: Session, tenant_id) -> dict:
    from app.voice_dialer.service import get_settings

    s = get_settings(db, tenant_id)
    rows = (
        db.query(TextCampaign)
        .filter(TextCampaign.tenant_id == tenant_id)
        .order_by(TextCampaign.created_at.desc())
        .limit(50)
        .all()
    )
    counts = _counts(db, [c.id for c in rows])
    now = _now()
    return {
        "campaigns": [campaign_dict(c, counts[c.id]) for c in rows],
        "limits": {
            "daily_cap": daily_cap(), "sent_today": sent_today(db, tenant_id),
            "numbers": senders(), "gap_seconds": SEND_GAP_SECONDS,
            # Eastern window right now — clients with no state in the file wait for it.
            "unknown_state_open": _in_hours(s, now, None),
            "window": [int(s.window_start_hour or 8), int(s.window_end_hour or 20)],
            "max_rows": MAX_ROWS, "max_template": MAX_TEMPLATE,
        },
        "business_name": s.business_name,
        "stop_line": STOP_LINE,
        "simulate": telnyx.simulating(),
    }


RECIPIENT_FILTERS = {
    "all": None,
    "waiting": ("status", ("QUEUED", "SENDING")),
    "sent": ("status", ("SENT", "DELIVERED")),
    "failed": ("status", ("FAILED", "SKIPPED", "CANCELLED")),
    "replied": ("replied", None),
    "positive": ("kind", POSITIVE),
    "opted_out": ("kind", tuple(replies.NEGATIVE_KINDS)),
}


def recipients(db: Session, c: TextCampaign, flt: str = "all", q: str | None = None,
               offset: int = 0, limit: int = 50) -> dict:
    query = db.query(TextCampaignRecipient).filter(TextCampaignRecipient.campaign_id == c.id)
    rule = RECIPIENT_FILTERS.get(flt)
    if rule:
        what, val = rule
        if what == "status":
            query = query.filter(TextCampaignRecipient.status.in_(val))
        elif what == "replied":
            query = query.filter(TextCampaignRecipient.replied_at.isnot(None))
        else:
            query = query.filter(TextCampaignRecipient.reply_kind.in_(val))
    if q and q.strip():
        term = q.strip()
        digits = _digits(term)
        cond = TextCampaignRecipient.first_name.ilike(f"%{term}%")
        if len(digits) >= 3:
            cond = cond | TextCampaignRecipient.phone_number.like(f"%{digits}%")
        query = query.filter(cond)
    limit = max(1, min(int(limit or 50), 200))
    rows = query.order_by(TextCampaignRecipient.position).offset(max(0, int(offset or 0))).limit(limit + 1).all()
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {
        "items": [{
            "id": str(r.id), "phone_number": r.phone_number, "first_name": r.first_name, "state": r.state,
            "status": r.status, "from_number": r.from_number, "body": r.body, "error": r.error, "sent_at": iso(r.sent_at),
            "delivered_at": iso(r.delivered_at), "replied_at": iso(r.replied_at),
            "reply_text": r.reply_text, "reply_kind": r.reply_kind,
        } for r in rows[:limit]],
        "has_more": len(rows) > limit,
    }


def texts_for_phone(db: Session, tenant_id, phone: str) -> list[dict]:
    """Campaign texts we sent this number (for the Auto Dialer inbox thread)."""
    rows = (
        db.query(TextCampaignRecipient, TextCampaign.name)
        .join(TextCampaign, TextCampaign.id == TextCampaignRecipient.campaign_id)
        .filter(TextCampaignRecipient.tenant_id == tenant_id, TextCampaignRecipient.phone_number == phone,
                TextCampaignRecipient.sent_at.isnot(None))
        .all()
    )
    return [{"at": r.sent_at.isoformat(), "body": r.body, "campaign": name, "status": r.status,
             "from_number": r.from_number} for r, name in rows]
