"""Hiree (applicant) Engage Cloud provider — a fully independent SMS channel.

Mirrors the lead Engage Cloud integration but on its OWN account credentials
(APPLICANT_ENGAGECLOUD_*) so admin↔hiree texting never crosses the lead channel and
the lead first-template lockdown is never involved. Authentication is HTTP **basic
auth** with a single API key + secret (the hiree account's key) — nothing is shared
with or falls back to the lead account, so the two stay completely separate.

Pure payload parsing (parse_webhook / parse_reply) is reused from the lead provider —
the MessageMedia/Engage payload shape is identical; only the credentials, base URL,
sender numbers and webhook secret differ here.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from base64 import b64decode, b64encode
from typing import Any, Dict

import httpx

from app.core.applicant_numbers import pick_sender
from app.core.config import settings

logger = logging.getLogger(__name__)


def _digits(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _e164(value: str) -> str:
    """Proper US E.164. A 10-digit number (e.g. a hiree entered as '551 359 6301')
    gets the US country code '1' so it isn't mis-read as another country — '5513596301'
    -> '+15513596301', NOT '+5513596301' (which MessageMedia treats as Brazil +55).
    An 11-digit US number already starting with '1' is kept as-is."""
    d = _digits(value)
    if not d:
        return ""
    if len(d) == 10:
        d = "1" + d
    return f"+{d}"


class ApplicantEngageProvider:
    provider_name = "engage_cloud_applicant"

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    # ----------------------------------------------------------------- config
    # Read straight from the dedicated APPLICANT_ENGAGECLOUD_* settings — no fallback
    # into the lead account, so the hiree channel is 100% independent.
    def base_url(self) -> str:
        return (settings.APPLICANT_ENGAGECLOUD_API_BASE_URL or "").strip()

    def api_key(self) -> str:
        return (settings.APPLICANT_ENGAGECLOUD_API_KEY or "").strip()

    def api_secret(self) -> str:
        return (settings.APPLICANT_ENGAGECLOUD_API_SECRET or "").strip()

    def auth_header(self) -> str:
        return (settings.APPLICANT_ENGAGECLOUD_AUTH_HEADER or "").strip()

    def webhook_secret(self) -> str:
        return (settings.APPLICANT_ENGAGE_CLOUD_WEBHOOK_SECRET or "").strip()

    @property
    def configured(self) -> bool:
        """Live send is possible when there's a base URL, a sender number, and auth —
        either a ready-made Authorization header OR a basic-auth key + secret."""
        has_auth = bool(self.auth_header() or (self.api_key() and self.api_secret()))
        return bool(self.base_url() and has_auth and pick_sender())

    def _endpoint(self, path: str) -> str:
        """Build a MessageMedia REST URL, tolerating a base URL given with OR without
        the '/v1' segment — the Sinch console shows the host as 'https://eu.app.api.
        sinch.com/' (no /v1), but the API lives under /v1/messages, /v1/replies."""
        base = self.base_url().rstrip("/")
        if not base.endswith("/v1"):
            base = base + "/v1"
        return f"{base}/{path.lstrip('/')}"

    def _auth(self):
        """(headers, auth) for an HTTP call: prefer api key + secret (basic auth — the
        hiree account's key); fall back to a ready-made Authorization header only when
        no key/secret is set, so a stray/blank header can't shadow valid credentials."""
        headers = {"Content-Type": "application/json"}
        auth = None
        if self.api_key() and self.api_secret():
            auth = (self.api_key(), self.api_secret())
        elif self.auth_header():
            headers["Authorization"] = self.auth_header()
        return headers, auth

    # ------------------------------------------------------------------- send
    def send(self, to: str, body: str) -> Dict[str, Any]:
        """Send ONE admin→hiree text from the dedicated applicant number via HTTP basic
        auth. Degrades gracefully: status 'skipped' when disabled/unconfigured so the
        caller records the message locally (dev). Never raises."""
        if not getattr(settings, "APPLICANT_SMS_LIVE_SEND_ENABLED", True):
            return {"message_sid": None, "status": "skipped", "to": to,
                    "provider": self.provider_name, "error": "applicant_live_send_disabled"}
        source_number = pick_sender()
        if not self.configured:
            return {"message_sid": None, "status": "skipped", "to": to,
                    "from": source_number or None, "provider": self.provider_name,
                    "error": "applicant_sms_not_configured"}
        try:
            payload = {"messages": [{
                "content": body,
                "destination_number": _e164(to),
                "source_number": source_number,
            }]}
            headers, auth = self._auth()
            url = self._endpoint("messages")
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload, headers=headers, auth=auth)
            data = self._json(response)
            if response.status_code >= 400:
                return {"message_sid": None, "status": "failed", "to": to, "from": source_number,
                        "provider": self.provider_name, "error": data.get("error") or response.text}
            msgs = data.get("messages")
            first = msgs[0] if isinstance(msgs, list) and msgs else {}
            mid = (first.get("message_id") or first.get("messageId") or data.get("id")
                   or data.get("message_id") or data.get("messageId"))
            return {"message_sid": mid,
                    "status": first.get("status") or data.get("status") or "queued",
                    "to": to, "from": source_number, "provider": self.provider_name,
                    "error": None, "raw": data}
        except Exception as exc:
            return {"message_sid": None, "status": "failed", "to": to, "from": source_number,
                    "provider": self.provider_name, "error": str(exc)}

    @staticmethod
    def _json(response: httpx.Response) -> Dict[str, Any]:
        try:
            data = response.json()
            return data if isinstance(data, dict) else {"data": data}
        except Exception:
            return {}

    # ------------------------------------------------------------- inbound poll
    def fetch_replies(self) -> Dict[str, Any]:
        """GET the hiree account's unconfirmed replies (the no-webhook inbound path).
        Reuses the lead parser since the payload shape is identical. Never raises."""
        if not self.configured:
            return {"success": False, "replies": [], "error": "applicant_sms_not_configured"}
        try:
            from app.ai.services.communication_provider import communication_service
            headers, auth = self._auth()
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(self._endpoint("replies"), headers=headers, auth=auth)
            data = self._json(response)
            if response.status_code >= 400:
                return {"success": False, "replies": [], "status_code": response.status_code,
                        "error": data.get("message") or data.get("error") or response.text}
            replies = data.get("replies") or []
            if not isinstance(replies, list):
                replies = []
            return {"success": True,
                    "replies": [communication_service.parse_reply(r) for r in replies if isinstance(r, dict)],
                    "raw": data}
        except Exception as exc:
            return {"success": False, "replies": [], "error": str(exc)}

    def confirm_replies(self, reply_ids) -> Dict[str, Any]:
        """Acknowledge processed replies so the hiree account stops re-delivering them."""
        reply_ids = [str(r) for r in (reply_ids or []) if r]
        if not reply_ids:
            return {"success": True, "confirmed": 0}
        if not self.configured:
            return {"success": False, "confirmed": 0, "error": "applicant_sms_not_configured"}
        try:
            headers, auth = self._auth()
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(self._endpoint("replies/confirmed"),
                                       json={"reply_ids": reply_ids}, headers=headers, auth=auth)
            if response.status_code >= 400:
                data = self._json(response)
                return {"success": False, "confirmed": 0, "status_code": response.status_code,
                        "error": data.get("message") or data.get("error") or response.text}
            return {"success": True, "confirmed": len(reply_ids)}
        except Exception as exc:
            return {"success": False, "confirmed": 0, "error": str(exc)}

    # ---------------------------------------------------------------- webhook
    def validate_webhook(self, body: bytes, headers: Dict[str, str]) -> bool:
        """Validate the dedicated applicant inbound webhook against the applicant
        webhook secret (mirrors the lead validator). Open in non-prod when no secret
        is configured, like the lead path."""
        secret = self.webhook_secret()
        if not secret:
            return settings.APP_ENV != "production"

        normalized = {k.lower(): v for k, v in headers.items()}
        shared = (normalized.get("x-engagecloud-webhook-secret")
                  or normalized.get("x-engage-cloud-webhook-secret"))
        if shared and hmac.compare_digest(shared, secret):
            return True

        # Reuse the lead provider's freshness check (secret-independent).
        from app.ai.services.communication_provider import communication_service
        if not communication_service._timestamp_is_fresh(normalized):
            return False

        signature = (normalized.get("x-engagecloud-signature")
                     or normalized.get("x-engage-cloud-signature")
                     or normalized.get("x-engage-signature"))
        if not signature:
            return False
        expected_raw = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
        expected_hex = expected_raw.hex()
        expected_b64 = b64encode(expected_raw).decode("utf-8")
        for candidate in {signature, signature.removeprefix("sha256=")}:
            if hmac.compare_digest(candidate, expected_hex) or hmac.compare_digest(candidate, expected_b64):
                return True
            try:
                if hmac.compare_digest(b64decode(candidate), expected_raw):
                    return True
            except Exception:
                pass
        return False


applicant_provider = ApplicantEngageProvider()
