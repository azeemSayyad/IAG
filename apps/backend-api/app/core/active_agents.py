"""The ONE definition of "an agent who is currently part of the team".

A user is DISABLED when an admin switched them to inactive (users.status =
'suspended', Settings -> team -> "Inactive users") or removed them
(users.deleted_at). A disabled agent must not appear in ANY live list, table,
dropdown, ranking or count, and must never be routed a lead — while everything
they already did (deals, commissions, ledger lines) stays on record, shown with
a "(disabled)" label.

Every screen that lists people should pull from here rather than writing its own
`User.status` check, so a new screen hides disabled agents without anyone
remembering to. Re-enabling is just the status flip: nothing is deleted, so the
agent comes back everywhere with their history.

NOT the same thing as `Agent.status`: that says whether a profile is ROUTABLE
(admins own an 'inactive' profile so they are never handed leads, see
core/agent_profile.py) — an admin with an inactive profile is not disabled.
"""
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.user import User

DISABLED_SUFFIX = " (disabled)"


def active_user_clause():
    """SQL condition for an enabled user. Use on any query that joins User."""
    return and_(User.status == "active", User.deleted_at.is_(None))


def user_is_active(user) -> bool:
    return bool(user) and user.status == "active" and user.deleted_at is None


def active_users_query(db: Session, tenant_id):
    return db.query(User).filter(User.tenant_id == tenant_id, active_user_clause())


def active_user_ids(db: Session, tenant_id) -> set[str]:
    return {str(uid) for (uid,) in db.query(User.id).filter(User.tenant_id == tenant_id, active_user_clause())}


def active_agents_query(db: Session, tenant_id):
    """Agent profiles whose user is enabled."""
    return (db.query(Agent).join(User, Agent.user_id == User.id)
            .filter(Agent.tenant_id == tenant_id, active_user_clause()))


def disabled_agent_ids(db: Session, tenant_id) -> set:
    """Agent-profile ids whose user is disabled (or gone)."""
    rows = (db.query(Agent.id, User.status, User.deleted_at)
            .outerjoin(User, Agent.user_id == User.id)
            .filter(Agent.tenant_id == tenant_id).all())
    return {aid for aid, status, deleted in rows if status != "active" or deleted is not None}


def labelled(name: str, active: bool) -> str:
    """A name for a HISTORY row: disabled people keep their records, labelled."""
    return name if active else f"{name}{DISABLED_SUFFIX}"
