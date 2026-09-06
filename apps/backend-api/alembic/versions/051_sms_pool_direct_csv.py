"""SMS pool — direct CSV uploads (no SMS sent)

Adds a second way for a lead to enter the SMS agent pool: an admin uploads a
CSV and the rows go STRAIGHT to sms_leads as QUEUED — no first template, no
Sinch, no reply needed. Agents work these by phone.

- sms_pool_batches: one row per upload (filename, counters, the extra column
  labels the file carried) so a batch shows as a card and can be removed as one.
- sms_leads.source: 'REPLY' (existing flow, backfilled) | 'CSV_DIRECT'.
- sms_leads.batch_id: FK to the batch (CSV_DIRECT only).
- sms_leads.details: JSONB — the rest of the CSV row for the agent's lead card.

Idempotent: safe to re-run against a DB that already has any of these.

Revision ID: 051
Revises: 050
Create Date: 2026-09-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "051"
down_revision: Union[str, None] = "050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspect(op.get_bind()).get_columns(table))


def _has_index(table: str, name: str) -> bool:
    return any(i["name"] == name for i in inspect(op.get_bind()).get_indexes(table))


def upgrade() -> None:
    if not _has_table("sms_pool_batches"):
        op.create_table(
            "sms_pool_batches",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("imported", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped_duplicates", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped_dnc", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("columns", postgresql.JSONB(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("idx_sms_pool_batches_tenant_created", "sms_pool_batches", ["tenant_id", "created_at"])

    if not _has_column("sms_leads", "source"):
        op.add_column(
            "sms_leads",
            sa.Column("source", sa.String(20), nullable=False, server_default="REPLY"),
        )
    if not _has_column("sms_leads", "batch_id"):
        op.add_column(
            "sms_leads",
            sa.Column("batch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sms_pool_batches.id"), nullable=True),
        )
    if not _has_column("sms_leads", "details"):
        op.add_column("sms_leads", sa.Column("details", postgresql.JSONB(), nullable=True))

    if not _has_index("sms_leads", "idx_sms_leads_tenant_source"):
        op.create_index("idx_sms_leads_tenant_source", "sms_leads", ["tenant_id", "source"])
    if not _has_index("sms_leads", "idx_sms_leads_batch"):
        op.create_index("idx_sms_leads_batch", "sms_leads", ["batch_id"])


def downgrade() -> None:
    if _has_index("sms_leads", "idx_sms_leads_batch"):
        op.drop_index("idx_sms_leads_batch", table_name="sms_leads")
    if _has_index("sms_leads", "idx_sms_leads_tenant_source"):
        op.drop_index("idx_sms_leads_tenant_source", table_name="sms_leads")
    for col in ("details", "batch_id", "source"):
        if _has_column("sms_leads", col):
            op.drop_column("sms_leads", col)
    if _has_table("sms_pool_batches"):
        op.drop_table("sms_pool_batches")
