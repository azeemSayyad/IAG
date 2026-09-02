"""
AI Personalization Engine (Step 36.10)

Adapts AI behavior based on customer attributes:

1. Geographic Personalization — State-specific insurance needs
2. Source-based Personalization — Adjust approach by lead source
3. Behavioral Personalization — Adapt to response patterns
4. Sentiment-based Personalization — Match emotional tone
5. Timing Personalization — Optimize contact timing
6. Engagement-based Personalization — Adjust intensity by engagement
"""

from typing import Dict, List, Optional
from datetime import datetime, timezone

from app.ai.conversation_engine.context_builder import ConversationContext


# State-specific insurance needs
STATE_INSURANCE_CONTEXT = {
    "FL": {
        "primary_needs": ["hurricane coverage", "flood insurance", "homeowners"],
        "talking_points": ["Florida weather risks", "hurricane deductible", "flood zone requirements"],
        "urgency": "high",
    },
    "TX": {
        "primary_needs": ["auto insurance", "homeowners", "business liability"],
        "talking_points": ["Texas-sized coverage", "competitive rates", "local agent network"],
        "urgency": "medium",
    },
    "CA": {
        "primary_needs": ["earthquake coverage", "wildfire protection", "auto insurance"],
        "talking_points": ["California-specific coverage", "earthquake retrofit discounts", "fire zone requirements"],
        "urgency": "high",
    },
    "NY": {
        "primary_needs": ["auto insurance", "renters insurance", "life insurance"],
        "talking_points": ["NYC coverage requirements", "competitive urban rates", "comprehensive protection"],
        "urgency": "medium",
    },
}

# Default state context
DEFAULT_STATE_CONTEXT = {
    "primary_needs": ["auto insurance", "homeowners", "life insurance"],
    "talking_points": ["comprehensive coverage", "competitive rates", "local support"],
    "urgency": "medium",
}


# Source-based approach adjustments
SOURCE_APPROACHES = {
    "referral": {
        "tone": "warm and appreciative",
        "approach": "Acknowledge the referral, build on existing trust",
        "opening": "Thanks for reaching out! {referrer_name} thought we could help.",
        "trust_level": "high",
    },
    "google": {
        "tone": "professional and informative",
        "approach": "They searched for insurance — provide clear value proposition",
        "opening": "Thanks for your interest in our insurance services!",
        "trust_level": "medium",
    },
    "facebook": {
        "tone": "friendly and casual",
        "approach": "Social context — be relatable and approachable",
        "opening": "Hey! Thanks for connecting with us on Facebook!",
        "trust_level": "medium",
    },
    "webhook": {
        "tone": "professional",
        "approach": "They filled out a form — follow up on their interest",
        "opening": "Hi {name}! I noticed you were interested in insurance options.",
        "trust_level": "medium",
    },
    "csv_import": {
        "tone": "friendly",
        "approach": "Cold outreach — need to establish trust quickly",
        "opening": "Hi {name}! I'm reaching out about insurance options in your area.",
        "trust_level": "low",
    },
    "manual": {
        "tone": "professional",
        "approach": "Direct contact — be clear about purpose",
        "opening": "Hi {name}! I'm reaching out to help with your insurance needs.",
        "trust_level": "low",
    },
}

DEFAULT_SOURCE_APPROACH = {
    "tone": "friendly",
    "approach": "Standard outreach",
    "opening": "Hi {name}! Thanks for your interest in our insurance services.",
    "trust_level": "medium",
}


# Response speed adaptations
RESPONSE_SPEED_ADAPTATIONS = {
    "fast": {
        "style": "concise and direct",
        "message_length": "short",
        "cta_style": "immediate",
        "patience": "low",
    },
    "moderate": {
        "style": "balanced",
        "message_length": "medium",
        "cta_style": "gentle",
        "patience": "medium",
    },
    "slow": {
        "style": "patient and understanding",
        "message_length": "short",
        "cta_style": "no-pressure",
        "patience": "high",
    },
    "unknown": {
        "style": "standard",
        "message_length": "medium",
        "cta_style": "gentle",
        "patience": "medium",
    },
}


# Sentiment-based tone adjustments
SENTIMENT_TONE_ADJUSTMENTS = {
    "positive": {
        "energy": "high",
        "approach": "Match their enthusiasm, push toward booking",
        "language": "Upbeat, encouraging, action-oriented",
    },
    "neutral": {
        "energy": "medium",
        "approach": "Build interest, provide value",
        "language": "Professional, informative, helpful",
    },
    "negative": {
        "energy": "low",
        "approach": "Acknowledge concerns, be empathetic",
        "language": "Understanding, patient, reassuring",
    },
}


