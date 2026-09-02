"""
Token Optimization (Step 18.6)

Reduces token usage while maintaining conversation quality.

Strategies:
1. Summarization — Compress long conversation histories
2. Compression — Remove redundant information
3. Retrieval Pruning — Only include relevant context
4. Prompt Caching — Reuse common prompt prefixes
"""

from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone


# Token estimation (rough: 1 token ≈ 4 chars for English)
def estimate_tokens(text: str) -> int:
    """Estimate token count for text."""
    return len(text) // 4


def estimate_messages_tokens(messages: List[Dict]) -> int:
    """Estimate total tokens for a list of messages."""
    total = 0
    for msg in messages:
        # Add overhead for message formatting
        total += 4  # message overhead
        total += estimate_tokens(msg.get("role", ""))
        total += estimate_tokens(msg.get("content", ""))
    return total


def summarize_conversation(
    messages: List[Dict],
    max_tokens: int = 500,
) -> List[Dict]:
    """
    Summarize a conversation to fit within token budget.

    Strategy:
    - Keep system message
    - Keep most recent messages
    - Summarize older messages
    """
    if not messages:
        return []

    # Separate system and conversation messages
    system_messages = [m for m in messages if m.get("role") == "system"]
    conversation_messages = [m for m in messages if m.get("role") != "system"]

    # Estimate current tokens
    current_tokens = estimate_messages_tokens(messages)

    if current_tokens <= max_tokens:
        return messages

    # Calculate how many recent messages to keep
    # Keep at least the last 4 messages (2 exchanges)
    min_recent = min(4, len(conversation_messages))

    # Try keeping more recent messages
    for keep_count in range(min_recent, len(conversation_messages) + 1):
        recent = conversation_messages[-keep_count:]
        older = conversation_messages[:-keep_count]

        # Create summary of older messages
        if older:
            summary_content = create_conversation_summary(older)
            summary_message = {
                "role": "system",
                "content": f"[Previous conversation summary: {summary_content}]",
            }
            candidate = system_messages + [summary_message] + recent
        else:
            candidate = system_messages + recent

        if estimate_messages_tokens(candidate) <= max_tokens:
            return candidate

    # If still over budget, truncate to most recent messages
    return system_messages + conversation_messages[-min_recent:]


def create_conversation_summary(messages: List[Dict]) -> str:
    """
    Create a brief summary of conversation messages.

    Extracts key points: customer interests, objections, decisions.
    """
    if not messages:
        return ""

    key_points = []

    for msg in messages:
        content = msg.get("content", "").lower()
        role = msg.get("role", "")

        # Extract customer interests
        if role == "user":
            if any(word in content for word in ["interested", "want", "need", "looking for"]):
                key_points.append(f"Customer expressed interest: {msg.get('content', '')[:100]}")

            # Extract objections
            if any(word in content for word in ["but", "however", "concerned", "worried", "expensive"]):
                key_points.append(f"Customer concern: {msg.get('content', '')[:100]}")

            # Extract decisions
            if any(word in content for word in ["yes", "no", "agree", "disagree", "book", "cancel"]):
                key_points.append(f"Customer decision: {msg.get('content', '')[:100]}")

    if not key_points:
        return "Previous conversation covered insurance options and scheduling."

    return "; ".join(key_points[:5])  # Limit to 5 key points


def compress_messages(
    messages: List[Dict],
    remove_system_duplicates: bool = True,
    remove_short_messages: bool = True,
    min_message_length: int = 10,
) -> List[Dict]:
    """
    Compress messages by removing redundancy.

    - Remove duplicate system messages
    - Remove very short messages (acknowledgments)
    - Merge consecutive same-role messages
    """
    if not messages:
        return []

    compressed = []
    seen_system_content = set()

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "").strip()

        # Skip empty messages
        if not content:
            continue

        # Remove duplicate system messages
        if role == "system" and remove_system_duplicates:
            if content in seen_system_content:
                continue
            seen_system_content.add(content)

        # Remove very short messages (but keep system messages)
        if remove_short_messages and role != "system":
            if len(content) < min_message_length:
                continue

        # Merge consecutive same-role messages
        if compressed and compressed[-1].get("role") == role:
            compressed[-1]["content"] += "\n" + content
        else:
            compressed.append(msg.copy())

    return compressed


