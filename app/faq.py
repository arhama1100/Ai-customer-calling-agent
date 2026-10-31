"""
app/faq.py
──────────
Knowledge-base lookup over the APPROVED FAQ file (data/faq.json).

Design rules:
- faq.json is the ONLY source of truth for policy answers. Nothing here
  invents an answer; it only selects an approved topic (or says none matched).
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
