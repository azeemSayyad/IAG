"""Staging import (TEST): sample old-portal deals into the ACA portal to verify
agent mapping. Run AFTER import_old_portal_agents.py.

- Samples up to DEALS_PER_AGENT (default 100) most-recent deals per agent, so we
  don't copy thousands — just enough to confirm deals land on the right agent.
- Maps old deal -> our `deals`, keying `agent_id` to the OLD `deals.agentId`
  (== old agent_profiles.id == our preserved agents.id). The agent's tenant is
  looked up from the target so the deal's tenant always matches its agent.
- Preserves the old deal id (idempotent: skips ids already present).
- Skips a deal if its agent isn't in the target (reports the count).
- Dry-run by default; --commit to write; single transaction.

Old portal data notes handled here:
  status: APPROVED->approved, REJECTED->blocked, else submitted
  state : full names ("Florida") -> USPS 2-letter; NULL (most rows) -> "NA"
  counts: aca/dental/vision from applicantsMedical/Dental/Vision (>=1 total)

Env: GAMIFIED_DB_URL (source, read-only), TARGET_DB_URL (dest), DEALS_PER_AGENT.
Run from apps/backend-api:
  PYTHONPATH=. GAMIFIED_DB_URL=... TARGET_DB_URL=... venv/bin/python \
      scripts/import_old_portal_deals.py [--commit]
"""

import os
import re
import sys

import psycopg2

PER_AGENT = int(os.environ.get("DEALS_PER_AGENT", "100"))
COMMIT = "--commit" in sys.argv

STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "puerto rico": "PR",
}

SOURCE_SQL = f"""
    SELECT * FROM (
      SELECT d.id, d."agentId", d."applicantFirstName" AS fn, d."applicantLastName" AS ln,
             d.phone, d.ssn, d."dateOfBirth" AS dob, d.street, d.apartment, d.city,
             d."zipCode" AS zip, d."monthlyIncome" AS income, d.carrier, d.state,
             d."typeOfCoverage" AS coverage, d.status, d."rejectionReason" AS reason,
             d."applicantsMedical" AS med, d."applicantsDental" AS den, d."applicantsVision" AS vis,
             d."createdAt" AS created,
             row_number() OVER (PARTITION BY d."agentId" ORDER BY d."createdAt" DESC NULLS LAST) rn
      FROM deals d
      WHERE d."agentId" IS NOT NULL
    ) t WHERE rn <= {PER_AGENT}
"""


def state_code(s):
    if not s:
        return "NA"
    s = str(s).strip()
    if len(s) == 2:
        return s.upper()
    return STATES.get(s.lower(), "NA")


def carrier_key(c):
    return re.sub(r"[^a-z0-9]+", "_", (c or "").lower()).strip("_") or "unknown"


def map_status(s):
    s = (s or "").upper()
    if s == "APPROVED":
        return "approved", "APPROVED"
    if s in ("REJECTED", "DENIED"):
        return "blocked", "NOT_APPROVED"
    return "submitted", None


def fetch_source():
    conn = psycopg2.connect(os.environ["GAMIFIED_DB_URL"])
    conn.set_session(readonly=True)
    try:
        cur = conn.cursor()
        cur.execute(SOURCE_SQL)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def main():
    target = os.environ.get("TARGET_DB_URL")
    if not target:
        sys.exit("TARGET_DB_URL is required (point at LOCAL first).")

    rows = fetch_source()
    tconn = psycopg2.connect(target)
    cur = tconn.cursor()
    # agent_id -> tenant_id, so each deal's tenant matches its agent.
    cur.execute("SELECT id, tenant_id FROM agents")
    agent_tenant = {str(a): str(t) for a, t in cur.fetchall()}

    print(f"Source : {len(rows)} sampled deals (<= {PER_AGENT}/agent)")
    print(f"Target : {target.split('@')[-1]}  | commit={COMMIT}\n")

    created = skipped_exist = skipped_noagent = 0
    by_agent = {}
    try:
        for r in rows:
            did = str(r["id"])
            aid = str(r["agentId"])
            tenant = agent_tenant.get(aid)
            if not tenant:
                skipped_noagent += 1
                continue
            cur.execute("SELECT 1 FROM deals WHERE id=%s", (did,))
            if cur.fetchone():
                skipped_exist += 1
                continue

            name = f"{(r['fn'] or '').strip()} {(r['ln'] or '').strip()}".strip() or None
            med = int(r["med"] or 0); den = int(r["den"] or 0); vis = int(r["vis"] or 0)
            if med + den + vis == 0:
                med = 1
            st, decision = map_status(r["status"])
            addr = " ".join(x for x in [(r["street"] or "").strip(), (r["apartment"] or "").strip()] if x) or None
            created_at = r["created"]

            if COMMIT:
                cur.execute(
                    """INSERT INTO deals
                       (id, tenant_id, agent_id, customer_name, customer_phone, customer_ssn,
                        customer_dob, customer_address, customer_city, customer_zip, customer_income,
                        carrier, carrier_key, state, plan_type, aca_count, dental_count, vision_count,
                        status, approval_decision, approval_reason, submitted_at, created_at, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                               COALESCE(%s, now()), COALESCE(%s, now()), now())""",
                    (did, tenant, aid, name, r["phone"], r["ssn"],
                     str(r["dob"]) if r["dob"] else None, addr, r["city"], r["zip"],
                     str(r["income"]) if r["income"] is not None else None,
                     r["carrier"], carrier_key(r["carrier"]), state_code(r["state"]),
                     r["coverage"], med, den, vis, st, decision, r["reason"],
                     created_at, created_at),
                )
            created += 1
            by_agent[aid] = by_agent.get(aid, 0) + 1

        if COMMIT:
            tconn.commit()
            print(f"DONE — inserted {created}, skipped {skipped_exist} existing, "
                  f"{skipped_noagent} had no matching agent.")
        else:
            print(f"DRY RUN — would insert {created}, skip {skipped_exist} existing, "
                  f"{skipped_noagent} no-agent. Re-run with --commit.")
        print(f"agents receiving deals: {len(by_agent)}")
    except Exception:
        tconn.rollback()
        raise
    finally:
        tconn.close()


if __name__ == "__main__":
    main()
