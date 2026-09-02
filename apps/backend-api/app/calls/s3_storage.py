"""
Permanent recording storage in OUR OWN S3 bucket.

Flow: call ends -> Sinch produces a recording -> Launchpad downloads it ->
Launchpad uploads it to S3 (permanent) -> we keep the S3 key as the system of
record. Sinch is NEVER the permanent store.

All settings are env-driven (AWS_S3_BUCKET / AWS_S3_REGION / AWS_ACCESS_KEY_ID /
AWS_SECRET_ACCESS_KEY). When unconfigured the service reports configured == False
and callers degrade gracefully (recording_status stays "pending").
"""
from __future__ import annotations

import logging
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class S3RecordingStorage:
    @property
    def bucket(self) -> str:
        return (settings.S3_BUCKET or settings.AWS_S3_BUCKET or "").strip()

    @property
    def region(self) -> str:
        return (settings.AWS_REGION or settings.AWS_S3_REGION or "us-east-1").strip()

    @property
    def prefix(self) -> str:
        return (settings.S3_RECORDINGS_PREFIX or "call-recordings").strip("/")

    def configured(self) -> bool:
        return bool(self.bucket and settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY)

    def _client(self):
        import boto3
        return boto3.client(
            "s3",
            region_name=self.region,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )

    def key_for(self, tenant_id: str, call_id: str, ext: str = "mp3") -> str:
        return f"{self.prefix}/{tenant_id}/{call_id}.{ext}"

    def download(self, url: str, headers: Optional[dict] = None) -> bytes:
        """Download the recording bytes from Sinch (or any URL)."""
        import requests
        resp = requests.get(url, headers=headers or {}, timeout=60)
        resp.raise_for_status()
        return resp.content

    def upload_bytes(self, data: bytes, key: str, content_type: str = "audio/mpeg") -> dict:
        """Upload bytes to S3 permanently (no expiry). Returns {bucket, key}.

        Raises RuntimeError if S3 isn't configured.
        """
        if not self.configured():
            raise RuntimeError("S3 is not configured (set AWS_S3_BUCKET / AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)")
        self._client().put_object(
            Bucket=self.bucket, Key=key, Body=data,
            ContentType=content_type, ServerSideEncryption="AES256",
        )
        logger.info("Stored recording s3://%s/%s (%d bytes)", self.bucket, key, len(data))
        return {"bucket": self.bucket, "key": key}

    def store_from_url(self, url: str, tenant_id: str, call_id: str,
                       headers: Optional[dict] = None) -> dict:
        """Download from Sinch and upload to our S3. One call = whole flow."""
        data = self.download(url, headers=headers)
        key = self.key_for(tenant_id, call_id)
        return self.upload_bytes(data, key)

    def signed_url(self, key: str, expires_seconds: int = 3600) -> Optional[str]:
        """Time-limited GET URL for playback (recordings are never public)."""
        if not self.configured() or not key:
            return None
        return self._client().generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_seconds,
        )


s3_storage = S3RecordingStorage()
