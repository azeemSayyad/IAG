"""
READ-ONLY production diagnostic for the login / 401 issue.

Run INSIDE the Railway container (where the private DB/Redis + env vars exist):

    /opt/venv/bin/python apps/backend-api/scripts/diagnose_prod.py

This script performs ONLY reads — SELECT queries and a Redis PING. It never
INSERTs, UPDATEs, DELETEs, commits, or runs migrations. Safe to run on prod.

It reports:
  * which Postgres it actually connects to (host + db, password masked) + user count
  * whether Redis is reachable
  * for admin@launchpad.com: FOUND / VERIFY / status / deleted_at / locked_until /
    failed_login_attempts / role / tenant_id
  * presence (set/missing, masked) of every env var login/tokens/DB/Redis need
  * the Alembic revision recorded in the live DB vs. the head in the code
"""
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

# Make the `app` package importable regardless of the working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # -> apps/backend-api

from sqlalchemy import text

from app.core.config import settings
from app.core.database import engine
from app.core.security import verify_password

DEMO_EMAIL = "admin@launchpad.com"
# Verify against the SAME password the seed would have used.
DEMO_PASSWORD = os.getenv("BOOTSTRAP_DEMO_PASSWORD", "Launchpad123!")
DEFAULT_DB = "postgresql://postgres:postgres@localhost:5432/launchpad"
DEFAULT_REDIS = "redis://localhost:6379/0"
DEFAULT_JWT = "change-me-in-production"


def mask(val, keep=4):
    if val is None:
        return "MISSING"
    s = str(val)
    if s == "":
        return "(empty)"
    if len(s) <= keep:
        return "*" * len(s)
    return f"...{s[-keep:]} (len={len(s)})"


def mask_url(url):
    try:
        p = urlsplit(url)
        host = p.hostname or ""
        if p.port:
            host += f":{p.port}"
        userinfo = ""
        if p.username:
            userinfo = p.username + (":****" if p.password else "") + "@"
        return f"{p.scheme}://{userinfo}{host}{p.path or ''}"
    except Exception:
        return "(unparseable)"


def section(title):
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


# --------------------------------------------------------------------------
section("1. DATABASE the app is configured to use (from settings.DATABASE_URL)")
db_url = settings.DATABASE_URL
print(f"  URL (masked): {mask_url(db_url)}")
try:
    p = urlsplit(db_url)
    print(f"  host        : {p.hostname}")
    print(f"  port        : {p.port}")
    print(f"  database    : {(p.path or '/').lstrip('/')}")
    print(f"  user        : {p.username}")
except Exception as e:
    print(f"  (could not parse URL: {e})")
if db_url == DEFAULT_DB:
    print("  >> WARNING: this is the LOCALHOST DEFAULT. No POSTGRES_URL/DATABASE_URL")
    print("     is set in the environment — the app is NOT pointed at Railway Postgres.")

# --------------------------------------------------------------------------
section("2. DB connectivity + user count + admin@launchpad.com (READ-ONLY)")
try:
    with engine.connect() as conn:
        total = conn.execute(text("SELECT count(*) FROM users")).scalar()
        print(f"  total users in DB: {total}")
        row = conn.execute(
            text(
                "SELECT email, password_hash, status, deleted_at, locked_until, "
                "failed_login_attempts, role, tenant_id "
                "FROM users WHERE email = :e"
            ),
            {"e": DEMO_EMAIL},
        ).mappings().first()

    print(f"\n  --- {DEMO_EMAIL} ---")
    if not row:
        print("  FOUND               : False   <-- user does NOT exist (run bootstrap_demo.py)")
    else:
        print("  FOUND               : True")
        try:
            ok = verify_password(DEMO_PASSWORD, row["password_hash"])
        except Exception as e:
            ok = f"ERROR verifying ({type(e).__name__}: {e})"
        print(f"  VERIFY({DEMO_PASSWORD!r}) : {ok}")
        print(f"  status              : {row['status']}   (login needs 'active', else 403)")
        print(f"  deleted_at          : {row['deleted_at']}   (non-null => login can't find it => 401)")
        print(f"  locked_until        : {row['locked_until']}   (future => 423 after a correct password)")
        print(f"  failed_login_attempts: {row['failed_login_attempts']}")
        print(f"  role                : {row['role']}")
        print(f"  tenant_id           : {row['tenant_id']}")
