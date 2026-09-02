"""
Call Transcript Models (Phase 41.3)

Database models for call recordings and transcripts:
- CallRecording — Stores recording metadata
- CallTranscript — Stores transcript text and segments
- CallAnalysis — Stores analysis results
"""

from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, JSON, Float, Integer, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from app.core.database import Base
import uuid


class CallRecording(Base):
    """Stores call recording metadata."""
    __tablename__ = "call_recordings"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=lambda: uuid.uuid4())
    tenant_id = Column(String, nullable=False, index=True)
    appointment_id = Column(PGUUID(as_uuid=True), ForeignKey("appointments.id"), nullable=True, index=True)
    lead_id = Column(PGUUID(as_uuid=True), ForeignKey("leads.id"), nullable=True, index=True)
    agent_id = Column(PGUUID(as_uuid=True), ForeignKey("agents.id"), nullable=True, index=True)

    # Recording metadata (twilio_* kept for backward compat / legacy rows)
    twilio_call_sid = Column(String, nullable=True, index=True)
    twilio_recording_sid = Column(String, nullable=True)
    audio_url = Column(String, nullable=True)
    duration_seconds = Column(Integer, default=0)
    channels = Column(Integer, default=1)
    status = Column(String, default="pending")  # pending, recording, completed, failed

    # === Sinch WebRTC outbound call fields ===
    provider = Column(String, default="sinch")          # sinch | twilio (legacy)
    sinch_call_id = Column(String, nullable=True, index=True)
    sinch_recording_id = Column(String, nullable=True)
    direction = Column(String, default="outbound")       # outbound | inbound
    from_number = Column(String, nullable=True)          # caller ID the lead saw (agent's number)
    to_number = Column(String, nullable=True)            # lead's number
    call_status = Column(String, default="initiated")    # initiated|ringing|answered|completed|failed|no_answer|canceled
    disclosure_played = Column(Integer, default=0)        # 1 once the recording disclosure was played
    started_at = Column(DateTime(timezone=True), nullable=True)
    answered_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    # Permanent storage in OUR S3 bucket (Sinch is not the system of record).
    s3_bucket = Column(String, nullable=True)
    s3_key = Column(String, nullable=True)
    recording_status = Column(String, default="none")    # none|pending|stored|failed

    # Metadata
    recording_metadata = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class CallTranscript(Base):
    """Stores call transcript text and segments."""
    __tablename__ = "call_transcripts"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=lambda: uuid.uuid4())
    tenant_id = Column(String, nullable=False, index=True)
    recording_id = Column(PGUUID(as_uuid=True), ForeignKey("call_recordings.id"), nullable=False, index=True)
    appointment_id = Column(PGUUID(as_uuid=True), ForeignKey("appointments.id"), nullable=True, index=True)
    lead_id = Column(PGUUID(as_uuid=True), ForeignKey("leads.id"), nullable=True, index=True)
    agent_id = Column(PGUUID(as_uuid=True), ForeignKey("agents.id"), nullable=True, index=True)

    # Transcript data
    full_text = Column(Text, nullable=True)
    segments = Column(JSON, default=list)  # List of {speaker, text, start, end, confidence}
    language = Column(String, default="en")
    transcription_service = Column(String, default="whisper")  # whisper, deepgram, assemblyai

    # Stats
    total_words = Column(Integer, default=0)
    customer_words = Column(Integer, default=0)
    agent_words = Column(Integer, default=0)
    talk_ratio = Column(Float, default=0.5)  # agent_words / total_words

    # Metadata
    transcription_metadata = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class CallAnalysis(Base):
    """Stores call analysis results."""
    __tablename__ = "call_analysis"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=lambda: uuid.uuid4())
    tenant_id = Column(String, nullable=False, index=True)
    transcript_id = Column(PGUUID(as_uuid=True), ForeignKey("call_transcripts.id"), nullable=False, index=True)
    recording_id = Column(PGUUID(as_uuid=True), ForeignKey("call_recordings.id"), nullable=True)
    appointment_id = Column(PGUUID(as_uuid=True), ForeignKey("appointments.id"), nullable=True, index=True)
    lead_id = Column(PGUUID(as_uuid=True), ForeignKey("leads.id"), nullable=True, index=True)
    agent_id = Column(PGUUID(as_uuid=True), ForeignKey("agents.id"), nullable=True, index=True)

    # Objection analysis
    objections_detected = Column(JSON, default=list)  # [{type, text, timestamp, handled}]
    objection_count = Column(Integer, default=0)
    objections_handled = Column(Integer, default=0)

    # Sentiment analysis
    overall_sentiment = Column(String, default="neutral")  # positive, neutral, negative
    sentiment_score = Column(Float, default=0.5)  # 0-1
    sentiment_timeline = Column(JSON, default=list)  # [{timestamp, sentiment, score}]

    # Engagement metrics
    engagement_score = Column(Float, default=0.5)  # 0-1
    interruption_count = Column(Integer, default=0)
    silence_periods = Column(Integer, default=0)  # Long pauses
    questions_asked = Column(Integer, default=0)

    # Compliance
    compliance_violations = Column(JSON, default=list)  # [{type, text, timestamp}]
    compliance_score = Column(Float, default=1.0)  # 0-1, 1 = perfect

    # Summary
    key_points = Column(JSON, default=list)
    next_steps = Column(JSON, default=list)
    probability_to_close = Column(Float, default=0.5)  # 0-1

    # Metadata
    analysis_metadata = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
