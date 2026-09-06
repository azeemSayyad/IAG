"""Direct-to-pool CSV ingest — the SECOND way a lead enters the SMS agent pool.

The original flow: upload → campaign → first-template SMS via Sinch → the
customer replies → lead_ingest / inbound_sync mirror the replier into
sms_leads. This module skips all of that: an admin uploads a list and every
usable row lands in sms_leads as QUEUED immediately. NO SMS is sent — these
leads are worked by phone, and the agent's lead card shows the whole row.

Guarantees:
- Never texts anyone. The linked `leads` rows are written with
  pacing_status='pooled' (NOT 'held') and are never enqueued, so neither the
  campaign drip nor the same-day pacing engine (which only select 'held') can
  ever pick them up. on_lead_created is deliberately NOT called.
- Do-Not-Call is honoured: a number on sms_do_not_call is dropped, not pooled.
- A number already open in the pool (QUEUED / ASSIGNED / IN_PROGRESS) is not
  pooled twice, and duplicates within the file collapse to one.
- Everything downstream (assignment, offer/accept/pass, dispositions,
  appointments, DNC) is untouched — it only ever reads sms_leads.
"""

import csv
import io
import re
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.timezones import lead_display_timezone
from app.ingestion.services.validation import (
    normalize_email,
    normalize_phone,
    validate_email,
    validate_phone,
)
from app.models.lead import Lead
from app.models.sms import SmsDoNotCall, SmsLead, SmsPoolBatch, SmsQueueAgent
from app.sms_queue.services.queue_service import _dnc_phone, _evt

LEAD_SOURCE = "csv_pool"        # leads.source for rows created here
SMS_SOURCE = "CSV_DIRECT"       # sms_leads.source
OPEN_STATUSES = ("QUEUED", "ASSIGNED", "IN_PROGRESS")
MAX_ROWS = 20000
_BATCH = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- Header mapping ------------------------------------------------------
# The upload is any spreadsheet an admin has — a policy book, a purchased list,
# a carrier export — so the core columns are matched loosely by token and
# EVERY other column is kept (label + value) for the agent's lead card. The
# admin never has to rename headers.

def _token(h: str) -> str:
    h = str(h or "").lstrip("﻿").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", h).strip("_")


# Columns that describe someone OTHER than the customer (broker/agent/rep) must
# never be mistaken for the customer's name or phone.
_OTHER_PARTY = re.compile(r"(^|_)(broker|agent|rep|producer|writing|owner|assigned)(_|$)")

_CORE = [
    ("first_name", re.compile(r"(^|_)(first|given)_?name$|^f_?name$|^first$")),
    ("last_name", re.compile(r"(^|_)(last|family)_?name$|^l_?name$|^surname$|^last$")),
    ("full_name", re.compile(r"^(full_?)?name$|(^|_)(member|customer|client|contact|insured)_?name$")),
    ("phone", re.compile(r"(^|_)(phone|mobile|cell|tel|telephone)(_|$)|(^|_)phone_?(number|no|num)$")),
    ("zip_code", re.compile(r"(^|_)(zip|postal|postcode)(_|$)")),
    ("city", re.compile(r"^(city|town)$")),
    ("state", re.compile(r"^(state|st|state_code|province)$")),
    ("address", re.compile(r"(^|_)(address|street|addr|county)(_|$)")),
]


def map_headers(headers: list[str]) -> dict:
    """{'core': {field: header}, 'extra': [header, ...]} — first match wins per core
    field; a header that names another party (Broker NPN, Agent Phone) is extra."""
    core: dict[str, str] = {}
    extra: list[str] = []
    for h in headers:
        if h is None or not str(h).strip():
            continue
        tok = _token(h)
        field = None
        if not _OTHER_PARTY.search(tok):
            for name, rx in _CORE:
                if name not in core and rx.search(tok):
                    field = name
                    break
        if field:
            core[field] = h
        else:
            extra.append(h)
    return {"core": core, "extra": extra}


