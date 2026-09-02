"""
Objection Handling (Step 5.4)

Detects and handles common insurance objections:
- Pricing objections
- Trust objections
- Timing objections
"""

import re
from typing import Dict, List, Optional, Tuple
from enum import Enum


class ObjectionType(str, Enum):
    PRICING = "pricing"
    TRUST = "trust"
    TIMING = "timing"
    NOT_INTERESTED = "not_interested"
    ALREADY_COVERED = "already_covered"
    NEED_TO_THINK = "need_to_think"
    SPOUSE_DECIDES = "spouse_decides"
    UNKNOWN = "unknown"


# Objection detection patterns
OBJECTION_PATTERNS = {
    ObjectionType.PRICING: [
        r"(too|way\s+too)\s+(expensive|costly|pricey|much)",
        r"(can'?t|cannot)\s+(afford|pay|handle)",
        r"(price|cost|rate|fee|premium)\s+(is|seems?|feels?)\s+(too\s+)?(high|expensive|much|crazy|insane|ridiculous)",
        r"(how\s+much|what\s+(does|is)\s+the\s+(cost|price|rate))",
        r"(budget|money|financial)\s+(issue|concern|problem|tight|strain)",
        r"(cheaper|less\s+expensive|more\s+affordable)\s+(option|alternative|plan)",
        r"(not\s+worth|isn'?t\s+worth)\s+(it|the\s+(price|cost|money))",
    ],
    ObjectionType.TRUST: [
        r"(scam|fake|fraud|rip\s*off|con\s*job)",
        r"(don'?t|do\s*not)\s+(trust|believe)\s+(you|this|it|them)",
        r"(is|are)\s+(this|you|these)\s+(real|legit|genuine|trustworthy|reliable)",
        r"(sounds?\s+too\s+good\s+to\s+be\s+true)",
        r"(prove|show)\s+(it|me|yourself|evidence)",
        r"(never\s+heard\s+of)\s+(you|this|your\s+company)",
        r"(how\s+do\s+i\s+know|why\s+should\s+i\s+(trust|believe))",
        r"(review|rating|complaint|bbb|better\s+business)",
    ],
    ObjectionType.TIMING: [
        r"(not\s+(a\s+)?(the\s+)?right\s+time|bad\s+timing|bad\s+time)",
        r"(too\s+busy|no\s+time|don'?t\s+have\s+time|no\s+free\s+time)",
        r"(later|another\s+time|some\s+other\s+time|next\s+(week|month|year))",
        r"(call\s+me\s+back|reach\s+out\s+later|contact\s+(me\s+)?later|try\s+(me\s+)?again)",
        r"(right\s+now|at\s+the\s+moment|currently)\s+(isn'?t|not)\s+(a\s+)?good",
        r"(holiday|vacation|trip|traveling|out\s+of\s+town)",
        r"(moving|relocating|renovation|busy\s+with\s+(work|family|life))",
    ],
    ObjectionType.ALREADY_COVERED: [
        r"(already|currently)\s+(have|got|covered|insured)",
        r"(existing|current)\s+(policy|coverage|insurance|plan|agent)",
        r"(happy|satisfied|content)\s+with\s+(my|our|current|existing)",
        r"(don'?t|do\s*not)\s+need\s+(more|additional|another|new)\s+(insurance|coverage|policy)",
        r"(covered|protected|insured)\s+(already|through|via|with|by)",
    ],
    ObjectionType.NEED_TO_THINK: [
        r"(need|want|have)\s+to\s+(think|consider|research|look|check|review|ponder)",
        r"(let\s+me\s+(think|consider|research|look|check|review))",
        r"(i'?ll|will)\s+(think|consider|research|look|get\s+back)",
        r"(not\s+ready|not\s+sure\s+yet|still\s+(thinking|deciding|considering))",
        r"(sleep\s+on\s+it|think\s+(it|this)\s+over)",
        r"(compare|comparison|shopping\s+around|other\s+options|alternatives)",
    ],
    ObjectionType.SPOUSE_DECIDES: [
        r"(spouse|wife|husband|partner|significant\s+other)\s+(needs?\s+to|has\s+to|would|should|decides?|makes?)",
        r"(need|want|have)\s+to\s+(ask|check|talk\s+to|discuss\s+with)\s+(my\s+)?(spouse|wife|husband|partner|family)",
        r"(we|both\s+of\s+us)\s+(need|have|want)\s+to\s+(decide|agree|discuss)",
        r"(joint|together|mutual)\s+(decision|choice|call)",
        r"(ask|check\s+with|talk\s+to)\s+(my\s+)?(wife|husband|spouse|partner|family)",
    ],
}


