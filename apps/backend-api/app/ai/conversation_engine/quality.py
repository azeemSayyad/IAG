"""
AI Quality Layer (Step 36.8)

Validates AI responses before sending to customers:

1. Hallucination Check — Detect false claims, invented facts
2. TCPA Compliance — Opt-out handling, consent, timing
3. Aggressive Language — Detect pushy, manipulative, or hostile tone
4. Fake Pricing — Prevent invented rates, quotes, guarantees
5. Spam Behavior — Detect repetitive, bulk-like, or robotic patterns

Quality Score: 0-100 (higher = safer)
Threshold: 70 minimum to send
"""

import re
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone


# Quality thresholds
MIN_QUALITY_SCORE = 70
MAX_MESSAGE_LENGTH = 1600  # SMS limit
MIN_MESSAGE_LENGTH = 10


class QualityCheck:
    """Result of a single quality check."""

    def __init__(
        self,
        check_name: str,
        passed: bool,
        score: int,
        violations: List[str] = None,
        details: str = "",
    ):
        self.check_name = check_name
        self.passed = passed
        self.score = score  # 0-100
        self.violations = violations or []
        self.details = details

    def to_dict(self) -> Dict:
        return {
            "check": self.check_name,
            "passed": self.passed,
            "score": self.score,
            "violations": self.violations,
            "details": self.details,
        }


class QualityResult:
    """Combined result of all quality checks."""

    def __init__(
        self,
        checks: List[QualityCheck],
        original_response: str,
        safe_response: str,
        overall_score: int,
        passed: bool,
    ):
        self.checks = checks
        self.original_response = original_response
        self.safe_response = safe_response
        self.overall_score = overall_score
        self.passed = passed
        self.violations = []
        for check in checks:
            self.violations.extend(check.violations)

    def to_dict(self) -> Dict:
        return {
            "overall_score": self.overall_score,
            "passed": self.passed,
            "checks": [c.to_dict() for c in self.checks],
            "violations": self.violations,
            "original_length": len(self.original_response),
            "safe_response_length": len(self.safe_response),
            "was_modified": self.original_response != self.safe_response,
        }


# --- Hallucination Patterns ---

HALLUCINATION_PATTERNS = {
    "false_authority": [
        r"\baccording to (?:our|my) (?:records|database|files|system)\b",
        r"\bour (?:data|records|system) (?:shows|indicates|confirms)\b",
        r"\bi (?:have|see|found) (?:your|the) (?:records|information|data)\b",
    ],
    "invented_facts": [
        r"\b(?:studies|research|data) (?:shows?|proves?|confirms?)\b",
        r"\b\d+% (?:of (?:our |)customers|of people|success rate)\b",
        r"\bguaranteed? (?:to |)\b.*\b(?:save|reduce|increase|improve)\b",
    ],
    "false_authority_figures": [
        r"\b(?:the|our) (?:CEO|president|doctor|lawyer|expert)\b.*\b(says?|recommends?|advises?)\b",
        r"\bi'?m (?:a |the |)licensed\b",
        r"\bwe (?:are|have) (?:certified|accredited|licensed)\b",
    ],
}

# --- TCPA Violation Patterns ---

TCPA_VIOLATIONS = {
    "opt_out_not_respected": [
        r"\b(?:but|however|although)\b.*\b(before you go|one more thing|wait)\b",
        r"\bare you (?:sure|certain)\b.*\b(opt out|stop|unsubscribe)\b",
    ],
    "excessive_contact": [
        r"\b(?:again|once more|one more time)\b.*\b(today|this week)\b",
        r"\b(?:i'?ve|we'?ve) (?:already|tried)\b.*\b(contacted|reached|called|texted)\b",
    ],
    "misleading_urgency": [
        r"\b(?:last|final|only) (?:chance|opportunity|day)\b",
        r"\b(?:expires?|ending|closing)\b.*\b(today|tonight|midnight|soon)\b",
        r"\b(?:act now|don'?t wait|hurry)\b",
    ],
    "deceptive_practices": [
        r"\b(?:free|no cost|no charge)\b.*\b(?:commitment|obligation|catch)\b",
        r"\b(?:you'?ve? (?:been|been) (?:selected|chosen|picked))\b",
        r"\b(?:limited time|exclusive offer|special deal)\b.*\b(just for you|only you)\b",
    ],
}

# --- Aggressive Language Patterns ---

