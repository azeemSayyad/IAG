"""The two texts around HealthSherpa's authorization link (owner's
"Authorization Texts — Developer Spec", 2026-11-17).

  1. MAIN    — sent when the automation reaches the authorization window, BEFORE
               the link: tells the client a text "Your agent <name> with Insurance
               Alliance Group..." is coming and what to do with it.
  2. the HealthSherpa link — the extension presses "Text link" PRE_LINK_WAIT
               (30-60 s, default 45) after MAIN, so ours arrives first.
  3. REMINDER — 10 minutes later, only if still not authorized. No STOP line on
               purpose (the client is already in a conversation with us).

{agent_name} = the agent's name as HealthSherpa writes it in ITS text (first +
last), at most 20 characters (spec rule below), plain GSM characters only. A
text that still isn't plain GSM or is over 306 characters falls back to
"Your agent... with Insurance Alliance Group..." (no name).
"""
from __future__ import annotations

import logging
import unicodedata

logger = logging.getLogger(__name__)

AUTH_MAIN_TEXT = (
    'You will get another text that starts with:\n'
    '"Your agent {agent_name} with Insurance Alliance Group..."\n'
    '\n'
    '1. Open that text\n'
    '2. Tap the link inside it\n'
    '3. Follow the steps to say YES\n'
    '\n'
    'This lets me finish your health plan. Takes 1 min. STOP to opt out'
)
AUTH_REMINDER_TEXT = (
    'Your health plan is almost done! Look for the text that starts with:\n'
    '"Your agent {agent_name} with Insurance Alliance Group..."\n'
    '\n'
    'Tap the link in it and say YES.'
)
QUOTED = '"Your agent {agent_name} with Insurance Alliance Group..."'
FALLBACK_QUOTED = '"Your agent... with Insurance Alliance Group..."'

NAME_MAX = 20
TEXT_MAX = 306  # two SMS segments

# GSM 03.38 basic character set (+ the extension table: one SMS character each
# here is enough — these texts never use them).
_GSM = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM_EXT = set("^{}\\[~]|€")


def is_gsm(text: str) -> bool:
    return all(ch in _GSM or ch in _GSM_EXT for ch in text)


def strip_accents(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))


def agent_name_for_sms(full_name: str | None) -> str:
    """The spec's rule: <= 20 characters; longer = whole words that fit in 17 +
    '...'; a first name over 17 = its first 17 letters + '...'. Three plain dots,
    never the ellipsis character; accents stripped."""
    name = " ".join(strip_accents(str(full_name or "")).replace("…", "...").split())
    if len(name) <= NAME_MAX:
        return name
    out = ""
    for w in name.split(" "):
        if len((out + " " + w).strip()) <= NAME_MAX - 3:
            out = (out + " " + w).strip()
        else:
            break
    if not out:
        out = name[:NAME_MAX - 3]
    return out + "..."


def build(template: str, agent_full_name: str | None) -> str:
    """The text to send. Plain-GSM and <= 306 characters, or the no-name fallback."""
    name = agent_name_for_sms(agent_full_name)
    text = template.replace("{agent_name}", name) if name else template.replace(QUOTED, FALLBACK_QUOTED)
    if is_gsm(text) and len(text) <= TEXT_MAX:
        return text
    logger.warning("hs auth text: not plain GSM or too long (%s chars) — sending without the agent name", len(text))
    return template.replace(QUOTED, FALLBACK_QUOTED).replace("{agent_name}", "")