def detect_objection(text: str) -> Tuple[ObjectionType, float]:
    """
    Detect the type of objection in a message.

    Returns:
        Tuple of (objection_type, confidence)
    """
    lower = text.lower().strip()

    for objection_type, patterns in OBJECTION_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, lower):
                return (objection_type, 0.9)

    return (ObjectionType.UNKNOWN, 0.3)


def get_objection_response(objection_type: ObjectionType, first_name: str) -> str:
    """
    Get an appropriate response for an objection type.
    """
    responses = {
        ObjectionType.PRICING: [
            f"I totally understand, {first_name}. Our agents can walk you through options that fit your budget. Would you like to chat with one?",
            f"Great point, {first_name}. We have flexible plans at different price points. Let me connect you with an agent who can find the right fit.",
            f"I hear you, {first_name}. Many of our customers were surprised by how affordable their coverage ended up being. Worth a quick look?",
        ],
        ObjectionType.TRUST: [
            f"I completely understand, {first_name}. We've helped thousands of families find the right coverage. Our agents are licensed professionals who'll answer all your questions.",
            f"That's a fair concern, {first_name}. We're here to help, not push. Our agents can provide credentials and references if you'd like.",
            f"I appreciate your honesty, {first_name}. We're a licensed company with real customer reviews. Happy to share more details.",
        ],
        ObjectionType.TIMING: [
            f"No rush at all, {first_name}. When would be a better time for you?",
            f"I understand, {first_name}. How about we schedule something at your convenience?",
            f"Fair enough, {first_name}. I can reach out later if that works better for you.",
        ],
        ObjectionType.ALREADY_COVERED: [
            f"That's great, {first_name}! Many of our customers found they could get better coverage or save money by comparing. Would you be open to a quick review?",
            f"Good to hear you're covered, {first_name}. We often help people find better rates or additional coverage they didn't know about.",
            f"Understood, {first_name}. Would you be interested in a free policy review to make sure you're getting the best deal?",
        ],
        ObjectionType.NEED_TO_THINK: [
            f"Of course, {first_name}. Take your time. Would it help if I sent you some information to review?",
            f"I completely understand, {first_name}. No pressure at all. When would be a good time to follow up?",
            f"Take your time, {first_name}. I'm here whenever you're ready to chat.",
        ],
        ObjectionType.SPOUSE_DECIDES: [
            f"That makes total sense, {first_name}. Would you like to schedule a call for both of you?",
            f"I understand, {first_name}. We can set up a time that works for you and your spouse.",
            f"Of course, {first_name}. Would it help if I sent some information for you to review together?",
        ],
        ObjectionType.NOT_INTERESTED: [
            f"No problem, {first_name}. If anything changes, we're here to help!",
            f"I appreciate your honesty, {first_name}. We'll be here if you need us.",
            f"Understood, {first_name}. Feel free to reach out anytime!",
        ],
    }

    import random
    response_list = responses.get(objection_type, responses[ObjectionType.NOT_INTERESTED])
    return random.choice(response_list)


def handle_objection(text: str, first_name: str) -> Dict[str, any]:
    """
    Detect and respond to an objection.

    Returns:
        Dict with objection_type, response, and confidence.
    """
    objection_type, confidence = detect_objection(text)
    response = get_objection_response(objection_type, first_name)

    return {
        "objection_type": objection_type.value,
        "confidence": confidence,
        "response": response,
    }