AGGRESSIVE_PATTERNS = {
    "pressure": [
        r"\byou (?:must|have to|need to|should)\b.*\b(now|today|immediately)\b",
        r"\b(?:if you don'?t|unless you)\b.*\b(?:you'?ll|you will)\b.*\b(?:miss|lose|regret)\b",
        r"\b(?:don'?t|do not)\b.*\b(?:miss out|lose out|pass up)\b",
    ],
    "guilt_tripping": [
        r"\bi'?ve? (?:spent|wasted)\b.*\b(?:time|effort)\b.*\b(?:on you|for you)\b",
        r"\bafter (?:all|everything)\b.*\b(?:i'?ve?|we'?ve?)\b.*\b(?:done|given)\b",
        r"\b(?:you owe|the least you can)\b",
    ],
    "threats": [
        r"\b(?:this is your (?:last|final))\b.*\b(?:chance|opportunity|warning)\b",
        r"\b(?:if (?:i|we) don'?t hear)\b.*\b(?:we'?ll|I'?ll)\b.*\b(?:close|cancel|remove)\b",
    ],
    "condescending": [
        r"\b(?:clearly|obviously|apparently)\b.*\b(?:you don'?t|you can'?t|you won'?t)\b",
        r"\b(?:let me explain|you need to understand)\b.*\b(?:how|why)\b.*\b(?:this works|important)\b",
    ],
}

# --- Fake Pricing Patterns ---

FAKE_PRICING_PATTERNS = {
    "invented_rates": [
        r"\$(?:\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*(?:per|/\s*(?:month|year|day|week))",
        r"\b(?:only|just|merely)\s*\$\d+",
        r"\b(?:premium|rate|cost|price)\s*(?:is|of|will be)\s*\$\d+",
    ],
    "guaranteed_savings": [
        r"\b(?:save|saving|saves?)\s*(?:up to|as much as|around)?\s*\$\d+",
        r"\b(?:guaranteed?|promise[ds]?)\s*(?:to save|savings?)\b",
        r"\b(?:you'?ll|you will)\s*(?:save|get back|receive)\s*\$\d+",
    ],
    "false_discounts": [
        r"\b(?:\d+%?\s*off|discount|reduction)\b.*\b(?:today|now|limited)\b",
        r"\b(?:special|exclusive|promotional)\s*(?:rate|price|offer)\b",
    ],
}

# --- Spam Patterns ---

SPAM_PATTERNS = {
    "robotic_repetition": None,  # Checked via algorithm
    "bulk_messaging": [
        r"\b(?:dear valued|dear customer|dear friend)\b",
        r"\b(?:this is (?:a |)automated|this message is (?:auto|generated))\b",
        r"\b(?:do not reply to this|this is (?:a |)no-reply)\b",
    ],
    "clickbait": [
        r"\b(?:you won'?t believe|shocking|amazing|incredible)\b",
        r"\b(?:click here|tap here|visit now)\b.*\b(?:to (?:claim|get|receive))\b",
    ],
}