def prune_retrieval_context(
    context: Dict,
    relevant_topics: List[str],
    max_context_tokens: int = 1000,
) -> Dict:
    """
    Prune retrieval context to only include relevant information.

    - Keep objection history if relevant
    - Keep sentiment if relevant
    - Keep preferences if relevant
    - Remove irrelevant message history
    """
    pruned = {}

    # Always include current state
    if "current_state" in context:
        pruned["current_state"] = context["current_state"]

    # Include objections if handling objections
    if "objections" in context and any(t in ["objection", "pricing", "trust", "timing"] for t in relevant_topics):
        pruned["objections"] = context["objections"]

    # Include sentiment if relevant
    if "sentiment" in context and any(t in ["sentiment", "emotion", "feeling"] for t in relevant_topics):
        pruned["sentiment"] = context["sentiment"]

    # Include preferences if relevant
    if "preferences" in context and any(t in ["preference", "interest", "need"] for t in relevant_topics):
        pruned["preferences"] = context["preferences"]

    # Prune message history to relevant messages
    if "message_history" in context:
        relevant_messages = []
        for msg in context["message_history"]:
            content = msg.get("content", "").lower()
            if any(topic.lower() in content for topic in relevant_topics):
                relevant_messages.append(msg)

        # Keep last 5 relevant messages max
        pruned["message_history"] = relevant_messages[-5:]

    return pruned


def optimize_prompt(
    system_prompt: str,
    messages: List[Dict],
    context: Optional[Dict] = None,
    max_total_tokens: int = 3000,
    relevant_topics: Optional[List[str]] = None,
) -> Tuple[str, List[Dict], int]:
    """
    Optimize a prompt for token efficiency.

    Returns:
        Tuple of (optimized_system_prompt, optimized_messages, estimated_tokens)
    """
    # 1. Compress messages
    compressed_messages = compress_messages(messages)

    # 2. Prune context if provided
    if context and relevant_topics:
        pruned_context = prune_retrieval_context(context, relevant_topics)
    else:
        pruned_context = context

    # 3. Add context to system prompt if available
    if pruned_context:
        context_str = format_context_for_prompt(pruned_context)
        if context_str:
            system_prompt = f"{system_prompt}\n\nContext:\n{context_str}"

    # 4. Summarize if still over budget
    all_messages = [{"role": "system", "content": system_prompt}] + compressed_messages
    estimated_tokens = estimate_messages_tokens(all_messages)

    if estimated_tokens > max_total_tokens:
        # Budget: 30% for system, 70% for conversation
        system_budget = int(max_total_tokens * 0.3)
        conversation_budget = max_total_tokens - system_budget

        # Summarize conversation
        optimized_messages = summarize_conversation(compressed_messages, conversation_budget)

        # Truncate system prompt if needed
        if estimate_tokens(system_prompt) > system_budget:
            system_prompt = system_prompt[:system_budget * 4]

        estimated_tokens = estimate_tokens(system_prompt) + estimate_messages_tokens(optimized_messages)
    else:
        optimized_messages = compressed_messages

    return system_prompt, optimized_messages, estimated_tokens


def format_context_for_prompt(context: Dict) -> str:
    """Format context dictionary as a string for inclusion in prompt."""
    parts = []

    if "current_state" in context:
        parts.append(f"Conversation state: {context['current_state']}")

    if "objections" in context:
        objections = context["objections"]
        if objections:
            parts.append(f"Customer objections: {', '.join(objections[-3:])}")

    if "sentiment" in context:
        parts.append(f"Customer sentiment: {context['sentiment']}")

    if "preferences" in context:
        prefs = context["preferences"]
        if prefs:
            pref_str = ", ".join(f"{k}: {v}" for k, v in list(prefs.items())[:3])
            parts.append(f"Customer preferences: {pref_str}")

    return "\n".join(parts) if parts else ""


def get_cached_prompt_prefix(
    tone: str,
    scenario: str,
) -> Optional[str]:
    """
    Get cached prompt prefix for common scenarios.

    Reduces token usage by reusing common prompt beginnings.
    """
    cache = {
        ("friendly", "outreach"): "You are a friendly insurance agent reaching out to a potential customer. Be warm, helpful, and professional.",
        ("friendly", "objection"): "You are a friendly insurance agent addressing a customer's concern. Be empathetic and provide clear information.",
        ("friendly", "booking"): "You are a friendly insurance agent helping schedule an appointment. Be accommodating and efficient.",
        ("professional", "outreach"): "You are a professional insurance representative. Be courteous and informative.",
        ("professional", "objection"): "You are a professional insurance representative addressing a concern. Be factual and reassuring.",
        ("professional", "booking"): "You are a professional insurance representative scheduling a consultation. Be clear and organized.",
        ("casual", "outreach"): "You are a casual, approachable insurance agent. Be friendly and conversational.",
        ("casual", "objection"): "You are a casual insurance agent addressing a concern. Be understanding and straightforward.",
        ("casual", "booking"): "You are a casual insurance agent helping with scheduling. Be relaxed and helpful.",
    }

    return cache.get((tone, scenario))
