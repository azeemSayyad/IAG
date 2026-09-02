"""Backfill aca/dental/vision policy counts on migrated deals from legacy_data.

The historical-deal migration loaded records but left aca_count/dental_count/
vision_count = 0, so the portal's "deals = members" metric reads far below the
real volume. The original deal is preserved in deals.legacy_data (JSONB), so we
derive the counts from it:

  typeOfCoverage contains MEDICAL -> aca_count    = applicantsMedical
                                                    (else numberOfApplicants, else 1), min 1
  typeOfCoverage contains DENTAL  -> dental_count = applicantsDental (else 1), min 1
  typeOfCoverage contains VISION  -> vision_count = applicantsVision (else 1), min 1
  (a product NOT in typeOfCoverage stays 0)

Scope: ONLY rows where legacy_data IS NOT NULL (the 14,253 migrated [OLD] deals).
Touches ONLY the three count columns. Nothing else. Idempotent.

Dry-run by default (prints current vs projected member totals); --commit to write.
Env: TARGET_DB_URL
"""
import os
import sys

import psycopg2

COMMIT = "--commit" in sys.argv
# --per-deal: count 1 per product in coverage (ignore applicant counts) -> ~14-16k.
# default: members = people (uses applicant counts) -> ~25k.
PER_DEAL = "--per-deal" in sys.argv

# Numeric-safe extraction (handles "3", "3.0", null) -> int.
def n(field):
    return f"floor(NULLIF((legacy_data->>'{field}'),'')::numeric)::int"

COV = "upper(coalesce(legacy_data->>'typeOfCoverage',''))"
if PER_DEAL:
    ACA = f"CASE WHEN {COV} LIKE '%MEDICAL%' THEN 1 ELSE 0 END"
    DEN = f"CASE WHEN {COV} LIKE '%DENTAL%'  THEN 1 ELSE 0 END"
    VIS = f"CASE WHEN {COV} LIKE '%VISION%'  THEN 1 ELSE 0 END"
else:
    ACA = f"CASE WHEN {COV} LIKE '%MEDICAL%' THEN GREATEST(COALESCE({n('applicantsMedical')}, {n('numberOfApplicants')}, 1), 1) ELSE 0 END"
    DEN = f"CASE WHEN {COV} LIKE '%DENTAL%'  THEN GREATEST(COALESCE({n('applicantsDental')}, 1), 1) ELSE 0 END"
    VIS = f"CASE WHEN {COV} LIKE '%VISION%'  THEN GREATEST(COALESCE({n('applicantsVision')}, 1), 1) ELSE 0 END"


def main():
    url = os.environ.get("TARGET_DB_URL")
    if not url:
        sys.exit("TARGET_DB_URL required.")
    c = psycopg2.connect(url)
    cur = c.cursor()

    cur.execute("SELECT count(*) FROM deals WHERE legacy_data IS NOT NULL")
    scope = cur.fetchone()[0]
    cur.execute("SELECT coalesce(sum(aca_count+dental_count+vision_count),0) FROM deals WHERE legacy_data IS NOT NULL")
    before = cur.fetchone()[0]
    cur.execute(f"""SELECT coalesce(sum(({ACA})+({DEN})+({VIS})),0),
                           coalesce(sum({ACA}),0), coalesce(sum({DEN}),0), coalesce(sum({VIS}),0)
                    FROM deals WHERE legacy_data IS NOT NULL""")
    proj_total, proj_aca, proj_den, proj_vis = cur.fetchone()

    print(f"scope (migrated deals): {scope}")
    print(f"members now (aca+den+vis): {before}")
    print(f"members projected        : {proj_total}   (aca {proj_aca} / dental {proj_den} / vision {proj_vis})")

    if COMMIT:
        cur.execute(f"""UPDATE deals SET aca_count=({ACA}), dental_count=({DEN}), vision_count=({VIS})
                        WHERE legacy_data IS NOT NULL""")
        print(f"\nUPDATED {cur.rowcount} rows.")
        c.commit()
        cur.execute("SELECT coalesce(sum(aca_count+dental_count+vision_count),0) FROM deals WHERE legacy_data IS NOT NULL")
        print("members after commit:", cur.fetchone()[0])
    else:
        print("\nDRY RUN — no changes. Re-run with --commit to apply.")
    c.close()


if __name__ == "__main__":
    main()
