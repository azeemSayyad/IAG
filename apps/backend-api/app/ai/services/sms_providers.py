"""Lead-SMS provider registry.

Two independent providers share ONE pipeline codebase, keyed by a provider string:

  * "sinch"   — the original lead-SMS pipeline (reads ENGAGECLOUD_* / ENGAGE_CLOUD_*).
                Behaviour is byte-identical to before this registry existed.
  * "engage2" — a SECOND, fully independent pipeline (reads ENGAGE2_*) selectable per
                campaign as the "Engage Cloud" chip. Different account/creds/numbers/
                webhook; dormant until ENGAGE2_* is filled.

Everything downstream (send chokepoint, sender pool, webhooks, reply poll) takes a
`provider` argument and resolves its config here, so the two pipelines never share
runtime state and Sinch is untouched when provider defaults to "sinch".
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings

# Canonical provider keys.
SINCH = "sinch"
ENGAGE2 = "engage2"
PROVIDERS = (SINCH, ENGAGE2)

# UI / campaign labels that map onto the engage2 pipeline. Anything not listed here
# (including "", None, "sinch") resolves to the original Sinch pipeline, so a missing
# or unknown selection can never accidentally divert Sinch traffic.
_ENGAGE2_ALIASES = {"engage2", "engage_cloud", "engagecloud", "engage cloud", "engage-cloud"}


def normalize_provider(provider) -> str:
    """Canonical provider key for any UI/campaign value. Defaults to SINCH."""
    p = str(provider or "").strip().lower()
    return ENGAGE2 if p in _ENGAGE2_ALIASES else SINCH


@dataclass(frozen=True)
class ProviderConfig:
    key: str            # canonical key: "sinch" | "engage2"
    name: str           # stored on records / used for delivery-webhook matching
    api_key: str
    api_secret: str
    base_url: str
    agency_id: str
    use_new_auth: bool
    from_numbers: str   # raw comma/semicolon-separated DID list
    webhook_secret: str
    sms_source: str


def get_provider_config(provider=SINCH) -> ProviderConfig:
    """Resolve the config for a provider. Unknown/blank => Sinch (the original)."""
    if normalize_provider(provider) == ENGAGE2:
        return ProviderConfig(
            key=ENGAGE2,
            name="engage2",
            api_key=settings.ENGAGE2_API_KEY,
            api_secret=settings.ENGAGE2_API_SECRET,
            base_url=settings.ENGAGE2_API_BASE_URL,
            agency_id=settings.ENGAGE2_AGENCY_ID,
            use_new_auth=settings.ENGAGE2_USE_NEW_AUTH,
            from_numbers=settings.ENGAGE2_FROM_NUMBERS,
            webhook_secret=settings.ENGAGE2_WEBHOOK_SECRET,
            sms_source=settings.ENGAGE2_SMS_SOURCE,
        )
    # Default: Sinch — the existing ENGAGECLOUD_* config. provider_name stays
    # "engage_cloud" so historical Message.provider records + delivery-webhook
    # matching are unchanged.
    return ProviderConfig(
        key=SINCH,
        name="engage_cloud",
        api_key=settings.ENGAGECLOUD_API_KEY,
        api_secret=settings.ENGAGECLOUD_API_SECRET,
        base_url=settings.ENGAGECLOUD_API_BASE_URL,
        agency_id=settings.ENGAGECLOUD_AGENCY_ID,
        use_new_auth=settings.ENGAGECLOUD_USE_NEW_AUTH,
        from_numbers=settings.ENGAGECLOUD_FROM_NUMBERS,
        webhook_secret=settings.ENGAGE_CLOUD_WEBHOOK_SECRET,
        sms_source=settings.ENGAGECLOUD_SMS_SOURCE,
    )


def _is_real(value: str) -> bool:
    v = str(value or "").strip().lower()
    return bool(v) and v not in {
        "placeholder", "your_key_here", "your_secret_here",
        "your_api_key_here", "your_api_secret_here",
    }


def is_configured(provider=SINCH) -> bool:
    """True when the provider has real api credentials."""
    cfg = get_provider_config(provider)
    return _is_real(cfg.api_key) and _is_real(cfg.api_secret)
