"""Work contact book — the numbers an admin needs to hand.

Deliberately NOT the same thing as a `Lead` (a customer being sold to) or a
`User`/`Agent` (someone with a login). This is the internal phone book: agents,
staff, vendors, carrier reps — anyone you might need to ring. Free-form on
purpose, so a number can be saved before the person exists anywhere else.

Soft-deleted (`deleted_at`) so removing a contact can be undone and never
orphans anything that referenced it.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)

    # Name is the only thing required — a contact with no name can't be found
    # again. Everything else is optional so a number can be captured in seconds.
    name = Column(String(120), nullable=False)
    phone = Column(String(40), nullable=True)
    email = Column(String(255), nullable=True)
    # Free text, not an enum: "Agent", "Developer", "Carrier rep", "Landlord".
    role = Column(String(80), nullable=True)
    notes = Column(Text, nullable=True)

    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc),
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_contacts_tenant_name", "tenant_id", "name"),
    )
