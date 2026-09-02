"""
Unit tests for AI Quality Layer (Phase 36.8)
"""

import pytest
from app.ai.conversation_engine.quality import AIQualityLayer, QualityResult


@pytest.fixture
def quality():
    return AIQualityLayer()


class TestAIQualityLayer:
    """Tests for AI quality validation."""

    def test_valid_response_passes(self, quality):
        """Valid response should pass quality check."""
        result = quality.validate("Thanks for reaching out! When would be a good time for a quick call?")
        assert result.passed == True
        assert result.overall_score >= 70

    def test_short_response_fails(self, quality):
        """Too short response should fail."""
        result = quality.validate("ok")
        assert result.passed == False
        assert any("too short" in v.lower() for v in result.violations)

    def test_hallucination_detected(self, quality):
        """False authority claims should be detected."""
        result = quality.validate("According to our records, your premium will be $50 per month.")
        assert any("hallucination" in v.lower() for v in result.violations)

    def test_aggressive_language_detected(self, quality):
        """Aggressive language should be detected."""
        result = quality.validate("You must act now or you will miss out on this amazing deal!")
        assert len(result.violations) > 0

    def test_fake_pricing_detected(self, quality):
        """Fake pricing should be detected."""
        result = quality.validate("Our rate is only $29.99 per month, guaranteed to save you $500!")
        assert any("pricing" in v.lower() for v in result.violations)

    def test_spam_detected(self, quality):
        """Spam patterns should be detected."""
        result = quality.validate("Dear valued customer, this is an automated message. Do not reply to this.")
        assert any("spam" in v.lower() for v in result.violations)

    def test_repetitive_content_detected(self, quality):
        """Repetitive content should be detected."""
        history = [
            {"sender": "ai", "content": "Thanks for reaching out! When works for a call?"},
        ]
        result = quality.validate(
            "Thanks for reaching out! When works for a call?",
            conversation_history=history,
        )
        assert any("repetitive" in v.lower() for v in result.violations)

    def test_safe_fallback_exists(self, quality):
        """Safe fallback should return valid response."""
        fallback = quality._get_safe_fallback("friendly")
        assert len(fallback) > 20

    def test_result_serialization(self, quality):
        """Result should serialize to dict."""
        result = quality.validate("Hello! I would love to help you find the right insurance coverage.")
        d = result.to_dict()
        assert "overall_score" in d
        assert "checks" in d
        assert "violations" in d
        assert "was_modified" in d

    def test_different_tones(self, quality):
        """Should handle different tones."""
        for tone in ["friendly", "professional", "casual", "urgent"]:
            fallback = quality._get_safe_fallback(tone)
            assert len(fallback) > 10
