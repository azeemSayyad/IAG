"""
Hallucination Prevention (Step 18.5)

Validates AI-generated responses before sending to customers.

Rules:
1. Never hallucinate pricing — only use campaign-provided pricing
2. Never violate compliance — no medical/legal claims
3. Never spam users — respect rate limits and opt-outs
4. Never give fake promises — no guarantees or false urgency

Validation Pipeline:
1. Length Check — Response must be reasonable length
2. Prohibited Content Scan — No banned phrases
3. Pricing Validation — Only use provided pricing
4. Compliance Check — TCPA, insurance regulations
5. Tone Check — Appropriate for context
"""

import re
from typing import Dict, List, Optional, Tuple


# Prohibited content patterns
PROHIBITED_PATTERNS = {
    "medical_advice": [
        r"\bcure[sd]?\b",
        r"\btreat[sd]?\b",
        r"\bdiagnos(?:e[ds]?|is)\b",
        r"\bmedic(?:al|ine)\b.*\badvice\b",
        r"\bdoctor\b.*\bsay\b",
    ],
    "legal_advice": [
        r"\blegal\s+advice\b",
        r"\blawyer\b.*\brecommend\b",
        r"\bsue\b",
        r"\blawsuit\b",
    ],
    "false_guarantees": [
        r"\bguarantee[ds]?\b",
        r"\b100%\s+(?:sure|certain|guaranteed)\b",
        r"\bdefinitely\s+will\b",
        r"\bpromise[ds]?\b",
    ],
    "false_urgency": [
        r"\blast\s+chance\b",
        r"\bonly\s+\d+\s+left\b",
        r"\bexpire[sd]?\s+(?:today|tonight|soon)\b",
        r"\bnever\s+(?:again|available)\b",
    ],
    "competitor_bashing": [
        r"\bcompetitor\b.*\b(bad|terrible|awful|worst)\b",
        r"\bother\s+compan(?:y|ies)\b.*\b(suck|horrible)\b",
    ],
    "pii_leak": [
        r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
        r"\b\d{16}\b",  # Credit card
    ],
}

# Pricing-related keywords
PRICING_KEYWORDS = [
    "price", "cost", "rate", "premium", "pay", "payment",
    "dollar", "$", "per month", "per year", "annual", "monthly",
    "afford", "expensive", "cheap", "discount", "save",
]

# Compliance phrases
COMPLIANCE_SAFE_PHRASES = [
    "i'm not able to provide medical advice",
    "please consult with a healthcare professional",
    "i'm not a licensed medical professional",
    "for legal questions, please consult an attorney",
    "i cannot guarantee specific outcomes",
    "results may vary",
    "coverage depends on your specific policy",
]


def check_length(response: str, min_length: int = 10, max_length: int = 1600) -> Tuple[bool, str]:
    """
    Check if response length is appropriate.

    SMS limit is 1600 characters.
    """
    length = len(response)

    if length < min_length:
        return False, f"Response too short ({length} chars, min {min_length})"
    if length > max_length:
        return False, f"Response too long ({length} chars, max {max_length})"

    return True, "Length OK"


def scan_prohibited_content(response: str) -> Tuple[bool, List[str]]:
    """
    Scan for prohibited content patterns.

    Returns (is_safe, list_of_violations).
    """
    violations = []
    response_lower = response.lower()

    for category, patterns in PROHIBITED_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, response_lower):
                violations.append(f"{category}: {pattern}")
                break  # One violation per category is enough

    return len(violations) == 0, violations


def validate_pricing(response: str, allowed_pricing: Optional[Dict] = None) -> Tuple[bool, str]:
    """
    Validate that pricing information is accurate.

    If response mentions pricing, it must match allowed_pricing.
    """
    if not allowed_pricing:
        # No pricing data provided — check if response mentions pricing
        response_lower = response.lower()
        for keyword in PRICING_KEYWORDS:
            if keyword in response_lower:
                return False, f"Response mentions pricing but no pricing data provided: '{keyword}'"
        return True, "No pricing mentioned"

    # Check if pricing values match
    response_lower = response.lower()

    # Extract numbers from response
    numbers = re.findall(r'\$?(\d+(?:\.\d{2})?)', response)
    for num in numbers:
        value = float(num)
        # Check if this number matches any allowed pricing
        found_match = False
        for price_key, price_value in allowed_pricing.items():
            if abs(value - price_value) < 0.01:
                found_match = True
                break
        if not found_match and value > 0:
            # Could be a non-price number, but flag if it's in price range
            if 1 <= value <= 10000:
                return False, f"Price ${value} not in allowed pricing data"

    return True, "Pricing validated"


