"""SignWell e-signature client for the onboarding agreement + W-9.

The onboarding form is browser-only until the final Submit, so signing happens
PRE-submit and is client-driven (no hiree row, no public webhook):

  1. /onboarding/esign/create  -> create_embedded_document()  -> {document_id, embedded_url}
  2. agent signs in the iframe (SignWell embedded flow)
  3. /onboarding/esign/finalize -> fetch_completed_pdf()      -> signed PDF bytes
     (agreement is then counter-stamped with the agency signature) -> stored as
     an OnboardingDocument, exactly like an ID upload.

All settings are env-driven (SIGNWELL_API_KEY / *_TEMPLATE_ID). When the key is
unconfigured the service reports configured == False and callers return a clear
error instead of calling out.

Docs: https://developers.signwell.com/reference/post_api-v1-document-templates-documents
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Template field api_ids must match the names placed in the SignWell templates.
AGREEMENT = "agreement"
W9 = "w9"


class SignWellError(RuntimeError):
    """Any failure talking to SignWell (network, auth, or 4xx/5xx)."""


def _is_ymd(v) -> bool:
    return isinstance(v, str) and len(v) == 10 and v[4] == "-" and v[7] == "-"


class SignWellClient:
    @property
    def base_url(self) -> str:
        return (settings.SIGNWELL_BASE_URL or "https://www.signwell.com/api/v1").rstrip("/")

    def configured(self) -> bool:
        return bool((settings.SIGNWELL_API_KEY or "").strip())

    def template_id(self, doc_type: str) -> str:
        tid = (settings.SIGNWELL_AGREEMENT_TEMPLATE_ID if doc_type == AGREEMENT
               else settings.SIGNWELL_W9_TEMPLATE_ID)
        if not tid:
            raise SignWellError(f"No SignWell template configured for '{doc_type}'")
        return tid

    def _headers(self) -> dict:
        return {"X-Api-Key": settings.SIGNWELL_API_KEY.strip(), "Accept": "application/json"}

    # -- API calls --------------------------------------------------------- #
    def create_embedded_document(
        self,
        doc_type: str,
        signer_name: str,
        signer_email: str,
        template_fields: list[dict],
    ) -> dict:
        """Create a document from a template with prefilled fields and return the
        embedded signing URL. Returns {document_id, embedded_url}."""
        if not self.configured():
            raise SignWellError("SignWell is not configured (set SIGNWELL_API_KEY)")

        tid = self.template_id(doc_type)
        # Keep only fields that actually exist in the template (a missing/renamed
        # field would 422 the whole request), and format dates per field type:
        # SignWell `date` fields need a full ISO8601 datetime; `text` fields keep
        # the plain YYYY-MM-DD we were given.
        field_types = self.template_field_types(tid)
        if field_types:
            cleaned = []
            for f in template_fields:
                aid = f.get("api_id")
                if aid not in field_types:
                    continue
                val = f.get("value")
                if _is_ymd(val):
                    if field_types[aid] == "date":
                        val = f"{val}T00:00:00Z"          # real date field: ISO datetime
                    elif field_types[aid] == "text":
                        val = f"{val[5:7]}/{val[8:10]}/{val[0:4]}"  # text field: MM/DD/YYYY
                cleaned.append({"api_id": aid, "value": val})
            template_fields = cleaned

        body = {
            "template_id": tid,
            "test_mode": bool(settings.SIGNWELL_TEST_MODE),
            "embedded_signing": True,
            "embedded_signing_notifications": False,
            "draft": False,
            "recipients": [{
                "id": "agent",
                "placeholder_name": settings.SIGNWELL_PLACEHOLDER_NAME or "Agent",
                "name": signer_name or "Agent",
                "email": signer_email or "",
            }],
            "template_fields": template_fields,  # flat [{api_id, value}, ...]
        }
        # Embedded signing is usually scoped to an API application (whitelisted
        # domains); include it when configured.
        if (settings.SIGNWELL_API_APP_ID or "").strip():
            body["api_application_id"] = settings.SIGNWELL_API_APP_ID.strip()
        data = self._post("/document_templates/documents", body)

        recipients = data.get("recipients") or []
        embedded_url = recipients[0].get("embedded_signing_url") if recipients else None
        document_id = data.get("id")
        if not document_id or not embedded_url:
            raise SignWellError("SignWell did not return an embedded signing URL")
        return {"document_id": document_id, "embedded_url": embedded_url}

    def template_field_types(self, template_id: str) -> dict:
        """Map of {api_id: field_type} for a template (e.g. text/date/checkbox/
        signature). Best-effort: returns {} on any failure so we fall back to
        sending all fields unchanged."""
        try:
            data = self._get(f"/document_templates/{template_id}")
        except SignWellError:
            return {}
        out: dict = {}

        def _walk(node):
            if isinstance(node, dict):
                if "api_id" in node and "type" in node:
                    out[node["api_id"]] = node.get("type")
                for v in node.values():
                    _walk(v)
            elif isinstance(node, list):
                for v in node:
                    _walk(v)

        _walk(data.get("fields"))
        return out

    def get_document(self, document_id: str) -> dict:
        return self._get(f"/documents/{document_id}")

    def is_completed(self, document_id: str) -> bool:
        return (self.get_document(document_id).get("status") or "").lower() == "completed"

    def fetch_completed_pdf(self, document_id: str, attempts: int = 15, delay: float = 2.0) -> bytes:
        """Download the signed PDF bytes once the document is completed.

        Two things lag after the embed 'completed' event: (1) the document
        'status' becoming 'completed', and (2) the signed PDF actually being
        generated (the completed_pdf endpoint 404s until then). So we poll for
        BOTH — each round we re-check status and, once completed, try the
        download; a 404/4xx on the PDF just means "not generated yet, retry".
        Raises SignWellError if neither is ready in time (caller returns 409)."""
        pdf_url = f"{self.base_url}/documents/{document_id}/completed_pdf/"
        headers = {"X-Api-Key": settings.SIGNWELL_API_KEY.strip()}
        last_status = ""
        last_err = ""
        for i in range(max(1, attempts)):
            last_status = (self.get_document(document_id).get("status") or "").lower()
            if last_status == "completed":
                try:
                    resp = httpx.get(pdf_url, headers=headers, timeout=60)
                except httpx.HTTPError as exc:
                    last_err = str(exc)
                else:
                    if resp.status_code < 400:
                        return resp.content
                    last_err = f"completed_pdf {resp.status_code}"
            if i < attempts - 1:
                time.sleep(delay)
        raise SignWellError(
            f"Signed PDF not ready for {document_id} (status={last_status!r}, last_err={last_err!r})"
        )

    # -- low-level --------------------------------------------------------- #
    def _post(self, path: str, body: dict) -> dict:
        try:
            resp = httpx.post(self.base_url + path, json=body, headers=self._headers(), timeout=30)
        except httpx.HTTPError as exc:  # network/timeout
            raise SignWellError(f"SignWell request failed: {exc}") from exc
        return self._json_or_raise(resp)

    def _get(self, path: str) -> dict:
        try:
            resp = httpx.get(self.base_url + path, headers=self._headers(), timeout=30)
        except httpx.HTTPError as exc:
            raise SignWellError(f"SignWell request failed: {exc}") from exc
        return self._json_or_raise(resp)

    @staticmethod
    def _json_or_raise(resp: httpx.Response) -> dict:
        if resp.status_code >= 400:
            detail = resp.text[:500]
            logger.warning("SignWell %s -> %s: %s", resp.request.url, resp.status_code, detail)
            raise SignWellError(f"SignWell returned {resp.status_code}: {detail}")
        try:
            return resp.json()
        except ValueError as exc:
            raise SignWellError("SignWell returned a non-JSON response") from exc


signwell = SignWellClient()
