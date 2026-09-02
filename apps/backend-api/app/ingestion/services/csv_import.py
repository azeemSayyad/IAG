"""
CSV Import Service (Step 3.1)

Handles CSV file uploads for bulk lead import.
"""

import csv
import io
import json
from typing import Dict, List, Tuple
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.ingestion.services.validation import (
    validate_lead_row,
    validate_csv_headers,
    normalize_row,
)
from app.ingestion.services.deduplication import find_duplicate_lead, merge_lead_data
from app.ingestion.services.events import on_lead_created
from app.core.config import settings


class CSVImportResult:
    def __init__(self):
        self.total_rows: int = 0
        self.successful: int = 0
        self.failed: int = 0
        self.skipped_duplicates: int = 0
        self.errors: List[Dict] = []
        self.leads: List[Lead] = []

    def to_dict(self) -> dict:
        return {
            "total_rows": self.total_rows,
            "successful": self.successful,
            "failed": self.failed,
            "skipped_duplicates": self.skipped_duplicates,
            "errors": self.errors[:50],  # Limit error output
        }


def parse_csv(file_content: str) -> Tuple[List[str], List[dict]]:
    """
    Parse CSV content into headers and rows.

    Returns (headers, rows).
    """
    # Strip a leading UTF-8 BOM (U+FEFF). Excel / Google Sheets "CSV UTF-8"
    # exports prepend one, which would otherwise corrupt the FIRST header (it
    # becomes U+FEFF followed by "first_name"), so validate_csv_headers reports
    # first_name as missing and the whole file imports as 0 leads. str.strip()
    # does NOT remove U+FEFF, so drop it explicitly here — the one chokepoint
    # every CSV path flows through.
    if file_content and ord(file_content[0]) == 0xFEFF:
        file_content = file_content[1:]
    reader = csv.DictReader(io.StringIO(file_content))
    headers = reader.fieldnames or []
    rows = [row for row in reader]
    return headers, rows


def import_leads_from_csv(
    db: Session,
    tenant_id: str,
    file_content: str,
    source: str = "csv_import",
    dedup_mode: str = "skip",  # "skip" or "merge"
) -> CSVImportResult:
    """
    Import leads from CSV file content.

    Args:
        db: Database session
        tenant_id: Tenant ID
        file_content: Raw CSV file content
        source: Lead source override
        dedup_mode: How to handle duplicates ("skip" or "merge")

    Returns:
        CSVImportResult with stats and errors.
    """
    result = CSVImportResult()

    # Parse CSV
    try:
        headers, rows = parse_csv(file_content)
    except Exception as e:
        result.errors.append({"row": 0, "error": f"CSV parse error: {str(e)}"})
        return result

    # Validate headers
    is_valid, missing = validate_csv_headers(headers)
    if not is_valid:
        result.errors.append({"row": 0, "error": f"Missing required headers: {missing}"})
        return result

    result.total_rows = len(rows)

    # Dedupe ONLY within this file. The same phone uploaded in a DIFFERENT file
    # is intentionally allowed through as a fresh lead so it re-runs the full
    # SMS -> booking process.
    seen_phones = set()
    for row_number, raw_row in enumerate(rows, start=1):
        try:
            # Normalize row
            row = normalize_row(raw_row)

            # Override source if provided
            if source:
                row["source"] = source

            # Validate
            validation = validate_lead_row(row, row_number)
            if not validation.is_valid:
                result.failed += 1
                result.errors.append({
                    "row": row_number,
                    "errors": validation.errors,
                })
                continue

            # Duplicate check is WITHIN THIS FILE ONLY: the same phone appearing
            # twice in one upload is collapsed to a single lead. The same phone
            # uploaded in a DIFFERENT file is allowed through as a fresh lead so
            # it re-runs the full SMS -> booking process.
            if row["phone"] in seen_phones:
                result.skipped_duplicates += 1
                result.errors.append({
                    "row": row_number,
                    "error": f"Duplicate within file (phone: {row['phone']})",
                })
                continue
            seen_phones.add(row["phone"])

            # Create new lead
            lead = Lead(
                tenant_id=tenant_id,
                source=row.get("source", source),
                first_name=row["first_name"],
                last_name=row["last_name"],
                phone=row["phone"],
                email=row.get("email"),
                state=row.get("state"),
                city=row.get("city"),
                zip_code=row.get("zip_code"),
                tags=row.get("tags", "").split(",") if row.get("tags") else [],
            )
            db.add(lead)
            db.flush()

            # Trigger lead created event
            on_lead_created(db, lead)

            result.leads.append(lead)
            result.successful += 1

        except Exception as e:
            result.failed += 1
            result.errors.append({
                "row": row_number,
                "error": str(e),
            })

    db.commit()

    # NOTE: this per-row importer handles small uploads (<=500 rows). It blasts
    # outreach immediately via on_lead_created and does NOT engage the capacity
    # engine — pacing/holding applies only to large bulk imports (see
    # bulk_import_leads_from_csv, >500 rows).
    return result


