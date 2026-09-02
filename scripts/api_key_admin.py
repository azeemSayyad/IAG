"""API-key admin CLI — mint / list / revoke / rotate.

Standalone (psycopg2 only, no app import) so it runs anywhere with a DB URL.
Reads the DB connection from $DATABASE_URL or $PORTAL_URL. NO secrets are stored
in this file. The plaintext key is printed ONCE on mint; only its SHA-256 hash is
persisted, exactly as app/core/api_key.py verifies it (hash = sha256(plaintext),
prefix = plaintext[:12]).

  python scripts/api_key_admin.py mint   --name "Partner Campaign Tool" [--tier master] [--rate 120] [--scopes "*"]
  python scripts/api_key_admin.py list
  python scripts/api_key_admin.py revoke <prefix>          # status->revoked, effective next request, no redeploy
  python scripts/api_key_admin.py rotate <prefix> --name "..."   # revoke old + mint new
"""
import argparse
import hashlib
import os
import secrets
import string
import sys
import uuid

import psycopg2
from psycopg2.extras import Json

# Our tenant (Launchpad / Endeavor). The key is pinned here — full SCOPE access,
# never cross-tenant. Override with --tenant if ever needed.
DEFAULT_TENANT = "04b5bd4c-0049-4f15-b6e7-339d59ee394e"
_B62 = string.ascii_uppercase + string.ascii_lowercase + string.digits
PREFIX_LEN = 12


def _b62(nbytes: int) -> str:
    num = int.from_bytes(secrets.token_bytes(nbytes), "big")
    out = []
    while num:
        num, r = divmod(num, 62)
        out.append(_B62[r])
    return "".join(out) or "0"


def generate_api_key() -> str:
    return f"ek_live_{_b62(32)}"


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _conn():
    url = os.environ.get("DATABASE_URL") or os.environ.get("PORTAL_URL")
    if not url:
        sys.exit("Set DATABASE_URL or PORTAL_URL")
    c = psycopg2.connect(url)
    c.autocommit = False
    return c


def cmd_mint(a):
    key = generate_api_key()
    kp, kh = key[:PREFIX_LEN], hash_key(key)
    scopes = [s.strip() for s in a.scopes.split(",")] if a.scopes else ["*"]
    c = _conn()
    cur = c.cursor()
    cur.execute(
        """insert into api_keys
           (id, key_hash, key_prefix, name, tenant_id, tier, scopes, status, rate_limit_per_min, created_at, created_by)
           values (%s,%s,%s,%s,%s,%s,%s,'active',%s, now(), %s)""",
        (str(uuid.uuid4()), kh, kp, a.name, a.tenant, a.tier, Json(scopes), a.rate, "api_key_admin"),
    )
    c.commit()
    c.close()
    print("=" * 72)
    print(f"  {a.tier.upper()} API KEY MINTED — copy now, shown ONCE, never retrievable:")
    print(f"\n    {key}\n")
    print(f"  prefix={kp}  tier={a.tier}  scopes={scopes}  rate={a.rate}/min")
    print(f"  tenant={a.tenant}")
    print("=" * 72)


def cmd_list(a):
    c = _conn()
    cur = c.cursor()
    cur.execute(
        "select key_prefix, name, tier, scopes, status, rate_limit_per_min, created_at, last_used_at "
        "from api_keys order by created_at"
    )
    rows = cur.fetchall()
    c.close()
    if not rows:
        print("(no api keys)")
        return
    for kp, name, tier, sc, st, rl, ca, lu in rows:
        print(f"  {kp}  {tier:7} {st:8} rate={rl:<4} used={str(lu)[:19] if lu else '—':19}  {name}  scopes={sc}")


def cmd_revoke(a):
    c = _conn()
    cur = c.cursor()
    cur.execute(
        "update api_keys set status='revoked', revoked_at=now() where key_prefix=%s and status='active'",
        (a.prefix,),
    )
    n = cur.rowcount
    c.commit()
    c.close()
    print(f"revoked {n} active key(s) with prefix {a.prefix} — effective on the NEXT request (no redeploy)")


def cmd_rotate(a):
    cmd_revoke(argparse.Namespace(prefix=a.prefix))
    cmd_mint(a)


def main():
    p = argparse.ArgumentParser(description="API-key admin")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mint")
    m.add_argument("--name", required=True)
    m.add_argument("--tier", default="master", choices=["master", "scoped"])
    m.add_argument("--rate", type=int, default=120)
    m.add_argument("--scopes", default="*")
    m.add_argument("--tenant", default=DEFAULT_TENANT)
    m.set_defaults(func=cmd_mint)
    sub.add_parser("list").set_defaults(func=cmd_list)
    r = sub.add_parser("revoke")
    r.add_argument("prefix")
    r.set_defaults(func=cmd_revoke)
    ro = sub.add_parser("rotate")
    ro.add_argument("prefix")
    ro.add_argument("--name", required=True)
    ro.add_argument("--tier", default="master", choices=["master", "scoped"])
    ro.add_argument("--rate", type=int, default=120)
    ro.add_argument("--scopes", default="*")
    ro.add_argument("--tenant", default=DEFAULT_TENANT)
    ro.set_defaults(func=cmd_rotate)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
