"""
Hybrid Intent Classifier (Step 5.3)

Architecture:
1. Fast Classifier (< 100ms) — keyword matching, regex patterns
   - Handles 80% of clear messages
   - High confidence threshold: 0.85
2. LLM Reasoning Layer — for ambiguous messages
   - Full conversation context
   - Handles sarcasm, ambiguity, slang

Confidence threshold: 0.85 for fast classifier
Below threshold → fall through to LLM
"""

import re
from typing import Dict, List, Optional, Tuple

from app.intent.services.intents import Intent
from app.intent.services.nlp import preprocess, extract_features


# Keyword patterns for each intent
INTENT_PATTERNS = {
    Intent.STOP: {
        "exact": ["stop", "unsubscribe", "remove me", "opt out", "opt-out", "cancel", "end", "quit", "leave me alone", "don't contact me", "do not contact"],
        "patterns": [
            r"^(stop|unsubscribe|remove|cancel|end|quit)$",
            r"(please|pls|plz)?\s*(stop|unsubscribe|remove)\s*(contacting|messaging|texting|calling)?\s*me?",
            r"don'?t\s*(contact|message|text|call|reach)\s*me",
            r"(leave|stop)\s*me\s*alone",
            r"i\s*(don'?t|do\s*not)\s*want\s*(this|to\s*hear|to\s*be\s*contacted)",
        ],
        "weight": 1.0,
    },
    Intent.BOOK_NOW: {
        "exact": ["book", "book it", "let's do it", "sign me up", "i'm in", "count me in", "let's go", "book now", "schedule it", "set it up"],
        "patterns": [
            r"(let'?s|lets)\s*(book|schedule|do\s*it|go)",
            r"(book|schedule)\s*(it|me|this|now|asap)",
            r"sign\s*me\s*up",
            r"i'?m\s*in",
            r"count\s*me\s*in",
            r"(yes|yeah|yep)\s*(,?\s*)?(let'?s|lets)?\s*(book|schedule|do\s*it)",
            r"(when|what\s*time).*(available|free|open)",
            r"(i'?d\s*like\s*to|want\s*to)\s*(book|schedule|set\s*up)\s*(an?\s*)?(appointment|call|meeting)?",
        ],
        "weight": 0.95,
    },
    Intent.POSITIVE: {
        "exact": ["yes", "yeah", "yep", "sure", "absolutely", "definitely", "of course", "sounds good", "perfect", "great", "awesome", "love it", "i'm interested", "interested", "tell me more", "go ahead", "please do"],
        "patterns": [
            r"^(yes|yeah|yep|sure|absolutely|definitely|of\s*course)$",
            r"(sounds|that\s*sounds)\s*(good|great|perfect|awesome|wonderful)",
            r"(i'?m|i\s*am)\s*(interested|in|ready|down)",
            r"(yes|yeah|yep)\s*(please|pls|plz)?",
            r"(tell|give)\s*me\s*more",
            r"(go\s*ahead|please\s*do|do\s*it)",
            r"i'?d\s*(like|love)\s*(that|to\s*learn|to\s*know|to\s*hear)",
            r"(that|this|it)\s*(sounds|seems|looks)\s*(great|good|perfect|interesting)",
        ],
        "weight": 0.9,
    },
    Intent.INTERESTED: {
        "exact": ["maybe", "possibly", "perhaps", "i'll think about it", "let me think", "not sure yet", "tell me more", "what is this about", "what do you offer"],
        "patterns": [
            r"(maybe|possibly|perhaps|i'?ll\s*think)",
            r"(tell|explain|describe)\s*me\s*more",
            r"what\s*(is|are|do)\s*(this|you|your)\s*(about|offer|provide|sell)",
            r"(not\s*sure|unsure|undecided)\s*(yet|right\s*now)?",
            r"(interested|curious)\s*(but|however|though)?",
            r"(what|how)\s*(does|do)\s*(this|it|your\s*service)\s*(work|cost|include)",
            r"i'?m\s*(curious|interested)\s*(about|in|to\s*know)",
            r"(can|could)\s*(you|u)\s*(tell|explain|share)\s*(me)?\s*more",
        ],
        "weight": 0.85,
        "priority_over": [Intent.POSITIVE, Intent.QUESTION],  # These patterns take priority
    },
    Intent.QUESTION: {
        "exact": ["what", "how", "when", "where", "who", "why", "can you explain", "i have a question"],
        "patterns": [
            r"^(what|how|when|where|who|why)\s",
            r"(can|could|would)\s*(you|u)\s*(explain|clarify|tell|describe)",
            r"(i\s*have|got)\s*a\s*question",
            r"(what|how)\s*(does|do|is|are|would|will|can)",
            r"(tell|explain)\s*(me\s*)?(about|how|what|why)",
            r"\?$",  # Ends with question mark
        ],
        "weight": 0.8,
    },
    Intent.SKEPTICAL: {
        "exact": ["i don't believe it", "sounds like a scam", "is this real", "how do i know", "prove it", "not convinced", "i'm not sure about this"],
        "patterns": [
            r"(don'?t|do\s*not)\s*(believe|trust|buy|think)",
            r"(sounds|seems)\s*(like\s*a?\s*)?(scam|fake|too\s*good|suspicious|sketchy)",
            r"(is|are)\s*(this|you|these)\s*(real|legit|genuine|trustworthy)",
            r"(how|why)\s*(do|can|should)\s*i\s*(know|trust|believe|be\s*sure)",
            r"(prove|show)\s*(it|me|yourself)",
            r"(not|ain'?t)\s*(convinced|sure|sold|buying\s*it)",
            r"(i'?m|i\s*am)\s*(not|n'?t)\s*(sure|convinced|sold)\s*(about|on)\s*(this|it)",
            r"(seems|sounds)\s*(too\s*good|fishy|weird|strange)",
        ],
        "weight": 0.85,
    },
    Intent.NEGATIVE: {
        "exact": ["no", "nah", "nope", "not interested", "no thanks", "not for me", "pass", "i'll pass", "hard pass", "don't want", "not now", "maybe later"],
        "patterns": [
            r"^(no|nah|nope|not\s*interested|no\s*thanks?|pass|i'?ll\s*pass|hard\s*pass)$",
            r"(no|nah|nope)\s*(thanks?|thank\s*you)?",
            r"(not|ain'?t)\s*(interested|for\s*me|right\s*now|now)",
            r"(don'?t|do\s*not)\s*(want|need|think\s*so|bother)",
            r"(i'?ll|i\s*will)\s*pass",
            r"(maybe|perhaps)\s*(some\s*other|later|another\s*time|next\s*time)",
            r"(not?\s*now|later|another\s*time)",
            r"(go\s*away|leave|get\s*lost|buzz\s*off)",
        ],
        "weight": 0.9,
    },
    Intent.RESCHEDULE: {
        "exact": ["reschedule", "change my appointment", "move my appointment", "different time", "another time", "can we reschedule"],
        "patterns": [
            r"(reschedule|re-schedule)",
            r"(change|move|shift|push)\s*(my|the)?\s*(appointment|booking|meeting|call)",
            r"(different|another|new|other)\s*(time|date|day|slot)",
            r"(can|could|would)\s*(we|i)\s*(reschedule|change|move|shift)",
            r"(i\s*can'?t|cannot|can'?t\s*make)\s*(it|the\s*appointment|that\s*time)",
            r"(something\s*came\s*up|conflict|overlap)",
        ],
        "weight": 0.9,
    },
}


