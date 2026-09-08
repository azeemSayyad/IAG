"""Every user owns an agent profile.

A deal is filed against an AGENT PROFILE (`deals.agent_id`), never against a user
account. So a user with no profile cannot log a deal at all — and worse, the Add
Deal form used to fall back to the FIRST agent in the tenant when it couldn't
find the signed-in user's own profile, silently filing an admin's deal under
somebody else's name.

The client wants every role to be able to log its own deals, so every user now
gets a profile. The catch is that an ACTIVE profile is also what lead
distribution and appointment booking select on:

    Agent.status == "active"      (leads/services/distribution.py,
                                   booking/services/*.py)

Handing an admin an active profile would therefore start routing real customers
to them. So non-operator roles get a profile that is deliberately NOT active:
they own the deals they log, and they are never handed work automatically.
Nothing reads `Agent.status` when creating or listing a deal, so an inactive
profile is fully functional for that purpose.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.user import User

# Roles that actually work distributed leads and take booked appointments.
# Everyone else gets a profile they can log deals against, but no work routed.
OPERATOR_ROLES = {"agent", "lead", "manager"}


def profile_status_for(role: str | None) -> str:
    """The agent-profile status a role should have. Operators are routable."""
    return "active" if (role or "").strip().lower() in OPERATOR_ROLES else "inactive"


def ensure_agent_profile(db: Session, user: User, commit: bool = True) -> Agent:
    """Return this user's agent profile, creating it on first use.

    Idempotent and safe to call on a read path: it only ever inserts the one
    missing row. Existing profiles are returned untouched — in particular their
    status is NOT rewritten here, so an admin who was deliberately made routable
    stays that way.
    """
    agent = db.query(Agent).filter(Agent.user_id == user.id).first()
    if agent:
        return agent
    agent = Agent(
        tenant_id=user.tenant_id,
        user_id=user.id,
        status=profile_status_for(user.role),
    )
    db.add(agent)
    if commit:
        db.commit()
        db.refresh(agent)
    else:
        db.flush()
    return agent


def sync_profile_status(db: Session, user: User) -> None:
    """Keep the profile in step after a role or account-status change.

    A suspended user is never routable whatever their role; an active user is
    routable only if their role is an operator role.
    """
    agent = ensure_agent_profile(db, user, commit=False)
    agent.status = "inactive" if user.status != "active" else profile_status_for(user.role)
