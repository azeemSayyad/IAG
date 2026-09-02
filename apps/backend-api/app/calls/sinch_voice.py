"""
Sinch Voice & Video (WebRTC) client.

Responsibilities:
  * mint application-signed JWT registration tokens for the in-browser SDK
  * build the SVAML the browser call triggers (caller ID + recording + disclosure)
  * call Sinch's Calling API (hang up / query) when needed
  * expose a stable per-agent "user identity" for the SDK

Everything is driven by config (app key/secret/project/region) — NOTHING is
hardcoded. When credentials are absent the client reports `configured == False`
and callers degrade gracefully (the UI shows "calling not configured").

The token is the documented Sinch "application-signed" JWT used by the in-app
voice SDK to register an instance: HS256 over the app secret, with the app key
in the header `kid` and the rtc application/user claims in the body.
"""
from __future__ import annotations

import time
import uuid
from typing import Dict, Optional

import jwt

from app.core.config import settings


def agent_identity(agent_id) -> str:
    """Stable Sinch user identity for an agent (no PII)."""
    return "agent_" + str(agent_id).replace("-", "")


class SinchVoiceClient:
    provider_name = "sinch"

    @property
    def app_key(self) -> str:
        return (settings.SINCH_APP_KEY or "").strip()

    @property
    def app_secret(self) -> str:
        return (settings.SINCH_APP_SECRET or "").strip()

    @property
    def project_id(self) -> str:
        return (settings.SINCH_PROJECT_ID or "").strip()

    @property
    def region(self) -> str:
        return (settings.SINCH_REGION or "us").strip().lower()

    def configured(self) -> bool:
        return bool(self.app_key and self.app_secret)

    def mint_webrtc_token(self, identity: str, ttl_seconds: int = 3600) -> Dict[str, str]:
        """Application-signed JWT the browser SDK uses to register `identity`.

        Returns {token, identity, appKey, expiresAt}. Raises RuntimeError if the
        Sinch app credentials are not configured.
        """
        if not self.configured():
            raise RuntimeError("Sinch Voice is not configured (set SINCH_APP_KEY / SINCH_APP_SECRET)")

        now = int(time.time())
        exp = now + max(60, int(ttl_seconds))
        app_ref = "//rtc/applications/" + self.app_key
        header = {"alg": "HS256", "typ": "JWT", "kid": self.app_key}
        payload = {
            "iss": app_ref,
            "sub": app_ref + "/users/" + identity,
            "iat": now,
            "exp": exp,
            "nonce": uuid.uuid4().hex,
        }
        token = jwt.encode(payload, self.app_secret, algorithm="HS256", headers=header)
        # PyJWT >=2 returns str
        if isinstance(token, bytes):
            token = token.decode("utf-8")
        return {
            "token": token,
            "identity": identity,
            "appKey": self.app_key,
            "region": self.region,
            "expiresAt": exp,
        }

    def build_call_svaml(self, *, from_number: str, to_number: str,
                         record: bool = True, disclosure: Optional[str] = None) -> dict:
        """SVAML returned to Sinch when the browser places the call.

        Sets the caller ID the lead sees (the agent's assigned number), plays the
        recording disclosure (compliance), enables recording, then connects PSTN.
        """
        instructions = []
        disc = (disclosure if disclosure is not None else settings.CALL_RECORDING_DISCLOSURE) or ""
        if disc:
            instructions.append({"name": "say", "text": disc, "locale": "en-US"})
        if record and settings.CALL_RECORDING_ENABLED:
            instructions.append({"name": "startRecording", "options": {
                "destinationUrl": "",  # Sinch stores then calls our recording webhook
                "credentials": "",
                "format": "mp3",
                "notificationEvents": True,
            }})
        action = {
            "name": "connectPSTN",
            "number": to_number,
            "cli": from_number,            # caller ID the lead sees
        }
        return {"instructions": instructions, "action": action}


sinch_voice = SinchVoiceClient()
