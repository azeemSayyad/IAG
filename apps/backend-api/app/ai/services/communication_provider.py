"""
Primary communication provider integration.

Engage Clouds is the source of truth for outbound messaging and inbound
webhook events. Twilio credentials may still exist behind Engage Clouds, but
application code should not call Twilio directly.
"""

import hashlib
import hmac
import json
import re
from base64 import b64decode, b64encode
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.core.audit import log_ai_action
from app.core.config import settings
from app.core.redis import redis_service


class EngageCloudService:
    def __init__(self, timeout: float = 10.0, provider: str = "sinch"):
        self.timeout = timeout
        # Which lead-SMS provider this instance drives ("sinch" = the original
        # ENGAGECLOUD_* pipeline; "engage2" = the independent ENGAGE2_* pipeline).
        # Defaults to "sinch" so every existing caller is byte-identical.
        self._provider = provider

    def _cfg(self):
        from app.ai.services.sms_providers import get_provider_config
        return get_provider_config(self._provider)

    @property
    def provider_name(self) -> str:
        # "engage_cloud" for sinch (unchanged record name) / "engage2" for the 2nd.
        return self._cfg().name

    @property
    def configured(self) -> bool:
        from app.ai.services.sms_providers import is_configured
        return is_configured(self._provider)

    def _sender(self, tenant_id: str = None, lead_id: str = None) -> str:
        # State-matched sending: a lead is messaged from a number of its OWN state
        # (Florida lead -> Florida number, Texas lead -> Texas number). The sender
        # pool then does sticky-per-lead + least-loaded healthy rotation + daily-cap
        # WITHIN that state's numbers. States with no dedicated numbers fall back to
        # this provider's configured pool. State pools are a Sinch-only concept (the
        # hardcoded STATE_SENDER_NUMBERS are Sinch DIDs), so the 2nd provider draws
        # only from its own flat pool.
        from app.core.applicant_numbers import is_applicant_number
        state_pool = self._state_pool_for_lead(lead_id) if self._provider == "sinch" else []
        # PRIORITY: a mapped state (FL/TX/…) sends from its OWN numbers, unchanged.
        # When the lead's state has NO dedicated numbers (e.g. Georgia), don't collapse
        # onto the single global ENGAGECLOUD_FROM_NUMBERS — round-robin across the WHOLE
        # fleet so unmatched-state leads spread over ALL our DIDs and no number is
        # burdened. Same sticky + per-second + daily-cap machinery, just a wider pool.
        send_pool = state_pool
        if self._provider == "sinch" and not send_pool:
            send_pool = self._full_fleet_pool()
        try:
            from app.ai.services.sender_pool import select_sender
            num = select_sender(tenant_id=tenant_id, lead_id=lead_id,
                                pool=send_pool or None, provider=self._provider)
            if num:
                return self._e164(num)
        except Exception:
            pass
        # Fallback: a number from the chosen pool if we have one, else the configured
        # pool — always excluding the reserved hiree numbers (never used for outreach).
        send_pool = [n for n in send_pool if not is_applicant_number(n)]
        if send_pool:
            return self._e164(send_pool[0])
        senders = [
            s.strip()
            for s in (self._cfg().from_numbers or "").split(",")
            if s.strip() and not is_applicant_number(s)
        ]
        if senders:
            return self._e164(senders[0])
        env_var = "ENGAGECLOUD_FROM_NUMBERS" if self._provider == "sinch" else "ENGAGE2_FROM_NUMBERS"
        raise RuntimeError(f"{env_var} is required for outbound messaging")

    def _state_pool_for_lead(self, lead_id: str = None) -> list:
        """The lead's state-matched ACTIVE sender numbers ([] if none/unknown).
        Looks the state up by lead id; best-effort, never raises."""
        if not lead_id:
            return []
        try:
            from app.core.database import SessionLocal
            from app.models.lead import Lead
            from app.core.state_sender_numbers import numbers_for_state
            db = SessionLocal()
            try:
                lead = db.query(Lead).filter(Lead.id == lead_id).first()
                return numbers_for_state(lead.state) if lead else []
            finally:
                db.close()
        except Exception:
            return []

    def _full_fleet_pool(self) -> list:
        """Every DID we can send lead outreach from: all state-mapped numbers
        (FL + TX + …) plus any global ENGAGECLOUD_FROM_NUMBERS not already covered —
        deduped, E.164, with the reserved hiree numbers excluded. Used as the pool
        for leads whose state has no dedicated numbers so they round-robin across the
        WHOLE fleet instead of the single global number."""
        from app.core.applicant_numbers import is_applicant_number
        from app.core.state_sender_numbers import all_sender_numbers
        out: list = []
        seen: set = set()

        def _add(raw):
            e = self._e164(raw)
            if e and e not in seen and not is_applicant_number(e):
                seen.add(e)
                out.append(e)

        for n in all_sender_numbers():
            _add(n)
        for s in (self._cfg().from_numbers or "").replace(";", ",").split(","):
            if s.strip():
                _add(s.strip())
        return out

    @staticmethod
    def _digits_only(value: str) -> str:
        return "".join(ch for ch in str(value or "") if ch.isdigit())

    def _e164(self, value: str) -> str:
        digits = self._digits_only(value)
        return f"+{digits}" if digits else ""

    def _headers(self) -> Dict[str, str]:
        cfg = self._cfg()
        headers = {"Content-Type": "application/json"}
        if cfg.use_new_auth and cfg.agency_id:
            headers["X-EngageCloud-Agency-Id"] = cfg.agency_id
        if cfg.use_new_auth:
            headers["X-EngageCloud-Api-Key"] = cfg.api_key
            headers["X-EngageCloud-Api-Secret"] = cfg.api_secret
        return headers

    @staticmethod
    def _carrier_of(number: str) -> str:
        """Which carrier the chosen sender number belongs to (multi-carrier registry).
        Defaults to 'sinch' so behaviour is unchanged until more carriers are added."""
        try:
            from app.ai.services.carrier_registry import carrier_of
            return carrier_of(number)
        except Exception:
            return "sinch"

    def send_sms(
        self,
        to: str,
        body: str,
        tenant_id: str = None,
        lead_id: str = None,
        kind: str = "other",
    ) -> Dict[str, Any]:
        # ===== FIRST-TEMPLATE-ONLY LOCKDOWN (permanent) =====================
        # The ONLY message the platform may ever send is the first-template
        # outreach (kind="first_template"). Every other path — AI replies,
        # booking/slots, objections, follow-ups, reminders, nurture, post-call
        # and manual agent sends — is blocked HERE, the single provider
        # chokepoint. Fail-safe: any missing/blank/unknown kind => blocked.
        # (See app.core.sending.FIRST_TEMPLATE_ONLY.)
        if kind != "first_template":
            try:
                from app.core.sending import record_send_decision
                record_send_decision(kind, allowed=False)
            except Exception:
                pass
            return {
                "message_sid": None,
                "status": "failed",
                "to": to,
                "provider": self.provider_name,
                "error": "blocked_first_template_only",
                "blocked_by": "first_template_only",
            }
        try:
            from app.core.sending import record_send_decision
            record_send_decision(kind, allowed=True)
        except Exception:
            pass
        # Global kill-switch: when sending is paused for this tenant, send nothing.
        # This is the single provider chokepoint, so it stops outreach, AI replies,
        # follow-ups and reminders alike.
        try:
            from app.core.sending import is_sending_paused
            if is_sending_paused(tenant_id):
                return {
                    "message_sid": None,
                    "status": "failed",
                    "to": to,
                    "provider": self.provider_name,
                    "error": "sending_paused",
                    "blocked_by": "kill_switch",
                }
        except Exception:
            pass
        if not self.configured:
            return {
                "message_sid": None,
                "status": "failed",
                "to": to,
                "provider": self.provider_name,
                "error": "Engage Clouds credentials not configured",
            }
        try:
            # Pick the sender number first so we know which CARRIER it belongs to.
            # Multi-carrier failover happens INSIDE select_sender (it jumps to another
            # carrier's number when one is capped/unhealthy); here we record the
            # carrier and route the send. All currently-configured numbers send via
            # Engage Cloud (Sinch); a non-sinch carrier registers its own adapter
            # below WITHOUT touching the first-template lockdown above.
            source_number = self._sender(tenant_id=tenant_id, lead_id=lead_id)
            carrier = self._carrier_of(source_number)
            # DID-fleet caps / T-Mobile dedup / working-hours gate. OBSERVE-ONLY by
            # default — only blocks when an *_ENFORCE flag is on. Fail-OPEN (any error
            # -> the send proceeds) so the cap engine can never break sending; the
            # first-template lockdown above is untouched.
            try:
                from app.ai.services import carrier_caps
                _cap = carrier_caps.evaluate_send(provider=carrier, to_number=to)
                if not _cap.allowed:
                    return {
                        "message_sid": None,
                        "status": "skipped",
                        "to": to,
                        "from": source_number,
                        "carrier": carrier,
                        "provider": self.provider_name,
                        "error": None,
                        "blocked_by": _cap.blocked_by,
                    }
            except Exception:
                pass
            payload = {
                "messages": [
                    {
                        "content": body,
                        "destination_number": self._e164(to),
                        "source_number": source_number,
                    }
                ]
            }
            cfg = self._cfg()
            url = f"{cfg.base_url.rstrip('/')}/messages"
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    url,
                    json=payload,
                    headers=self._headers(),
                    auth=(cfg.api_key, cfg.api_secret),
                )
            response_data = self._json_response(response)
            if response.status_code >= 400:
                return {
                    "message_sid": None,
                    "status": "failed",
                    "to": to,
                    "provider": self.provider_name,
                    "error": response_data.get("error") or response.text,
                }

            response_messages = response_data.get("messages")
            first_message = response_messages[0] if isinstance(response_messages, list) and response_messages else {}
            message_id = (
                first_message.get("message_id")
                or first_message.get("messageId")
                or response_data.get("id")
                or response_data.get("message_id")
                or response_data.get("messageId")
            )
            result = {
                "message_sid": message_id,
                "status": first_message.get("status") or response_data.get("status") or "queued",
                "to": to,
                "from": source_number,
                "carrier": carrier,
                "provider": self.provider_name,
                "error": None,
                "raw": response_data,
            }
            if tenant_id:
                log_ai_action(
                    tenant_id=tenant_id,
                    action="sms_sent",
                    resource_type="lead",
                    resource_id=lead_id,
                    details={
                        "provider": self.provider_name,
                        "to": to,
                        "message_sid": message_id,
                        "status": result["status"],
                    },
                )
            # Count this real send toward the DID-fleet provider + T-Mobile totals
            # (best-effort, observe-only — never changes the result).
            try:
                from app.ai.services import carrier_caps
                carrier_caps.record_send(provider=carrier, to_number=to)
            except Exception:
                pass
            return result
        except Exception as exc:
            return {
                "message_sid": None,
                "status": "failed",
                "to": to,
                "provider": self.provider_name,
                "error": str(exc),
            }

    @staticmethod
    def _json_response(response: httpx.Response) -> Dict[str, Any]:
        try:
            data = response.json()
            return data if isinstance(data, dict) else {"data": data}
        except Exception:
            return {}

    def fetch_replies(self) -> Dict[str, Any]:
        if not self.configured:
            return {
                "success": False,
                "replies": [],
                "error": "Engage Clouds credentials not configured",
            }

        try:
            cfg = self._cfg()
            url = f"{cfg.base_url.rstrip('/')}/replies"
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(
                    url,
                    headers=self._headers(),
                    auth=(cfg.api_key, cfg.api_secret),
                )
            response_data = self._json_response(response)
            if response.status_code >= 400:
                return {
                    "success": False,
                    "replies": [],
                    "status_code": response.status_code,
                    "error": response_data.get("message") or response_data.get("error") or response.text,
                }
            replies = response_data.get("replies") or []
            if not isinstance(replies, list):
                replies = []
            return {
                "success": True,
                "replies": [self.parse_reply(reply) for reply in replies if isinstance(reply, dict)],
                "raw": response_data,
            }
        except Exception as exc:
            return {"success": False, "replies": [], "error": str(exc)}

    def confirm_replies(self, reply_ids: List[str]) -> Dict[str, Any]:
        reply_ids = [str(reply_id) for reply_id in reply_ids if reply_id]
        if not reply_ids:
            return {"success": True, "confirmed": 0}
        if not self.configured:
            return {
                "success": False,
                "confirmed": 0,
                "error": "Engage Clouds credentials not configured",
            }

        try:
            cfg = self._cfg()
            url = f"{cfg.base_url.rstrip('/')}/replies/confirmed"
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    url,
                    json={"reply_ids": reply_ids},
                    headers=self._headers(),
                    auth=(cfg.api_key, cfg.api_secret),
                )
            response_data = self._json_response(response)
            if response.status_code >= 400:
                return {
                    "success": False,
                    "confirmed": 0,
                    "status_code": response.status_code,
                    "error": response_data.get("message") or response_data.get("error") or response.text,
                }
            return {
                "success": True,
                "confirmed": len(reply_ids),
                "status_code": response.status_code,
            }
        except Exception as exc:
            return {"success": False, "confirmed": 0, "error": str(exc)}

    def parse_reply(self, reply: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "kind": "inbound_message",
            "reply_id": reply.get("reply_id") or reply.get("replyId") or reply.get("id"),
            "message_sid": reply.get("message_id") or reply.get("messageId") or reply.get("message_sid"),
            "from": reply.get("source_number") or reply.get("sourceNumber") or reply.get("from"),
            "to": reply.get("destination_number") or reply.get("destinationNumber") or reply.get("to"),
            "body": (reply.get("content") or reply.get("body") or reply.get("message") or "").strip(),
            "received_at": reply.get("date_received") or reply.get("dateReceived") or reply.get("created_at"),
            "raw": reply,
        }

    def validate_webhook(self, body: bytes, headers: Dict[str, str]) -> bool:
        secret = self._cfg().webhook_secret
        if not secret:
            return settings.APP_ENV != "production"

        normalized = {k.lower(): v for k, v in headers.items()}
        shared_secret = (
            normalized.get("x-engagecloud-webhook-secret")
            or normalized.get("x-engage-cloud-webhook-secret")
        )
        if shared_secret and hmac.compare_digest(shared_secret, secret):
            return True

        if not self._timestamp_is_fresh(normalized):
            return False

        signature = (
            normalized.get("x-engagecloud-signature")
            or normalized.get("x-engage-cloud-signature")
            or normalized.get("x-engage-signature")
        )
        if not signature:
            return False

        expected_raw = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
        expected_hex = expected_raw.hex()
        expected_b64 = b64encode(expected_raw).decode("utf-8")
        candidates = {signature, signature.removeprefix("sha256=")}
        for candidate in candidates:
            if hmac.compare_digest(candidate, expected_hex):
                return True
            if hmac.compare_digest(candidate, expected_b64):
                return True
            try:
                if hmac.compare_digest(b64decode(candidate), expected_raw):
                    return True
            except Exception:
                pass
        return False

    @staticmethod
    def _timestamp_is_fresh(headers: Dict[str, str], max_age_seconds: int = 300) -> bool:
        timestamp = (
            headers.get("x-engagecloud-timestamp")
            or headers.get("x-engage-cloud-timestamp")
            or headers.get("x-engage-timestamp")
        )
        if not timestamp:
            return True
        try:
            value = float(timestamp)
            if value > 10_000_000_000:
                value = value / 1000
            event_time = datetime.fromtimestamp(value, timezone.utc)
        except Exception:
            try:
                event_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except Exception:
                return False
        age = abs((datetime.now(timezone.utc) - event_time).total_seconds())
        return age <= max_age_seconds

    def replay_key(self, payload: Dict[str, Any]) -> Optional[str]:
        event_id = (
            payload.get("event_id")
            or payload.get("eventId")
            or payload.get("id")
            or payload.get("webhook_id")
            or payload.get("webhookId")
        )
        if event_id:
            return f"engage_cloud:webhook:{event_id}"
        message_id = self._message_id(payload)
        event_type = self._event_type(payload)
        if message_id and event_type:
            return f"engage_cloud:webhook:{event_type}:{message_id}"
        return None

    def mark_replay_seen(self, payload: Dict[str, Any], ttl: int = 86400) -> bool:
        key = self.replay_key(payload)
        if not key:
            return True
        try:
            return bool(redis_service.client.set(key, "1", nx=True, ex=ttl))
        except Exception:
            return True

    def mark_inbound_seen(self, from_number: str, body: str, ttl: int = 300) -> bool:
        """Shared content-based dedup for an inbound customer reply.

        The webhook and the reply-polling fallback used DIFFERENT replay keys, so
        the same reply could be processed by BOTH — causing duplicate AI replies
        and out-of-order ("mixed up") messages. Both paths now claim this single
        per-(phone, body) key first; only the first caller processes the reply.
        Returns True the first time a given reply is seen (within ttl seconds).
        """
        import hashlib
        norm = (from_number or "").strip()
        digest = hashlib.sha1(((body or "").strip().lower()).encode("utf-8")).hexdigest()[:16]
        key = f"inbound:dedup:{norm}:{digest}"
        try:
            return bool(redis_service.client.set(key, "1", nx=True, ex=ttl))
        except Exception:
            return True

    def parse_webhook(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        event_type = self._event_type(payload)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if self._is_delivery_event(event_type, data):
            return {
                "kind": "delivery_status",
                "message_sid": self._message_id(data),
                "status": self._status(data),
                "to": data.get("to") or data.get("To") or data.get("recipient") or data.get("phone") or data.get("destination_number"),
                "error_code": data.get("error_code") or data.get("errorCode") or data.get("ErrorCode"),
                "error_message": data.get("error_message") or data.get("ErrorMessage") or data.get("error") or data.get("reason"),
                "raw": payload,
            }
        return {
            "kind": "inbound_message",
            "from": data.get("from") or data.get("From") or data.get("from_number") or data.get("fromNumber") or data.get("source_number") or data.get("sender"),
            "to": data.get("to") or data.get("To") or data.get("to_number") or data.get("toNumber") or data.get("destination_number") or data.get("recipient"),
            "body": self._inbound_body(data),
            "message_sid": self._message_id(data),
            "thread_id": data.get("thread_id") or data.get("threadId") or data.get("conversation_id") or data.get("conversationId"),
            "raw": payload,
        }

    # Velocity/handlebars placeholders that the provider failed to substitute,
    # e.g. "$esc.json($moContent)" or "${content}". If the webhook payload
    # template in Sinch isn't rendered, the literal template text arrives as the
    # "message" — we must NOT store that as the customer's reply.
    _TEMPLATE_RE = re.compile(r"^\s*\$\{?[A-Za-z_][\w.]*\}?(\s*\(.*\))?\s*$")

    @classmethod
    def _looks_like_unrendered_template(cls, value) -> bool:
        if not isinstance(value, str):
            return False
        s = value.strip()
        if not s:
            return False
        return (
            "$esc." in s
            or "$moContent" in s
            or "${" in s
            or bool(cls._TEMPLATE_RE.match(s))
        )

    @classmethod
    def _inbound_body(cls, data: Dict[str, Any]) -> str:
        """First real message field, skipping unrendered provider templates."""
        for key in ("body", "Body", "message", "text", "content"):
            val = data.get(key)
            if isinstance(val, str) and val.strip() and not cls._looks_like_unrendered_template(val):
                return val.strip()
        # Everything was empty or an unrendered template (Sinch webhook payload
        # misconfigured). Return "" so the garbage never reaches the agent.
        return ""

    @staticmethod
    def _event_type(payload: Dict[str, Any]) -> str:
        return str(
            payload.get("event")
            or payload.get("event_type")
            or payload.get("eventType")
            or payload.get("type")
            or ""
        )

    @staticmethod
    def _message_id(payload: Dict[str, Any]) -> str:
        return str(
            payload.get("message_sid")
            or payload.get("messageSid")
            or payload.get("message_id")
            or payload.get("messageId")
            or payload.get("MessageSid")
            or payload.get("SmsSid")
            or payload.get("id")
            or ""
        )

    @staticmethod
    def _status(payload: Dict[str, Any]) -> str:
        return str(
            payload.get("status")
            or payload.get("delivery_status")
            or payload.get("deliveryStatus")
            or payload.get("message_status")
            or payload.get("messageStatus")
            or payload.get("MessageStatus")
            or ""
        ).lower()

    def _is_delivery_event(self, event_type: str, payload: Dict[str, Any]) -> bool:
        marker = event_type.lower()
        return (
            "delivery" in marker
            or marker.endswith(".delivered")
            or marker.endswith(".failed")
            or self._status(payload) in {"queued", "sent", "delivered", "failed", "undelivered"}
        )


communication_service = EngageCloudService()                       # provider "sinch" (the original)
engage2_service = EngageCloudService(provider="engage2")           # provider "engage2" (independent)

# Map a provider key -> its service instance. Defaults to the Sinch service so any
# unknown/blank provider can never divert traffic off Sinch.
_SERVICES = {"sinch": communication_service, "engage2": engage2_service}


def get_sms_service(provider: str = "sinch"):
    from app.ai.services.sms_providers import normalize_provider
    return _SERVICES.get(normalize_provider(provider), communication_service)


def send_sms_to_lead(
    phone: str,
    message: str,
    tenant_id: str,
    lead_id: str,
    rate_limit_scope: str = "marketing",
    kind: str = "other",
    campaign_id: str = None,
    provider: str = "sinch",
) -> Dict[str, Any]:
    # Route to the selected provider's pipeline ("sinch" default = unchanged).
    svc = get_sms_service(provider)
    max_requests = 3 if rate_limit_scope == "marketing" else 10
    rate_key = f"sms:{rate_limit_scope}:{tenant_id}:{lead_id}"
    try:
        allowed = redis_service.check_rate_limit(rate_key, max_requests=max_requests, window_seconds=86400)
    except Exception:
        allowed = True
    if not allowed:
        return {
            "success": False,
            "error": f"Rate limit exceeded for this lead (max {max_requests} {rate_limit_scope} messages per day)",
        }

    # Outreach fatigue (P4): per-phone frequency cap + cooldown across campaigns, so
    # a person isn't over-texted on re-runs. A capped/cooling lead is a no-op SUCCESS
    # (the queue consumes the job, no retry). Checked before the send-once claim so a
    # skipped lead never leaks a claim.
    if kind == "first_template":
        from app.core.fatigue import fatigue_ok
        if not fatigue_ok(phone):
            return {
                "success": True,
                "suppressed": True,
                "status": "fatigue_capped",
                "provider": svc.provider_name,
            }

    # First-template send-once PER CAMPAIGN: a number gets the first template at
    # most once per campaign (non-campaign sends share one bucket), so duplicate
    # lead-rows in the same campaign can't re-text it. Claim before sending so
    # concurrent drip waves can't double-send; a suppressed duplicate is a no-op
    # SUCCESS so the queue consumes the job without retrying it.
    if kind == "first_template":
        from app.core.send_once import claim_first_template_send, record_duplicate_suppressed
        if not claim_first_template_send(tenant_id, campaign_id, phone):
            record_duplicate_suppressed()
            return {
                "success": True,
                "suppressed": True,
                "status": "suppressed_duplicate",
                "provider": svc.provider_name,
            }

    result = svc.send_sms(
        to=phone,
        body=message,
        tenant_id=tenant_id,
        lead_id=lead_id,
        kind=kind,
    )
    # Record the send outcome on the chosen number -> per-number health + per-carrier
    # circuit breaker (CD). result["from"] is the DID picked by select_sender; absent
    # on a blocked/kill-switched send, where record_result no-ops. Provider-namespaced
    # so the 2nd provider's health is independent of Sinch.
    try:
        from app.ai.services.sender_pool import record_result
        record_result(result.get("from"), not bool(result.get("error")), provider=provider)
    except Exception:
        pass
    if result.get("error"):
        if kind == "first_template":
            # The send genuinely failed — release the claim so a retry can re-send.
            from app.core.send_once import release_first_template_claim
            release_first_template_claim(tenant_id, campaign_id, phone)
        return {
            "success": False,
            "error": result["error"],
            "provider": svc.provider_name,
            "status": result.get("status"),
        }
    if kind == "first_template":
        from app.core.fatigue import fatigue_record
        fatigue_record(phone)   # real send -> bump count + start cooldown
    return {
        "success": True,
        "message_sid": result.get("message_sid"),
        "status": result.get("status"),
        "provider": svc.provider_name,
    }
