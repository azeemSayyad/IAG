"""
Seed the developer (super-user) login.

The "dev" role has full access to every page and action — including the SMS
Queue, SMS Manager and SMS Monitoring pages that are otherwise restricted.
Only a dev can create other dev users, so this seed is the entry point for the
first dev account.

Idempotent: re-running skips the user if it already exists and reuses an
existing tenant if one is present (so it composes with bootstrap_demo.py).

Override the credentials with env BOOTSTRAP_DEV_EMAIL / BOOTSTRAP_DEV_PASSWORD.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.tenant import Tenant
from app.models.user import User


EMAIL = os.getenv("BOOTSTRAP_DEV_EMAIL", "dev@gmail.com")
PASSWORD = os.getenv("BOOTSTRAP_DEV_PASSWORD", "Forget789!")
TENANT_NAME = os.getenv("BOOTSTRAP_TENANT_NAME", "Launchpad")


def main() -> None:
    db = SessionLocal()
    try:
        # Reuse an existing tenant if present, else create the default one.
        tenant = db.query(Tenant).filter(Tenant.deleted_at.is_(None)).first() \
            if hasattr(Tenant, "deleted_at") else db.query(Tenant).first()
        if not tenant:
            tenant = Tenant(name=TENANT_NAME)
            db.add(tenant)
            db.flush()
            print(f"Created tenant: {tenant.name} ({tenant.id})")
        else:
            print(f"Reusing existing tenant: {tenant.name} ({tenant.id})")

        existing = db.query(User).filter(
            User.email == EMAIL, User.deleted_at.is_(None)
        ).first()
        if existing:
            # Make sure the account is actually a dev (e.g. promote a prior seed).
            if existing.role != "dev":
                existing.role = "dev"
                db.commit()
                print(f"Promoted existing user to dev: {EMAIL}")
            else:
                print(f"Dev user already exists (skipped): {EMAIL}")
            return

        user = User(
            tenant_id=tenant.id,
            email=EMAIL,
            password_hash=hash_password(PASSWORD),
            first_name="Dev",
            last_name="User",
            role="dev",
            status="active",
        )
        db.add(user)
        db.commit()
        print("\n=== Dev seed complete ===")
        print(f"  email:    {EMAIL}")
        print(f"  password: {PASSWORD}")
        print(f"  role:     dev")
    finally:
        db.close()


if __name__ == "__main__":
    main()
