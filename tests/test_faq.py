"""
tests/test_faq.py
─────────────────
Verifies the FAQ lookup returns ONLY approved answers from data/faq.json,
and honestly returns None when nothing matches (never guesses a policy).

Run with:  .\\.venv\\Scripts\\python.exe -m pytest
"""

from app.faq import find_answer, load_topics


def test_faq_file_has_topics():
    topics = load_topics()
    assert len(topics) >= 1
    # Every topic must have the required fields to be usable downstream.
    for t in topics:
        assert t["id"] and t["title"] and t["answer"]
        assert isinstance(t["keywords"], list) and t["keywords"]


def test_faq_matches_shipping():
    hit = find_answer("How long does shipping usually take?")
    assert hit is not None
    assert hit["id"] == "shipping"
    # The wording must come verbatim from the approved file (determinism).
    shipping = next(t for t in load_topics() if t["id"] == "shipping")
    assert hit["answer"] == shipping["answer"]


def test_faq_no_match_returns_none():
    # A sentence with no FAQ keywords must NOT match anything.
    assert find_answer("what is the weather on mars today") is None


def test_faq_empty_input_returns_none():
    assert find_answer("") is None
    assert find_answer("   ") is None


def test_faq_picks_highest_scoring_topic():
    # This sentence touches BOTH cancellation (2 keywords) and changes
    # (1 keyword); the topic with the higher keyword score must win.
    hit = find_answer("I want to cancel my order and change the address")
    assert hit is not None
    assert hit["id"] == "cancellation"
    assert hit["score"] >= 2


def test_faq_is_case_insensitive():
    hit = find_answer("HOW LONG DOES SHIPPING TAKE??")
    assert hit is not None
    assert hit["id"] == "shipping"
