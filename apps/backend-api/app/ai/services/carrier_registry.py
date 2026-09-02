"""Carrier registry — which carrier each outbound DID belongs to, plus each
carrier's safe limits. Lets the sender pool span multiple carriers and overflow
from one to the next automatically when a carrier hits its safe ceiling.

Config-driven, no DB. ``CARRIER_POOLS_JSON`` (settings) defines the carriers; when
it is empty the registry is a single ``sinch`` carrier built from
``ENGAGECLOUD_FROM_NUMBERS`` — i.e. behaviour identical to today until more carriers
are configured.

Carrier dict shape::

    {"name": str, "priority": int (lower = tried first), "role": "primary"|"reserve",
     "daily_cap": int, "mps": int, "numbers": ["+1...", ...]}

Pure helpers (parse_carrier_pools / carrier_of_map / ordered_numbers) are split from
the settings-backed accessors so they unit-test without any config or DB.
"""
import json
from typing import Dict, List

from app.core.config import settings


def _norm(n: str) -> str:
    d = "".join(c for c in str(n) if c.isdigit() or c == "+")
    if d and not d.startswith("+"):
        d = "+" + d
    return d


def _split_numbers(raw) -> List[str]:
    items = raw if isinstance(raw, list) else str(raw or "").replace(";", ",").split(",")
    out = []
    for s in items:
        n = _norm(str(s).strip())
        if n:
            out.append(n)
    return out


# ---------------------------------------------------------------------------
# PURE helpers (no settings / no DB) — unit-tested directly.
# ---------------------------------------------------------------------------
def parse_carrier_pools(json_str: str, default_numbers, default_cap: int = 2000,
                        default_mps: int = 1) -> List[Dict]:
    """Parse CARRIER_POOLS_JSON into a normalized carrier list. Empty or invalid
    JSON -> a single primary 'sinch' carrier from default_numbers (today's behaviour),
    so a bad config can never wipe out the fleet."""
    carriers: List[Dict] = []
    raw = (json_str or "").strip()
    if raw:
        try:
            for c in json.loads(raw):
                nums = _split_numbers(c.get("numbers"))
                if not nums:
                    continue
                carriers.append({
                    "name": str(c.get("name") or "carrier"),
                    "priority": int(c.get("priority", 1)),
                    "role": "reserve" if str(c.get("role")) == "reserve" else "primary",
                    "daily_cap": int(c.get("daily_cap", default_cap)),
                    "mps": int(c.get("mps", default_mps)),
                    # Carrier-wide sends-per-second ceiling (across ALL its numbers).
                    # 0 = no carrier-level throttle (rely on the per-DID 1/sec cap).
                    "max_per_sec": int(c.get("max_per_sec", 0)),
                    # Recipient carrier this provider/route delivers to (e.g. "tmobile"),
                    # for the DID-fleet T-Mobile cap. "" = unknown / mixed.
                    "recipient_carrier": " ".join(str(c.get("recipient_carrier") or "").strip().lower().split()),
                    "numbers": nums,
                })
        except Exception:
            carriers = []
    if not carriers:
        carriers = [{
            "name": "sinch", "priority": 1, "role": "primary",
            "daily_cap": int(default_cap), "mps": int(default_mps), "max_per_sec": 0,
            "recipient_carrier": "", "numbers": _split_numbers(default_numbers),
        }]
    return carriers


def carrier_of_map(carriers: List[Dict]) -> Dict[str, str]:
    """number -> carrier name. First carrier listed wins a duplicate number."""
    out: Dict[str, str] = {}
    for c in carriers:
        for n in c["numbers"]:
            out.setdefault(n, c["name"])
    return out


def ordered_numbers(carriers: List[Dict], include_reserve: bool = True) -> List[str]:
    """Numbers in failover order: primary carriers first (by ascending priority),
    reserve carriers last. Declared order kept within a carrier; dupes removed."""
    prim = sorted([c for c in carriers if c["role"] == "primary"], key=lambda c: c["priority"])
    res = sorted([c for c in carriers if c["role"] == "reserve"], key=lambda c: c["priority"])
    out: List[str] = []
    seen = set()
    for c in prim + (res if include_reserve else []):
        for n in c["numbers"]:
            if n not in seen:
                seen.add(n)
                out.append(n)
    return out


# ---------------------------------------------------------------------------
# settings-backed accessors.
# ---------------------------------------------------------------------------
def load_carriers() -> List[Dict]:
    cap = int(getattr(settings, "SENDER_DAILY_CAP", 2000))
    return parse_carrier_pools(getattr(settings, "CARRIER_POOLS_JSON", ""),
                               settings.ENGAGECLOUD_FROM_NUMBERS or "", cap, 1)


def carrier_of(number: str) -> str:
    return carrier_of_map(load_carriers()).get(_norm(number), "sinch")


def recipient_carrier_of(provider_name: str) -> str:
    """Recipient carrier a provider/route delivers to (e.g. 'tmobile'), '' if unknown.
    Source order: the pool's inline `recipient_carrier`, then the TMOBILE_PROVIDERS
    env list ("we have T-Mobile through Sinch")."""
    p = " ".join(str(provider_name or "").strip().lower().split())
    if not p:
        return ""
    for c in load_carriers():
        if str(c.get("name", "")).strip().lower() == p:
            rc = str(c.get("recipient_carrier") or "").strip().lower()
            if rc:
                return rc
            break
    tmo = {x.strip().lower() for x in str(getattr(settings, "TMOBILE_PROVIDERS", "")).split(",") if x.strip()}
    return "tmobile" if p in tmo else ""


def primary_numbers() -> List[str]:
    return ordered_numbers(load_carriers(), include_reserve=False)


def reserve_numbers() -> List[str]:
    prim = set(primary_numbers())
    return [n for n in ordered_numbers(load_carriers(), include_reserve=True) if n not in prim]


def all_numbers() -> List[str]:
    return ordered_numbers(load_carriers(), include_reserve=True)
