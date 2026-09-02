"""One-off backfill: drain EngageCloud's UNCONFIRMED reply backlog into the Lead Pool.

Why this exists
---------------
Inbound replies the OLD code dropped (sender didn't match a lead → `no_lead_found`)
were never confirmed back to EngageCloud, so they're still sitting unconfirmed in
its `/replies` queue. The 5s poll (Celery beat → worker) re-pulls and captures them
automatically once the new code is deployed — but only if the worker+beat are
running. This script force-drains that backlog on demand through the SAME fixed
capture (format-agnostic match → else auto-create → block-word filter → Lead Pool
or Parked-Unqualified), so you don't have to wait for the poll.

Safe to run repeatedly: each processed reply is confirmed (removed) at EngageCloud,
so it's never re-pulled or double-processed. Sends nothing (inbound capture only;
the first-template-only lockdown is untouched).

Run on a host with the backend code + DB + Redis (e.g. the Render web/worker shell):
    PYTHONPATH=apps/backend-api python apps/backend-api/scripts/drain_sinch_backlog.py
"""
import json
import os
import sys
import time

# Make `app.*` importable regardless of CWD.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.database import SessionLocal
from app.ai.services.reply_polling import poll_provider_replies_once

MAX_ROUNDS = int(os.getenv("DRAIN_MAX_ROUNDS", "100"))


def reset_locks() -> None:
    """Clear stale per-reply locks + content-dedup keys so replies the poll has
    been SKIPPING (stuck on an old 24h lock left by an interrupted run / deploy)
    get re-pulled and captured. Safe: the durable dedup is the provider confirming
    the reply, not these keys."""
    from app.core.redis import redis_service
    n = 0
    for pattern in ("sinch:reply:*", "inbound:dedup:*"):
        try:
            for key in redis_service.client.scan_iter(match=pattern, count=500):
                redis_service.client.delete(key)
                n += 1
        except Exception as exc:
            print(f"  reset {pattern}: {exc}")
    print(f"  cleared {n} stale lock/dedup key(s)")


def inspect() -> None:
    """Dump what /replies actually returns + WHY each reply would be skipped, so we
    can tell apart: malformed parse (field-name mismatch), a stale lock, or content
    dedup. Read-only — captures nothing."""
    from app.ai.services.communication_provider import communication_service
    from app.core.redis import redis_service
    f = communication_service.fetch_replies()
    if not f.get("success"):
        print(f"  /replies fetch FAILED: {f.get('error')} (status {f.get('status_code')})")
        return
    replies = f.get("replies", [])
    print(f"  /replies returned {len(replies)} reply(ies):")
    for idx, r in enumerate(replies, 1):
        rid, frm, body = r.get("reply_id"), r.get("from"), r.get("body")
        reasons = []
        if not rid:
            reasons.append("missing reply_id")
        if not frm:
            reasons.append("missing from (source_number)")
        if not body:
            reasons.append("empty body (content)")
        try:
            if rid and redis_service.client.get(f"sinch:reply:{rid}"):
                reasons.append("LOCKED (stale sinch:reply key)")
        except Exception:
            pass
        raw = r.get("raw", {}) if isinstance(r, dict) else {}
        print(f"   [{idx}] reply_id={rid!r} from={frm!r} body={(body or '')[:50]!r}")
        print(f"       -> {'WOULD SKIP: ' + ', '.join(reasons) if reasons else 'OK — should be captured'}")
        # Full raw reply so we can see WHICH field actually holds the message text
        # (if `content` is empty, the body may live in `metadata`/another field).
        try:
            print(f"       raw: {json.dumps(raw, default=str)[:700]}")
        except Exception:
            print(f"       raw keys: {sorted(raw.keys())}")


def main() -> None:
    if "--inspect" in sys.argv:
        print("Inspecting EngageCloud /replies (read-only)...")
        inspect()
        return
    if "--reset" in sys.argv:
        print("Clearing stale reply locks / dedup keys first (--reset)...")
        reset_locks()
    total = {"processed": 0, "confirmed": 0, "skipped": 0, "failed": 0}
    print(f"Draining EngageCloud reply backlog (up to {MAX_ROUNDS} rounds)...")
    for i in range(MAX_ROUNDS):
        db = SessionLocal()
        try:
            r = poll_provider_replies_once(db)
        finally:
            db.close()
        if r.get("error"):
            # The /replies fetch itself failed (e.g. credentials not set, API error).
            print(f"  round {i + 1}: stopped — {r.get('error')} (status {r.get('status_code')})", flush=True)
            break
        for k in total:
            total[k] += int(r.get(k, 0) or 0)
        print(f"  round {i + 1}: {r}", flush=True)
        # Stop once a round captures nothing NEW — the rest are already-seen
        # (dedup/confirmed) or empty, so further rounds won't pull anything.
        if int(r.get("processed", 0) or 0) == 0:
            print("  nothing new to capture — done.", flush=True)
            break
        time.sleep(0.4)
    print(f"DONE — captured (processed)={total['processed']}, confirmed={total['confirmed']}, "
          f"skipped={total['skipped']}, failed={total['failed']}")


if __name__ == "__main__":
    main()
