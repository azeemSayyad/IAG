import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ApplicantMessage(Base):
    """A single SMS in an admin↔contact conversation shown in the Inbox.

    The contact is polymorphic: ``contact_type`` is 'hiree' (a job applicant in
    hiree_onboarding) or 'user' (a portal user). Exactly one of ``hiree_id`` /
    ``user_id`` is set. One thread per contact.

    Outbound = admin → contact; inbound = contact → admin (delivered by the SMS
    webhook once a live provider is wired). No live provider yet: outbound rows
    are recorded locally with settings.APPLICANT_SMS_FROM_NUMBER as the sender.
    """

    __tablename__ = "applicant_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)

    # Polymorphic contact: 'hiree' | 'user'
    contact_type = Column(String(10), nullable=False, default="hiree")
    hiree_id = Column(UUID(as_uuid=True), ForeignKey("hiree_onboarding.id"), nullable=True, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)

    phone_number = Column(String(50), nullable=True)   # the contact's number
    from_number = Column(String(50), nullable=True)     # the sending DID (env-configured)
    # INBOUND | OUTBOUND
    direction = Column(String(10), nullable=False)
    body = Column(Text, nullable=False)
    # ADMIN | APPLICANT | USER
    sender_type = Column(String(20), nullable=False)
    # PENDING | SENT | DELIVERED | FAILED | RECEIVED
    status = Column(String(20), nullable=False, default="SENT")
    sent_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("idx_applicant_messages_hiree_created", "hiree_id", "created_at"),
        Index("idx_applicant_messages_user_created", "user_id", "created_at"),
        Index("idx_applicant_messages_tenant_created", "tenant_id", "created_at"),
    )
