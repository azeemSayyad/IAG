"""One-off migration: import Gamified agent accounts into the ACA portal.

Reads ACTIVE, non-deleted users with designation 'Agent' from the Gamified
database and creates matching ACA `users` (role=agent) + `agents` rows.

- Idempotent: skips any email that already exists in the target (no overwrites).
- Dry-run by default; pass --commit to actually write.
- All imported agents get the same password (DEFAULT_PW), bcrypt-hashed.

Connection strings come from env (no secrets in this file):
  GAMIFIED_DB_URL   source (read-only)            [required]
  TARGET_DB_URL     destination ACA DB            [optional; defaults to app DB]
  TENANT_ID         ACA tenant for the new users  [default: Launchpad]
  DEFAULT_PW        password for all imported      [default: Forget789!]

Run inside the backend-api container, e.g.:
  GAMIFIED_DB_URL=... TARGET_DB_URL=... python scripts/import_gamified_agents.py
  GAMIFIED_DB_URL=... TARGET_DB_URL=... python scripts/import_gamified_agents.py --commit
"""

import os
import sys

import psycopg2
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.models.agent import Agent
from app.models.user import User

TENANT_ID = os.environ.get("TENANT_ID", "04b5bd4c-0049-4f15-b6e7-339d59ee394e")
DEFAULT_PW = os.environ.get("DEFAULT_PW", "Forget789!")
COMMIT = "--commit" in sys.argv

SOURCE_SQL = """
    SELECT u."firstName", u."lastName", u.email, u.phone
    FROM users u
    JOIN employees e ON e."userId" = u.id
    JOIN designations d ON d.id = e."designationId"
    WHERE d.name = 'Agent'
      AND u."userStatus" = 'ACTIVE'
      AND u.email NOT LIKE 'deleted_%'
    ORDER BY u."firstName", u."lastName"
"""


def fetch_source() -> list[tuple]:
    src = os.environ["GAMIFIED_DB_URL"]
    conn = psycopg2.connect(src)
    try:
        cur = conn.cursor()
        cur.execute(SOURCE_SQL)
        return cur.fetchall()
    finally:
        conn.close()


def main() -> None:
    target_url = os.environ.get("TARGET_DB_URL")
    if not target_url:
        from app.core.config import settings
        target_url = settings.DATABASE_URL

    rows = fetch_source()
    print(f"Source: {len(rows)} Agent-designation accounts found.")
    print(f"Target: {target_url.split('@')[-1]}  | tenant={TENANT_ID}  | commit={COMMIT}\n")

    pw_hash = hash_password(DEFAULT_PW)
    engine = create_engine(target_url)
    Session = sessionmaker(bind=engine)
    db = Session()

    created, skipped = 0, []
    try:
        for fn, ln, email, phone in rows:
            em = (email or "").strip().lower()
            if not em:
                continue
            if db.query(User).filter(User.email == em).first():
                skipped.append(em)
                print(f"  SKIP (exists): {em}")
                continue
            first = (fn or "").strip() or "Agent"
            last = (ln or "").strip()
            prefs = {}
            phone_digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
            if len(phone_digits) >= 7:  # ignore placeholders like "-" / blanks
                prefs["personal_phone"] = str(phone).strip()
            print(f"  {'CREATE' if COMMIT else 'WOULD CREATE'}: {em}  ({first} {last})  phone={prefs.get('personal_phone','-')}")
            if COMMIT:
                u = User(
                    tenant_id=TENANT_ID,
                    email=em,
                    password_hash=pw_hash,
                    first_name=first,
                    last_name=last,
                    role="agent",
                    status="active",
                    preferences=prefs,
                )
                db.add(u)
                db.flush()
                db.add(Agent(tenant_id=TENANT_ID, user_id=u.id, status="active"))
                created += 1

        if COMMIT:
            db.commit()
            print(f"\nDONE — created {created}, skipped {len(skipped)} existing.")
        else:
            print(f"\nDRY RUN — would create {len(rows) - len(skipped)}, skip {len(skipped)} existing. Re-run with --commit to apply.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
