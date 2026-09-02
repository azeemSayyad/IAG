"""Staging import: copy ALL old-portal agents into the ACA portal.

Brings every agent from the old "Gamified" portal in as a NEW, clearly-marked
record, WITHOUT touching anything already in the target. Existing prod/local
agents, users and deals are never modified or deleted.

Key guarantees
--------------
- ID PRESERVED: our ``agents.id`` is set to the old ``agent_profiles.id`` — the
  exact value the old ``deals.agentId`` carries — so deals map once migrated.
- ADDITIVE ONLY: nothing existing is changed; we only INSERT.
- MARKED: imported agents are easy to spot —
    * last name gets a " [OLD]" suffix (visible on leaderboard / All Deals),
    * email is aliased with "+old" (also avoids the global-unique email clash
      with the ~15 emails that already exist in the target),
    * users.preferences carries {source:"old_portal", old_agent_id, old_email,
      old_status} for clean programmatic filtering during the later reconcile.
- IDEMPOTENT: re-running skips any agent whose id already exists in the target.
- ALL-OR-NOTHING: writes happen in a single transaction on --commit.

NPN / licenses / appointments are NOT imported — the source only has placeholder
("8764") / empty values, so there is nothing real to bring.

Connection strings come from env (no secrets in this file):
  GAMIFIED_DB_URL   old-portal source (read-only)        [required]
  TARGET_DB_URL     destination ACA DB                   [required]
  TENANT_ID         target tenant for the new rows       [optional; auto-detects
                                                          the tenant with the most
                                                          existing agents]
  DEFAULT_PW        password for all imported agents      [default: Forget789!]
  MAP_CSV           where to write the old->new map CSV   [default: alongside cwd]

Run from apps/backend-api (so `app` is importable), e.g.:
  PYTHONPATH=. GAMIFIED_DB_URL=... TARGET_DB_URL=... venv/bin/python \
      scripts/import_old_portal_agents.py            # dry run
  PYTHONPATH=. GAMIFIED_DB_URL=... TARGET_DB_URL=... venv/bin/python \
      scripts/import_old_portal_agents.py --commit   # apply
"""

import csv
import os
import sys
import uuid

import psycopg2

from app.core.security import hash_password

DEFAULT_PW = os.environ.get("DEFAULT_PW", "Forget789!")
COMMIT = "--commit" in sys.argv
MAP_CSV = os.environ.get("MAP_CSV", "old_portal_import_map.csv")

# Pull EVERY agent profile (regardless of status/designation/duplicates) plus its
# user. agent_profiles.id is the value old deals.agentId points at.
SOURCE_SQL = """
    SELECT a.id            AS old_agent_id,
           u.id            AS old_user_id,
           u."firstName"   AS first_name,
           u."lastName"    AS last_name,
           u.email         AS email,
           u.phone         AS phone,
           u."userStatus"  AS user_status
    FROM agent_profiles a
    JOIN users u ON u.id = a."userId"
    ORDER BY u."firstName", u."lastName"
"""


def alias_email(email: str, old_agent_id) -> str:
    """Insert '+old' so the imported login never clashes with an existing one."""
    em = (email or "").strip().lower()
    if "@" in em:
        local, dom = em.split("@", 1)
        return f"{local}+old@{dom}"
    return f"old-{old_agent_id}@oldportal.local"


def resolve_tenant(cur) -> str:
    tid = os.environ.get("TENANT_ID")
    if tid:
        return tid
    cur.execute("SELECT tenant_id FROM agents GROUP BY tenant_id ORDER BY count(*) DESC LIMIT 1")
    row = cur.fetchone()
    if not row:
        cur.execute("SELECT id FROM tenants ORDER BY created_at LIMIT 1")
        row = cur.fetchone()
    if not row:
        sys.exit("No tenant found in target and TENANT_ID not set.")
    return str(row[0])