def check_compliance(response: str) -> Tuple[bool, List[str]]:
    """
    Check response for compliance issues.

    Returns (is_compliant, list_of_issues).
    """
    issues = []
    response_lower = response.lower()

    # Check for medical claims
    medical_patterns = [
        r"\bcure[ds]?\b",
        r"\btreat(?:s|ed|ing)?\b.*\b(?:diabetes|cancer|heart)\b",
        r"\bprevents?\b.*\b(?:disease|illness|death)\b",
    ]
    for pattern in medical_patterns:
        if re.search(pattern, response_lower):
            issues.append(f"Potential medical claim: {pattern}")

    # Check for legal claims
    legal_patterns = [
        r"\blegally\s+(?:required|obligated)\b",
        r"\blaw\s+(?:requires|mandates)\b",
    ]
    for pattern in legal_patterns:
        if re.search(pattern, response_lower):
            issues.append(f"Potential legal claim: {pattern}")

    # Check for guaranteed outcomes
    guarantee_patterns = [
        r"\bguarantee[ds]?\b",
        r"\bcert(?:ain|ainly)\b.*\bresult\b",
    ]
    for pattern in guarantee_patterns:
        if re.search(pattern, response_lower):
            issues.append(f"Potential guarantee: {pattern}")

    return len(issues) == 0, issues


def check_tone(response: str, expected_tone: str = "friendly") -> Tuple[bool, str]:
    """
    Check if response matches expected tone.

    Basic heuristic checks.
    """
    response_lower = response.lower()

    # Check for aggressive language
    aggressive_words = ["stupid", "idiot", "dumb", "fool", "shut up", "go away"]
    for word in aggressive_words:
        if word in response_lower:
            return False, f"Aggressive language detected: '{word}'"

    # Check for overly formal language in casual tone
    if expected_tone == "casual":
        formal_phrases = ["pursuant to", "hereinafter", "aforementioned", "notwithstanding"]
        for phrase in formal_phrases:
            if phrase in response_lower:
                return False, f"Too formal for casual tone: '{phrase}'"

    # Check for all caps (shouting)
    if response.isupper() and len(response) > 20:
        return False, "Response is all caps (appears as shouting)"

    return True, "Tone OK"


def validate_response(
    response: str,
    allowed_pricing: Optional[Dict] = None,
    expected_tone: str = "friendly",
    context: Optional[Dict] = None,
) -> Dict:
    """
    Full validation pipeline for AI response.

    Returns:
        Dict with is_valid, violations, warnings, safe_response
    """
    violations = []
    warnings = []

    # 1. Length check
    is_valid, message = check_length(response)
    if not is_valid:
        violations.append(f"length: {message}")

    # 2. Prohibited content scan
    is_safe, content_violations = scan_prohibited_content(response)
    if not is_safe:
        violations.extend([f"prohibited: {v}" for v in content_violations])

    # 3. Pricing validation
    is_valid, message = validate_pricing(response, allowed_pricing)
    if not is_valid:
        violations.append(f"pricing: {message}")

    # 4. Compliance check
    is_compliant, compliance_issues = check_compliance(response)
    if not is_compliant:
        warnings.extend([f"compliance: {i}" for i in compliance_issues])

    # 5. Tone check
    is_valid, message = check_tone(response, expected_tone)
    if not is_valid:
        violations.append(f"tone: {message}")

    # Determine if response is safe to send
    is_valid = len(violations) == 0

    # Generate safe response if needed
    safe_response = response if is_valid else get_safe_fallback(expected_tone, context)

    return {
        "is_valid": is_valid,
        "violations": violations,
        "warnings": warnings,
        "original_response": response,
        "safe_response": safe_response,
        "replaced": not is_valid,
    }


def get_safe_fallback(tone: str = "friendly", context: Optional[Dict] = None) -> str:
    """
    Get a safe fallback response when validation fails.

    These responses are pre-approved and guaranteed to be compliant.
    """
    fallbacks = {
        "friendly": [
            "Thanks for your interest! I'd love to help you explore our insurance options. When would be a good time for a quick call?",
            "Great question! Let me connect you with one of our specialists who can provide accurate information. When works best for you?",
            "I appreciate you reaching out! Our team would be happy to discuss your insurance needs. What's the best time to call?",
        ],
        "professional": [
            "Thank you for your inquiry. I'd be happy to arrange a consultation with one of our insurance specialists. Please let me know your availability.",
            "I understand your interest. Our team can provide detailed information about our coverage options. When would be convenient for a call?",
            "Thank you for considering us. I'd like to connect you with a specialist who can address your specific needs. What time works best?",
        ],
        "casual": [
            "Hey! Thanks for reaching out. Want to hop on a quick call to chat about your insurance options? Let me know when works!",
            "Thanks for getting in touch! Our team is great at finding the right coverage. When's a good time to talk?",
            "Appreciate you connecting with us! Let's find a time to chat about what you need. What works for you?",
        ],
        "urgent": [
            "Thank you for your interest. I'd like to connect you with a specialist right away. When can we schedule a call?",
            "I understand you're interested in learning more. Our team is available to help. What's the best time to reach you?",
            "Thanks for reaching out. Let's get you connected with a specialist as soon as possible. When works for you?",
        ],
    }

    import random
    tone_fallbacks = fallbacks.get(tone, fallbacks["friendly"])
    return random.choice(tone_fallbacks)
