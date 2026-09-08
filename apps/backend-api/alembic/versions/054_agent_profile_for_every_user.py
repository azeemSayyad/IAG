"""An agent profile for every user

Revision ID: 054
Revises: 053
Create Date: 2026-09-08

Deals are filed against an agent profile (deals.agent_id), so a user without one
could not log a deal — and the Add Deal form fell back to the first agent in the
tenant, filing the deal under the wrong person. Every user now gets a profile.

Non-operator roles (head / tenant_admin / super_admin / admin / dev) get an
INACTIVE profile on purpose: lead distribution and appointment booking select on
Agent.status == "active", so an active profile would start routing customers to
an admin. Inactive profiles are fully usable for logging and listing deals.

Backfill only — no schema change, and it never touches an existing profile.
Idempotent.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "054"
down_revision: Union[str, None] = "053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OPERATOR = ("agent", "lead", "manager")


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO agents (id, tenant_id, user_id, timezone, daily_capacity,
                            max_concurrent, skills, weight, status,
                            created_at, updated_at)
        SELECT gen_random_uuid(), u.tenant_id, u.id, 'America/New_York', 8,
               1, '[]'::jsonb, 100,
               CASE WHEN lower(coalesce(u.role, '')) IN ('agent','lead','manager')
                    THEN 'active' ELSE 'inactive' END,
               now(), now()
        FROM users u
        WHERE u.deleted_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM agents a WHERE a.user_id = u.id)
        """
    )


def downgrade() -> None:
    # Only remove the profiles this migration could have created: non-operator
    # roles with no deals of their own. Anything with history is left alone.
    op.execute(
        """
        DELETE FROM agents a
        USING users u
        WHERE a.user_id = u.id
          AND lower(coalesce(u.role, '')) NOT IN ('agent','lead','manager')
          AND NOT EXISTS (SELECT 1 FROM deals d WHERE d.agent_id = a.id)
        """
    )
