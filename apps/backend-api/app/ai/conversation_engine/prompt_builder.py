"""
Dynamic Prompt Builder (Step 36.2)

Constructs 4-layer prompts for LLM:

Layer 1 — System Prompt:
    Role, rules, goals, constraints

Layer 2 — Context Layer:
    Lead data, source, timezone, booking state

Layer 3 — Conversation Layer:
    Last 10-20 messages, sentiment, objections

Layer 4 — Campaign Layer:
    Campaign tone, scripts, insurance type, targeting
"""

from typing import Dict, List, Optional
from datetime import datetime, timezone

from app.ai.conversation_engine.context_builder import ConversationContext
from app.ai.conversation_engine.tool_calling import TOOL_DEFINITIONS


# Base system prompt
BASE_SYSTEM_PROMPT = """You are an elite insurance sales assistant for a call center.

YOUR GOALS:
- Build trust and rapport with the customer
- Qualify the lead (understand their insurance needs)
- Book an appointment with a licensed insurance agent
- Handle objections with empathy and facts
- Never be pushy or aggressive

RULES:
- Never invent pricing, rates, or guarantees
- Never claim to be human — you are an AI assistant
- Never give medical or legal advice
- Never invent appointment dates or times. Use search_slots before offering availability.
- Never offer past dates or outdated calendar dates.
- Keep messages concise (under 160 characters when possible for SMS)
- Be warm, professional, and conversational
- If asked about specific pricing, connect them with an agent
- Respect opt-out requests immediately
- Reply in the same language the customer writes in (mirror their language)

COMMUNICATION STYLE:
- Use natural, conversational language
- Vary your greetings and closings
- Show empathy for concerns
- Be persistent but not pushy
- Ask questions to understand needs"""


# Tone-specific additions
TONE_MODIFIERS = {
    "friendly": "Be warm, approachable, and personable. Use casual language and show genuine interest.",
    "professional": "Be courteous, informative, and business-like. Maintain a polished tone.",
    "casual": "Be relaxed and conversational. Use shorter sentences and a laid-back style.",
    "urgent": "Convey importance without being aggressive. Highlight time-sensitive opportunities.",
    "empathetic": "Show deep understanding of concerns. Acknowledge feelings before providing information.",
    "enthusiastic": "Show genuine excitement about helping. Be energetic and positive.",
}


# State-specific behavior
STATE_BEHAVIORS = {
    "new_lead": "This is your first contact. Introduce yourself and gauge interest.",
    "contacted": "You've reached out before. Check if they received your message.",
    "replied": "They've responded. Engage with their message and move toward booking.",
    "interested": "They're interested! Push gently toward booking an appointment.",
    "skeptical": "They have concerns. Address objections with empathy and facts.",
    "booking": "They want to book! Help them find a good time slot.",
    "booked": "They have an appointment. Confirm details and build excitement.",
    "follow_up": "Following up after previous contact. Re-engage with value.",
    "nurture": "Long-term nurturing. Be helpful without pressure.",
    "stopped": "They've opted out. Respect this and do not contact again.",
    "escalated": "Conversation is with a human agent now.",
}


def build_system_prompt(
    ctx: ConversationContext,
    include_tools: bool = True,
    language: Optional[str] = None,
) -> str:
    """
    Build the complete system prompt from context.

    Layers:
    1. Base system prompt (role, rules, goals)
    2. Tone modifier (from campaign)
    3. State behavior (from conversation state)
    4. Tool definitions (if tool calling enabled)

    If ``language`` is provided (and not English) the AI is instructed to
    always respond in that language — this comes from the agent's selected
    language in Settings. Regardless, the base prompt already mirrors the
    customer's own language.
    """
    parts = [BASE_SYSTEM_PROMPT]
    now = datetime.now(timezone.utc)
    parts.append(
        f"\nCURRENT DATE: {now.date().isoformat()} UTC. "
        "All booking suggestions must be today or later in the lead's timezone."
    )

    # Explicit language override (from Settings → Language).
    lang = (language or "").strip()
    if lang and lang.lower() not in ("english", "english (us)", "english (uk)", "en", "en-us", "en-gb"):
        parts.append(f"\nLANGUAGE: Always respond in {lang}, regardless of the language of this prompt.")

    # Layer 1: Tone modifier
    tone = ctx.campaign.tone if ctx.campaign else "friendly"
    tone_modifier = TONE_MODIFIERS.get(tone, TONE_MODIFIERS["friendly"])
    parts.append(f"\nTONE: {tone_modifier}")

    # Layer 2: State behavior
    state = ctx.conversation_state or "new_lead"
    state_behavior = STATE_BEHAVIORS.get(state, STATE_BEHAVIORS["new_lead"])
    parts.append(f"\nCURRENT STATE: {state_behavior}")

    # Layer 3: Campaign-specific instructions
    if ctx.campaign:
        if ctx.campaign.prompt_template:
            parts.append(f"\nCAMPAIGN SCRIPT:\n{ctx.campaign.prompt_template}")

        if ctx.campaign.objection_prompts:
            objection_text = "\n".join(
                f"- {k}: {v}" for k, v in ctx.campaign.objection_prompts.items()
            )
            parts.append(f"\nOBJECTION HANDLING:\n{objection_text}")

    # Layer 4: Tool definitions
    if include_tools:
        parts.append(f"\n{TOOL_DEFINITIONS}")

    return "\n".join(parts)


