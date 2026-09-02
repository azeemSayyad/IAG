"""Real-time capacity metrics: per-state fill %, in-flight, waitlist, waste.

`snapshot()` computes a live view on demand (used by the dashboard endpoint).
`write_cycle()` is called by the controller each cycle to cache the snapshot and
push it to the realtime dashboard channel (best-effort).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List

from sqlalchemy.orm import Session

from app.core.redis import redis_service
from app.pacing import capacity, events

logger = logging.getLogger(__name__)


def _cache_key(tenant_id: str) -> str:
    return f"pace:metrics:{tenant_id}"


def snapshot(db: Session, tenant_id: str) -> Dict:
    """Live per-state + overall pacing metrics."""
    from app.core.config import settings

    held = events.states_with_leads(db, tenant_id, "held")
    waiting = events.states_with_leads(db, tenant_id, "awaiting_slot")
    released = events.states_with_leads(db, tenant_id, "released")
    done = events.states_with_leads(db, tenant_id, "booked")
    all_states = set(held) | set(waiting) | set(released) | set(done)

    rows: List[Dict] = []
    tot = {"slots_total": 0, "booked": 0, "slots_open": 0, "held": 0,
           "waitlisted": 0, "in_flight": 0, "done": 0}
    for st in sorted(all_states):
        cap = capacity.capacity_today(db, tenant_id, st or None)
        row = {
            "state": st or "(none)",
            "licensed_agents": cap["licensed_agents"],
            "slots_total": cap["slots_total"],
            "booked": cap["booked"],
            "slots_open": cap["slots_open"],
            "fill_pct": cap["fill_pct"],
            "held": held.get(st, 0),
            "waitlisted": waiting.get(st, 0),
            "in_flight": released.get(st, 0),
            "done": done.get(st, 0),
            "no_capacity": cap["licensed_agents"] == 0,
        }
        # "wasted": held leads in a state that can never be serviced (no agent).
        row["wasted"] = row["held"] if row["no_capacity"] else 0
        row["shortfall"] = max(0, cap["slots_open"])  # bookings still needed to fill today
        rows.append(row)
        for k in tot:
            tot[k] += row.get(k, 0)

    overall_fill = round(tot["booked"] / tot["slots_total"] * 100, 1) if tot["slots_total"] else 0.0
    overall = {**tot, "fill_pct": overall_fill,
               "wasted": sum(r["wasted"] for r in rows)}
    from app.core import engine_flags
    return {
        "enabled": engine_flags.same_day_pacing_enabled(),
        "dry_run": bool(getattr(settings, "PACING_DRY_RUN", True)),
        "states": rows,
        "overall": overall,
    }


def _emit_safe(tenant_id: str, payload: Dict) -> None:
    try:
        from app.realtime.websocket import emit_to_tenant
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(emit_to_tenant(tenant_id, "pacing_updated", payload))
        except RuntimeError:
            asyncio.run(emit_to_tenant(tenant_id, "pacing_updated", payload))
    except Exception as exc:  # pragma: no cover
        logger.debug("pacing emit skipped: %s", exc)


def write_cycle(db: Session, tenant_id: str, reports, dry_run: bool, wave_id: str) -> None:
    """Cache the latest snapshot and push it to the dashboard (best-effort)."""
    try:
        snap = snapshot(db, tenant_id)
        snap["last_wave"] = wave_id
        snap["dry_run"] = dry_run
        redis_service.set_cache(_cache_key(tenant_id), snap, ttl=3600)
        _emit_safe(tenant_id, snap)
    except Exception as exc:  # pragma: no cover
        logger.debug("write_cycle metrics skipped: %s", exc)


def cached(tenant_id: str):
    try:
        return redis_service.get_cache(_cache_key(tenant_id))
    except Exception:
        return None
