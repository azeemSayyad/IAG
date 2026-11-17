"""HealthSherpa campaigns: approved authorization reminder (10 min, on)

Revision ID: 076
Revises: 075
Create Date: 2026-11-17

Owner's "Authorization Texts" spec: a MAIN text before HealthSherpa's link and ONE
reminder 10 minutes later (approved wording, no STOP line). The old reminder was
a placeholder, OFF at 5/7 minutes: campaigns still on it get the approved text,
switched on, at minute 10. A reminder somebody wrote themselves is kept (only
switched on / moved to 10 if it was still the placeholder). Idempotent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "076"
down_revision: Union[str, None] = "075"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD = ("Insurance Alliance Group: Did you receive the HealthSherpa link? Tap it to authorize "
       "help with your plan. Reply STOP to opt out")
NEW = ('Your health plan is almost done! Look for the text that starts with:\n'
       '"Your agent {agent_name} with Insurance Alliance Group..."\n'
       '\n'
       'Tap the link in it and say YES.')


def upgrade() -> None:
    op.execute("ALTER TABLE hs_text_campaigns ALTER COLUMN reminder_enabled SET DEFAULT true")
    op.execute("ALTER TABLE hs_text_campaigns ALTER COLUMN reminder_minutes SET DEFAULT 10")
    op.get_bind().execute(
        sa.text("UPDATE hs_text_campaigns SET reminder_text = :new, reminder_enabled = true, reminder_minutes = 10 "
                "WHERE reminder_text IS NULL OR reminder_text = :old"),
        {"new": NEW, "old": OLD})


def downgrade() -> None:
    op.execute("ALTER TABLE hs_text_campaigns ALTER COLUMN reminder_enabled SET DEFAULT false")
    op.execute("ALTER TABLE hs_text_campaigns ALTER COLUMN reminder_minutes SET DEFAULT 5")
