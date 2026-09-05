"""Deal: consent forms

Revision ID: 048
Revises: 047
Create Date: 2026-09-04

Adds:
  * deals.consent_form_ids (JSONB) — the signed consent / scope-of-appointment
    paperwork attached on the Add Deal form (list of deal_recordings UUID strings).
  * deal_recordings.kind — 'recording' (the call audio that gates Log Sale) or
    'consent' (a consent form). Existing rows are all call recordings.
Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "048"
down_revision: Union[str, None] = "047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, col: str) -> bool:
    bind = op.get_bind()
    return col in [c["name"] for c in inspect(bind).get_columns(table)]


def upgrade() -> None:
    if not _has_column("deals", "consent_form_ids"):
        op.add_column(
            "deals",
            sa.Column("consent_form_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )
    if not _has_column("deal_recordings", "kind"):
        op.add_column(
            "deal_recordings",
            sa.Column("kind", sa.String(length=20), nullable=False, server_default="recording"),
        )


def downgrade() -> None:
    if _has_column("deal_recordings", "kind"):
        op.drop_column("deal_recordings", "kind")
    if _has_column("deals", "consent_form_ids"):
        op.drop_column("deals", "consent_form_ids")
