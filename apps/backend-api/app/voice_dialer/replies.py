"""Inbound-text classification and phone normalisation for the auto dialer.

THE RULE (owner's decision): every reply to our outreach is called, EXCEPT a
negative one. Negative = opt-out (STOP, unsubscribe, cancel, "block",
"do not disturb"/DND…), a plain no / "not interested", abuse (profanity,
slurs, threats, "scam"), or "wrong number". Every negative puts the number on
Do-Not-Call forever. Everything else is called: "who is this?", "where are
you?", "I'm fine", "I'm good", "did you eat?", "maybe", "how much?", "not now",
"call me later", "Hi", "?".

classify() also says whether the reply is a CLEAR yes: only that reopens a
client an agent already closed (a "thank you" after a sale must not ring them
again — see service.on_inbound_sms), and only that takes a number that opted
out (STOP…) back OFF the Do-Not-Call list ("start", "unstop", "yes", "ok",
"call me", "I'm interested", "I want a quote"…). A vague text from an
opted-out number ("cool", "hi") is saved and nothing else happens.
"""
from __future__ import annotations

import re

YES = "YES"              # clear yes / interest — called, and may reopen a closed client
REPLY = "REPLY"          # any other non-negative reply — called (first reply / regular pool)
STOP = "STOP"            # opt-out words                  -> DNC "OPTED_OUT"
NEGATIVE = "NEGATIVE"    # plain no / not interested      -> DNC "NOT_INTERESTED"
ABUSE = "ABUSE"          # profanity, slurs, threats, scam -> DNC "ABUSIVE"
WRONG = "WRONG"          # wrong number / wrong person    -> DNC "WRONG_NUMBER"
OTHER = REPLY            # old name, kept for callers
NEGATIVE_KINDS = (STOP, NEGATIVE, ABUSE, WRONG)
DNC_REASON = {STOP: "OPTED_OUT", NEGATIVE: "NOT_INTERESTED", ABUSE: "ABUSIVE", WRONG: "WRONG_NUMBER"}

# A reply that STARTS with one of these is a yes, whatever follows.
YES_WORDS = {
    "yes", "y", "ya", "yea", "yeah", "yep", "yup", "ye", "yess", "yas",
    "sure", "ok", "okay", "k", "kk", "okey", "oki", "alright", "allright", "aight",
    "absolutely", "definitely", "certainly", "correct", "right", "yesplease",
    # The carriers' standard opt-back-in words (after a STOP).
    "start", "unstop", "resubscribe", "subscribe",
}
YES_STARTS = (
    "of course", "go ahead", "sounds good", "that works", "please do", "why not",
    "si", "claro",
)
# …or that CONTAINS one of these anywhere.
INTEREST = re.compile(
    r"\b(call me|give me a call|please call|call (?:me )?(?:now|back|anytime|today)|you can call|can you call"
    r"|ring me|reach me|i'?m interested|i am interested|interested|i want|i need|i would like|i'?d like"
    r"|quote|sounds good|go ahead|why not|let'?s talk|lets do it|i'?m in|count me in|ready)\b"
)
EMOJI_YES = ("👍", "✅", "👌", "🙏", "☑")
# Any of these words means it is not a clean yes ("not sure", "don't know").
NEGATION = re.compile(r"\b(no|not|nope|nah|never|don'?t|dont|cant|can'?t|won'?t|wont|wrong|unsubscribe)\b")

STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "optout", "opt out", "revoke"}


def _clean(text: str | None) -> str:
    """Lowercase, punctuation/emoji stripped, whitespace collapsed."""
    t = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", t).strip()


