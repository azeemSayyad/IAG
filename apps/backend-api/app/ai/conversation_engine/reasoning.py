"""
Multi-Turn Reasoning (Step 36.6)

Enables AI to:
- Persuade without being pushy
- Handle objections across multiple turns
- Negotiate appointment times
- Understand customer uncertainty
- Recover dead/stalled conversations

Strategies:
1. Progressive persuasion — escalate gently over turns
2. Objection threading — track and address objections across messages
3. Dead conversation recovery — re-engage with new value propositions
4. Uncertainty handling — provide reassurance and social proof
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum

from app.ai.conversation_engine.context_builder import ConversationContext


class PersuasionLevel(str, Enum):
    """Progressive persuasion intensity."""
    SOFT = "soft"           # Initial contact, gauge interest
    MODERATE = "moderate"   # Engaged, push gently toward booking
    FIRM = "firm"           # Multiple touches, create urgency
    FINAL = "final"         # Last attempt, strong value prop


class ConversationHealth(str, Enum):
    """Health of the conversation."""
    HEALTHY = "healthy"         # Active engagement
    STALLING = "stalling"       # Response times increasing
    DEAD = "dead"               # No response in 48+ hours
    OBJECTION_LOOP = "objection_loop"  # Same objection repeated
    LOST = "lost"               # Explicit disinterest


# Persuasion strategies by level
PERSUASION_STRATEGIES = {
    PersuasionLevel.SOFT: {
        "approach": "Build rapport and gauge interest",
        "techniques": [
            "Ask open-ended questions",
            "Show genuine curiosity about their needs",
            "Share a brief value proposition",
            "Offer help without pressure",
        ],
        "example": "I'd love to help you explore your options. What kind of insurance are you looking for?",
    },
    PersuasionLevel.MODERATE: {
        "approach": "Gently push toward booking",
        "techniques": [
            "Highlight specific benefits",
            "Use social proof (other customers)",
            "Create mild urgency (limited availability)",
            "Offer a low-commitment next step",
        ],
        "example": "Many people in your area have found our coverage really helpful. Would you like to chat with an agent for just 15 minutes?",
    },
    PersuasionLevel.FIRM: {
        "approach": "Create urgency and value",
        "techniques": [
            "Emphasize time-sensitive benefits",
            "Share success stories",
            "Address lingering concerns directly",
            "Offer a specific, easy next step",
        ],
        "example": "I understand you're busy, but this is a quick call that could save you hundreds. Can we lock in 10 minutes tomorrow?",
    },
    PersuasionLevel.FINAL: {
        "approach": "Last chance value proposition",
        "techniques": [
            "Make a compelling final offer",
            "Acknowledge their hesitation",
            "Provide maximum value",
            "Leave the door open",
        ],
        "example": "I don't want to bother you, but I genuinely think this could help. If now isn't the right time, just let me know when might work better.",
    },
}


# Dead conversation recovery strategies
RECOVERY_STRATEGIES = [
    {
        "trigger_hours": 48,
        "approach": "Gentle check-in",
        "message": "Hey {name}! Just checking in. Did you get a chance to think about your insurance options?",
        "tone": "friendly",
    },
    {
        "trigger_hours": 72,
        "approach": "Value offer",
        "message": "Hi {name}! I wanted to share something that might help — we're offering free insurance consultations this week. Interested?",
        "tone": "professional",
    },
    {
        "trigger_hours": 168,  # 7 days
        "approach": "Final touch",
        "message": "Hi {name}, I know life gets busy. If insurance is still on your mind, I'm here whenever you're ready. No pressure!",
        "tone": "casual",
    },
]


# Uncertainty handling responses
UNCERTAINTY_RESPONSES = {
    "need_to_think": {
        "acknowledgment": "Absolutely, it's a big decision.",
        "value_add": "Can I send you some information to help with your research?",
        "next_step": "What if we schedule a no-obligation call for later this week?",
    },
    "not_sure": {
        "acknowledgment": "I completely understand the uncertainty.",
        "value_add": "Our agents can walk you through everything step by step.",
        "next_step": "Would a quick 10-minute call help clarify things?",
    },
    "spouse_decides": {
        "acknowledgment": "That makes sense — it's a family decision.",
        "value_add": "We can include your spouse in the call so everyone's on the same page.",
        "next_step": "When would work for both of you?",
    },
    "already_covered": {
        "acknowledgment": "That's great that you have coverage!",
        "value_add": "Many people find they can get better rates or additional coverage. A quick review never hurts.",
        "next_step": "Would you like a free comparison? It takes just 5 minutes.",
    },
}


class MultiTurnReasoner:
    """
    Manages multi-turn reasoning for the conversation engine.

    Determines:
    - Current persuasion level
    - Conversation health
    - Recovery strategy
    - Objection threading
    """

    def __init__(self):
        pass

    def analyze_conversation(
        self,
        ctx: ConversationContext,
    ) -> Dict:
        """
        Analyze the conversation state and determine reasoning strategy.

        Returns:
            Dict with persuasion_level, health, strategy recommendations
        """
        health = self._assess_health(ctx)
        persuasion_level = self._determine_persuasion_level(ctx, health)
        recovery_strategy = self._get_recovery_strategy(ctx, health)
        uncertainty_type = self._detect_uncertainty(ctx)

        return {
            "health": health.value,
            "persuasion_level": persuasion_level.value,
            "persuasion_strategy": PERSUASION_STRATEGIES[persuasion_level],
            "recovery_strategy": recovery_strategy,
            "uncertainty_type": uncertainty_type,
            "uncertainty_response": UNCERTAINTY_RESPONSES.get(uncertainty_type),
            "should_continue": health not in (ConversationHealth.LOST,),
            "should_escalate": health == ConversationHealth.DEAD and ctx.message_count > 5,
        }

    def get_reasoning_prompt_addition(
        self,
        ctx: ConversationContext,
    ) -> str:
        """
        Generate additional prompt instructions based on reasoning analysis.

        This is appended to the system prompt to guide the LLM's
        multi-turn behavior.
        """
        analysis = self.analyze_conversation(ctx)
        parts = []

        # Persuasion guidance
        level = analysis["persuasion_level"]
        strategy = PERSUASION_STRATEGIES.get(level, PERSUASION_STRATEGIES[PersuasionLevel.SOFT])
        parts.append(f"PERSUASION LEVEL: {level}")
        parts.append(f"Approach: {strategy['approach']}")
        parts.append(f"Techniques: {', '.join(strategy['techniques'])}")

        # Health-based guidance
        health = analysis["health"]
        if health == ConversationHealth.STALLING:
            parts.append("CONVERSATION IS STALLING: Try a new angle or offer something valuable to re-engage.")
        elif health == ConversationHealth.DEAD:
            parts.append("CONVERSATION IS DEAD: Use a recovery strategy. Be brief and offer clear value.")
        elif health == ConversationHealth.OBJECTION_LOOP:
            parts.append("OBJECTION LOOP DETECTED: Acknowledge the concern, address it directly, then pivot to booking.")

        # Uncertainty handling
        if analysis["uncertainty_type"]:
            resp = analysis["uncertainty_response"]
            if resp:
                parts.append(f"UNCERTAINTY DETECTED ({analysis['uncertainty_type']}):")
                parts.append(f"Acknowledge: {resp['acknowledgment']}")
                parts.append(f"Value add: {resp['value_add']}")
                parts.append(f"Next step: {resp['next_step']}")

        # Objection threading
        if ctx.objections:
            recent_obj = ctx.objections[-1]
            obj_type = recent_obj.get("type", "unknown")
            parts.append(f"RECENT OBJECTION: {obj_type}")
            parts.append("Address this objection before pushing for booking.")

        return "\n".join(parts)

    def _assess_health(self, ctx: ConversationContext) -> ConversationHealth:
        """Assess the health of the conversation."""
        # Check for explicit disinterest
        if ctx.conversation_state in ("stopped", "unqualified"):
            return ConversationHealth.LOST

        # Check for dead conversation
        if ctx.hours_since_last_message > 168:  # 7 days
            return ConversationHealth.DEAD

        if ctx.hours_since_last_message > 48:
            return ConversationHealth.STALLING

        # Check for objection loop
        if len(ctx.objections) >= 3:
            recent_types = [o.get("type") for o in ctx.objections[-3:]]
            if len(set(recent_types)) == 1:  # Same objection 3 times
                return ConversationHealth.OBJECTION_LOOP

        return ConversationHealth.HEALTHY

    def _determine_persuasion_level(
        self,
        ctx: ConversationContext,
        health: ConversationHealth,
    ) -> PersuasionLevel:
        """Determine the appropriate persuasion level."""
        # Based on message count
        if ctx.message_count <= 2:
            return PersuasionLevel.SOFT
        elif ctx.message_count <= 5:
            return PersuasionLevel.MODERATE
        elif ctx.message_count <= 10:
            return PersuasionLevel.FIRM
        else:
            return PersuasionLevel.FINAL

    def _get_recovery_strategy(
        self,
        ctx: ConversationContext,
        health: ConversationHealth,
    ) -> Optional[Dict]:
        """Get recovery strategy for dead/stalling conversations."""
        if health not in (ConversationHealth.DEAD, ConversationHealth.STALLING):
            return None

        hours = ctx.hours_since_last_message

        for strategy in reversed(RECOVERY_STRATEGIES):
            if hours >= strategy["trigger_hours"]:
                name = ctx.lead.first_name if ctx.lead else "there"
                return {
                    "approach": strategy["approach"],
                    "message": strategy["message"].format(name=name),
                    "tone": strategy["tone"],
                }

        return RECOVERY_STRATEGIES[0]

    def _detect_uncertainty(self, ctx: ConversationContext) -> Optional[str]:
        """Detect if the customer is expressing uncertainty."""
        if not ctx.messages:
            return None

        last_msg = ctx.messages[-1] if ctx.messages else {}
        content = last_msg.get("content", "").lower()

        # Check for uncertainty patterns
        uncertainty_patterns = {
            "need_to_think": ["need to think", "let me think", "think about it", "not sure yet", "still deciding"],
            "not_sure": ["not sure", "don't know", "unsure", "maybe", "i guess", "perhaps"],
            "spouse_decides": ["wife", "husband", "spouse", "partner", "ask my", "talk to"],
            "already_covered": ["already have", "covered", "current insurance", "my insurance", "have coverage"],
        }

        for utype, patterns in uncertainty_patterns.items():
            if any(p in content for p in patterns):
                return utype

        return None
