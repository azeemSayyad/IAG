"""HealthSherpa 2027 authorizations (Chrome extension)

Revision ID: 067
Revises: 066
Create Date: 2026-11-10

* hs_auth_batches: one uploaded sheet of clients.
* hs_auth_requests: one client to authorize + the status the extension reports.
* hs_auth_runs: one row per Start press — what the daily limit counts.
Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "067"
down_revision: Union[str, None] = "066"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    uid = postgresql.UUID(as_uuid=True)
    tz = sa.DateTime(timezone=True)
    if "hs_auth_batches" not in _tables():
        op.create_table(
            "hs_auth_batches",
            sa.Column("id", uid, primary_key=True),
            sa.Column("tenant_id", uid, sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("uploaded_by", uid, sa.ForeignKey("users.id"), nullable=True),
            sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("invalid_rows", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", tz, nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_hs_auth_batches_tenant_id", "hs_auth_batches", ["tenant_id"])
    if "hs_auth_requests" not in _tables():
        op.create_table(
            "hs_auth_requests",
            sa.Column("id", uid, primary_key=True),
            sa.Column("tenant_id", uid, sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("batch_id", uid, sa.ForeignKey("hs_auth_batches.id"), nullable=True),
            sa.Column("row_number", sa.Integer(), nullable=True),
            sa.Column("first_name", sa.String(80), nullable=False, server_default=""),
            sa.Column("last_name", sa.String(80), nullable=False, server_default=""),
            sa.Column("dob", sa.Date(), nullable=True),
            sa.Column("state", sa.String(2), nullable=True),
            sa.Column("phone", sa.String(20), nullable=True),
            sa.Column("email", sa.String(200), nullable=True),
            sa.Column("assigned_to", uid, sa.ForeignKey("users.id"), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
            sa.Column("status_detail", sa.Text(), nullable=True),
            sa.Column("step", sa.String(40), nullable=True),
            sa.Column("hs_app_id", sa.String(40), nullable=True),
            sa.Column("link_sends", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("runs", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("started_by", uid, sa.ForeignKey("users.id"), nullable=True),
            sa.Column("started_at", tz, nullable=True),
            sa.Column("link_sent_at", tz, nullable=True),
            sa.Column("authorized_at", tz, nullable=True),
            sa.Column("finished_at", tz, nullable=True),
            sa.Column("last_report_at", tz, nullable=True),
            sa.Column("job_token_hash", sa.String(64), nullable=True),
            sa.Column("events", postgresql.JSONB(), nullable=True),
            sa.Column("created_at", tz, nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", tz, nullable=False, server_default=sa.func.now()),
            sa.Column("deleted_at", tz, nullable=True),
        )
        op.create_index("ix_hs_auth_requests_tenant_id", "hs_auth_requests", ["tenant_id"])
        op.create_index("ix_hs_auth_requests_batch_id", "hs_auth_requests", ["batch_id"])
        op.create_index("ix_hs_auth_requests_assigned_to", "hs_auth_requests", ["assigned_to"])
        op.create_index("ix_hs_auth_requests_tenant_status", "hs_auth_requests", ["tenant_id", "status"])
        op.create_index("ix_hs_auth_requests_started", "hs_auth_requests", ["tenant_id", "started_by", "started_at"])
    if "hs_auth_runs" not in _tables():
        op.create_table(
            "hs_auth_runs",
            sa.Column("id", uid, primary_key=True),
            sa.Column("tenant_id", uid, sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("request_id", uid, sa.ForeignKey("hs_auth_requests.id"), nullable=False),
            sa.Column("user_id", uid, sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", tz, nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_hs_auth_runs_user_day", "hs_auth_runs", ["tenant_id", "user_id", "created_at"])


def downgrade() -> None:
    for t in ("hs_auth_runs", "hs_auth_requests", "hs_auth_batches"):
        if t in _tables():
            op.drop_table(t)
