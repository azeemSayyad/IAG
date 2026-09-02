"""AI-generated coaching insights (Ollama), focused on converting leads to deals.

Coach Mode used to surface only rule-based, canned coaching strings. This layer
asks the local/remote Ollama model to turn the agent's REAL conversion-funnel
numbers into specific, actionable priorities for closing more deals.

It is deliberately defensive: if the model is unavailable, slow, or returns
anything we can't parse, `generate_ai_insights` returns None so the caller falls
back to the deterministic rule-based generator — Coach Mode always works, with
or without an LLM configured.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.ai.services.ollama import OllamaClient

logger = logging.getLogger(__name__)

_ALLOWED_PRIORITY = {"high", "medium", "low"}

# Only the conversion-relevant numbers we actually compute get sent to the model
# (never invent metrics). Keep this list in sync with PerformanceAnalyzer.
_METRIC_KEYS = [
    "total_appointments",
    "completed_calls",
    "won_deals",
    "lost_deals",
    "completion_rate",
    "win_rate",
    "loss_rate",
    "no_show_rate",
    "objection_handle_rate",
    "avg_talk_ratio",
]

_SYSTEM = (
    "You are an elite sales coach for an ACA health-insurance call center. Your "
    "only goal is to help the agent convert more leads into closed deals (won "
    "policies). Be specific, practical, and tie every point to the agent's real "
    "numbers. Never invent metrics you were not given. If the numbers are strong, "
    "still give one way to push conversion higher."
)


def _build_prompt(metrics: Dict[str, Any], agent_name: str) -> str:
    lines = []
    for k in _METRIC_KEYS:
        v = metrics.get(k)
        if v is not None:
            lines.append(f"- {k}: {v}")
    common = metrics.get("common_objections") or []
    if common:
        top = ", ".join(
            f"{c.get('type')}({c.get('count')})" for c in common[:3] if isinstance(c, dict)
        )
        if top:
            lines.append(f"- top_objections: {top}")
    metrics_block = "\n".join(lines) if lines else "- (no calls completed in this period yet)"
    return (
        f"Agent: {agent_name}\n"
        f"Their recent conversion numbers:\n{metrics_block}\n\n"
        "Give 1 to 3 coaching priorities that will help THIS agent turn more of "
        "their leads into closed deals. Cite their numbers. Respond with ONLY "
        "valid JSON (no prose, no code fences) in exactly this shape:\n"
        '{"insights":[{"title":"short title","priority":"high|medium|low",'
        '"description":"1-2 sentences citing their numbers",'
        '"impact":"the deal/revenue upside of fixing it",'
        '"action_items":["concrete step","concrete step"],"category":"conversion"}]}'
    )


def _coerce(insights_raw: Any) -> List[Dict[str, Any]]:
    """Validate/normalize the model's JSON into the CoachingInsight dict shape."""
    out: List[Dict[str, Any]] = []
    if not isinstance(insights_raw, list):
        return out
    for it in insights_raw:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        desc = str(it.get("description") or "").strip()
        if not title or not desc:
            continue
        pr = str(it.get("priority") or "medium").lower()
        if pr not in _ALLOWED_PRIORITY:
            pr = "medium"
        actions = it.get("action_items")
        actions = (
            [str(a).strip() for a in actions if str(a).strip()][:4]
            if isinstance(actions, list)
            else []
        )
        out.append(
            {
                "category": str(it.get("category") or "conversion"),
                "priority": pr,
                "title": title[:80],
                "description": desc[:400],
                "evidence": "",
                "action_items": actions,
                "impact": str(it.get("impact") or "").strip()[:300],
                "generated_at": None,
                "source": "ai",
            }
        )
    # Conversion-focused: surface highest-priority first.
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda i: order.get(i["priority"], 3))
    return out[:5]


async def generate_ai_insights(
    metrics: Dict[str, Any], agent_name: str
) -> Optional[List[Dict[str, Any]]]:
    """Return AI coaching insights, or None to signal 'fall back to rule-based'."""
    client = OllamaClient()
    try:
        if not await client.is_available():
            return None
    except Exception:
        return None

    try:
        raw = await client.chat(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _build_prompt(metrics, agent_name or "the agent")},
            ],
            temperature=0.4,
            max_tokens=700,
        )
    except Exception as exc:
        logger.warning("AI coaching generation failed: %s", exc)
        return None

    if not raw:
        return None
    # The model may wrap JSON in prose or code fences — extract the object.
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except Exception:
        return None

    insights = _coerce(data.get("insights"))
    return insights or None