class IntentResult:
    def __init__(self, intent: Intent, confidence: float, method: str, details: Dict = None):
        self.intent = intent
        self.confidence = confidence
        self.method = method  # "fast" or "llm"
        self.details = details or {}

    def to_dict(self) -> dict:
        return {
            "intent": self.intent.value,
            "confidence": round(self.confidence, 3),
            "method": self.method,
            "details": self.details,
        }


def fast_classify(text: str) -> IntentResult:
    """
    Fast intent classifier using keyword matching and regex.

    Returns IntentResult with confidence score.
    If confidence < 0.85, caller should fall through to LLM.
    """
    processed, features = preprocess(text)
    lower = processed.lower().strip()

    # Check for negation patterns first (e.g., "not interested", "not now")
    negation_patterns = [
        (r"^not\s+interested", Intent.NEGATIVE),
        (r"^not\s+now", Intent.NEGATIVE),
        (r"^no\s+thanks?", Intent.NEGATIVE),
        (r"^don'?t\s+want", Intent.NEGATIVE),
        (r"^i'?m\s+not\s+interested", Intent.NEGATIVE),
        (r"^not\s+for\s+me", Intent.NEGATIVE),
    ]
    for pattern, intent in negation_patterns:
        if re.match(pattern, lower):
            return IntentResult(intent, 0.95, "fast", {"reason": "negation_pattern"})

    # Check for reschedule patterns before QUESTION
    reschedule_patterns = [
        r"(reschedule|re-schedule)",
        r"(change|move|shift)\s*(my|the)?\s*(appointment|booking|meeting|call)",
        r"(different|another|new)\s*(time|date|day|slot)",
        r"can'?t\s+make\s+(it|the)",
        r"(can|could|would)\s*(we|i)\s*(change|move|shift)\s*(the)?\s*(time|date|slot)",
        r"(change|move)\s*(the)?\s*(time|date)",
    ]
    for pattern in reschedule_patterns:
        if re.search(pattern, lower):
            return IntentResult(Intent.RESCHEDULE, 0.9, "fast", {"reason": "reschedule_pattern"})

    # Check for SKEPTICAL patterns before QUESTION
    skeptical_patterns = [
        r"(is|are)\s*(this|you|these)\s*(real|legit|genuine|trustworthy)",
        r"(sounds|seems)\s*(like\s*a?\s*)?(scam|fake|too\s*good|suspicious)",
        r"(don'?t|do\s*not)\s*(believe|trust)",
        r"(prove|show)\s*(it|me)",
    ]
    for pattern in skeptical_patterns:
        if re.search(pattern, lower):
            return IntentResult(Intent.SKEPTICAL, 0.9, "fast", {"reason": "skeptical_pattern"})

    # Check for QUESTION patterns first
    question_patterns = [
        r"^what\s+(is|are|do)\s+(this|it|you|your)",
        r"^how\s+(does|do|can|would)",
        r"^when\s+(is|are|do|can)",
        r"^where\s+(is|are|do|can)",
        r"^why\s+(is|are|do|did)",
        r"^who\s+(is|are|do|can)",
    ]
    for pattern in question_patterns:
        if re.search(pattern, lower):
            return IntentResult(Intent.QUESTION, 0.9, "fast", {"reason": "question_pattern"})

    # Check for INTERESTED patterns before POSITIVE
    interested_patterns = [
        r"(tell|explain|describe)\s*me\s*more",
        r"(can|could)\s*(you|u)\s*(tell|explain|share)\s*(me)?\s*more",
    ]
    for pattern in interested_patterns:
        if re.search(pattern, lower):
            return IntentResult(Intent.INTERESTED, 0.9, "fast", {"reason": "interested_pattern"})

    scores = {}

    for intent, patterns in INTENT_PATTERNS.items():
        score = 0.0
        matched = []

        # Check exact matches
        for exact in patterns["exact"]:
            if lower == exact or lower.startswith(exact + " ") or lower.endswith(" " + exact) or (" " + exact + " ") in (" " + lower + " "):
                score = 1.0
                matched.append(f"exact:{exact}")
                break

        # Check regex patterns if no exact match
        if score < 1.0:
            for pattern in patterns["patterns"]:
                if re.search(pattern, lower):
                    score = max(score, 0.85)
                    matched.append(f"pattern:{pattern[:30]}")

        # Apply feature adjustments
        if score > 0:
            # Boost STOP if stop words detected
            if intent == Intent.STOP and features["has_stop"]:
                score = min(score * 1.1, 1.0)

            # Boost BOOK_NOW if booking words detected
            if intent == Intent.BOOK_NOW and features["booking_count"] > 0:
                score = min(score * 1.05, 1.0)

            # Boost QUESTION if question mark detected
            if intent == Intent.QUESTION and features["has_question"]:
                score = min(score * 1.05, 1.0)

            # Reduce NEGATIVE if positive words outweigh
            if intent == Intent.NEGATIVE and features["positive_count"] > features["negative_count"]:
                score *= 0.7

            scores[intent] = (score, matched)

    if not scores:
        # No match — default to INTERESTED if positive signals, else QUESTION
        if features["positive_count"] > 0:
            return IntentResult(Intent.INTERESTED, 0.5, "fast", {"reason": "positive_signals"})
        elif features["has_question"]:
            return IntentResult(Intent.QUESTION, 0.6, "fast", {"reason": "question_detected"})
        else:
            return IntentResult(Intent.INTERESTED, 0.3, "fast", {"reason": "default"})

    # Get best match
    best_intent = max(scores, key=lambda k: scores[k][0])
    best_score, matched = scores[best_intent]

    return IntentResult(
        intent=best_intent,
        confidence=best_score,
        method="fast",
        details={"matched": matched, "features": {
            "has_question": features["has_question"],
            "has_negation": features["has_negation"],
            "positive_count": features["positive_count"],
            "negative_count": features["negative_count"],
        }},
    )


