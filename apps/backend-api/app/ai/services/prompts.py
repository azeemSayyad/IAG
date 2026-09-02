"""
Prompt Engine (Step 4.1)

Dynamic prompt templates with variable injection.

Variables:
- {first_name} — Customer's first name
- {last_name} — Customer's last name
- {source} — Lead source
- {campaign_name} — Campaign name
- {prior_replies} — Summary of prior conversation
- {objections} — Known objections
- {tone} — Desired tone (friendly, professional, urgent, casual)
- {agent_name} — Assigned agent's name
- {slot_1}, {slot_2}, {slot_3} — Available booking slots
"""

import random
from typing import Dict, List, Optional
from datetime import datetime


# System prompts for different contexts
SYSTEM_PROMPTS = {
    "outreach": (
        "You are a friendly insurance assistant helping customers explore insurance options. "
        "You help them book appointments with licensed agents. "
        "RULES: Never invent pricing or guarantees. Never claim to be human. "
        "Keep messages under 160 characters when possible. Be warm but professional. "
        "If asked about pricing, say you'll connect them with an agent."
    ),
    "objection_handling": (
        "You handle customer objections about insurance with empathy and facts. "
        "RULES: Acknowledge the concern first. Provide factual information only. "
        "Never dismiss their concern. Redirect to booking after addressing."
    ),
    "booking": (
        "You help customers book appointment slots. "
        "RULES: Present exactly 3 available appointment times without numbering. Accept the chosen slot text as a reply. "
        "Confirm the booking after selection."
    ),
    "follow_up": (
        "You follow up with customers who haven't replied. "
        "RULES: Be persistent but not pushy. Vary your approach each time. "
        "Offer value in each message."
    ),
    "reschedule": (
        "You help customers reschedule missed or cancelled appointments. "
        "RULES: Be understanding. Offer new slots. Make it easy to rebook."
    ),
}

PRIMARY_OUTREACH_TEMPLATE = (
    "hey {first_name} it's Michael. Your coverage might be flagged possible lapse. "
    "$0/mo before close. Reply Yes, takes 2 min."
)


# Outreach message templates by tone
OUTREACH_TEMPLATES = {
    "friendly": [
        PRIMARY_OUTREACH_TEMPLATE,
    ],
    "professional": [
        PRIMARY_OUTREACH_TEMPLATE,
    ],
    "casual": [
        PRIMARY_OUTREACH_TEMPLATE,
    ],
    "urgent": [
        PRIMARY_OUTREACH_TEMPLATE,
    ],
}

# Objection handling templates
OBJECTION_TEMPLATES = {
    "pricing": [
        "I totally get that, {first_name}. Our agents can walk you through options that fit your budget. Want to chat with one?",
        "Great point, {first_name}. We have flexible plans. Let me connect you with an agent who can find the right fit.",
        "Understood, {first_name}. Many of our customers found surprisingly affordable options. Worth a quick look?",
    ],
    "trust": [
        "I hear you, {first_name}. We've helped thousands of families find the right coverage. Happy to share more details.",
        "Totally fair, {first_name}. Our agents are licensed professionals who'll answer all your questions. No pressure.",
        "I understand the hesitation, {first_name}. We're here to help, not push. Want to see what we offer?",
    ],
    "timing": [
        "No rush, {first_name}. When would be a better time for you?",
        "I understand, {first_name}. How about we schedule something at your convenience?",
        "Fair enough, {first_name}. I can reach out later if that works better for you.",
    ],
    "not_interested": [
        "No problem, {first_name}. If anything changes, we're here to help!",
        "I appreciate your honesty, {first_name}. We'll be here if you need us.",
        "Understood, {first_name}. Feel free to reach out anytime!",
    ],
}

# Follow-up templates
FOLLOWUP_TEMPLATES = {
    "no_reply_1": [
        "Hey {first_name}! Just checking in. Did you get my last message?",
        "Hi {first_name}! Wanted to make sure you saw my message. Any questions?",
    ],
    "no_reply_2": [
        "Hi {first_name}! We've helped hundreds of families save on insurance. Want to see how?",
        "Hey {first_name}! Our agents are booking up fast. Want to grab a spot?",
    ],
    "no_reply_3": [
        "Hi {first_name}, last chance to explore your insurance options. Let me know!",
        "Hey {first_name}! Don't miss out on current rates. Want to chat?",
    ],
    "no_show": [
        "Hey {first_name}! We missed you. Want to reschedule?",
        "Hi {first_name}! No worries about missing the call. Want to book a new time?",
    ],
}

# Booking templates
BOOKING_TEMPLATES = [
    "Great i am available at\n{slot_1}\n{slot_2}\n{slot_3}",
]

# Confirmation templates
CONFIRMATION_TEMPLATES = [
    "Great i will reach out",
]


def render_template(template: str, variables: Dict[str, str]) -> str:
    """Render a template with variables."""
    result = template
    for key, value in variables.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result


# --- Editable first-outreach template (per tenant) -------------------------
# The first message that auto-sends to a lead after upload. Admins edit it from
# the Upload Leads page; the override is stored in Redis (shared across web +
# worker + beat, survives redeploys). Falls back to PRIMARY_OUTREACH_TEMPLATE.
_OUTREACH_TEMPLATE_KEY = "outreach:template:{tid}"


