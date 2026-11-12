"""HealthSherpa text campaigns

Revision ID: 068
Revises: 067
Create Date: 2026-11-12

* hs_text_campaigns: a CSV of clients + one blast message, texted through the
  Telnyx sender; replies never reach the dialer (app/hs_auth/campaigns.py).
* hs_campaign_texts: every text of those campaigns, both ways.
* hs_auth_requests: the conversation columns for campaign rows.
Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "068"
down_revision: Union[str, None] = "067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REQ_COLS = (
    ("convo", sa.String(16)),
    ("convo_detail", sa.Text()),
    ("yes_text", sa.Text()),
    ("yes_at", sa.DateTime(timezone=True)),
    ("followup_sent_at", sa.DateTime(timezone=True)),
    ("renewing_company", sa.Text()),
    ("answered_at", sa.DateTime(timezone=True)),
    ("reminder_sent_at", sa.DateTime(timezone=True)),
    ("last_text_at", sa.DateTime(timezone=True)),
)


def _tables() -> set:
    return set(inspect(op.get_bind()).get_table_names())


def _cols(table: str) -> set:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    uid = postgresql.UUID(as_uuid=True)
    tz = sa.DateTime(timezone=True)
    if "hs_text_campaigns" not in _tables():
        op.create_table(
            "hs_text_campaigns",
            sa.Column("id", uid, primary_key=True),
            sa.Column("tenant_id", uid, sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("template", sa.Text(), nullable=False),
            sa.Column("status", sa.String(12), nullable=False, server_default="DRAFT"),
            sa.Column("pause_reason", sa.Text(), nullable=True),
            sa.Column("file_name", sa.String(255), nullable=True),
            sa.Column("hs_agent_id", uid, sa.ForeignKey("users.id"), nullable=False),
            sa.Column("batch_id", uid, sa.ForeignKey("hs_auth_batches.id"), nullable=True),
            sa.Column("rows_in_file", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped_invalid", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped_no_phone", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped_duplicate", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped_dnc", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("answer_wait_minutes", sa.Integer(), nullable=False, server_default="10"),
            sa.Column("reminder_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("reminder_text", sa.Text(), nullable=True),
            sa.Column("reminder_minutes", sa.Integer(), nullable=False, server_default="7"),
            sa.Column("expire_minutes", sa.Integer(), nullable=False, server_default="15"),
            sa.Column("resend_on_expiry", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_by", uid, sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", tz, nullable=False, server_default=sa.func.now()),
            sa.Column("started_at", tz, nullable=True),
            sa.Column("finished_at", tz, nullable=True),
        )
        op.create_index("ix_hs_text_campaigns_tenant_id", "hs_text_campaigns", ["tenant_id"])

    have = _cols("hs_auth_requests")
    if "campaign_id" not in have:
        op.add_column("hs_auth_requests", sa.Column(
            "campaign_id", uid, sa.ForeignKey("hs_text_campaigns.id", ondelete="SET NULL"), nullable=True))
        op.create_index("ix_hs_auth_requests_campaign_id", "hs_auth_requests", ["campaign_id"])
    for name, typ in _REQ_COLS:
        if name not in have:
            op.add_column("hs_auth_requests", sa.Column(name, typ, nullable=True))

    if "hs_campaign_texts" not in _tables():
        op.create_table(
            "hs_campaign_texts",
            sa.Column("id", uid, primary_key=True),
            sa.Column("tenant_id", uid, sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("campaign_id", uid, sa.ForeignKey("hs_text_campaigns.id", ondelete="CASCADE"), nullable=False),
            sa.Column("request_id", uid, sa.ForeignKey("hs_auth_requests.id", ondelete="CASCADE"), nullable=False),
            sa.Column("phone_number", sa.String(32), nullable=False),
            sa.Column("direction", sa.String(3), nullable=False),
            sa.Column("kind", sa.String(10), nullable=False),
            sa.Column("body", sa.Text(), nullable=True),
            sa.Column("from_number", sa.String(32), nullable=True),
            sa.Column("status", sa.String(10), nullable=False, server_default="QUEUED"),
            sa.Column("reply_kind", sa.String(12), nullable=True),
            sa.Column("provider_message_id", sa.String(64), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", tz, nullable=False, server_default=sa.func.now()),
            sa.Column("sent_at", tz, nullable=True),
            sa.Column("delivered_at", tz, nullable=True),
        )
        op.create_index("ix_hs_campaign_texts_request", "hs_campaign_texts", ["request_id", "created_at"])
        op.create_index("ix_hs_campaign_texts_phone", "hs_campaign_texts", ["tenant_id", "phone_number", "sent_at"])
        op.create_index("ix_hs_campaign_texts_queue", "hs_campaign_texts", ["tenant_id", "status"])
        op.create_index("ix_hs_campaign_texts_message", "hs_campaign_texts", ["provider_message_id"])


def downgrade() -> None:
    if "hs_campaign_texts" in _tables():
        op.drop_table("hs_campaign_texts")
    have = _cols("hs_auth_requests")
    for name, _typ in _REQ_COLS:
        if name in have:
            op.drop_column("hs_auth_requests", name)
    if "campaign_id" in have:
        op.drop_index("ix_hs_auth_requests_campaign_id", table_name="hs_auth_requests")
        op.drop_column("hs_auth_requests", "campaign_id")
    if "hs_text_campaigns" in _tables():
        op.drop_table("hs_text_campaigns")
