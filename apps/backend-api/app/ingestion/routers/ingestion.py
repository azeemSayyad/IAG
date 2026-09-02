"""
Ingestion Router (Step 3.1)

Endpoints:
- POST /ingestion/csv — Upload CSV file
- POST /ingestion/webhook/{source} — Receive webhook from CRM
- POST /ingestion/api — API import for single lead
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_tenant_id, get_current_active_user, require_role
from app.core.sending import (
    is_sending_paused,
    set_sending_paused,
    is_autopilot_paused,
    set_autopilot_paused,
    get_drip_config,
    set_drip_config,
)
from app.models.user import User
from app.models.lead import Lead
from app.models.campaign import Campaign
from app.ingestion.services.csv_import import import_leads_from_csv
from app.ingestion.services.webhook import import_lead_from_webhook
from app.ingestion.services.validation import validate_lead_row, normalize_row
from app.ingestion.services.deduplication import find_duplicate_lead, merge_lead_data
from app.ingestion.services.events import on_lead_created
from app.schemas.lead import LeadCreate, LeadResponse

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


@router.post("/csv", response_model=dict)
async def upload_csv(
    file: UploadFile = File(...),
    source: str = Form("csv_import"),
    dedup_mode: str = Form("skip"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """
    Upload a CSV file to import leads.

    Required CSV headers: first_name, last_name, phone
    Optional headers: email, source, state, city, zip_code, tags
    """
    # Validate file type
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")

    # Read file content
    try:
        content = await file.read()
        file_content = content.decode("utf-8-sig")  # utf-8-sig strips a leading BOM (Excel/Sheets CSV exports)
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded")

    # Large files (lakhs / 100k+) use the high-throughput bulk importer
    # (batch timezone, bulk dedup, bulk insert, pipelined enqueue). Small files
    # keep the per-row path so single-lead behavior is unchanged.
    row_estimate = file_content.count("\n")
    if row_estimate > 500:
        from app.ingestion.services.csv_import import bulk_import_leads_from_csv
        result = bulk_import_leads_from_csv(
            db=db, tenant_id=tenant_id, file_content=file_content,
            source=source, dedup_mode=dedup_mode,
        )
        return {"summary": result.to_dict(), "leads": [], "mode": "bulk"}

    result = import_leads_from_csv(
        db=db,
        tenant_id=tenant_id,
        file_content=file_content,
        source=source,
        dedup_mode=dedup_mode,
    )

    return {
        "summary": result.to_dict(),
        "leads": [LeadResponse.model_validate(lead) for lead in result.leads[:100]],
        "mode": "row",
    }


@router.post("/webhook/{source}", response_model=dict)
async def receive_webhook(
    source: str,
    payload: dict,
    dedup_mode: str = Query("merge"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    """
    Receive a webhook from an external CRM.

    Supported sources: hubspot, salesforce, zapier, generic
    """
    supported_sources = ["hubspot", "salesforce", "zapier", "generic"]
    if source not in supported_sources:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported source: {source}. Supported: {supported_sources}",
        )

    result = import_lead_from_webhook(
        db=db,
        tenant_id=tenant_id,
        payload=payload,
        source=source,
        dedup_mode=dedup_mode,
    )

    return result


@router.post("/api", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
def import_lead_api(
    request: LeadCreate,
    dedup_mode: str = Query("skip"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """
    Import a single lead via API.

    Validates, deduplicates, scores, and triggers outreach.
    """
    # Normalize
    lead_data = request.model_dump()
    lead_data["source"] = lead_data.get("source", "api")

    # Validate
    validation = validate_lead_row(lead_data)
    if not validation.is_valid:
        raise HTTPException(status_code=400, detail=validation.errors)

    # Check for duplicates
    existing = find_duplicate_lead(
        db=db,
        tenant_id=tenant_id,
        phone=lead_data["phone"],
        email=lead_data.get("email"),
    )

    if existing:
        if dedup_mode == "merge":
            updates = merge_lead_data(existing, lead_data)
            for key, value in updates.items():
                setattr(existing, key, value)
            db.commit()
            db.refresh(existing)
            return existing
        else:
            raise HTTPException(
                status_code=409,
                detail=f"Duplicate lead found with phone: {lead_data['phone']}",
            )

    # Create lead
    lead = Lead(tenant_id=tenant_id, **lead_data)
    db.add(lead)
    db.commit()
    db.refresh(lead)

    # Trigger lead created event
    on_lead_created(db, lead)

    return lead


# ---- Global send kill-switch (Upload Leads "Stop sending" button) ----
@router.get("/sending/status")
def sending_status(
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Whether outbound sending is currently paused for this tenant."""
    return {"paused": is_sending_paused(tenant_id)}


