"""
Call Recording & Transcription Router (Phase 41)

Exposes call recording, transcription, analysis, and summary endpoints.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user, get_tenant_id
from app.models.user import User
from app.calls.models import CallRecording, CallTranscript, CallAnalysis
from app.calls.recording import CallRecordingService
from app.calls.transcription import TranscriptionPipeline
from app.calls.analysis import CallAnalyzer
from app.calls.summary import CallSummaryGenerator

router = APIRouter(prefix="/calls", tags=["calls"])

recording_service = CallRecordingService()
transcription_pipeline = TranscriptionPipeline()
call_analyzer = CallAnalyzer()
summary_generator = CallSummaryGenerator()


# --- WebRTC (browser calling) ---

@router.get("/config", status_code=status.HTTP_200_OK)
def call_config(current_user: User = Depends(get_current_active_user)):
    """Whether browser calling is available + the agent's caller ID number.

    Lets the frontend decide whether to show the softphone, with a clear reason
    when it can't (not configured, or no number assigned to this agent).
    """
    from app.calls.sinch_voice import sinch_voice
    from app.core.config import settings
    return {
        "configured": sinch_voice.configured(),
        "region": sinch_voice.region,
        "recording_enabled": bool(settings.CALL_RECORDING_ENABLED),
    }


@router.post("/webrtc-token", status_code=status.HTTP_200_OK)
def webrtc_token(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Mint an application-signed JWT so this user's browser SDK can register and
    place calls. Requires Sinch to be configured."""
    from app.models.agent import Agent
    from app.calls.sinch_voice import sinch_voice, agent_identity

    if not sinch_voice.configured():
        raise HTTPException(status_code=503, detail="Calling is not configured")

    agent = db.query(Agent).filter(Agent.user_id == current_user.id, Agent.tenant_id == tenant_id).first()
    ident = agent_identity(agent.id) if agent else agent_identity(current_user.id)
    try:
        token = sinch_voice.mint_webrtc_token(ident)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    token["caller_number"] = (agent.caller_number if agent else None)
    token["can_call"] = bool(agent and agent.caller_number)
    return token


# --- Outbound call lifecycle (Sinch WebRTC) ---

def _validate_voice_callback(request) -> bool:
    """Validate a Sinch voice callback via shared secret. In non-production with
    no secret configured, allow (so local testing works); production requires it."""
    from app.core.config import settings
    secret = (settings.SINCH_VOICE_CALLBACK_SECRET or "").strip()
    if not secret:
        return (settings.APP_ENV or "").lower() != "production"
    got = request.headers.get("x-sinch-callback-secret") or request.headers.get("x-sinch-voice-secret") or ""
    return got == secret


