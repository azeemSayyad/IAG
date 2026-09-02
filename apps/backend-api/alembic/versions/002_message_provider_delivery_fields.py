"""ensure generic provider delivery fields on messages

Revision ID: 002
Revises: 001
Create Date: 2026-05-25

"""
from typing import Sequence, Union

from alembic import op


revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS provider VARCHAR(50)")
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS provider_message_sid VARCHAR(255)")
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS delivery_status VARCHAR(50)")
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS delivery_error_code VARCHAR(50)")
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS delivery_error_message TEXT")
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_messages_provider_message_sid "
        "ON messages (provider_message_sid)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_messages_provider_message_sid")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS delivered_at")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS delivery_error_message")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS delivery_error_code")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS delivery_status")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS provider_message_sid")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS provider")
