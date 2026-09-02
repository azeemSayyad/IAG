"""SMS Do-Not-Call suppression list

Revision ID: 024
Revises: 023
Create Date: 2026-06-17

Creates sms_do_not_call: phone numbers dispositioned Unqualified / Wrong Number /
Not Interested (the "Parked — Unqualified" panel) are stored here so the SMS
queue can NEVER re-ingest them, even if the originating sms_leads row is later
deleted. Backfills the table from the existing parked-unqualified leads. The
phone is stored digits-only so format differences collapse. Idempotent.
"""
import uuid as _uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BACKFILL_SELECT = """
    SELECT tenant_id,
           regexp_replace(phone_number, '[^0-9]', '', 'g') AS p,
           max(disposition) AS reason
    FROM sms_leads
    WHERE disposition IN ('UNQUALIFIED', 'NOT_INTERESTED', 'WRONG_NUMBER')
      AND regexp_replace(phone_number, '[^0-9]', '', 'g') <> ''
    GROUP BY tenant_id, regexp_replace(phone_number, '[^0-9]', '', 'g')
"""

_BACKFILL_INSERT = """
    INSERT INTO sms_do_not_call (id, tenant_id, phone_number, reason, created_at)
    VALUES (:id, :tid, :phone, :reason, now())
    ON CONFLICT (tenant_id, phone_number) DO NOTHING
"""


def _has_table(name: str) -> bool:
    return name in inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("sms_do_not_call"):
        op.create_table(
            "sms_do_not_call",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("phone_number", sa.String(length=32), nullable=False),
            sa.Column("reason", sa.String(length=40), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("tenant_id", "phone_number", name="uq_sms_dnc_tenant_phone"),
        )
        op.create_index("idx_sms_dnc_tenant_phone", "sms_do_not_call", ["tenant_id", "phone_number"])

    # Backfill every existing parked-unqualified number (digits-only, de-duped).
    bind = op.get_bind()
    for tid, phone, reason in bind.execute(sa.text(_BACKFILL_SELECT)).fetchall():
        bind.execute(sa.text(_BACKFILL_INSERT),
                     {"id": str(_uuid.uuid4()), "tid": str(tid), "phone": phone, "reason": reason})


def downgrade() -> None:
    if _has_table("sms_do_not_call"):
        op.drop_table("sms_do_not_call")
