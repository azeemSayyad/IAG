"""
NLP Pipeline (Step 5.1)

Message processing pipeline:
1. Cleaning — strip whitespace, normalize text, handle emojis
2. Preprocessing — tokenize, lowercase, remove noise
3. Feature extraction — keywords, patterns, signals
"""

import re
import string
from typing import Dict, List, Tuple, Optional


# Common SMS abbreviations and their expansions
SMS_EXPANSIONS = {
    "u": "you",
    "r": "are",
    "ur": "your",
    "y": "why",
    "k": "ok",
    "ok": "okay",
    "pls": "please",
    "plz": "please",
    "thx": "thanks",
    "tx": "thanks",
    "ty": "thank you",
    "np": "no problem",
    "nvm": "nevermind",
    "idk": "i don't know",
    "imo": "in my opinion",
    "tbh": "to be honest",
    "rn": "right now",
    "lmao": "",
    "lol": "",
    "rofl": "",
    "omg": "",
    "wya": "where are you",
    "wyd": "what are you doing",
    "hmu": "hit me up",
    "smh": "",
    "fyi": "for your information",
    "asap": "as soon as possible",
    "btw": "by the way",
    "dm": "direct message",
    "imo": "in my opinion",
    "im": "i'm",
    "ive": "i've",
    "dont": "don't",
    "cant": "can't",
    "wont": "won't",
    "didnt": "didn't",
    "isnt": "isn't",
    "arent": "aren't",
    "wasnt": "wasn't",
    "werent": "weren't",
    "hasnt": "hasn't",
    "havent": "haven't",
    "hadnt": "hadn't",
    "wouldnt": "wouldn't",
    "shouldnt": "shouldn't",
    "couldnt": "couldn't",
}


def clean_text(text: str) -> str:
    """
    Clean and normalize text message.

    Steps:
    1. Strip whitespace
    2. Normalize unicode
    3. Remove excessive punctuation
    4. Normalize whitespace
    """
    if not text:
        return ""

    # Strip
    text = text.strip()

    # Remove zero-width characters
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)

    # Normalize repeated characters (e.g., "nooooo" → "noo")
    text = re.sub(r"(.)\1{3,}", r"\1\1", text)

    # Normalize repeated punctuation (e.g., "!!!" → "!")
    text = re.sub(r"([!?.]){3,}", r"\1", text)

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)

    return text


def expand_sms_slang(text: str) -> str:
    """Expand common SMS abbreviations."""
    words = text.split()
    expanded = []
    for word in words:
        lower = word.lower().strip(string.punctuation)
        if lower in SMS_EXPANSIONS:
            expansion = SMS_EXPANSIONS[lower]
            if expansion:
                expanded.append(expansion)
        else:
            expanded.append(word)
    return " ".join(expanded)


def extract_features(text: str) -> Dict[str, any]:
    """
    Extract features from text for intent classification.

    Returns:
        Dict with extracted features:
        - keywords: List of important words
        - has_question: Whether text contains a question
        - has_negation: Whether text contains negation
        - sentiment_words: Positive/negative word counts
        - word_count: Number of words
        - char_count: Number of characters
    """
    lower = text.lower()
    words = lower.split()

    # Question detection
    has_question = "?" in text or any(
        lower.startswith(q) for q in ["what", "when", "where", "who", "why", "how", "can", "do", "is", "are", "will", "would", "could", "should"]
    )

    # Negation detection
    negation_words = ["no", "not", "never", "don't", "dont", "can't", "cant", "won't", "wont", "isn't", "isnt", "aren't", "arent", "nothing", "nowhere", "nobody"]
    has_negation = any(word in negation_words for word in words)

    # Positive words
    positive_words = ["yes", "yeah", "yep", "sure", "absolutely", "definitely", "interested", "love", "great", "perfect", "awesome", "good", "please", "thanks", "thank"]
    positive_count = sum(1 for word in words if word in positive_words)

    # Negative words
    negative_words = ["no", "nah", "nope", "never", "stop", "unsubscribe", "remove", "hate", "terrible", "awful", "bad", "worst", "annoying", "spam"]
    negative_count = sum(1 for word in words if word in negative_words)

    # Stop/opt-out detection
    stop_words = ["stop", "unsubscribe", "remove", "opt out", "opt-out", "cancel", "end", "quit"]
    has_stop = any(phrase in lower for phrase in stop_words)

    # Booking signals
    booking_words = ["book", "schedule", "appointment", "meeting", "call", "available", "when", "time", "slot"]
    booking_count = sum(1 for word in words if word in booking_words)

    # Price/cost signals
    price_words = ["price", "cost", "expensive", "cheap", "afford", "budget", "rate", "fee", "how much", "pricing"]
    has_price_question = any(phrase in lower for phrase in price_words)

    # Trust signals
    trust_words = ["legit", "real", "scam", "trust", "reliable", "honest", "genuine", "fake"]
    has_trust_concern = any(word in words for word in trust_words)

    return {
        "keywords": words,
        "has_question": has_question,
        "has_negation": has_negation,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "has_stop": has_stop,
        "booking_count": booking_count,
        "has_price_question": has_price_question,
        "has_trust_concern": has_trust_concern,
        "word_count": len(words),
        "char_count": len(text),
    }


def preprocess(text: str) -> Tuple[str, Dict[str, any]]:
    """
    Full preprocessing pipeline.

    Returns:
        Tuple of (cleaned_text, features)
    """
    # Clean
    cleaned = clean_text(text)

    # Expand slang
    expanded = expand_sms_slang(cleaned)

    # Extract features
    features = extract_features(expanded)

    return expanded, features