def get_outreach_template(tenant_id=None) -> str:
    """The tenant's saved first-outreach template, or the built-in default."""
    if tenant_id:
        try:
            from app.core.redis import redis_service
            val = redis_service.client.get(_OUTREACH_TEMPLATE_KEY.format(tid=tenant_id))
            if val:
                return val.decode("utf-8") if isinstance(val, bytes) else str(val)
        except Exception:
            pass
    return PRIMARY_OUTREACH_TEMPLATE


def set_outreach_template(tenant_id, template: str) -> bool:
    """Save a tenant's first-outreach template override."""
    try:
        from app.core.redis import redis_service
        redis_service.client.set(_OUTREACH_TEMPLATE_KEY.format(tid=tenant_id), template)
        return True
    except Exception:
        return False


def reset_outreach_template(tenant_id) -> bool:
    """Clear the override so the built-in default is used again."""
    try:
        from app.core.redis import redis_service
        redis_service.client.delete(_OUTREACH_TEMPLATE_KEY.format(tid=tenant_id))
        return True
    except Exception:
        return False


def get_outreach_message(
    first_name: str,
    tone: str = "friendly",
    source: str = "",
    campaign_name: str = "",
    tenant_id=None,
) -> str:
    """Generate an outreach message from the tenant's template (or default).

    Only the FIRST name is used in the template. Some callers pass a full
    "First Last" display name (the SMS queue passes the lead's full name), so
    take the first whitespace-delimited token and fall back to "there".
    """
    first = (str(first_name or "").split() or ["there"])[0]
    return render_template(get_outreach_template(tenant_id), {
        "first_name": first,
        "source": source,
        "campaign_name": campaign_name,
    })


def _campaign_first_template(campaign_id):
    """The campaign's per-campaign first-message body, or None to use the global
    default. Looked up by id; safe — returns None on any error."""
    if not campaign_id:
        return None
    try:
        from app.core.database import SessionLocal
        from app.models.campaign import Campaign
        db = SessionLocal()
        try:
            c = db.query(Campaign).filter(Campaign.id == campaign_id).first()
            return ((c.first_template or "").strip() or None) if c else None
        finally:
            db.close()
    except Exception:
        return None


def resolve_first_message(job: dict) -> str:
    """Compose the first-message body for a queued outbound send:
      1. an explicit job['message'] (rare), else
      2. the lead's CAMPAIGN first_template (with {first_name} rendered), else
      3. the tenant's global outreach template.
    Only the BODY varies — the send 'kind' stays 'first_template', so the
    first-template lockdown / send chokepoint is untouched."""
    if job.get("message"):
        return job["message"]
    name = job.get("lead_name") or "there"
    ct = _campaign_first_template(job.get("campaign_id"))
    if ct:
        first = (str(name).split() or ["there"])[0]
        return render_template(ct, {"first_name": first, "source": job.get("source", ""), "campaign_name": ""})
    return get_outreach_message(name, tone="friendly", source=job.get("source", ""), tenant_id=job.get("tenant_id"))


def get_objection_response(
    first_name: str,
    objection_type: str,
    tone: str = "friendly",
) -> str:
    """Generate an objection handling response."""
    templates = OBJECTION_TEMPLATES.get(objection_type, OBJECTION_TEMPLATES["not_interested"])
    template = random.choice(templates)
    return render_template(template, {"first_name": first_name})


def get_followup_message(
    first_name: str,
    followup_number: int = 1,
) -> str:
    """Generate a follow-up message."""
    key = f"no_reply_{min(followup_number, 3)}"
    templates = FOLLOWUP_TEMPLATES.get(key, FOLLOWUP_TEMPLATES["no_reply_1"])
    template = random.choice(templates)
    return render_template(template, {"first_name": first_name})


def get_booking_message(
    slots: List[str],
) -> str:
    """Generate a booking message with available slots."""
    template = random.choice(BOOKING_TEMPLATES)
    return render_template(template, {
        "slot_1": slots[0] if len(slots) > 0 else "N/A",
        "slot_2": slots[1] if len(slots) > 1 else "N/A",
        "slot_3": slots[2] if len(slots) > 2 else "N/A",
    })


def get_confirmation_message(
    first_name: str,
    slot: str,
) -> str:
    """Generate a booking confirmation message."""
    template = random.choice(CONFIRMATION_TEMPLATES)
    return render_template(template, {"first_name": first_name, "slot": slot})


def get_system_prompt(context: str) -> str:
    """Get the system prompt for a given context."""
    return SYSTEM_PROMPTS.get(context, SYSTEM_PROMPTS["outreach"])


def build_llm_prompt(
    system_context: str,
    lead_data: Dict[str, str],
    conversation_history: List[Dict[str, str]],
    task: str,
    tone: str = "friendly",
) -> List[Dict[str, str]]:
    """
    Build a complete prompt for the LLM.

    Returns a list of message dicts suitable for chat-based LLMs.
    """
    system_prompt = get_system_prompt(system_context)

    # Add context variables to system prompt
    context_vars = []
    if lead_data.get("first_name"):
        context_vars.append(f"Customer name: {lead_data['first_name']}")
    if lead_data.get("source"):
        context_vars.append(f"Lead source: {lead_data['source']}")
    if tone:
        context_vars.append(f"Desired tone: {tone}")

    if context_vars:
        system_prompt += "\n\nContext:\n" + "\n".join(context_vars)

    messages = [{"role": "system", "content": system_prompt}]

    # Add conversation history (last 10 messages)
    for msg in conversation_history[-10:]:
        role = "assistant" if msg.get("sender") == "ai" else "user"
        messages.append({"role": role, "content": msg.get("content", "")})

    # Add current task
    messages.append({"role": "user", "content": task})

    return messages