def bulk_import_leads_from_csv(
    db: Session,
    tenant_id: str,
    file_content: str,
    source: str = "csv_import",
    dedup_mode: str = "skip",
    batch_size: int = 2000,
    campaign_id: str = None,
) -> CSVImportResult:
    """
    High-throughput bulk importer for large CSVs (lakhs / 100k+ rows).

    Optimizations vs. the per-row importer:
      - batch timezone resolution (unique ZIPs, cached -> ~0.1ms each)
      - single bulk dedup query (phones IN (...)) instead of one query per row
      - bulk INSERT (execute_values) in batches, one commit per batch
      - pipelined Redis enqueue of outreach jobs
      - one summary audit log instead of one per lead
    Measured ~16k inserts/sec vs ~213/sec for the per-row path.
    """
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz
    from app.core.redis import redis_service
    from app.core.audit import log_ai_action
    from app.ingestion.services.scoring import calculate_lead_score, get_score_tier
    from app.models.lead import Lead  # noqa

    result = CSVImportResult()
    try:
        headers, rows = parse_csv(file_content)
    except Exception as e:
        result.errors.append({"row": 0, "error": f"CSV parse error: {str(e)}"})
        return result
    is_valid, missing = validate_csv_headers(headers)
    if not is_valid:
        result.errors.append({"row": 0, "error": f"Missing required headers: {missing}"})
        return result
    result.total_rows = len(rows)

    # 1) normalize + validate
    norm = []
    for i, raw in enumerate(rows, start=1):
        row = normalize_row(raw)
        if source:
            row["source"] = source
        v = validate_lead_row(row, i)
        if not v.is_valid:
            result.failed += 1
            result.errors.append({"row": i, "errors": v.errors})
            continue
        norm.append(row)

    # 2) Dedup is WITHIN THIS FILE ONLY (handled by `seen` below). The same
    # phone uploaded in a DIFFERENT file is intentionally allowed through as a
    # fresh lead so it re-runs the full SMS -> booking process. (No cross-file
    # existing-lead query.)

    # 3) Customer-facing display timezone: Eastern, except Texas leads -> Central.
    #    State-based only (no ZIP/Geoapify lookup).
    from app.core.timezones import lead_display_timezone

    # 4) build mappings (skip dupes), score in-memory
    #
    # Appointment Capacity Engine: when SAME_DAY_PACING_ENABLED is on, imported
    # leads are HELD (lifecycle 'pending_outreach', pacing_status 'held') and NOT
    # enqueued here — the capacity controller releases them in waves to match
    # real per-state appointment capacity. With the flag off, behavior is exactly
    # as before (lifecycle 'new' + immediate outreach enqueue).
    # Campaign uploads ALWAYS hold their leads (sent only when the campaign is
    # "run"), regardless of the global pacing flag.
    from app.core import engine_flags
    _pacing = bool(campaign_id) or engine_flags.same_day_pacing_enabled()
    _lifecycle = "pending_outreach" if _pacing else "new"
    _pstatus = "held" if _pacing else None
    now = _dt.now(_tz.utc)
    mappings, jobs, seen = [], [], set()
    for r in norm:
        ph = r["phone"]
        if ph in seen:                       # within-file duplicate only
            result.skipped_duplicates += 1
            continue
        seen.add(ph)
        score = calculate_lead_score(r, created_at=now)
        tier = get_score_tier(score)
        lid = str(_uuid.uuid4())
        mappings.append({
            "id": lid, "tenant_id": tenant_id, "source": r.get("source", source),
            "first_name": r["first_name"], "last_name": r["last_name"], "phone": ph,
            "email": r.get("email"), "state": r.get("state"), "city": r.get("city"),
            "zip_code": r.get("zip_code"), "timezone": lead_display_timezone(r.get("state")),
            "lead_score": score, "status": "new", "lifecycle_stage": _lifecycle,
            "pacing_status": _pstatus, "priority_score": float(score or 0),
            "campaign_id": campaign_id,
        })
        jobs.append({"lead_id": lid, "tenant_id": tenant_id,
                     "lead_name": f"{r['first_name']} {r['last_name']}", "phone": ph,
                     "source": r.get("source", source), "score": score, "tier": tier,
                     "kind": "first_template"})   # the ONLY message allowed past the send chokepoint

    # 5) bulk INSERT in batches (one commit per batch)
    insert_sql = text(
        "INSERT INTO leads (id,tenant_id,source,first_name,last_name,phone,email,state,city,zip_code,"
        "timezone,lead_score,status,lifecycle_stage,pacing_status,priority_score,campaign_id,created_at,updated_at) "
        "VALUES (:id,:tenant_id,:source,:first_name,:last_name,:phone,:email,:state,:city,:zip_code,"
        ":timezone,:lead_score,:status,:lifecycle_stage,:pacing_status,:priority_score,:campaign_id,now(),now())"
    )
    for i in range(0, len(mappings), batch_size):
        batch = mappings[i:i + batch_size]
        db.execute(insert_sql, batch)
        db.commit()
        result.successful += len(batch)

    # 6) outreach enqueue — SKIPPED when pacing holds the leads for controlled release.
    if campaign_id:
        # Campaign leads stay held; they are released only when the campaign is
        # "run" (per-campaign drip). Do NOT kick off a release wave here.
        pass
    elif _pacing:
        try:
            from app.pacing.release import on_import_complete
            on_import_complete(db, tenant_id, len(mappings))
        except Exception:
            pass
    else:
        # pipelined Redis enqueue of outreach jobs (immediate-dispatch worker drains them)
        try:
            client = redis_service.client
            pipe = client.pipeline()
            for j in jobs:
                pipe.rpush("queue:outbound_sms", json.dumps(j))
            pipe.execute()
        except Exception:
            for j in jobs:
                try:
                    redis_service.enqueue_sms(j)
                except Exception:
                    pass

    # 7) one summary audit log (not per-lead)
    try:
        log_ai_action(tenant_id=tenant_id, action="bulk_import",
                      resource_type="lead", resource_id=None,
                      details={"source": source, "imported": result.successful,
                               "skipped": result.skipped_duplicates, "failed": result.failed})
    except Exception:
        pass
    return result