@router.post("/sending/stop")
def sending_stop(
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """STOP all outbound sending (outreach, AI replies, follow-ups, reminders) and
    halt the capacity engine's lead releases. Admin only."""
    set_sending_paused(tenant_id, True, actor=str(current_user.id))
    return {"paused": True, "message": "All sending stopped."}


@router.post("/sending/resume")
def sending_resume(
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Resume outbound sending. Admin only."""
    set_sending_paused(tenant_id, False, actor=str(current_user.id))
    return {"paused": False, "message": "Sending resumed."}


# --- Queue-Only Mode (booking autopilot pause) ---
# Narrower than the kill-switch above: the FIRST outreach template still sends
# and inbound replies are still recorded (so the SMS human queue picks up
# positive replies), but the AI sends NO booking reply and ALL follow-ups /
# reminders / nurture are suppressed. The appointment-booking pipeline stays
# intact and returns the moment this is turned off.

@router.get("/sending/autopilot/status")
def autopilot_status(
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Whether Queue-Only Mode (booking autopilot paused) is active."""
    return {"autopilot_paused": is_autopilot_paused(tenant_id), "tenant_id": tenant_id}


@router.post("/sending/autopilot/pause")
def autopilot_pause(
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Enter Queue-Only Mode: first template still sends, but no AI booking
    replies and no follow-ups/reminders — humans in the SMS queue handle the
    rest. Admin only."""
    set_autopilot_paused(tenant_id, True, actor=str(current_user.id))
    # Return the REAL re-read state, not a hardcoded True — so a failed Redis
    # write can never make the UI show "on" while booking is actually live.
    real = is_autopilot_paused(tenant_id)
    return {
        "autopilot_paused": real,
        "tenant_id": tenant_id,
        "message": "Queue-Only Mode on — booking AI paused." if real
                   else "Could not enable Queue-Only Mode — please retry.",
    }


@router.post("/sending/autopilot/resume")
def autopilot_resume(
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """DISABLED. Queue-Only Mode (first-template-only lockdown) is permanently ON
    and cannot be turned off — the platform may only ever send the first template.
    This endpoint is intentionally a no-op kept for backward compatibility."""
    return {
        "autopilot_paused": True,
        "tenant_id": tenant_id,
        "locked": True,
        "message": "Queue-Only Mode is permanently ON (first-template-only). It cannot be disabled.",
    }


@router.get("/sending/autopilot/drip")
def autopilot_drip_get(
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Current Queue-Only drip rate: release `leads` first-templates every
    `minutes` for big (>500) held batches while Queue-Only Mode is on."""
    return get_drip_config(tenant_id)


@router.post("/sending/autopilot/drip")
def autopilot_drip_set(
    body: dict,
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Set the Queue-Only drip rate (leads per interval). Admin only."""
    try:
        leads = int(body.get("leads"))
        minutes = int(body.get("minutes"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="leads and minutes must be integers")
    if leads < 1 or minutes < 1:
        raise HTTPException(status_code=422, detail="leads and minutes must be >= 1")
    cfg = set_drip_config(tenant_id, leads, minutes, actor=str(current_user.id))
    return {**cfg, "message": f"Drip set: {cfg['leads']} leads every {cfg['minutes']} min."}


# --- First-outreach template (the message auto-sent to leads after upload) ---
# Stored per tenant in Redis; falls back to the built-in default when unset.
_TEMPLATE_MAX = 1000


@router.get("/outreach-template")
def outreach_template_get(
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Current first-outreach template for this tenant, plus the built-in
    default and the placeholders that get filled per lead."""
    from app.ai.services.prompts import get_outreach_template, PRIMARY_OUTREACH_TEMPLATE
    template = get_outreach_template(tenant_id)
    return {
        "template": template,
        "default": PRIMARY_OUTREACH_TEMPLATE,
        "is_custom": template != PRIMARY_OUTREACH_TEMPLATE,
        "placeholders": ["first_name"],
    }


@router.post("/outreach-template")
def outreach_template_set(
    body: dict,
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Save a new first-outreach template for this tenant. Admin only."""
    from app.ai.services.prompts import set_outreach_template
    template = (body.get("template") or "").strip()
    if not template:
        raise HTTPException(status_code=422, detail="Message cannot be empty.")
    if len(template) > _TEMPLATE_MAX:
        raise HTTPException(status_code=422, detail=f"Message must be {_TEMPLATE_MAX} characters or fewer.")
    if not set_outreach_template(tenant_id, template):
        raise HTTPException(status_code=503, detail="Could not save the message — please retry.")
    return {"template": template, "is_custom": True, "message": "First message saved."}


@router.post("/outreach-template/reset")
def outreach_template_reset(
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Clear the override so the built-in default first message is used. Admin only."""
    from app.ai.services.prompts import reset_outreach_template, PRIMARY_OUTREACH_TEMPLATE
    reset_outreach_template(tenant_id)
    return {"template": PRIMARY_OUTREACH_TEMPLATE, "is_custom": False, "message": "Reset to the default message."}


# ===== Campaign manager (CSV-upload campaigns with per-campaign run control) =====
# Each uploaded CSV becomes a Campaign (marked with description == _UPLOAD_BATCH).
# Leads are held until the campaign is "run"; only ONE campaign runs at a time.
_UPLOAD_BATCH = "upload_batch"


def _campaign_counts(db: Session, camp) -> tuple:
    """(total_leads, sent, yes, failed, delivered) for one campaign. yes =
    distinct conversations with a positive customer reply; failed = outbound the
    provider could not deliver; delivered = outbound DLR-confirmed delivered —
    all scoped to THIS campaign's leads."""
    from sqlalchemy import func, distinct
    from app.models.conversation import Conversation
    from app.models.message import Message
    total = db.query(func.count(Lead.id)).filter(
        Lead.campaign_id == camp.id, Lead.deleted_at.is_(None)).scalar() or 0
    sent = db.query(func.count(Lead.id)).filter(
        Lead.campaign_id == camp.id, Lead.deleted_at.is_(None),
        Lead.pacing_status == "released").scalar() or 0
    # Fast path: an un-run campaign (nothing released) has sent NO outbound texts
    # and received NO replies, so yes/failed/delivered are provably 0. Skip the
    # three messages->conversations->leads joins entirely — those joins are what
    # made campaign upload (and the 15s card refresh) slow on a big messages table.
    if sent == 0:
        return total, 0, 0, 0, 0
    yes = (
        db.query(func.count(distinct(Message.conversation_id)))
        .join(Conversation, Message.conversation_id == Conversation.id)
        .join(Lead, Conversation.lead_id == Lead.id)
        .filter(
            Lead.campaign_id == camp.id,
            Message.sender == "customer",
            Message.intent.in_(["POSITIVE", "BOOK_NOW", "INTERESTED", "SLOT_SELECTED"]),
        )
        .scalar() or 0
    )

    def _outbound_status_count(statuses):
        q = (
            db.query(func.count(Message.id))
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(Lead, Conversation.lead_id == Lead.id)
            .filter(Lead.campaign_id == camp.id, Message.sender != "customer")
        )
        if len(statuses) == 1:
            q = q.filter(func.lower(Message.delivery_status) == statuses[0])
        else:
            q = q.filter(func.lower(Message.delivery_status).in_(statuses))
        return q.scalar() or 0

    failed = _outbound_status_count(["failed", "undelivered", "rejected", "expired"])
    delivered = _outbound_status_count(["delivered"])
    return total, sent, yes, failed, delivered


def _campaign_dict(db: Session, camp) -> dict:
    total, sent, yes, failed, delivered = _campaign_counts(db, camp)
    return {
        "id": str(camp.id), "name": camp.name, "send_state": camp.send_state,
        "drip_leads": camp.drip_leads, "drip_minutes": camp.drip_minutes,
        "total_leads": total, "sent": sent, "remaining": max(0, total - sent),
        "yes": yes, "failed": failed, "delivered": delivered,
        "first_template": camp.first_template or None,
        "provider": getattr(camp, "provider", None) or "sinch",
        "created_at": camp.created_at.isoformat() if camp.created_at else None,
    }


def _get_upload_campaign(db: Session, tenant_id: str, campaign_id):
    camp = db.query(Campaign).filter(
        Campaign.id == campaign_id, Campaign.tenant_id == tenant_id,
        Campaign.description == _UPLOAD_BATCH, Campaign.deleted_at.is_(None)).first()
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return camp


@router.post("/campaigns/upload")
async def campaign_upload(
    file: UploadFile = File(...),
    name: str = Form(None),
    first_template: str = Form(None),     # per-campaign first message (optional)
    provider: str = Form("sinch"),        # "sinch" (default) | "engage2" (Engage Cloud)
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Upload one CSV as a campaign. Leads are HELD (sent only when the campaign
    is run). Returns the created campaign."""
    if not (file.filename or "").endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")
    try:
        content = (await file.read()).decode("utf-8-sig")  # utf-8-sig strips a leading BOM (Excel/Sheets CSV exports)
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded")
    from app.ai.services.sms_providers import normalize_provider
    camp = Campaign(
        tenant_id=tenant_id, name=(name or file.filename or "Campaign").strip()[:255],
        description=_UPLOAD_BATCH, status="paused", send_state="ready",
        drip_leads=50, drip_minutes=10,
        first_template=(first_template.strip()[:1000] if first_template and first_template.strip() else None),
        provider=normalize_provider(provider),
    )
    db.add(camp); db.commit(); db.refresh(camp)
    from app.ingestion.services.csv_import import bulk_import_leads_from_csv
    result = bulk_import_leads_from_csv(
        db=db, tenant_id=tenant_id, file_content=content,
        source="csv_import", dedup_mode="skip", campaign_id=str(camp.id),
    )
    # Just the lead count here — a plain COUNT(*), no analytics joins. (The full
    # counts come from _campaign_dict below, which now skips the joins for an
    # un-run campaign.)
    from sqlalchemy import func as _func
    total = db.query(_func.count(Lead.id)).filter(
        Lead.campaign_id == camp.id, Lead.deleted_at.is_(None)).scalar() or 0
    camp.total_leads = total
    if total == 0:
        # Nothing imported — delete the empty campaign and tell the user WHY,
        # instead of leaving junk behind and showing a silent green "uploaded".
        # Common causes: a header row that isn't first_name/last_name/phone, or
        # no rows with a valid phone.
        db.delete(camp); db.commit()
        detail = "No leads were imported — check the CSV's header row (needs first_name, last_name, phone) and phone column."
        if result.errors:
            first = result.errors[0]
            extra = first.get("error") or first.get("errors")
            if extra:
                detail += f" ({extra})"
        raise HTTPException(status_code=400, detail=str(detail))
    db.commit()
    return {"campaign": _campaign_dict(db, camp), "summary": result.to_dict()}


@router.get("/campaigns")
def campaign_list(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """List the CSV-upload campaigns with their state, counts and drip rate."""
    camps = db.query(Campaign).filter(
        Campaign.tenant_id == tenant_id, Campaign.description == _UPLOAD_BATCH,
        Campaign.deleted_at.is_(None)).order_by(Campaign.created_at.desc()).limit(20).all()
    return {"campaigns": [_campaign_dict(db, c) for c in camps]}


@router.post("/campaigns/{campaign_id}/run")
def campaign_run(
    campaign_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Start (or resume) sending this campaign. Only one campaign may run at a
    time — blocks if another is already running."""
    camp = _get_upload_campaign(db, tenant_id, campaign_id)
    other = db.query(Campaign).filter(
        Campaign.tenant_id == tenant_id, Campaign.description == _UPLOAD_BATCH,
        Campaign.send_state == "running", Campaign.id != camp.id).first()
    if other:
        raise HTTPException(status_code=409,
                            detail=f"'{other.name}' is already running — pause or stop it first")
    camp.send_state = "running"; db.commit()
    # Fire the first wave IMMEDIATELY (don't wait up to ~60s for the next drip
    # tick). Subsequent waves are paced by the drip controller at the campaign's
    # rate. Best-effort — respects Queue-Only Mode / kill-switch gates inside.
    try:
        from app.core.redis import redis_service
        redis_service.client.delete(f"autopilot:drip:last:campaign:{camp.id}")
    except Exception:
        pass
    try:
        from app.pacing.release import drip_cycle
        drip_cycle(db, tenant_id)
    except Exception:
        pass
    return {"campaign": _campaign_dict(db, camp), "message": f"{camp.name} is running"}


@router.post("/campaigns/{campaign_id}/pause")
def campaign_pause(
    campaign_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Temporarily halt this campaign (resumable)."""
    camp = _get_upload_campaign(db, tenant_id, campaign_id)
    camp.send_state = "paused"; db.commit()
    return {"campaign": _campaign_dict(db, camp), "message": f"{camp.name} paused"}


@router.post("/campaigns/{campaign_id}/resume")
def campaign_resume(
    campaign_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Resume a paused campaign (blocks if another is running)."""
    return campaign_run(campaign_id, db, tenant_id, current_user)


@router.post("/campaigns/{campaign_id}/stop")
def campaign_stop(
    campaign_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """End this campaign's sending (re-runnable later)."""
    camp = _get_upload_campaign(db, tenant_id, campaign_id)
    camp.send_state = "stopped"; db.commit()
    return {"campaign": _campaign_dict(db, camp), "message": f"{camp.name} stopped"}


@router.post("/campaigns/{campaign_id}/drip")
def campaign_drip(
    campaign_id: str,
    body: dict,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Set this campaign's own drip rate (N leads every M minutes)."""
    camp = _get_upload_campaign(db, tenant_id, campaign_id)
    try:
        leads = int(body.get("leads")); minutes = int(body.get("minutes"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="leads and minutes must be integers")
    if leads < 1 or minutes < 1:
        raise HTTPException(status_code=422, detail="leads and minutes must be >= 1")
    camp.drip_leads = min(leads, 5000); camp.drip_minutes = min(minutes, 1440); db.commit()
    return {"campaign": _campaign_dict(db, camp), "message": f"Rate: {camp.drip_leads}/{camp.drip_minutes}min"}


@router.post("/campaigns/{campaign_id}/rename")
def campaign_rename(
    campaign_id: str,
    body: dict,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Rename a campaign (admin). Display name only — leads, drip rate and sending
    are untouched, so the send path / first-template lockdown are unaffected."""
    camp = _get_upload_campaign(db, tenant_id, campaign_id)
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    camp.name = name[:255]
    db.commit()
    return {"campaign": _campaign_dict(db, camp), "message": f"Renamed to {camp.name}"}


@router.post("/campaigns/{campaign_id}/provider")
def campaign_set_provider(
    campaign_id: str,
    body: dict,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Set which lead-SMS provider this campaign sends through ("sinch" | "engage2").
    Only changes WHICH account/numbers this campaign's first-templates use — the send
    path and first-template lockdown are unaffected."""
    from app.ai.services.sms_providers import normalize_provider
    camp = _get_upload_campaign(db, tenant_id, campaign_id)
    camp.provider = normalize_provider(body.get("provider"))
    db.commit()
    return {"campaign": _campaign_dict(db, camp), "message": f"Provider set to {camp.provider}"}


@router.delete("/campaigns/{campaign_id}")
def campaign_delete(
    campaign_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(require_role("tenant_admin", "super_admin")),
):
    """Remove a campaign and soft-delete its leads (clears the card)."""
    from datetime import datetime, timezone
    camp = _get_upload_campaign(db, tenant_id, campaign_id)
    now = datetime.now(timezone.utc)
    db.query(Lead).filter(Lead.campaign_id == camp.id).update(
        {Lead.deleted_at: now}, synchronize_session=False)
    camp.send_state = "stopped"; camp.deleted_at = now
    db.commit()
    return {"deleted": str(camp.id), "message": f"{camp.name} removed"}