except Exception as e:
    print(f"  >> DB ERROR: {type(e).__name__}: {e}")
    print("     (cannot reach Postgres — check DATABASE_URL/POSTGRES_URL above)")

# --------------------------------------------------------------------------
section("3. REDIS connectivity (READ-ONLY ping)")
print(f"  URL (masked): {mask_url(settings.REDIS_URL)}")
if settings.REDIS_URL == DEFAULT_REDIS:
    print("  >> NOTE: localhost default (no REDIS_URL/REDIS_HOST set). Login does not")
    print("     need Redis, but Celery worker/beat + realtime do.")
try:
    import redis

    rc = redis.from_url(settings.REDIS_URL, socket_connect_timeout=5)
    print(f"  CONNECTED: {bool(rc.ping())}")
except Exception as e:
    print(f"  CONNECTED: False  ({type(e).__name__}: {e})")

# --------------------------------------------------------------------------
section("4. REQUIRED env vars (set/missing, masked)")
print("  -- DB (one of these MUST be set, else localhost default) --")
print(f"  POSTGRES_URL : {mask_url(os.environ['POSTGRES_URL']) if 'POSTGRES_URL' in os.environ else 'MISSING'}")
print(f"  DATABASE_URL : {mask_url(os.environ['DATABASE_URL']) if 'DATABASE_URL' in os.environ else 'MISSING'}")
print("  -- JWT (signs/verifies tokens; login works even on default but is INSECURE) --")
jwt_env = os.environ.get("JWT_SECRET")
print(f"  JWT_SECRET   : {mask(jwt_env)}")
if not jwt_env or settings.JWT_SECRET == DEFAULT_JWT:
    print("     >> WARNING: JWT_SECRET not set / still default — set a long random value.")
print(f"  JWT_ALGORITHM: {settings.JWT_ALGORITHM}")
print("  -- Redis (worker/beat/realtime) --")
print(f"  REDIS_URL    : {mask_url(os.environ['REDIS_URL']) if 'REDIS_URL' in os.environ else 'MISSING'}")
print(f"  REDIS_HOST   : {os.environ.get('REDIS_HOST', 'MISSING')}")
print(f"  REDIS_PASSWORD: {mask(os.environ.get('REDIS_PASSWORD'))}")
print("  -- App env --")
print(f"  APP_ENV      : {os.environ.get('APP_ENV', 'MISSING')}")
print(f"  NODE_ENV     : {os.environ.get('NODE_ENV', 'MISSING')}")
print(f"  AUTH_ENABLED : {settings.AUTH_ENABLED}")
print(f"  effective APP_ENV (settings): {settings.APP_ENV}")

# --------------------------------------------------------------------------
section("5. Alembic revision: live DB vs. code head")
versions_dir = Path(__file__).resolve().parents[1] / "alembic" / "versions"
revs, downs = {}, set()
for f in sorted(versions_dir.glob("*.py")):
    txt = f.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"^revision\s*(?::[^=]+)?=\s*['\"]([^'\"]+)['\"]", txt, re.M)
    d = re.search(r"^down_revision\s*(?::[^=]+)?=\s*['\"]([^'\"]+)['\"]", txt, re.M)
    if m:
        revs[m.group(1)] = f.name
        if d:
            downs.add(d.group(1))
code_heads = sorted(r for r in revs if r not in downs)
print(f"  code revisions: {sorted(revs)}")
print(f"  code head(s)  : {code_heads}")

try:
    with engine.connect() as conn:
        live = [r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()]
    print(f"  live DB version: {live}")
    if not live:
        print("  >> alembic_version is EMPTY — migrations have not stamped the DB.")
    elif set(live) == set(code_heads):
        print("  >> OK: live DB is at the code head (all migrations applied).")
    else:
        print("  >> MISMATCH: live DB is NOT at code head — migrations did not fully run.")
except Exception as e:
    print(f"  live DB version: ERROR ({type(e).__name__}: {e})")
    print("  >> If 'alembic_version' is missing, migrations never ran on this DB.")

print("\nDone. (read-only — no data was modified)")
