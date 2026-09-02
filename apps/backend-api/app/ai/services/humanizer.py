"""
Human-like Messaging (Step 4.5)

Makes AI messages feel natural and human:
- Varies tone and wording
- Avoids robotic patterns
- Adds natural imperfections
- Prevents repetitive messaging
"""

import random
from typing import List, Optional


# Greeting variations
GREETINGS = {
    "friendly": [
        "Hey", "Hi", "Hey there", "Hi there", "Hello",
        "What's up", "How's it going", "Good to connect",
    ],
    "professional": [
        "Hello", "Good day", "Greetings", "Hi",
    ],
    "casual": [
        "Hey", "Yo", "Sup", "Hey hey", "Hiya",
    ],
    "urgent": [
        "Hi", "Hey", "Quick heads up", "Just letting you know",
    ],
}

# Closing variations
CLOSINGS = {
    "friendly": [
        "Let me know!", "What do you think?", "Interested?",
        "Would love to chat!", "Give me a shout!",
    ],
    "professional": [
        "Please let me know.", "I look forward to hearing from you.",
        "Would you like to learn more?", "Shall we schedule a call?",
    ],
    "casual": [
        "Lmk!", "Thoughts?", "Wdyt?", "Hmu!",
    ],
    "urgent": [
        "Don't miss out!", "Spots are limited!", "Act fast!",
    ],
}

# Fillers to make messages feel more natural
FILLERS = [
    "So,", "Well,", "By the way,", "Just wanted to say,",
    "Quick question -", "I was thinking,", "Real quick -",
]

# Emoji sets by context (light use)
CONTEXT_EMOJIS = {
    "positive": ["😊", "👍", "🎉", "💪"],
    "neutral": ["👋", "📱", "💬"],
    "urgent": ["⏰", "🔥", "⚡"],
    "none": [""],
}


def vary_greeting(tone: str = "friendly") -> str:
    """Get a varied greeting based on tone."""
    greetings = GREETINGS.get(tone, GREETINGS["friendly"])
    return random.choice(greetings)


def vary_closing(tone: str = "friendly") -> str:
    """Get a varied closing based on tone."""
    closings = CLOSINGS.get(tone, CLOSINGS["friendly"])
    return random.choice(closings)


def add_natural_variation(message: str, tone: str = "friendly") -> str:
    """
    Add natural variation to a message:
    - Random greeting
    - Random closing
    - Occasional filler
    - Light emoji use
    """
    parts = []

    # Sometimes add a greeting (60% chance)
    if random.random() < 0.6:
        parts.append(vary_greeting(tone))

    # Add the core message
    parts.append(message)

    # Sometimes add a closing (40% chance)
    if random.random() < 0.4:
        parts.append(vary_closing(tone))

    result = " ".join(parts)

    # Sometimes add a light emoji (20% chance)
    if random.random() < 0.2:
        emoji_set = CONTEXT_EMOJIS.get("neutral", [""])
        emoji = random.choice(emoji_set)
        if emoji:
            result = f"{result} {emoji}"

    return result


def prevent_repetition(messages: List[str], new_message: str) -> str:
    """
    Check if a message is too similar to recent messages.
    If so, modify it to be different.
    """
    if not messages:
        return new_message

    # Simple similarity check: count shared words
    new_words = set(new_message.lower().split())
    for prev_msg in messages[-3:]:  # Check last 3 messages
        prev_words = set(prev_msg.lower().split())
        overlap = len(new_words & prev_words)
        total = max(len(new_words), len(prev_words))
        if total > 0 and overlap / total > 0.7:
            # Too similar, add variation
            return add_natural_variation(new_message)

    return new_message


def humanize_message(
    message: str,
    tone: str = "friendly",
    recent_messages: List[str] = None,
) -> str:
    """
    Full humanization pipeline:
    1. Add natural variation
    2. Check for repetition
    3. Ensure message length is appropriate
    """
    # Add variation
    result = add_natural_variation(message, tone)

    # Check repetition
    if recent_messages:
        result = prevent_repetition(recent_messages, result)

    # Ensure SMS length (160 chars for single SMS, 1600 max)
    if len(result) > 1600:
        result = result[:1597] + "..."

    return result


def get_tone_from_campaign(campaign_settings: dict) -> str:
    """Extract tone from campaign settings."""
    return campaign_settings.get("tone", "friendly")


def adjust_tone_for_context(base_tone: str, intent: str) -> str:
    """
    Adjust tone based on conversation context.
    - Skeptical → more empathetic
    - Negative → more understanding
    - Positive → match enthusiasm
    """
    adjustments = {
        "SKEPTICAL": "empathetic",
        "NEGATIVE": "understanding",
        "POSITIVE": "enthusiastic",
        "INTERESTED": "helpful",
        "BOOK_NOW": "efficient",
        "QUESTION": "informative",
    }
    return adjustments.get(intent, base_tone)
