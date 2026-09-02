"""Reconcile the '025' revision applied to prod but never committed

Revision ID: 025
Revises: 024
Create Date: 2026-06-18

The production database was stamped at revision '025' by a migration applied
directly to prod (during the historical-deal migration) but never committed to
this repo. With the repo head at '024', `alembic upgrade head` aborted on startup
with:

    Can't locate revision identified by '025'

which crashed `start_all_in_one.sh` and failed every deploy (Exited with status 1).

That phantom '025' added three columns to `deals` (used by the deal migration):
    legacy_source_id  UUID         + unique index ux_deals_legacy_source_id
    legacy_data       JSONB
    closed_at         TIMESTAMPTZ

This migration reproduces that DDL **idempotently** (IF NOT EXISTS), so:
  - on prod (columns already exist, already stamped 025) it is a safe no-op, and
  - a fresh database built from 001..025 ends up matching prod.
"""
from alembic import op

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: prod already has these (added by the phantom 025); IF NOT EXISTS
    # makes this a no-op there, while fresh databases get the columns.
    op.execute("ALTER TABLE deals ADD COLUMN IF NOT EXISTS legacy_source_id UUID")
    op.execute("ALTER TABLE deals ADD COLUMN IF NOT EXISTS legacy_data JSONB")
    op.execute("ALTER TABLE deals ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_deals_legacy_source_id "
        "ON deals (legacy_source_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_deals_legacy_source_id")
    op.execute("ALTER TABLE deals DROP COLUMN IF EXISTS closed_at")
    op.execute("ALTER TABLE deals DROP COLUMN IF EXISTS legacy_data")
    op.execute("ALTER TABLE deals DROP COLUMN IF EXISTS legacy_source_id")
