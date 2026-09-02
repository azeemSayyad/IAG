"""Hardcoded opt-out / profanity / scam filter for the SMS lead pool.

NO AI/ML — a deterministic word list. A lead whose inbound message matches is
parked as Unqualified (and added to the Do-Not-Call list) instead of entering
the pool, so agents never see opt-outs, abuse, or obvious junk.

Two match modes:
  EXACT_BLOCK     — the WHOLE normalized message must equal an entry. Normalized
                    = lowercased, trimmed, punctuation stripped, whitespace
                    collapsed. Keeps short words safe: "no" alone blocks, but
                    "no I need Medicare" passes.
  SUBSTRING_BLOCK — the phrase appearing ANYWHERE in the (lowercased) message
                    blocks it. Reserved for strong signals (profanity, slurs,
                    explicit harassment / opt-out phrases, scam accusations).
"""
import re

# NOTE: extend EXACT_BLOCK / SUBSTRING_BLOCK below as new opt-out phrasings show
# up in real inbound replies — it's a plain hardcoded list, safe to edit.
_PUNCT_RE = re.compile(r"[^\w\s]+")
_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, trim."""
    t = (text or "").lower()
    t = _PUNCT_RE.sub("", t)
    t = _WS_RE.sub(" ", t).strip()
    return t


# --- EXACT: whole normalized message must equal one of these ----------------
_EXACT_SOURCE = [
    # STOP variants (pure)
    "stop", "stopp", "stoop", "stpo", "s t o p", "stop it", "stop now",
    "stop texting", "stop messaging", "stop calling", "stop please", "stop all",
    "stopall", "end",
    # Opt-out
    "unsubscribe", "unsubscribe me", "opt out", "optout",
    # Spanish / other-language stop
    "pare", "detener", "alto", "basta",
    # Cancel / remove
    "cancel", "cancelar", "remove", "remove me", "take me off",
    "delete my number", "delete me",
    # Pure NO
    "no", "nope", "nah", "na", "no thanks", "no thank you", "no gracias",
    "no grasias", "not interested", "not intrested", "not interesed",
    "not intersted",
    # Leave-me-alone
    "leave me alone", "quit", "quit texting", "dont text me", "don't text me",
    "dont call me", "don't call me", "dont contact me", "don't contact me",
    # Wrong person
    "wrong number", "wrong person", "who is this", "who r u", "who are you",
    # Dismissals seen in real data
    "fake", "huh", "spam",
]
# Normalize once so apostrophes/punctuation in the source collapse to canonical.
EXACT_BLOCK = {_normalize(p) for p in _EXACT_SOURCE if _normalize(p)}


# --- SUBSTRING: phrase anywhere in the lowercased message -------------------
# Kept lowercased but with punctuation intact, so "f*ck"/"a**hole"/"f u " match.
SUBSTRING_BLOCK = [
    # Profanity / aggression
    "fuck", "fuk", "fck", "f off", "f*ck", "f u ", "shit", "bullshit",
    "dont bullshit", "don't bullshit", "go to hell", "asshole", "a**hole",
    "bitch", "b****", "motherfuck", "mother fuck", "mother f", "go suck",
    # Slurs (zero tolerance)
    "nigger", "nigga", "faggot",
    # Harassment / opt-out phrases that span words
    "leave me alone", "dont harass", "don't harass", "stop harass",
    "dont text me", "don't text me", "do not text me", "dont contact me",
    "don't contact me", "do not contact me", "dont call me", "don't call me",
    "do not call me", "take me off", "take my name off", "stop texting",
    "stop messaging", "stop calling", "unsubscribe", "report you",
    "reported you", "sue you", "sue me", "lawsuit",
    # Scam accusations (strong rejection signal)
    "scammer", "this is a scam", "this is spam", "this is fake",
]


def block_reason(text: str) -> str | None:
    """Return the matched phrase if this message must be auto-parked as
    Unqualified, else None. Hardcoded — no AI/ML."""
    if not text or not text.strip():
        return None
    norm = _normalize(text)
    if norm in EXACT_BLOCK:
        return norm
    low = text.lower()
    for phrase in SUBSTRING_BLOCK:
        if phrase in low:
            return phrase
    return None