async def llm_classify(
    text: str,
    conversation_history: List[Dict] = None,
) -> IntentResult:
    """
    LLM-based intent classification for ambiguous messages.

    Uses Ollama to analyze the message in context.
    """
    from app.ai.services.ollama import ollama_client

    # Build context
    history_text = ""
    if conversation_history:
        for msg in conversation_history[-5:]:
            role = "Customer" if msg.get("sender") == "customer" else "AI"
            history_text += f"{role}: {msg.get('content', '')}\n"

    prompt = f"""Classify this customer message into one of these intents:
- POSITIVE: Customer is ready or eager
- INTERESTED: Customer wants more info
- SKEPTICAL: Customer has objections
- NEGATIVE: Customer is not interested
- STOP: Customer wants to opt out
- BOOK_NOW: Customer explicitly wants to book
- QUESTION: Customer asked a specific question
- RESCHEDULE: Customer wants to change existing booking

Conversation history:
{history_text}

Customer message: "{text}"

Respond with ONLY the intent name (e.g., "POSITIVE")."""

    try:
        response = await ollama_client.generate(
            prompt=prompt,
            temperature=0.1,  # Low temperature for consistent classification
            max_tokens=20,
        )

        # Parse response
        intent_str = response.strip().upper()
        try:
            intent = Intent(intent_str)
            return IntentResult(intent, 0.9, "llm", {"raw_response": response})
        except ValueError:
            # Invalid response from LLM
            return IntentResult(Intent.INTERESTED, 0.5, "llm", {"raw_response": response, "parse_error": True})

    except Exception as e:
        # LLM failed — return low-confidence default
        return IntentResult(Intent.INTERESTED, 0.3, "llm", {"error": str(e)})


async def classify_intent(
    text: str,
    conversation_history: List[Dict] = None,
    force_llm: bool = False,
) -> IntentResult:
    """
    Hybrid intent classification.

    1. Try fast classifier first
    2. If confidence < 0.85, fall through to LLM
    3. If LLM fails, use fast classifier result
    """
    # Fast classifier
    fast_result = fast_classify(text)

    # If high confidence or forced, return fast result
    if fast_result.confidence >= 0.85 and not force_llm:
        return fast_result

    # Fall through to LLM
    try:
        llm_result = await llm_classify(text, conversation_history)

        # If LLM has higher confidence, use it
        if llm_result.confidence > fast_result.confidence:
            return llm_result

        # Otherwise use fast result
        return fast_result

    except Exception:
        # LLM failed — use fast result
        return fast_result
