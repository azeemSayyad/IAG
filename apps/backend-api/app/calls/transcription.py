"""
Transcription Pipeline (Phase 41.2)

Converts call recordings to text using:
- Whisper (OpenAI) — local or API
- Deepgram — cloud API
- AssemblyAI — cloud API

Pipeline:
1. Download audio from provider recording URL
2. Send to transcription service
3. Parse response into segments
4. Store transcript
"""

import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.calls.models import CallRecording, CallTranscript
from app.core.config import settings

logger = logging.getLogger(__name__)


class TranscriptSegment:
    """A single segment of a transcript."""

    def __init__(
        self,
        speaker: str,
        text: str,
        start_time: float = 0,
        end_time: float = 0,
        confidence: float = 0.9,
    ):
        self.speaker = speaker  # "agent", "customer", "unknown"
        self.text = text
        self.start_time = start_time
        self.end_time = end_time
        self.confidence = confidence

    def to_dict(self) -> Dict:
        return {
            "speaker": self.speaker,
            "text": self.text,
            "start_time": round(self.start_time, 2),
            "end_time": round(self.end_time, 2),
            "confidence": round(self.confidence, 3),
        }


class TranscriptionPipeline:
    """
    Converts audio to text using various services.

    Features:
    - Multi-service support (Whisper, Deepgram, AssemblyAI)
    - Speaker diarization
    - Segment parsing
    - Confidence scoring
    """

    def __init__(self, db: Session = None):
        self.db = db

    async def transcribe_recording(
        self,
        recording_id: UUID,
        service: str = "whisper",
    ) -> Optional[CallTranscript]:
        """
        Transcribe a call recording.

        Args:
            recording_id: Recording UUID
            service: Transcription service to use

        Returns:
            CallTranscript or None
        """
        recording = self.db.query(CallRecording).filter(
            CallRecording.id == recording_id,
        ).first()

        if not recording or not recording.audio_url:
            logger.error(f"Recording {recording_id} not found or no audio URL")
            return None

        # Transcribe based on service
        if service == "whisper":
            result = await self._transcribe_whisper(recording.audio_url)
        elif service == "deepgram":
            result = await self._transcribe_deepgram(recording.audio_url)
        elif service == "assemblyai":
            result = await self._transcribe_assemblyai(recording.audio_url)
        else:
            logger.error(f"Unknown transcription service: {service}")
            return None

        if not result:
            return None

        # Create transcript
        segments = result.get("segments", [])
        full_text = result.get("text", "")

        # Calculate stats
        total_words = len(full_text.split())
        agent_words = sum(
            len(s.text.split()) for s in segments if s.speaker == "agent"
        )
        customer_words = sum(
            len(s.text.split()) for s in segments if s.speaker == "customer"
        )

        talk_ratio = agent_words / total_words if total_words > 0 else 0.5

        transcript = CallTranscript(
            tenant_id=recording.tenant_id,
            recording_id=recording.id,
            appointment_id=recording.appointment_id,
            lead_id=recording.lead_id,
            agent_id=recording.agent_id,
            full_text=full_text,
            segments=[s.to_dict() for s in segments],
            language=result.get("language", "en"),
            transcription_service=service,
            total_words=total_words,
            customer_words=customer_words,
            agent_words=agent_words,
            talk_ratio=round(talk_ratio, 3),
            transcription_metadata=result.get("metadata", {}),
        )

        self.db.add(transcript)
        self.db.commit()
        self.db.refresh(transcript)

        logger.info(f"Transcribed recording {recording_id}: {total_words} words")
        return transcript

    async def _transcribe_whisper(self, audio_url: str) -> Optional[Dict]:
        """
        Transcribe using OpenAI Whisper API.

        Falls back to local whisper if API unavailable.
        """
        try:
            import httpx

            # Try OpenAI Whisper API
            api_key = getattr(settings, "OPENAI_API_KEY", None)
            if api_key:
                return await self._whisper_api(audio_url, api_key)

            # Try local Whisper
            return await self._whisper_local(audio_url)

        except Exception as e:
            logger.error(f"Whisper transcription failed: {e}")
            return None

    async def _whisper_api(self, audio_url: str, api_key: str) -> Optional[Dict]:
        """Use OpenAI Whisper API."""
        import httpx

        # Download audio
        async with httpx.AsyncClient() as client:
            audio_response = await client.get(audio_url)
            if audio_response.status_code != 200:
                return None

            audio_data = audio_response.content

            # Send to Whisper API
            response = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("audio.wav", audio_data, "audio/wav")},
                data={"model": "whisper-1", "response_format": "verbose_json"},
            )

            if response.status_code == 200:
                data = response.json()
                segments = []
                for seg in data.get("segments", []):
                    segments.append(TranscriptSegment(
                        speaker="unknown",
                        text=seg.get("text", ""),
                        start_time=seg.get("start", 0),
                        end_time=seg.get("end", 0),
                        confidence=seg.get("avg_logprob", 0.9),
                    ))

                return {
                    "text": data.get("text", ""),
                    "segments": segments,
                    "language": data.get("language", "en"),
                    "metadata": {"service": "whisper_api"},
                }

        return None

    async def _whisper_local(self, audio_url: str) -> Optional[Dict]:
        """Use local Whisper installation."""
        try:
            import whisper
            import tempfile
            import httpx

            # Download audio
            async with httpx.AsyncClient() as client:
                response = await client.get(audio_url)
                if response.status_code != 200:
                    return None

                # Save to temp file
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    f.write(response.content)
                    temp_path = f.name

                # Transcribe
                model = whisper.load_model("base")
                result = model.transcribe(temp_path, verbose=False)

                segments = []
                for seg in result.get("segments", []):
                    segments.append(TranscriptSegment(
                        speaker="unknown",
                        text=seg.get("text", ""),
                        start_time=seg.get("start", 0),
                        end_time=seg.get("end", 0),
                        confidence=seg.get("avg_logprob", 0.9),
                    ))

                return {
                    "text": result.get("text", ""),
                    "segments": segments,
                    "language": result.get("language", "en"),
                    "metadata": {"service": "whisper_local"},
                }

        except ImportError:
            logger.warning("whisper not installed, skipping local transcription")
            return None

    async def _transcribe_deepgram(self, audio_url: str) -> Optional[Dict]:
        """Use Deepgram API."""
        try:
            import httpx

            api_key = getattr(settings, "DEEPGRAM_API_KEY", None)
            if not api_key:
                logger.warning("DEEPGRAM_API_KEY not set")
                return None

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.deepgram.com/v1/listen",
                    headers={
                        "Authorization": f"Token {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"url": audio_url},
                    params={"model": "nova-2", "diarize": "true", "punctuate": "true"},
                )

                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", {})
                    channels = results.get("channels", [])

                    if channels:
                        alternatives = channels[0].get("alternatives", [])
                        if alternatives:
                            transcript_data = alternatives[0]
                            words = transcript_data.get("words", [])

                            # Build segments from words with speaker info
                            segments = self._build_segments_from_words(words)

                            return {
                                "text": transcript_data.get("transcript", ""),
                                "segments": segments,
                                "language": "en",
                                "metadata": {"service": "deepgram"},
                            }

        except Exception as e:
            logger.error(f"Deepgram transcription failed: {e}")

        return None

    async def _transcribe_assemblyai(self, audio_url: str) -> Optional[Dict]:
        """Use AssemblyAI API."""
        try:
            import httpx

            api_key = getattr(settings, "ASSEMBLYAI_API_KEY", None)
            if not api_key:
                logger.warning("ASSEMBLYAI_API_KEY not set")
                return None

            async with httpx.AsyncClient() as client:
                # Submit for transcription
                submit_response = await client.post(
                    "https://api.assemblyai.com/v2/transcript",
                    headers={"authorization": api_key},
                    json={
                        "audio_url": audio_url,
                        "speaker_labels": True,
                    },
                )

                if submit_response.status_code == 200:
                    transcript_id = submit_response.json().get("id")

                    # Poll for completion (simplified)
                    import asyncio
                    for _ in range(60):  # Max 5 minutes
                        await asyncio.sleep(5)
                        status_response = await client.get(
                            f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
                            headers={"authorization": api_key},
                        )

                        if status_response.status_code == 200:
                            data = status_response.json()
                            if data.get("status") == "completed":
                                utterances = data.get("utterances", [])
                                segments = [
                                    TranscriptSegment(
                                        speaker="agent" if u.get("speaker") == "A" else "customer",
                                        text=u.get("text", ""),
                                        start_time=u.get("start", 0) / 1000,
                                        end_time=u.get("end", 0) / 1000,
                                        confidence=u.get("confidence", 0.9),
                                    )
                                    for u in utterances
                                ]

                                return {
                                    "text": data.get("text", ""),
                                    "segments": segments,
                                    "language": data.get("language_code", "en"),
                                    "metadata": {"service": "assemblyai"},
                                }

        except Exception as e:
            logger.error(f"AssemblyAI transcription failed: {e}")

        return None

    def _build_segments_from_words(self, words: List[Dict]) -> List[TranscriptSegment]:
        """Build transcript segments from word-level data with speaker info."""
        segments = []
        current_speaker = None
        current_text = []
        current_start = 0

        for word in words:
            speaker = "agent" if word.get("speaker", 0) == 0 else "customer"

            if speaker != current_speaker:
                if current_text:
                    segments.append(TranscriptSegment(
                        speaker=current_speaker,
                        text=" ".join(current_text),
                        start_time=current_start,
                        end_time=word.get("start", 0),
                    ))
                current_speaker = speaker
                current_text = [word.get("word", "")]
                current_start = word.get("start", 0)
            else:
                current_text.append(word.get("word", ""))

        # Final segment
        if current_text:
            segments.append(TranscriptSegment(
                speaker=current_speaker,
                text=" ".join(current_text),
                start_time=current_start,
                end_time=words[-1].get("end", 0) if words else 0,
            ))

        return segments

    def get_transcript(self, transcript_id: UUID) -> Optional[CallTranscript]:
        """Get a transcript by ID."""
        return self.db.query(CallTranscript).filter(
            CallTranscript.id == transcript_id,
        ).first()

    def get_transcripts_for_recording(self, recording_id: UUID) -> list:
        """Get transcripts for a recording."""
        return self.db.query(CallTranscript).filter(
            CallTranscript.recording_id == recording_id,
        ).all()
