"""
Bootstrap a production tenant admin and matching agent record.

This script intentionally does not create fake leads, appointments, messages,
or analytics. It only creates the minimum real account needed to sign in and
operate a fresh environment.
"""

import os
import sys

from sqlalchemy.exc import IntegrityError

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.models.user import User


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def main() -> None:
    email = require_env("BOOTSTRAP_ADMIN_EMAIL").lower()
    password = require_env("BOOTSTRAP_ADMIN_PASSWORD")
    tenant_name = os.getenv("BOOTSTRAP_TENANT_NAME", "Launchpad").strip() or "Launchpad"
    first_name = os.getenv("BOOTSTRAP_ADMIN_FIRST_NAME", "Admin").strip() or "Admin"
    last_name = os.getenv("BOOTSTRAP_ADMIN_LAST_NAME", "User").strip() or "User"

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email, User.deleted_at.is_(None)).first()
        if existing:
            print(f"Admin already exists: {existing.email}")
            return

        tenant = Tenant(name=tenant_name)
        db.add(tenant)
        db.flush()

        user = User(
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password(password),
            first_name=first_name,
            last_name=last_name,
            role="tenant_admin",
            status="active",
        )
        db.add(user)
        db.flush()

        agent = Agent(
            tenant_id=tenant.id,
            user_id=user.id,
            status="active",
        )
        db.add(agent)
        db.commit()
        print(f"Created tenant admin {email} for tenant {tenant_name}")
    except IntegrityError:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