def fetch_source() -> list:
    conn = psycopg2.connect(os.environ["GAMIFIED_DB_URL"])
    conn.set_session(readonly=True)
    try:
        cur = conn.cursor()
        cur.execute(SOURCE_SQL)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def main() -> None:
    target_url = os.environ.get("TARGET_DB_URL")
    if not target_url:
        sys.exit("TARGET_DB_URL is required (point it at LOCAL first).")

    rows = fetch_source()
    pw_hash = hash_password(DEFAULT_PW)

    tconn = psycopg2.connect(target_url)
    cur = tconn.cursor()
    tenant_id = resolve_tenant(cur)

    print(f"Source : {len(rows)} old-portal agents")
    print(f"Target : {target_url.split('@')[-1]}  | tenant={tenant_id}  | commit={COMMIT}\n")

    created, skipped, mapping = 0, [], []
    try:
        for r in rows:
            old_agent_id = str(r["old_agent_id"])
            first = (r["first_name"] or "").strip() or "Agent"
            last = (r["last_name"] or "").strip()
            marked_last = (last + " [OLD]").strip()
            new_email = alias_email(r["email"], old_agent_id)

            # Idempotent: skip if this agent id is already present.
            cur.execute("SELECT 1 FROM agents WHERE id = %s", (old_agent_id,))
            if cur.fetchone():
                skipped.append(old_agent_id)
                print(f"  SKIP (agent exists): {first} {last}  [{old_agent_id}]")
                continue
            # Guard the aliased email against an unexpected clash.
            cur.execute("SELECT 1 FROM users WHERE email = %s", (new_email,))
            if cur.fetchone():
                new_email = f"{new_email.split('@')[0]}-{old_agent_id[:8]}@{new_email.split('@')[1]}"

            phone = str(r["phone"] or "").strip()
            phone_digits = "".join(ch for ch in phone if ch.isdigit())
            prefs = {
                "source": "old_portal",
                "old_agent_id": old_agent_id,
                "old_user_id": str(r["old_user_id"]),
                "old_email": (r["email"] or "").strip().lower(),
                "old_status": r["user_status"],
            }
            if len(phone_digits) >= 7:
                prefs["personal_phone"] = phone

            new_user_id = str(uuid.uuid4())
            print(f"  {'CREATE' if COMMIT else 'WOULD CREATE'}: {first} {marked_last}  "
                  f"<{new_email}>  agent.id={old_agent_id}")

            if COMMIT:
                import json
                cur.execute(
                    """INSERT INTO users (id, tenant_id, email, password_hash, first_name,
                                          last_name, role, status, preferences)
                       VALUES (%s,%s,%s,%s,%s,%s,'agent','active',%s)""",
                    (new_user_id, tenant_id, new_email, pw_hash, first, marked_last,
                     json.dumps(prefs)),
                )
                cur.execute(
                    """INSERT INTO agents (id, tenant_id, user_id, status)
                       VALUES (%s,%s,%s,'active')""",
                    (old_agent_id, tenant_id, new_user_id),
                )
                created += 1

            mapping.append({
                "old_agent_id": old_agent_id,
                "new_agent_id": old_agent_id,   # preserved — same value
                "new_user_id": new_user_id,
                "name": f"{first} {marked_last}",
                "new_email": new_email,
                "old_email": prefs["old_email"],
                "old_status": r["user_status"],
                "action": "create",
            })

        if COMMIT:
            tconn.commit()
            print(f"\nDONE — created {created}, skipped {len(skipped)} existing.")
        else:
            print(f"\nDRY RUN — would create {len(rows) - len(skipped)}, "
                  f"skip {len(skipped)} existing. Re-run with --commit to apply.")

        with open(MAP_CSV, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["old_agent_id", "new_agent_id", "new_user_id",
                                              "name", "new_email", "old_email", "old_status", "action"])
            w.writeheader()
            w.writerows(mapping)
        print(f"Mapping written -> {MAP_CSV}")
    except Exception:
        tconn.rollback()
        raise
    finally:
        tconn.close()


if __name__ == "__main__":
    main()
