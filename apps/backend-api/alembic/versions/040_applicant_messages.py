"""inbox messages (admin↔ hiree/user SMS)

Revision ID: 040
Revises: 039
Create Date: 2026-06-23

Adds the applicant_messages table powering the admin/dev Inbox: a per-contact SMS
thread between admins and either a job applicant (hiree) or a portal user. The
contact is polymorphic — contact_type is 'hiree' or 'user' and exactly one of
hiree_id / user_id is set. Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import inspect


revision: str = "040"
down_revision: Union[str, None] = "039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return name in inspect(bind).get_table_names()


def upgrade() -> None:
    if _has_table("applicant_messages"):
        return
    op.create_table(
        "applicant_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        # Polymorphic contact: 'hiree' | 'user' (exactly one id below is set).
        sa.Column("contact_type", sa.String(length=10), nullable=False, server_default="hiree"),
        sa.Column("hiree_id", UUID(as_uuid=True), sa.ForeignKey("hiree_onboarding.id"), nullable=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("phone_number", sa.String(length=50), nullable=True),
        sa.Column("from_number", sa.String(length=50), nullable=True),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("sender_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="SENT"),
        sa.Column("sent_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_applicant_messages_tenant_id", "applicant_messages", ["tenant_id"])
    op.create_index("idx_applicant_messages_hiree_created", "applicant_messages", ["hiree_id", "created_at"])
    op.create_index("idx_applicant_messages_user_created", "applicant_messages", ["user_id", "created_at"])
    op.create_index("idx_applicant_messages_tenant_created", "applicant_messages", ["tenant_id", "created_at"])


def downgrade() -> None:
    if _has_table("applicant_messages"):
        op.drop_table("applicant_messages")
