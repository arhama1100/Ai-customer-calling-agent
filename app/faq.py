"""
app/faq.py
──────────
Knowledge-base lookup over the SAMPLE FAQ file (data/faq.json).

Design rules:
- faq.json is the ONLY source of truth for policy answers. Nothing here
  invents an answer; it only selects a sample topic (or says none matched).
  The answers are FICTIONAL demonstration policies (see _meta in faq.json);
  a real business must replace them with its own approved policies.
- Matching is simple keyword scoring so behavior is deterministic and testable.
"""

import json

from .config import BASE_DIR

FAQ_PATH = BASE_DIR / "data" / "faq.json"

# Loaded once and reused. A missing file is a hard error at call time so we
# never silently return empty answers.
_cache = None


def load_topics(faq_path=None):
    """Read and cache the FAQ topics from faq.json."""
    global _cache
    if _cache is not None and faq_path is None:
        return _cache

    path = faq_path or FAQ_PATH
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    topics = data.get("topics", [])
    if faq_path is None:
        _cache = topics
    return topics


def find_answer(text, faq_path=None):
    """Return the best-matching approved answer for the user's text.

    Scoring: count how many of a topic's keywords appear in the text
    (case-insensitive, whole-word-ish via simple containment). The topic with
    the highest score wins. If NOTHING matches, return None so the caller can
    honestly say it doesn't know (never guess a policy).

    Returns a dict: {"id", "title", "answer", "score"} or None.
    """
    if not text or not text.strip():
        return None

    lowered = text.lower()
    best = None
    best_score = 0

    for topic in load_topics(faq_path):
        score = 0
        for kw in topic.get("keywords", []):
            if kw.lower() in lowered:
                score += 1
        if score > best_score:
            best_score = score
            best = topic

    if best is None:
        return None
    return {
        "id": best["id"],
        "title": best["title"],
        "answer": best["answer"],
        "score": best_score,
    }


def get_answer_by_id(topic_id, faq_path=None):
    """Return the SAMPLE answer text for a known topic id, or None.

    Used by the router to fetch a detected topic's answer verbatim from
    data/faq.json by id. The file stays the single source of truth: we never
    invent a policy here, and an unknown/empty id honestly returns None so the
    caller can fall through (clarify or fallback) rather than guess.
    """
    if not topic_id:
        return None
    for topic in load_topics(faq_path):
        if topic.get("id") == topic_id:
            return topic.get("answer")
    return None
