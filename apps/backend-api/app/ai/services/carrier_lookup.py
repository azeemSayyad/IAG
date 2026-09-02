"""Per-number recipient-carrier lookup, Redis-cached.

Mixed-channel reality: every provider/route carries many recipient carriers, so the
carrier must be resolved per destination NUMBER, not per provider. US number
portability means the prefix does NOT reveal the carrier, so real number->carrier
data can only come from an external source: an HLR / number-lookup API, or a lead
list already enriched with carrier. This module is the cached front for that source.

Split of duties (so a blast never does N live lookups in the hot send path):
  get(number)              -> CACHE-ONLY read. Used by the send path. Fast; returns
                              "" (unknown) on a miss — never makes a network call.
  put(number, carrier)     -> cache one number's carrier (e.g. from an enriched lead).
  put_bulk(mapping)        -> cache many at once.
  set_backend(fn)          -> install a live lookup (your HLR / number-lookup API).
  enrich(number)/enrich_bulk(numbers) -> call the backend for misses and cache the
                              result. Meant for a BACKGROUND/ingestion job, not the
                              send path.

Safe by default: with no backend and an empty cache, every number is "" -> never
classified T-Mobile, so the caps observe nothing until carrier data is provided. The
carrier rarely changes, so entries are cached for CARRIER_LOOKUP_CACHE_DAYS.
"""
from typing import Callable, Dict, Iterable, Optional

from app.core.config import settings

_KEY = "carrier:num:{digits}"
_BACKEND: Optional[Callable[[str], str]] = None


def _digits(num) -> str:
    """Canonical key for a US number: digits only, with the leading '1' country code
    dropped, so '+1 (305) 555-1234' and '3055551234' resolve to the same entry."""
    d = "".join(c for c in str(num or "") if c.isdigit())
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d


def _norm(name) -> str:
    return " ".join(str(name or "").strip().lower().split())


def _client():
    from app.core.redis import redis_service
    return redis_service.client


def _ttl() -> int:
    return max(1, int(getattr(settings, "CARRIER_LOOKUP_CACHE_DAYS", 30))) * 86400


def set_backend(fn: Optional[Callable[[str], str]]) -> None:
    """Install (or clear with None) the live lookup used by enrich()/enrich_bulk().
    The function takes a destination number and returns a carrier name."""
    global _BACKEND
    _BACKEND = fn


def put(number: str, carrier: str) -> None:
    d, c = _digits(number), _norm(carrier)
    if not d or not c:
        return
    try:
        cl = _client()
        key = _KEY.format(digits=d)
        cl.set(key, c)
        cl.expire(key, _ttl())
    except Exception:
        pass


def put_bulk(mapping: Dict[str, str]) -> int:
    n = 0
    for num, car in (mapping or {}).items():
        put(num, car)
        n += 1
    return n


def get(number: str) -> str:
    """Cached carrier for a number ("" if not cached). CACHE-ONLY — never calls the
    backend, so it's safe to use in the hot send path."""
    d = _digits(number)
    if not d:
        return ""
    try:
        v = _client().get(_KEY.format(digits=d))
        if v is not None:
            return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)
    except Exception:
        pass
    return ""


def enrich(number: str) -> str:
    """Resolve + cache one number via the live backend (for a background/ingestion
    job, NOT the send path). Returns the carrier, or "" if no backend / on error."""
    cached = get(number)
    if cached:
        return cached
    if _BACKEND is None:
        return ""
    try:
        carrier = _norm(_BACKEND(number))
    except Exception:
        carrier = ""
    if carrier:
        put(number, carrier)
    return carrier


def enrich_bulk(numbers: Iterable[str]) -> Dict[str, str]:
    """Resolve + cache many numbers via the backend. Returns {number: carrier} for the
    ones that resolved. Run this from a job/at ingestion so the send path stays a pure
    cache read."""
    out: Dict[str, str] = {}
    for num in (numbers or []):
        c = enrich(num)
        if c:
            out[num] = c
    return out


def _dig_path(data, dotted: str):
    """Pull a nested value out of a JSON dict by a dotted path (e.g. 'data.carrier.name')."""
    for key in (dotted or "carrier").split("."):
        data = data.get(key) if isinstance(data, dict) else None
        if data is None:
            return None
    return data


def http_backend(number: str) -> str:
    """Generic HTTP carrier lookup, driven entirely by env so ANY number-lookup service
    plugs in with no code:
      CARRIER_LOOKUP_URL     endpoint; "{number}" -> the digit string. (Off until set.)
      CARRIER_LOOKUP_METHOD  "GET" (default) or "POST".
      CARRIER_LOOKUP_AUTH    convenience -> Authorization header.
      CARRIER_LOOKUP_HEADERS JSON object of extra headers, e.g. {"X-API-Key":"abc"}.
      CARRIER_LOOKUP_BODY    POST only: JSON body template ("{number}" allowed).
      CARRIER_LOOKUP_FIELD   dotted path to the carrier in the JSON response.
    Returns the carrier name, or "" if not configured / on any error. Used only by
    enrich()/enrich_bulk() (a background job), never in the hot send path."""
    url = (getattr(settings, "CARRIER_LOOKUP_URL", "") or "").strip()
    if not url:
        return ""
    try:
        import json as _json
        import httpx
        digits = _digits(number)
        headers: Dict[str, str] = {}
        auth = (getattr(settings, "CARRIER_LOOKUP_AUTH", "") or "").strip()
        if auth:
            headers["Authorization"] = auth
        raw_headers = (getattr(settings, "CARRIER_LOOKUP_HEADERS", "") or "").strip()
        if raw_headers:
            try:
                extra = _json.loads(raw_headers)
                if isinstance(extra, dict):
                    headers.update({str(k): str(v) for k, v in extra.items()})
            except Exception:
                pass
        url = url.replace("{number}", digits)
        method = (getattr(settings, "CARRIER_LOOKUP_METHOD", "GET") or "GET").strip().upper()
        if method == "POST":
            body = (getattr(settings, "CARRIER_LOOKUP_BODY", "") or "").strip()
            payload = _json.loads(body.replace("{number}", digits)) if body else None
            resp = httpx.post(url, headers=headers, json=payload, timeout=8.0)
        else:
            resp = httpx.get(url, headers=headers, timeout=8.0)
        if resp.status_code >= 400:
            return ""
        val = _dig_path(resp.json(), getattr(settings, "CARRIER_LOOKUP_FIELD", "carrier") or "carrier")
        return _norm(val) if isinstance(val, str) else ""
    except Exception:
        return ""


# Default the live backend to the generic HTTP connector: setting CARRIER_LOOKUP_URL
# activates enrichment automatically; with no URL it is a safe no-op.
_BACKEND = http_backend