_ISO_DT = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")


def _clean_value(v) -> str:
    s = str(v if v is not None else "").strip()
    m = _ISO_DT.match(s)
    if m:  # "2025-01-01T08:00" → "2025-01-01": the time part is export noise
        return m.group(1)
    return s


def _compose_address(address: str, city: str, state: str, zip_code: str) -> str | None:
    region = " ".join(p for p in (state, zip_code) if p)
    parts = [p for p in (address, city, region) if p]
    return ", ".join(parts) or None


def parse_csv(content: str) -> tuple[list[str], list[dict]]:
    if content and content[0] == "﻿":
        content = content[1:]
    reader = csv.DictReader(io.StringIO(content))
    headers = [h for h in (reader.fieldnames or []) if h is not None]
    return headers, list(reader)


# ---- Ingest ---------------------------------------------------------------

def ingest_csv(
    db: Session,
    tenant_id: str,
    content: str,
    name: str,
    uploaded_by: str | None = None,
) -> tuple[dict, list[dict]]:
    """Import one CSV straight into the pool. Returns (result, socket events).

    result = {"ok", "batch": {...}, "summary": {...}, "errors": [...]} — on a
    file with no usable rows, ok=False and NO batch is created."""
    headers, rows = parse_csv(content)
    errors: list[dict] = []
    if not headers:
        return {"ok": False, "reason": "empty_file", "errors": [{"row": 0, "error": "The file has no header row."}]}, []
    if len(rows) > MAX_ROWS:
        return {"ok": False, "reason": "too_many_rows",
                "errors": [{"row": 0, "error": f"Up to {MAX_ROWS:,} rows per upload."}]}, []

    mapping = map_headers(headers)
    core, extra = mapping["core"], mapping["extra"]
    if "phone" not in core:
        return {"ok": False, "reason": "no_phone_column",
                "errors": [{"row": 0, "error": "No phone column found — the file needs a Phone / Mobile / Cell column."}]}, []
    if not ({"first_name", "last_name", "full_name"} & set(core)):
        return {"ok": False, "reason": "no_name_column",
                "errors": [{"row": 0, "error": "No name column found — the file needs First/Last Name or a Name column."}]}, []

    # Numbers already open in the pool (any source) — never pool someone twice.
    open_digits = {
        _dnc_phone(p)
        for (p,) in db.query(SmsLead.phone_number)
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.status.in_(OPEN_STATUSES))
        .all()
    }
    # Do-Not-Call, loaded once (digits-only keys, same as queue_service.is_dnc).
    dnc_digits = {
        d for (d,) in db.query(SmsDoNotCall.phone_number)
        .filter(SmsDoNotCall.tenant_id == tenant_id)
        .all()
    }

    batch = SmsPoolBatch(
        tenant_id=tenant_id,
        name=(name or "upload.csv").strip()[:255],
        uploaded_by=uploaded_by,
        total_rows=len(rows),
        columns=extra,
    )
    db.add(batch)
    db.flush()

    seen: set[str] = set()
    imported = dup = dnc = failed = 0
    pending: list[tuple[Lead, SmsLead]] = []

    def _flush_pending():
        # Two-step so each SmsLead gets its Lead's id: add the Lead rows, flush
        # for ids, then link + add the SmsLead rows.
        for lead, _ in pending:
            db.add(lead)
        db.flush()
        for lead, sl in pending:
            sl.lead_id = lead.id
            db.add(sl)
        db.flush()
        pending.clear()

    for n, raw in enumerate(rows, start=1):
        get = lambda f: _clean_value(raw.get(core[f])) if f in core else ""  # noqa: E731
        first, last, full = get("first_name"), get("last_name"), get("full_name")
        if not first and not last and full:
            parts = full.split()
            first, last = parts[0], " ".join(parts[1:])
        if not first and not last:
            failed += 1
            errors.append({"row": n, "error": "No name"})
            continue

        phone_raw = get("phone")
        if not phone_raw or not validate_phone(phone_raw):
            failed += 1
            errors.append({"row": n, "error": f"Invalid phone: {phone_raw or '(blank)'}"})
            continue
        phone = normalize_phone(phone_raw)
        digits = _dnc_phone(phone)
        if digits in seen or digits in open_digits:
            dup += 1
            continue
        seen.add(digits)
        if digits in dnc_digits:
            dnc += 1
            continue

        state = get("state").upper()[:50] or None
        city = get("city")[:255] or None
        zip_code = get("zip_code")[:20] or None
        street = get("address")[:255]
        address = _compose_address(street, city, state, zip_code)

        # Every non-core column, in file order, for the agent card.
        fields = [[h, _clean_value(raw.get(h))] for h in extra]
        fields = [[h, v] for h, v in fields if v]
        email = next((v for h, v in fields if re.search(r"e_?mail", _token(h))), "")
        email = normalize_email(email) if email and validate_email(email) else None

        lead = Lead(
            tenant_id=tenant_id,
            source=LEAD_SOURCE,
            first_name=(first or "-")[:255],
            last_name=(last or "-")[:255],
            phone=phone,
            phone_normalized=phone[:20],
            email=email,
            email_normalized=email,
            state=state,
            city=city,
            zip_code=zip_code,
            timezone=lead_display_timezone(state),
            lifecycle_stage="new",
            status="new",
            # 'pooled' — NOT 'held' — so no release/drip query can ever pick it up.
            pacing_status="pooled",
            custom_fields={"pool_batch_id": str(batch.id), "csv": {h: v for h, v in fields}},
            created_by=uploaded_by,
        )
        sl = SmsLead(
            tenant_id=tenant_id,
            phone_number=phone,
            customer_name=" ".join(p for p in (first, last) if p)[:255] or None,
            source=SMS_SOURCE,
            batch_id=batch.id,
            details={"fields": fields, "address": address},
            priority="NORMAL",
            status="QUEUED",
            message_count=0,
        )
        pending.append((lead, sl))
        imported += 1
        if len(pending) >= _BATCH:
            _flush_pending()
    if pending:
        _flush_pending()

    batch.imported = imported
    batch.skipped_duplicates = dup
    batch.skipped_dnc = dnc
    batch.failed = failed

    if imported == 0:
        # Nothing usable — don't leave an empty card behind.
        db.delete(batch)
        db.commit()
        return {
            "ok": False,
            "reason": "no_rows_imported",
            "summary": _summary(len(rows), imported, dup, dnc, failed),
            "errors": errors[:50],
        }, []

    db.commit()
    events = [_evt("tenant", tenant_id, "sms:queue_updated", {"reason": "pool_upload"})]
    return {
        "ok": True,
        "batch": _batch_dict(batch, _live_counts(db, tenant_id, [batch.id]).get(str(batch.id), {})),
        "summary": _summary(len(rows), imported, dup, dnc, failed),
        "errors": errors[:50],
    }, events


