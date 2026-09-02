"""
Call Recording Service (Phase 41.1)

Integrates with the configured communication provider to record calls:
- Start/stop recording
- Store recording metadata
- Handle recording webhooks
- Download audio files

Provider Recording Flow:
1. Call starts → recording.start()
2. Call ends → provider webhook with recording URL
3. Store recording metadata
4. Trigger transcription pipeline
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.calls.models import CallRecording
logger = logging.getLogger(__name__)


class CallRecordingService:
    """
    Manages call recordings from provider callbacks.

    Features:
    - Start/stop recording
    - Handle recording webhooks
    - Store metadata
    - Trigger transcription
    """

    def __init__(self, db: Session = None):
        self.db = db

    def create_recording(
        self,
        tenant_id: str,
        appointment_id: Optional[UUID] = None,
        lead_id: Optional[UUID] = None,
        agent_id: Optional[UUID] = None,
        twilio_call_sid: Optional[str] = None,
    ) -> CallRecording:
        """
        Create a new recording entry.

        Called when a call starts.
        """
        recording = CallRecording(
            tenant_id=tenant_id,
            appointment_id=appointment_id,
            lead_id=lead_id,
            agent_id=agent_id,
            twilio_call_sid=twilio_call_sid,
            status="pending",
        )
        self.db.add(recording)
        self.db.commit()
        self.db.refresh(recording)

        logger.info(f"Created recording {recording.id}")
        return recording

    def update_recording(
        self,
        recording_id: UUID,
        twilio_recording_sid: Optional[str] = None,
        audio_url: Optional[str] = None,
        duration_seconds: Optional[int] = None,
        status: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Optional[CallRecording]:
        """
        Update recording with webhook data.

        Called when the provider sends a recording webhook.
        """
        recording = self.db.query(CallRecording).filter(
            CallRecording.id == recording_id,
        ).first()

        if not recording:
            return None

        if twilio_recording_sid:
            recording.twilio_recording_sid = twilio_recording_sid
        if audio_url:
            recording.audio_url = audio_url
        if duration_seconds is not None:
            recording.duration_seconds = duration_seconds
        if status:
            recording.status = status
        if metadata:
            recording.recording_metadata = metadata

        recording.updated_at = datetime.now(timezone.utc)
        self.db.commit()

        logger.info(f"Updated recording {recording_id}: status={status}")
        return recording

    def handle_recording_webhook(self, webhook_data: Dict) -> Optional[CallRecording]:
        """
        Handle recording webhook data from Engage Clouds or a compatible provider.

        Webhook payload includes:
        - recording id / RecordingSid
        - recording URL / RecordingUrl
        - duration / RecordingDuration
        - call id / CallSid
        """
        call_sid = webhook_data.get("call_id") or webhook_data.get("callId") or webhook_data.get("CallSid")
        recording_sid = (
            webhook_data.get("recording_id")
            or webhook_data.get("recordingId")
            or webhook_data.get("RecordingSid")
        )
        recording_url = (
            webhook_data.get("recording_url")
            or webhook_data.get("recordingUrl")
            or webhook_data.get("RecordingUrl")
        )
        duration = int(
            webhook_data.get("duration_seconds")
            or webhook_data.get("durationSeconds")
            or webhook_data.get("RecordingDuration")
            or 0
        )

        if not call_sid:
            logger.warning("No provider call id in webhook data")
            return None

        # Legacy column name stores provider call ids for historical compatibility.
        recording = self.db.query(CallRecording).filter(
            CallRecording.twilio_call_sid == call_sid,
        ).first()

        if not recording:
            logger.warning(f"No recording found for provider call id: {call_sid}")
            return None

        # Update recording
        recording.twilio_recording_sid = recording_sid
        recording.audio_url = recording_url
        recording.duration_seconds = duration
        recording.status = "completed"
        recording.recording_metadata = webhook_data
        recording.updated_at = datetime.now(timezone.utc)

        self.db.commit()

        logger.info(f"Recording webhook processed: {recording.id}")
        return recording

    def get_recording(self, recording_id: UUID) -> Optional[CallRecording]:
        """Get a recording by ID."""
        return self.db.query(CallRecording).filter(
            CallRecording.id == recording_id,
        ).first()

    def get_recordings_for_appointment(self, appointment_id: UUID) -> list:
        """Get all recordings for an appointment."""
        return self.db.query(CallRecording).filter(
            CallRecording.appointment_id == appointment_id,
        ).order_by(CallRecording.created_at.desc()).all()

    def get_recordings_for_lead(self, lead_id: UUID, limit: int = 10) -> list:
        """Get recordings for a lead."""
        return self.db.query(CallRecording).filter(
            CallRecording.lead_id == lead_id,
        ).order_by(CallRecording.created_at.desc()).limit(limit).all()

    def get_recording_stats(self, tenant_id: str) -> Dict[str, Any]:
        """Get recording statistics for a tenant."""
        recordings = self.db.query(CallRecording).filter(
            CallRecording.tenant_id == tenant_id,
        ).all()

        total = len(recordings)
        completed = sum(1 for r in recordings if r.status == "completed")
        total_duration = sum(r.duration_seconds or 0 for r in recordings)

        return {
            "total_recordings": total,
            "completed": completed,
            "pending": total - completed,
            "total_duration_seconds": total_duration,
            "total_duration_minutes": round(total_duration / 60, 1),
            "avg_duration_seconds": round(total_duration / completed, 1) if completed > 0 else 0,
        }


def get_provider_recording_url(recording_url: Optional[str] = None) -> str:
    """Return the recording URL supplied by Engage Clouds or the active provider."""
    if not recording_url:
        raise RuntimeError("Provider recording URL was not supplied in the webhook payload")
    return recording_url


def get_twilio_recording_url(recording_sid: str) -> str:
    """Legacy alias retained for import compatibility; direct provider URL construction is disabled."""
    raise RuntimeError("Direct Twilio recording URLs are not supported; use provider webhook recording_url")
