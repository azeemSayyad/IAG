"""text campaigns: Telnyx text blasts from the Auto Dialer page

Revision ID: 066
Revises: 065
Create Date: 2026-11-10

* text_campaigns: one CSV + one message, sent in paced batches.
* text_campaign_recipients: one row per person — what they got, delivery,
  and their first reply.
Separate from the SMS Manager's `campaigns` table. Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "066"
down_revision: Union[str, None] = "065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    uid = postgresql.UUID(as_uuid=True)
    if "text_campaigns" not in _tables():
        op.create_table(
            "text_campaigns",
            sa.Column("id", uid, primary_key=True),
            sa.Column("tenant_id", uid, sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("template", sa.Text(), nullable=False),
            sa.Column("status", sa.String(12), nullable=False, server_default="DRAFT"),
            sa.Column("pause_reason", sa.Text(), nullable=True),
            sa.Column("file_name", sa.String(255), nullable=True),
            sa.Column("rows_in_file", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped_invalid", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped_duplicate", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped_dnc", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_by", uid, sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("idx_text_campaigns_tenant_created", "text_campaigns", ["tenant_id", "created_at"])
    if "text_campaign_recipients" not in _tables():
        op.create_table(
            "text_campaign_recipients",
            sa.Column("id", uid, primary_key=True),
            sa.Column("campaign_id", uid, sa.ForeignKey("text_campaigns.id", ondelete="CASCADE"), nullable=False),
            sa.Column("tenant_id", uid, sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("phone_number", sa.String(32), nullable=False),
            sa.Column("first_name", sa.String(120), nullable=True),
            sa.Column("state", sa.String(4), nullable=True),
            sa.Column("fields", postgresql.JSONB(), nullable=True),
            sa.Column("status", sa.String(12), nullable=False, server_default="QUEUED"),
            sa.Column("from_number", sa.String(32), nullable=True),
            sa.Column("body", sa.Text(), nullable=True),
            sa.Column("provider_message_id", sa.String(64), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reply_text", sa.Text(), nullable=True),
            sa.Column("reply_kind", sa.String(12), nullable=True),
        )
        op.create_index("idx_text_recipients_campaign_status", "text_campaign_recipients", ["campaign_id", "status"])
        op.create_index("idx_text_recipients_phone", "text_campaign_recipients", ["tenant_id", "phone_number"])
        op.create_index("idx_text_recipients_message", "text_campaign_recipients", ["provider_message_id"])


def downgrade() -> None:
    for t in ("text_campaign_recipients", "text_campaigns"):
        if t in _tables():
            op.drop_table(t)