def _summary(total, imported, dup, dnc, failed) -> dict:
    return {"total_rows": total, "imported": imported, "skipped_duplicates": dup,
            "skipped_dnc": dnc, "failed": failed}


# ---- Batches --------------------------------------------------------------

def _live_counts(db: Session, tenant_id: str, batch_ids: list) -> dict:
    """{batch_id: {in_pool, working, done, appointments, sales}} from sms_leads."""
    out: dict[str, dict] = {}
    if not batch_ids:
        return out
    rows = (
        db.query(SmsLead.batch_id, SmsLead.status, SmsLead.disposition, func.count(SmsLead.id))
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.batch_id.in_(batch_ids))
        .group_by(SmsLead.batch_id, SmsLead.status, SmsLead.disposition)
        .all()
    )
    for bid, status, disp, n in rows:
        c = out.setdefault(str(bid), {"in_pool": 0, "working": 0, "done": 0, "appointments": 0, "sales": 0})
        if status == "QUEUED":
            c["in_pool"] += n
        elif status in ("ASSIGNED", "IN_PROGRESS"):
            c["working"] += n
        elif status == "DISPOSITIONED":
            c["done"] += n
            if disp == "APPOINTMENT_SET":
                c["appointments"] += n
            elif disp == "SALE":
                c["sales"] += n
    return out