def build_context_layer(ctx: ConversationContext) -> str:
    """
    Build the context layer with lead and situation data.

    This is injected into the conversation as a system message
    providing background information.
    """
    parts = []

    # Lead information
    if ctx.lead:
        lead_info = [
            f"Customer: {ctx.lead.first_name} {ctx.lead.last_name}",
            f"Source: {ctx.lead.source}",
            f"Lead Score: {ctx.lead.lead_score or 0}/100 (tier: {ctx.lead_tier})",
            f"Status: {ctx.lead.status}",
        ]

        if ctx.lead.state:
            lead_info.append(f"State: {ctx.lead.state}")
        if ctx.lead.timezone:
            lead_info.append(f"Timezone: {ctx.lead.timezone}")
        if ctx.lead.phone:
            lead_info.append(f"Phone: {ctx.lead.phone}")

        parts.append("LEAD PROFILE:\n" + "\n".join(lead_info))

    # Conversation context
    if ctx.conversation:
        conv_info = [
            f"Messages exchanged: {ctx.message_count}",
            f"Conversation state: {ctx.conversation_state}",
        ]

        if ctx.hours_since_last_message > 0:
            conv_info.append(
                f"Hours since last message: {round(ctx.hours_since_last_message, 1)}"
            )

        parts.append("CONVERSATION:\n" + "\n".join(conv_info))

    # Objection history
    if ctx.objections:
        obj_lines = []
        for obj in ctx.objections[-5:]:  # Last 5 objections
            obj_lines.append(f"- {obj.get('type', 'unknown')}: {obj.get('text', '')[:100]}")
        parts.append("PAST OBJECTIONS:\n" + "\n".join(obj_lines))

    # Sentiment (only if non-default)
    if ctx.sentiment and ctx.sentiment.get("current") != "neutral":
        parts.append(
            f"SENTIMENT: {ctx.sentiment.get('current', 'neutral')} "
            f"(trend: {ctx.sentiment.get('trend', 'stable')})"
        )

    # Customer preferences
    if ctx.preferences:
        pref_lines = [f"- {k}: {v}" for k, v in ctx.preferences.items()]
        parts.append("PREFERENCES:\n" + "\n".join(pref_lines))

    # Appointment info
    if ctx.appointment:
        appt_time = ctx.appointment.start_time.strftime("%A, %B %d at %I:%M %p")
        parts.append(
            f"EXISTING APPOINTMENT: {appt_time} (status: {ctx.appointment.status})"
        )

    # Booking state
    if ctx.booking_state:
        parts.append(f"BOOKING STATE: {ctx.booking_state}")

    return "\n\n".join(parts)


def build_messages(
    ctx: ConversationContext,
    user_message: str,
    include_tools: bool = True,
    max_history: int = 20,
    language: Optional[str] = None,
) -> List[Dict[str, str]]:
    """
    Build the complete message list for LLM.

    Structure:
    1. System prompt (role + rules + tools)
    2. Context message (lead data + situation)
    3. Conversation history (last N messages)
    4. Current user message
    """
    messages = []

    # Layer 1: System prompt
    system_prompt = build_system_prompt(ctx, include_tools=include_tools, language=language)
    messages.append({"role": "system", "content": system_prompt})

    # Layer 2: Context layer (as system message)
    context_layer = build_context_layer(ctx)
    if context_layer:
        messages.append({
            "role": "system",
            "content": f"CURRENT CONTEXT:\n{context_layer}",
        })

    # Layer 3: Conversation history
    for msg in ctx.messages[-max_history:]:
        role = "assistant" if msg.get("sender") == "ai" else "user"
        messages.append({"role": role, "content": msg.get("content", "")})

    # Layer 4: Current message
    messages.append({"role": "user", "content": user_message})

    return messages


def build_booking_prompt(
    ctx: ConversationContext,
    available_slots: List[Dict],
) -> str:
    """
    Build a prompt specifically for the booking flow.

    Formats available slots as numbered options.
    """
    slot_lines = []
    for i, slot in enumerate(available_slots[:3], start=1):
        slot_lines.append(
            f"{i}. {slot.get('start_display', 'N/A')} "
            f"({slot.get('date_display', 'N/A')})"
        )

    slots_text = "\n".join(slot_lines)

    return f"""The customer wants to book an appointment.

Available time slots:
{slots_text}

Present these options to the customer and ask them to reply with the number (1, 2, or 3).
If none work, offer to check other times.

Keep your response concise and friendly."""


def build_objection_prompt(
    ctx: ConversationContext,
    objection_type: str,
) -> str:
    """
    Build a prompt for handling a specific objection.
    """
    objection_guidance = {
        "pricing": "Emphasize value, flexible plans, and long-term savings. Offer to connect with an agent for specific pricing.",
        "trust": "Provide social proof, credentials, and guarantees. Be transparent and reassuring.",
        "timing": "Be flexible and understanding. Offer to schedule at their convenience.",
        "already_covered": "Acknowledge their coverage. Offer a free comparison or review.",
        "need_to_think": "Give them space. Offer to send information and follow up later.",
        "not_interested": "Respect their decision. Leave the door open for the future.",
        "spouse_decides": "Offer to schedule a call for both of them.",
    }

    guidance = objection_guidance.get(objection_type, "Address their concern with empathy and facts.")

    return f"""The customer has raised a {objection_type} objection.

GUIDANCE: {guidance}

Respond with empathy first, then address their concern. Keep it concise and natural."""