@router.post("/dial", status_code=status.HTTP_201_CREATED)
async def dial(
    body: dict,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Agent initiates a browser call to a lead. Compliance-gated. Creates the
    call row and returns what the browser SDK needs to place the call."""
    from datetime import datetime, timezone
    from app.models.agent import Agent
    from app.models.lead import Lead
    from app.calls.sinch_voice import sinch_voice, agent_identity
    from app.realtime.websocket import emit_to_tenant

    if not sinch_voice.configured():
        raise HTTPException(status_code=503, detail="Calling is not configured")

    agent = db.query(Agent).filter(Agent.user_id == current_user.id, Agent.tenant_id == tenant_id).first()
    if not agent:
        raise HTTPException(status_code=403, detail="Only agents can place calls")
    if not agent.caller_number:
        raise HTTPException(status_code=409, detail="No caller ID number assigned to you — ask an admin to assign one")

    lead_id = body.get("lead_id")
    if not lead_id:
        raise HTTPException(status_code=422, detail="lead_id is required")
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.tenant_id == tenant_id, Lead.deleted_at.is_(None)).first()
    if not lead or not lead.phone:
        raise HTTPException(status_code=404, detail="Lead not found or has no phone number")

    rec = CallRecording(
        tenant_id=str(tenant_id), agent_id=agent.id, lead_id=lead.id,
        appointment_id=body.get("appointment_id"),
        provider="sinch", direction="outbound",
        from_number=agent.caller_number, to_number=lead.phone,
        call_status="initiated", recording_status="none",
        started_at=datetime.now(timezone.utc),
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)

    try:
        await emit_to_tenant(str(tenant_id), "call_initiated", {
            "call_id": str(rec.id), "lead_id": str(lead.id), "to": lead.phone,
            "from": agent.caller_number, "agent_id": str(agent.id),
        })
    except Exception:
        pass

    return {
        "call_id": str(rec.id),
        "identity": agent_identity(agent.id),
        "to_number": lead.phone,
        "from_number": agent.caller_number,
        "lead_name": f"{lead.first_name} {lead.last_name}".strip(),
    }


@router.post("/svaml", status_code=status.HTTP_200_OK)
async def svaml_callback(request: Request, db: Session = Depends(get_db)):
    """Sinch fetches this when the browser places the call. We return SVAML that
    sets caller ID = agent's number, plays the recording disclosure, and records."""
    from app.calls.sinch_voice import sinch_voice
    if not _validate_voice_callback(request):
        raise HTTPException(status_code=403, detail="Invalid voice callback signature")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    to_number = (payload.get("to") or {}).get("endpoint") if isinstance(payload.get("to"), dict) else payload.get("to") or payload.get("destination")
    cli = payload.get("cli") or payload.get("from")
    # Match the most recent initiated call for this destination → mark disclosure.
    rec = (
        db.query(CallRecording)
        .filter(CallRecording.to_number == to_number, CallRecording.call_status.in_(["initiated", "ringing"]))
        .order_by(CallRecording.created_at.desc())
        .first()
    )
    if rec:
        rec.disclosure_played = 1
        rec.call_status = "ringing"
        db.commit()
        cli = rec.from_number or cli
    return sinch_voice.build_call_svaml(from_number=cli or "", to_number=to_number or "")


@router.post("/voice-webhook", status_code=status.HTTP_200_OK)
async def voice_webhook(request: Request, db: Session = Depends(get_db)):
    """Sinch posts call lifecycle (answered/hangup) and recording-finished events.
    On recording-finished we download from Sinch and store permanently in S3,
    then auto-transcribe + QA-score the call."""
    from datetime import datetime, timezone
    from app.calls.s3_storage import s3_storage
    from app.realtime.websocket import emit_to_tenant

    if not _validate_voice_callback(request):
        raise HTTPException(status_code=403, detail="Invalid voice callback signature")
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    event = (payload.get("event") or payload.get("type") or "").lower()
    call_id = payload.get("callId") or payload.get("callid") or payload.get("sinch_call_id")
    to_number = payload.get("to") if isinstance(payload.get("to"), str) else (payload.get("to") or {}).get("endpoint")

    rec = None
    if call_id:
        rec = db.query(CallRecording).filter(CallRecording.sinch_call_id == call_id).first()
    if not rec and to_number:
        rec = (db.query(CallRecording)
               .filter(CallRecording.to_number == to_number)
               .order_by(CallRecording.created_at.desc()).first())
    if not rec:
        return {"status": "no_matching_call"}

    if call_id and not rec.sinch_call_id:
        rec.sinch_call_id = call_id

    # Lifecycle
    if event in ("ace", "answered", "call_answered"):
        rec.call_status = "answered"; rec.answered_at = datetime.now(timezone.utc)
    elif event in ("dice", "hangup", "call_ended", "disconnected"):
        rec.call_status = payload.get("result", "completed").lower() if payload.get("result") else "completed"
        rec.ended_at = datetime.now(timezone.utc)
        if payload.get("duration"):
            try: rec.duration_seconds = int(payload["duration"])
            except Exception: pass

    # Recording finished → download from Sinch and store in OUR S3 (permanent).
    rec_url = payload.get("recordingUrl") or payload.get("recording_url") or (payload.get("recording") or {}).get("url")
    if rec_url and rec.recording_status != "stored":
        rec.recording_status = "pending"
        rec.sinch_recording_id = payload.get("recordingId") or payload.get("recording_id")
        db.commit()
        try:
            stored = s3_storage.store_from_url(rec_url, str(rec.tenant_id), str(rec.id))
            rec.s3_bucket = stored["bucket"]; rec.s3_key = stored["key"]
            rec.recording_status = "stored"; rec.status = "completed"
            db.commit()
            try:
                await emit_to_tenant(str(rec.tenant_id), "recording_ready", {"call_id": str(rec.id)})
            except Exception:
                pass
            # P6: auto transcription + QA (best-effort, non-blocking failures).
            await _auto_transcribe_and_qa(db, rec)
        except Exception as e:
            rec.recording_status = "failed"
            rec.recording_metadata = {**(rec.recording_metadata or {}), "s3_error": str(e)[:300]}
            db.commit()

    db.commit()
    try:
        await emit_to_tenant(str(rec.tenant_id), "call_" + (event or "event"), {"call_id": str(rec.id), "status": rec.call_status})
    except Exception:
        pass
    return {"status": "ok", "call_status": rec.call_status, "recording_status": rec.recording_status}


async def _auto_transcribe_and_qa(db: Session, rec: CallRecording) -> None:
    """Transcribe the stored recording then run AI QA. Best-effort: any failure
    (e.g. transcription provider not configured) is swallowed so it never breaks
    call ingestion."""
    try:
        result = await transcription_pipeline.transcribe_recording(db, str(rec.id), "whisper")
        transcript_id = result.get("transcript_id") if isinstance(result, dict) else None
        if transcript_id:
            try:
                call_analyzer.analyze_transcript(transcript_id)
            except Exception:
                pass
    except Exception:
        pass


@router.get("/{recording_id}/recording-url", status_code=status.HTTP_200_OK)
def recording_signed_url(
    recording_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Time-limited signed S3 URL for playback. Role-scoped: agents may only
    play their OWN calls; managers/admins may play any in the tenant."""
    from app.calls.s3_storage import s3_storage
    from app.models.agent import Agent

    rec = db.query(CallRecording).filter(CallRecording.id == recording_id, CallRecording.tenant_id == str(tenant_id)).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recording not found")
    role = getattr(current_user, "role", None)
    if role in ("agent", "lead"):
        my = db.query(Agent).filter(Agent.user_id == current_user.id).first()
        if not my or rec.agent_id != my.id:
            raise HTTPException(status_code=403, detail="You can only play your own calls")
    if not rec.s3_key:
        raise HTTPException(status_code=409, detail="Recording not stored yet")
    url = s3_storage.signed_url(rec.s3_key)
    if not url:
        raise HTTPException(status_code=503, detail="Recording storage not configured")
    return {"url": url, "expires_in": 3600}


# --- Recordings ---

@router.get("/recordings", status_code=status.HTTP_200_OK)
def list_recordings(
    appointment_id: Optional[UUID] = None,
    lead_id: Optional[UUID] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """List call recordings with optional filters."""
    query = db.query(CallRecording).filter(CallRecording.tenant_id == tenant_id)

    if appointment_id:
        query = query.filter(CallRecording.appointment_id == appointment_id)
    if lead_id:
        query = query.filter(CallRecording.lead_id == lead_id)

    total = query.count()
    items = query.order_by(CallRecording.created_at.desc()).offset((page - 1) * size).limit(size).all()

    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/recordings/stats", status_code=status.HTTP_200_OK)
def get_recording_stats(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get recording statistics for the tenant."""
    return recording_service.get_recording_stats(db, tenant_id)


@router.get("/recordings/{recording_id}", status_code=status.HTTP_200_OK)
def get_recording(
    recording_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get a specific recording."""
    recording = db.query(CallRecording).filter(
        CallRecording.id == recording_id,
        CallRecording.tenant_id == tenant_id,
    ).first()
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    return recording


# --- Transcripts ---

@router.get("/transcripts", status_code=status.HTTP_200_OK)
def list_transcripts(
    recording_id: Optional[UUID] = None,
    appointment_id: Optional[UUID] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """List transcripts with optional filters."""
    query = db.query(CallTranscript).filter(CallTranscript.tenant_id == tenant_id)

    if recording_id:
        query = query.filter(CallTranscript.recording_id == recording_id)
    if appointment_id:
        query = query.filter(CallTranscript.appointment_id == appointment_id)

    total = query.count()
    items = query.order_by(CallTranscript.created_at.desc()).offset((page - 1) * size).limit(size).all()

    return {"items": items, "total": total, "page": page, "size": size}


@router.post("/transcripts/transcribe/{recording_id}", status_code=status.HTTP_201_CREATED)
async def transcribe_recording(
    recording_id: UUID,
    service: str = Query("whisper", pattern="^(whisper|deepgram|assemblyai)$"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Transcribe a recording using the specified service."""
    result = await transcription_pipeline.transcribe_recording(db, str(recording_id), service)
    if not result:
        raise HTTPException(status_code=400, detail="Transcription failed")
    return result


@router.get("/transcripts/{transcript_id}", status_code=status.HTTP_200_OK)
def get_transcript(
    transcript_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get a specific transcript."""
    transcript = db.query(CallTranscript).filter(
        CallTranscript.id == transcript_id,
        CallTranscript.tenant_id == tenant_id,
    ).first()
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return transcript


# --- Analysis ---

@router.get("/analysis", status_code=status.HTTP_200_OK)
def list_analyses(
    appointment_id: Optional[UUID] = None,
    lead_id: Optional[UUID] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """List call analyses with optional filters."""
    query = db.query(CallAnalysis).filter(CallAnalysis.tenant_id == tenant_id)

    if appointment_id:
        query = query.filter(CallAnalysis.appointment_id == appointment_id)
    if lead_id:
        query = query.filter(CallAnalysis.lead_id == lead_id)

    total = query.count()
    items = query.order_by(CallAnalysis.created_at.desc()).offset((page - 1) * size).limit(size).all()

    return {"items": items, "total": total, "page": page, "size": size}


@router.post("/analysis/analyze/{transcript_id}", status_code=status.HTTP_201_CREATED)
async def analyze_transcript(
    transcript_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Analyze a transcript for objections, sentiment, compliance."""
    result = await call_analyzer.analyze_transcript(db, str(transcript_id))
    if not result:
        raise HTTPException(status_code=400, detail="Analysis failed")
    return result


@router.get("/analysis/{analysis_id}", status_code=status.HTTP_200_OK)
def get_analysis(
    analysis_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get a specific call analysis."""
    analysis = db.query(CallAnalysis).filter(
        CallAnalysis.id == analysis_id,
        CallAnalysis.tenant_id == tenant_id,
    ).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return analysis


# --- Summaries ---

@router.get("/summary/{appointment_id}", status_code=status.HTTP_200_OK)
def get_call_summary(
    appointment_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get AI-generated call summary for an appointment."""
    summary = CallSummaryGenerator(db).generate_summary(appointment_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not available")
    return summary.to_dict()


@router.get("/summary/{appointment_id}/quick", status_code=status.HTTP_200_OK)
def get_quick_summary(
    appointment_id: UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """Get quick one-paragraph call summary."""
    summary = CallSummaryGenerator(db).generate_quick_summary(appointment_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not available")
    return {"summary": summary}
