import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


class ApiKey(Base):
    """A hashed, scoped, revocable API key for programmatic access.

    The plaintext (``ek_live_<base62>``) is shown ONCE at creation; only its
    SHA-256 hash is stored, so a leaked DB never reveals a usable key.

    ``tier='master'`` (scopes ``["*"]``) passes every scope check; ``tier='scoped'``
    passes only the scope tags in ``scopes``. The key is pinned to ONE ``tenant_id``
    and acts within that tenant exactly like a JWT user of that tenant — full
    *scope* access, never cross-tenant.
    """

    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_hash = Column(Text, nullable=False)              # SHA-256 hex of the full key
    key_prefix = Column(String(16), nullable=False, index=True)  # first chars, for display + lookup
    name = Column(String(120), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    tier = Column(String(16), nullable=False, default="scoped")  # master | scoped
    scopes = Column(JSONB, nullable=False, default=list)         # ["*"] for master
    status = Column(String(16), nullable=False, default="active")  # active | revoked
    rate_limit_per_min = Column(Integer, nullable=False, default=120)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(String(120), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