class PersonalizationEngine:
    """
    Generates personalized AI behavior based on customer attributes.

    Produces prompt additions and behavior modifications.
    """

    def personalize(
        self,
        ctx: ConversationContext,
    ) -> Dict:
        """
        Generate personalization data from context.

        Returns:
            Dict with personalized prompt additions and behavior settings
        """
        state_context = self._get_state_context(ctx)
        source_approach = self._get_source_approach(ctx)
        speed_adaptation = self._get_speed_adaptation(ctx)
        sentiment_adjustment = self._get_sentiment_adjustment(ctx)
        engagement_style = self._get_engagement_style(ctx)

        return {
            "state_context": state_context,
            "source_approach": source_approach,
            "speed_adaptation": speed_adaptation,
            "sentiment_adjustment": sentiment_adjustment,
            "engagement_style": engagement_style,
            "prompt_addition": self._build_prompt_addition(
                state_context, source_approach, speed_adaptation,
                sentiment_adjustment, engagement_style,
            ),
        }

    def get_personalized_greeting(
        self,
        ctx: ConversationContext,
    ) -> str:
        """Get a personalized greeting based on context."""
        source = ctx.lead.source if ctx.lead else "unknown"
        approach = SOURCE_APPROACHES.get(source, DEFAULT_SOURCE_APPROACH)

        name = ctx.lead.first_name if ctx.lead else "there"
        greeting = approach["opening"].format(name=name, referrer_name="your friend")

        return greeting

    def _get_state_context(self, ctx: ConversationContext) -> Dict:
        """Get state-specific insurance context."""
        state = ctx.lead.state if ctx.lead else None
        if not state:
            return DEFAULT_STATE_CONTEXT
        return STATE_INSURANCE_CONTEXT.get(state.upper(), DEFAULT_STATE_CONTEXT)

    def _get_source_approach(self, ctx: ConversationContext) -> Dict:
        """Get source-based approach adjustments."""
        source = ctx.lead.source if ctx.lead else "unknown"
        return SOURCE_APPROACHES.get(source, DEFAULT_SOURCE_APPROACH)

    def _get_speed_adaptation(self, ctx: ConversationContext) -> Dict:
        """Get response speed adaptation."""
        # Analyze from lead context if available
        response_pattern = "unknown"
        if ctx.preferences:
            response_pattern = ctx.preferences.get("response_pattern", "unknown")

        return RESPONSE_SPEED_ADAPTATIONS.get(response_pattern, RESPONSE_SPEED_ADAPTATIONS["unknown"])

    def _get_sentiment_adjustment(self, ctx: ConversationContext) -> Dict:
        """Get sentiment-based tone adjustment."""
        sentiment = ctx.sentiment.get("current", "neutral") if ctx.sentiment else "neutral"
        return SENTIMENT_TONE_ADJUSTMENTS.get(sentiment, SENTIMENT_TONE_ADJUSTMENTS["neutral"])

    def _get_engagement_style(self, ctx: ConversationContext) -> Dict:
        """Get engagement-based style adjustments."""
        tier = ctx.lead_tier

        if tier == "hot":
            return {
                "intensity": "high",
                "focus": "booking",
                "urgency": "create urgency",
                "persistence": "high",
            }
        elif tier == "warm":
            return {
                "intensity": "medium",
                "focus": "qualification",
                "urgency": "gentle urgency",
                "persistence": "medium",
            }
        elif tier == "cool":
            return {
                "intensity": "low",
                "focus": "education",
                "urgency": "no pressure",
                "persistence": "low",
            }
        else:  # cold
            return {
                "intensity": "minimal",
                "focus": "awareness",
                "urgency": "none",
                "persistence": "minimal",
            }

    def _build_prompt_addition(
        self,
        state_context: Dict,
        source_approach: Dict,
        speed_adaptation: Dict,
        sentiment_adjustment: Dict,
        engagement_style: Dict,
    ) -> str:
        """Build prompt addition from personalization data."""
        parts = []

        parts.append("PERSONALIZATION:")

        # State context
        needs = ", ".join(state_context.get("primary_needs", []))
        parts.append(f"- Customer location needs: {needs}")

        # Source approach
        parts.append(f"- Approach: {source_approach.get('approach', 'Standard')}")
        parts.append(f"- Trust level: {source_approach.get('trust_level', 'medium')}")

        # Speed adaptation
        parts.append(f"- Response style: {speed_adaptation.get('style', 'standard')}")
        parts.append(f"- Message length: {speed_adaptation.get('message_length', 'medium')}")

        # Sentiment
        parts.append(f"- Energy level: {sentiment_adjustment.get('energy', 'medium')}")
        parts.append(f"- Language: {sentiment_adjustment.get('language', 'Professional')}")

        # Engagement
        parts.append(f"- Focus: {engagement_style.get('focus', 'qualification')}")
        parts.append(f"- Persistence: {engagement_style.get('persistence', 'medium')}")

        return "\n".join(parts)