class AIQualityLayer:
    """
    Validates AI responses for quality, compliance, and safety.

    Runs 5 checks:
    1. Hallucination detection
    2. TCPA compliance
    3. Aggressive language
    4. Fake pricing
    5. Spam behavior

    Returns quality score and safe response.
    """

    def validate(
        self,
        response: str,
        expected_tone: str = "friendly",
        allowed_pricing: Optional[Dict] = None,
        conversation_history: Optional[List[Dict]] = None,
    ) -> QualityResult:
        """
        Run all quality checks on an AI response.

        Args:
            response: The AI-generated response text
            expected_tone: Expected tone (friendly, professional, etc.)
            allowed_pricing: Allowed pricing values for validation
            conversation_history: Recent messages for repetition check

        Returns:
            QualityResult with score, violations, and safe response
        """
        checks = []

        # 1. Length check
        checks.append(self._check_length(response))

        # 2. Hallucination check
        checks.append(self._check_hallucinations(response))

        # 3. TCPA compliance
        checks.append(self._check_tcpa(response))

        # 4. Aggressive language
        checks.append(self._check_aggressive(response, expected_tone))

        # 5. Fake pricing
        checks.append(self._check_fake_pricing(response, allowed_pricing))

        # 6. Spam behavior
        checks.append(self._check_spam(response, conversation_history))

        # Calculate overall score
        scores = [c.score for c in checks]
        overall_score = sum(scores) // len(scores) if scores else 0
        passed = overall_score >= MIN_QUALITY_SCORE and all(c.passed for c in checks)

        # Generate safe response if needed
        safe_response = response if passed else self._get_safe_fallback(expected_tone)

        return QualityResult(
            checks=checks,
            original_response=response,
            safe_response=safe_response,
            overall_score=overall_score,
            passed=passed,
        )

    def _check_length(self, response: str) -> QualityCheck:
        """Check response length."""
        length = len(response)

        if length < MIN_MESSAGE_LENGTH:
            return QualityCheck(
                check_name="length",
                passed=False,
                score=0,
                violations=["Response too short"],
                details=f"Length {length} < minimum {MIN_MESSAGE_LENGTH}",
            )

        if length > MAX_MESSAGE_LENGTH:
            return QualityCheck(
                check_name="length",
                passed=False,
                score=50,
                violations=["Response too long for SMS"],
                details=f"Length {length} > maximum {MAX_MESSAGE_LENGTH}",
            )

        return QualityCheck(check_name="length", passed=True, score=100)

    def _check_hallucinations(self, response: str) -> QualityCheck:
        """Check for hallucinated content."""
        violations = []

        for category, patterns in HALLUCINATION_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, response, re.IGNORECASE):
                    violations.append(f"Hallucination ({category})")

        score = max(0, 100 - len(violations) * 30)
        return QualityCheck(
            check_name="hallucination",
            passed=len(violations) == 0,
            score=score,
            violations=violations,
        )

    def _check_tcpa(self, response: str) -> QualityCheck:
        """Check for TCPA compliance violations."""
        violations = []

        for category, patterns in TCPA_VIOLATIONS.items():
            for pattern in patterns:
                if re.search(pattern, response, re.IGNORECASE):
                    violations.append(f"TCPA violation ({category})")

        score = max(0, 100 - len(violations) * 25)
        return QualityCheck(
            check_name="tcpa",
            passed=len(violations) == 0,
            score=score,
            violations=violations,
        )

    def _check_aggressive(self, response: str, expected_tone: str) -> QualityCheck:
        """Check for aggressive or manipulative language."""
        violations = []

        for category, patterns in AGGRESSIVE_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, response, re.IGNORECASE):
                    violations.append(f"Aggressive language ({category})")

        # Extra penalty for urgent tone with aggressive language
        if expected_tone == "urgent" and violations:
            violations = [v + " (tone mismatch)" for v in violations]

        score = max(0, 100 - len(violations) * 25)
        return QualityCheck(
            check_name="aggressive",
            passed=len(violations) == 0,
            score=score,
            violations=violations,
        )

    def _check_fake_pricing(
        self,
        response: str,
        allowed_pricing: Optional[Dict] = None,
    ) -> QualityCheck:
        """Check for invented or fake pricing."""
        violations = []

        for category, patterns in FAKE_PRICING_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, response, re.IGNORECASE):
                    violations.append(f"Fake pricing ({category})")

        # If allowed_pricing provided, check extracted amounts
        if allowed_pricing and violations:
            # Extract dollar amounts from response
            amounts = re.findall(r'\$(\d+(?:\.\d{2})?)', response)
            for amount in amounts:
                if float(amount) not in allowed_pricing.get("allowed_amounts", []):
                    violations.append(f"Unapproved amount: ${amount}")

        score = max(0, 100 - len(violations) * 30)
        return QualityCheck(
            check_name="fake_pricing",
            passed=len(violations) == 0,
            score=score,
            violations=violations,
        )

    def _check_spam(
        self,
        response: str,
        conversation_history: Optional[List[Dict]] = None,
    ) -> QualityCheck:
        """Check for spam-like behavior."""
        violations = []

        # Pattern-based spam detection
        for category, patterns in SPAM_PATTERNS.items():
            if patterns is None:
                continue
            for pattern in patterns:
                if re.search(pattern, response, re.IGNORECASE):
                    violations.append(f"Spam ({category})")

        # Repetition check against history
        if conversation_history:
            recent_ai = [
                m.get("content", "")
                for m in conversation_history[-5:]
                if m.get("sender") == "ai"
            ]
            for prev in recent_ai:
                similarity = self._calculate_similarity(response, prev)
                if similarity > 0.8:
                    violations.append("Spam (repetitive content)")
                    break

        score = max(0, 100 - len(violations) * 25)
        return QualityCheck(
            check_name="spam",
            passed=len(violations) == 0,
            score=score,
            violations=violations,
        )

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """Calculate simple word overlap similarity."""
        if not text1 or not text2:
            return 0.0

        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())

        if not words1 or not words2:
            return 0.0

        intersection = words1 & words2
        union = words1 | words2

        return len(intersection) / len(union) if union else 0.0

    def _get_safe_fallback(self, tone: str = "friendly") -> str:
        """Get a safe fallback response when quality check fails."""
        import random

        fallbacks = {
            "friendly": [
                "Thanks for your interest! Let me connect you with one of our specialists who can help.",
                "Great question! Our team would be happy to help you with that. When works for a quick call?",
            ],
            "professional": [
                "Thank you for your inquiry. I'd be happy to arrange a consultation with our team.",
                "I appreciate your interest. Let me connect you with a specialist who can provide detailed information.",
            ],
            "casual": [
                "Thanks for reaching out! Want to chat with one of our team members? They're great!",
                "Hey! Good question. Our team can help with that — when's a good time to talk?",
            ],
            "urgent": [
                "Thank you for your interest. Our team is available to help right away.",
                "I understand. Let me connect you with someone who can assist immediately.",
            ],
        }

        tone_fallbacks = fallbacks.get(tone, fallbacks["friendly"])
        return random.choice(tone_fallbacks)
