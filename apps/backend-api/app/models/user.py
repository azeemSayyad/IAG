import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    email = Column(String(255), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    role = Column(String(50), nullable=False, default="agent")
    status = Column(String(50), nullable=False, default="active")
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    failed_login_attempts = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    preferences = Column(JSON, nullable=True, default=dict)
    # Profile photo: S3 object (bucket/key) when S3 is configured — the same
    # client as training videos and deal consent forms (app/auth/avatar.py).
    # `avatar_url` is only the inline data-URL fallback when S3 isn't set up.
    avatar_url = Column(Text, nullable=True)
    avatar_s3_bucket = Column(String(255), nullable=True)
    avatar_s3_key = Column(String(512), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    tenant = relationship("Tenant", back_populates="users")
    agent = relationship("Agent", back_populates="user", uselist=False)
