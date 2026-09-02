"""
Seed a full set of demo logins (one per role) into a single shared tenant so the
entire platform can be exercised end-to-end: Admin, Manager, and Agent — plus a
real Agent record for the agent user (needed for calendar/appointments/dispositions).

Idempotent: re-running skips users that already exist and reuses an existing
tenant if one is present (so it composes with scripts/bootstrap_admin.py).

Role taxonomy enforced by the backend (app/core/permissions.py):
    super_admin > tenant_admin > manager > agent

Override the shared password with env BOOTSTRAP_DEMO_PASSWORD (default below).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.models.user import User


PASSWORD = os.getenv("BOOTSTRAP_DEMO_PASSWORD", "Launchpad123!")
TENANT_NAME = os.getenv("BOOTSTRAP_TENANT_NAME", "Launchpad")

# (email, role, first_name, last_name, is_agent)
# Roles map to the login page labels: Admin=tenant_admin, Head Manager=head,
# Team Leader=lead, Agent=agent (super_admin is the org owner).
USERS = [
    ("superadmin@launchpad.com",  "super_admin",  "Super",  "Admin",   False),
    ("admin@launchpad.com",       "tenant_admin", "Tenant", "Admin",   False),
    ("headmanager@launchpad.com", "head",         "Henry",  "Manager", False),
    ("teamleader@launchpad.com",  "lead",         "Tara",   "Leader",  True),
    ("manager@launchpad.com",     "manager",      "Mary",   "Manager", True),
    ("agent@launchpad.com",       "agent",        "Alex",   "Agent",   True),
]


def main() -> None:
    db = SessionLocal()
    created, skipped = [], []
    try:
        # Reuse an existing tenant if present, else create the demo tenant.
        tenant = db.query(Tenant).filter(Tenant.deleted_at.is_(None)).first() \
            if hasattr(Tenant, "deleted_at") else db.query(Tenant).first()
        if not tenant:
            tenant = Tenant(name=TENANT_NAME)
            db.add(tenant)
            db.flush()
            print(f"Created tenant: {tenant.name} ({tenant.id})")
        else:
            print(f"Reusing existing tenant: {tenant.name} ({tenant.id})")

        for email, role, first, last, is_agent in USERS:
            existing = db.query(User).filter(
                User.email == email, User.deleted_at.is_(None)
            ).first()
            if existing:
                skipped.append((email, role))
                # Ensure the agent user still has an Agent record.
                if is_agent and not db.query(Agent).filter(Agent.user_id == existing.id).first():
                    db.add(Agent(tenant_id=tenant.id, user_id=existing.id, status="active"))
                    db.flush()
                    print(f"  + added missing Agent record for {email}")
                continue

            user = User(
                tenant_id=tenant.id,
                email=email,
                password_hash=hash_password(PASSWORD),
                first_name=first,
                last_name=last,
                role=role,
                status="active",
            )
            db.add(user)
            db.flush()
            if is_agent:
                db.add(Agent(tenant_id=tenant.id, user_id=user.id, status="active"))
                db.flush()
            created.append((email, role))

        db.commit()
    finally:
        db.close()

    print("\n=== Seed complete ===")
    print(f"Tenant: {TENANT_NAME}")
    print(f"Password for ALL logins: {PASSWORD}")
    if created:
        print("Created:")
        for email, role in created:
            print(f"  {email:30s} role={role}")
    if skipped:
        print("Already existed (skipped):")
        for email, role in skipped:
            print(f"  {email:30s} role={role}")


if __name__ == "__main__":
    main()