# Opt-out: an exact message, or one of these phrases anywhere ("yes stop texting me").
STOP_PHRASES = re.compile(
    r"\b(stop|stopall|unsubscribe|opt ?out|remove me|take me off|take my (?:name|number) off"
    r"|do ?n'?o?t (?:text|call|contact|message) me|don'?t (?:text|call|contact|message) me"
    r"|leave me alone|delete my number|lose my number"
    r"|block(?:ed|ing)?|dnd|do ?n'?o?t disturb|don'?t disturb|dont disturb)\b"
)
# Plain negatives — the WHOLE message (normalised) is one of these.
NEGATIVE_EXACT = {
    "no", "nope", "nah", "na", "noo", "nooo", "no thanks", "no thank you", "no thx", "no ty",
    "no gracias", "no grasias", "not interested", "not intrested", "not interesed", "not intersted",
    # "I'm good" / "I'm ok" ALONE are called (owner's rule); with a "no" they decline.
    "no im good", "no i m good", "no i am good", "pass",
    "never", "no way",
}
# …or containing one of these anywhere.
NEGATIVE_PHRASES = re.compile(r"\b(not interested|not intrested|no thanks|no thank you|not looking)\b")
WRONG_PHRASES = re.compile(r"\b(wrong (?:number|person|#)|you have the wrong|not (?:the right|this) person|no one by that name)\b")
# Abuse and accusations, matched as substrings so "f*ck" / "a**hole" / "fuuuck" hit.
ABUSE_PATTERNS = re.compile(
    r"f+u+c+k|\bfck|\bfuk|f\*+c?k|\bf off\b|\bf u\b|sh+i+t|bitch|b\*{2,}|a+s+s+h+o+l+e|a\*\*hole|\bdick\b|bastard|cunt"
    r"|motherf|mother f|go to hell|go suck|suck my|idiot|stupid|moron"
    r"|nigg|fagg|retard"
    r"|scam|spam|fraud|fake|report you|reported you|sue you|lawsuit|harass|police"
)


def _is_clear_yes(raw: str, t: str, low: str) -> bool:
    words = t.split()
    if words and (words[0] in YES_WORDS or re.fullmatch(r"y+e+s+|o+k+(a+y+)?|y+e+a+h*|y+e+p+|y+u+p+|s+u+r+e+", words[0])):
        return True
    if any(t.startswith(p) for p in YES_STARTS):
        return True
    return bool(INTEREST.search(low) or any(e in raw for e in EMOJI_YES))


def classify(text: str | None) -> str:
    """One inbound text -> YES | REPLY | STOP | NEGATIVE | ABUSE | WRONG."""
    raw = text or ""
    t = _clean(raw)
    low = raw.lower().replace("’", "'")
    if t in STOP_WORDS or STOP_PHRASES.search(low):
        return STOP
    if ABUSE_PATTERNS.search(low):
        return ABUSE
    if WRONG_PHRASES.search(low):
        return WRONG
    if t in NEGATIVE_EXACT or NEGATIVE_PHRASES.search(low) or raw.strip() in ("👎", "🖕"):
        return NEGATIVE
    if _is_clear_yes(raw, t, low):
        return YES
    return REPLY  # anything else that isn't negative: "who is this?", "maybe", "?", even a photo


def test_numbers() -> set[str]:
    """TESTING ONLY (VONAGE_TEST_NUMBERS): non-US numbers the dialer treats like
    a US client — texts, auto-dial, callbacks, manual dial — with no calling
    hours. Empty the setting to switch it off."""
    from app.core.config import settings

    out = set()
    for raw in (getattr(settings, "VONAGE_TEST_NUMBERS", "") or "").split(","):
        d = re.sub(r"\D", "", raw)
        if 8 <= len(d) <= 15:
            out.add("+" + d)
    return out


def is_test_number(phone: str | None) -> bool:
    return bool(phone) and phone in test_numbers()


def normalize_phone(raw: str | None) -> str | None:
    """+1XXXXXXXXXX for a US/Canada number (or a listed test number), else None."""
    digits = re.sub(r"\D", "", str(raw or ""))
    if digits and "+" + digits in test_numbers():
        return "+" + digits
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return "+1" + digits


def vonage_number(e164: str | None) -> str:
    """Vonage wants E.164 without the plus: 15615550100."""
    return re.sub(r"\D", "", str(e164 or ""))


def last10(raw: str | None) -> str:
    return re.sub(r"\D", "", str(raw or ""))[-10:]