def _batch_dict(b: SmsPoolBatch, live: dict) -> dict:
    return {
        "id": str(b.id),
        "name": b.name,
        "total_rows": b.total_rows,
        "imported": b.imported,
        "skipped_duplicates": b.skipped_duplicates,
        "skipped_dnc": b.skipped_dnc,
        "failed": b.failed,
        "columns": b.columns or [],
        "in_pool": live.get("in_pool", 0),
        "working": live.get("working", 0),
        "done": live.get("done", 0),
        "appointments": live.get("appointments", 0),
        "sales": live.get("sales", 0),
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }


def list_batches(db: Session, tenant_id: str, limit: int = 50) -> dict:
    batches = (
        db.query(SmsPoolBatch)
        .filter(SmsPoolBatch.tenant_id == tenant_id, SmsPoolBatch.deleted_at.is_(None))
        .order_by(SmsPoolBatch.created_at.desc())
        .limit(limit)
        .all()
    )
    live = _live_counts(db, tenant_id, [b.id for b in batches])
    return {"batches": [_batch_dict(b, live.get(str(b.id), {})) for b in batches]}


def delete_batch(db: Session, tenant_id: str, batch_id: str) -> tuple[dict, list[dict]]:
    """Pull a batch back out of the pool. Leads still waiting (QUEUED) or only
    offered (ASSIGNED) are removed; a lead an agent has already accepted or
    dispositioned is left alone — that work happened."""
    batch = (
        db.query(SmsPoolBatch)
        .filter(SmsPoolBatch.id == batch_id, SmsPoolBatch.tenant_id == tenant_id,
                SmsPoolBatch.deleted_at.is_(None))
        .first()
    )
    if not batch:
        return {"ok": False, "reason": "not_found"}, []

    leads = (
        db.query(SmsLead)
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.batch_id == batch.id,
                SmsLead.status.in_(("QUEUED", "ASSIGNED")))
        .all()
    )
    now = _now()
    removed = 0
    freed_agents: set[str] = set()
    for sl in leads:
        if sl.assigned_agent_id:
            agent = (
                db.query(SmsQueueAgent)
                .filter(SmsQueueAgent.tenant_id == tenant_id, SmsQueueAgent.user_id == sl.assigned_agent_id)
                .first()
            )
            if agent and str(agent.current_lead_id) == str(sl.id):
                agent.current_lead_id = None
            freed_agents.add(str(sl.assigned_agent_id))
        # Same tombstone the manager's delete uses — DELETED is excluded from
        # every pool / active / manage view.
        sl.status = "DELETED"
        sl.assigned_agent_id = None
        sl.disposition = None
        sl.pass_count = 0
        if sl.lead_id:
            db.query(Lead).filter(Lead.id == sl.lead_id).update(
                {Lead.deleted_at: now}, synchronize_session=False
            )
        removed += 1
    # Leads an agent already accepted / finished stay as they are.
    kept = (
        db.query(func.count(SmsLead.id))
        .filter(SmsLead.tenant_id == tenant_id, SmsLead.batch_id == batch.id,
                SmsLead.status.in_(("IN_PROGRESS", "DISPOSITIONED")))
        .scalar()
        or 0
    )
    batch.deleted_at = now
    db.commit()

    events = [_evt("tenant", tenant_id, "sms:queue_updated", {"reason": "pool_batch_removed"})]
    for uid in freed_agents:  # close the offer popup on whoever was holding one
        events.append(_evt("agent", uid, "sms:queue_updated", {"reason": "pool_batch_removed"}))
    return {"ok": True, "removed": removed, "kept": kept}, events
