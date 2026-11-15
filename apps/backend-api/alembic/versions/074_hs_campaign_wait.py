"""HealthSherpa campaigns: 10-minute link wait, 5-minute reminder

Revision ID: 074
Revises: 073
Create Date: 2026-11-15

Owner (2026-11-15): one client at a time, wait at most 10 minutes for the
authorization, then move on (status UNCONFIRMED — checked by hand); the
reminder goes at 5 minutes. New defaults + existing campaigns that still had
the old defaults (7 / 15). Idempotent.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "074"
down_revision: Union[str, None] = "073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE hs_text_campaigns ALTER COLUMN reminder_minutes SET DEFAULT 5")
    op.execute("ALTER TABLE hs_text_campaigns ALTER COLUMN expire_minutes SET DEFAULT 10")
    op.execute("UPDATE hs_text_campaigns SET expire_minutes = 10 WHERE expire_minutes = 15")
    op.execute("UPDATE hs_text_campaigns SET reminder_minutes = 5 WHERE reminder_minutes = 7")


def downgrade() -> None:
    op.execute("ALTER TABLE hs_text_campaigns ALTER COLUMN reminder_minutes SET DEFAULT 7")
    op.execute("ALTER TABLE hs_text_campaigns ALTER COLUMN expire_minutes SET DEFAULT 15")
