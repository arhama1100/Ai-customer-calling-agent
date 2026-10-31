"""
app/assistant.py
────────────────
Routes a customer message to the right APPROVED data source. This is the
brain of the text/voice chat, and it is intentionally deterministic.

Routing rules (checked in order):
1. Contains an order id (e.g. ORD-1002)  -> answer from SQLite (orders.py)
2. OPTIONAL Gemini branch (only when GEMINI_ENABLED + a key are set): a plain
   farewell is first answered deterministically (so "bye" behaves the SAME on
   and off - Gemini has no `farewell` intent), then we classify the message's
   intent + language, route order questions by asking for an id, and answer a
   policy question from the SAMPLE demonstration FAQ (rephrased into the
   customer's language). Any failure here falls through to the deterministic
   rules below (and never pretends Gemini answered).
3. Order-status wording in Urdu / Roman Urdu with NO id -> ask for the order id
4. Matches the FAQ -> return that topic's SAMPLE policy answer (source marks it
   as demonstration data, e.g. faq_kb:<topic>:sample). An ambiguous policy
   request with no single clear topic -> ask which one; a request naming a
   specific policy we have no topic for (e.g. a warranty) -> honest fallback.
   NEVER invent a policy.
5. A plain greeting or farewell        -> fixed greeting / farewell reply
6. Nothing matched                       -> honest fallback (never invents)

Hard rules: this module NEVER fabricates an order status or a policy. Order
facts come ONLY from SQLite. Policy answers come ONLY from data/faq.json, which
holds FICTIONAL SAMPLE policies (demonstration data, not an approved real shop
policy). The optional Gemini layer may only CLASSIFY intent and rephrase our
OWN approved sample text - it can never invent facts.
"""

import re
import unicodedata

from . import faq as faq_module
from . import gemini as gemini_module
from .orders import describe_order

# Order ids are stored canonically as ORD-1002, but speech-to-text often drops
# the hyphen or prefixes the word "order" ("ord 1002", "order ord 1002",
# "ord1002"). We match ORD + an OPTIONAL separator (hyphen / space / none) +
# the 4 digits, case-insensitively, then normalize to the canonical ORD-####.
# \b before ORD prevents accidental mid-word matches (e.g. "word", "record").
ORDER_ID_RE = re.compile(r"\bORD[-\s]?(\d{4})\b", re.IGNORECASE)

# ── Order-status wording in Urdu / Roman Urdu (when NO order id is present) ──
# Such customers clearly want an order status but forgot the number, so we ask
# for it. We NEVER guess an order or invent a status (hard rule in the header).
ORDER_ID_REQUEST_REPLY = (
    "I can check your order status — please share your order number "
    "(for example ORD-1002) and I'll look it up for you."
)

# ── Multilingual routing vocabulary (Phase E) ──────────────────────────────
# A message is treated as an ORDER-STATUS request only when it references the
# customer's OWN order/parcel (ORDER_REF_TOKENS) AND carries a status/arrival
# cue (ORDER_STATUS_CUES) AND is not actually a POLICY request (see
# POLICY_ACTION_TOKENS). Order ids are matched earlier and hit SQLite.
ORDER_REF_TOKENS = {
    "order", "orders", "parcel", "package", "shipment", "consignment",
    "آرڈر", "پارسل", "پیکج", "پیکیج", "شپمنٹ",
}

# Status / arrival cues across English, Roman Urdu and Urdu script. Bare verbs
# like "deliver / shipped / received" describe THIS order here, so "my parcel
# was not delivered" is an order-status question, while "how long does delivery
# take" (no order reference) stays on the shipping-policy path.
ORDER_STATUS_CUES = {
    # English
    "where", "status", "track", "tracking", "arrived", "arrive", "reached",
    "reach", "here", "delivered", "deliver", "delivery", "shipped", "ship",
    "dispatched", "dispatch", "received", "receive", "coming", "pending",
    # Roman Urdu
    "kahan", "kahaan", "kab", "pohncha", "pohnche", "pohancha", "pohanche",
    "pohnch", "aya", "aaya", "aayi", "hua", "hui", "hogia", "hogaya", "bata",
    "batao", "mila", "milega", "mili",
    # Urdu script
    "کہاں", "کب", "اسٹیٹس", "ٹریکنگ", "پہنچ", "پہنچا", "پہنچے", "آیا",
    "آئی", "ہوا", "ہوئی", "بھیجا", "بھیج", "دیا", "گیا", "ملا", "ملی", "ملے",
}

# Words that mean the customer wants to CANCEL / RETURN / CHANGE / PAY, i.e. a
# POLICY question. If any appear we route to the policy branch, never to "your
# order status", even when the message also mentions an order. Kept here purely
# for ROUTING - faq.json content is never modified.
POLICY_ACTION_TOKENS = {
    "cancel", "cancelled", "cancellation", "return", "returns", "refunded",
    "refund", "refundable", "exchange", "change", "changed", "changes",
    "modify", "address", "payment", "pay", "paid", "card", "invoice",
    "منسوخ", "رد", "واپس", "واپسی", "تبدیل", "بدل", "ادائیگی", "بل",
    "wapas", "wapsi", "tabdeel", "badalna", "ada", "adaigi", "paisa",
    "karwana", "karwa",
}

# Urdu-script / Roman-Urdu policy cues that the English-only faq.json keywords
# cannot match. Maps a policy topic id to cue words; used to decide WHICH sample
# policy topic to answer (the answer text itself is fetched from faq.json by id).
POLICY_TOPIC_CUES = {
    "cancellation": {"cancel", "cancellation", "منسوخ", "رد", "karwana", "karwa"},
    "returns": {"return", "returns", "refund", "exchange", "واپس", "واپسی",
                "wapas", "wapsi"},
    "changes": {"change", "modify", "address", "edit", "تبدیل", "بدل",
                "tabdeel", "badalna"},
    "payment": {"payment", "pay", "card", "credit", "ada", "adaigi", "paisa",
                "ادائیگی", "بل", "invoice"},
}

# Generic policy words with no specific topic. A message that merely says
# "policy/policies" (and matches no single topic) is AMBIGUOUS, so we ask which
# one rather than guessing a sample answer. Never invents anything.
POLICY_GENERIC_TOKENS = {"policy", "policies", "rules", "نियم", "قواعد"}

# Specific policy SUBJECTS that are NOT among our five sample FAQ topics (a
# product warranty / guarantee). Naming one is an unsupported POLICY question,
# so the assistant gives its honest fallback rather than the vague "tell me
# more" clarification (which stays reserved for a bare "policy" ask with no
# specific unmapped subject). Extensible if the shop later adds such a policy.
UNSUPPORTED_POLICY_TOPICS = {"warranty", "guarantee"}

# Greetings are matched as WHOLE short words/phrases (never a substring), so
# "nahi" (which contains 'hi') is NOT a greeting.
GREETING_TOKENS = {
    "hi", "hello", "hey", "yo", "howdy", "hola", "namaste", "aloha",
    "greetings", "assalam", "salaam", "salam", "shalom",
}
GREETING_PHRASES = {
    "good morning", "good afternoon", "good evening", "how are you",
    "how are you doing", "how r u", "kaise ho", "kaise ho aap",
    "assalam o alaikum", "salam alaikum", "hello there", "hi there",
}

# Farewells are matched the same WHOLE-word / whole-phrase way as greetings and
# share the same short-message guard, so 'bye' never fires inside an unrelated
# sentence ("I want to say goodbye to my friend" is NOT a farewell) and a bare
# "thank you" (no bye/goodbye) is NOT treated as leaving.
FAREWELL_TOKENS = {"bye", "goodbye"}
FAREWELL_PHRASES = {
    "see you", "see you later", "allah hafiz", "khuda hafiz",
}

GREETING_REPLY = (
    "Hello! I can help with order status, shipping, returns, cancellations, "
    "and payments. What would you like to know?"
)

FAREWELL_REPLY = (
    "Thank you for contacting us! Have a great day. Goodbye!"
)

FALLBACK_REPLY = (
    "I'm not sure how to help with that yet. I can check an order status "
    "(try 'ORD-1002') or answer questions about shipping, returns, "
    "cancellations, and payments."
)

# ── Gemini-enabled routing (Phase B2 / G2) ────────────────────────────────
# Intents that map to a policy topic in data/faq.json. The FAQ now holds
# activated SAMPLE demonstration policies, so a policy intent returns that
# topic's sample answer (grounded in the file, never invented by the model).
POLICY_INTENTS = {"shipping", "returns", "cancellation", "payment", "changes"}

CLARIFICATION_REPLY = (
    "Could you tell me a little more about what you need help with - for "
    "example an order status, shipping, returns, cancellation, or payment?"
)


def _looks_like_greeting(text):
    """Whole-word / whole-phrase greeting test (token-based, never a substring),
    so 'hi' inside 'nahi' cannot trigger it. A length guard keeps a long
    sentence that merely contains a greeting word from counting as a greeting."""
    tokens = _match_tokens(text)
    if not tokens or len(tokens) > 4:
        return False
    if " ".join(tokens) in GREETING_PHRASES:
        return True
    return any(tok in GREETING_TOKENS for tok in tokens)


def _looks_like_farewell(text):
    """Whole-word / whole-phrase farewell test (token-based, never a substring).

    Shares the greeting length guard (<= 4 tokens), so only a short closing
    remark counts: 'bye' / 'goodbye' / 'okay bye' / 'thank you bye' /
    'see you later' / 'allah hafiz' / 'khuda hafiz'. A bare 'thank you' (no
    bye/goodbye) and a long sentence merely mentioning 'goodbye' do NOT match.
    """
    tokens = _match_tokens(text)
    if not tokens or len(tokens) > 4:
        return False
    if " ".join(tokens) in FAREWELL_PHRASES:
        return True
    return any(tok in FAREWELL_TOKENS for tok in tokens)


def _normalize_for_matching(text):
    """Return a normalized COPY used only for intent matching.

    Applies Unicode NFKC, swaps Urdu/Arabic punctuation for spaces (so words
    split cleanly), collapses whitespace, and lowercases for Roman Urdu.
    The caller keeps the ORIGINAL message untouched.
    """
    t = unicodedata.normalize("NFKC", text)
    for ch in "،؛؟۔":  #، ؛ ؟ ۔
        t = t.replace(ch, " ")
    return re.sub(r"\s+", " ", t).strip().lower()


def _match_tokens(text):
    """Split a normalized message into clean, punctuation-stripped tokens.

    Used by the token-based greeting / order-status / policy detectors so a
    trailing '?' or '.' never hides a keyword (e.g. 'order?' -> 'order').
    """
    stripped = ".,!?\"'():;\u060c\u061b\u061f\u06d4"
    toks = []
    for tok in _normalize_for_matching(text).split():
        tok = tok.strip(stripped)
        if tok:
            toks.append(tok)
    return toks


def _is_order_status_no_id(tokens):
    """True when the words ask about the customer's OWN order/parcel status
    (English, Roman Urdu or Urdu script) without giving an order id. An id
    lookup is handled earlier, so this is the no-id case: ask for the id. A
    POLICY request (cancel/return/refund/... even mentioning an order) is NOT
    order status - it is routed to the policy branch instead."""
    token_set = set(tokens)
    if POLICY_ACTION_TOKENS & token_set:
        return False
    has_ref = bool(ORDER_REF_TOKENS & token_set)
    has_cue = bool(ORDER_STATUS_CUES & token_set)
    return has_ref and has_cue


def _detect_policy_topic(text, tokens, faq_path):
    """Pick a policy topic id (shipping/returns/cancellation/changes/payment) or
    None. English questions are matched by the SAMPLE FAQ keywords; Urdu
    script and Roman Urdu use POLICY_TOPIC_CUES because faq.json is English-only.
    This ONLY decides which SAMPLE topic to answer; the answer text is fetched
    from faq.json by id in respond() (never invented here).
    """
    token_set = set(tokens)
    if not (POLICY_ACTION_TOKENS & token_set):
        hit = faq_module.find_answer(text, faq_path)
        return hit["id"] if hit else None
    best_topic, best_score = None, 0
    for topic, cues in POLICY_TOPIC_CUES.items():
        score = len(token_set & cues)
        if score > best_score:
            best_topic, best_score = topic, score
    if best_topic:
        return best_topic
    hit = faq_module.find_answer(text, faq_path)
    if hit:
        return hit["id"]
    if "address" in token_set:
        return "changes"
    return None


def _localized(reply_text, language):
    """Return our OWN approved fixed text, rephrased into the customer's
    language when possible. Falls back to the original English when translation
    is disabled, unsupported, or fails - it never invents new content.
    """
    if not language or language in ("en", "other"):
        return reply_text
    translated = gemini_module.rephrase_to_language(reply_text, language)
    return translated or reply_text


def _respond_via_gemini(text):
    """Use Gemini to classify intent + language, then return a GROUNDED result.

    Returns None on any failure (disabled/timeout/None classification) so the
    caller falls back to the deterministic assistant. Order ids were already
    handled by the reliable regex + SQLite lookup above, so an `order_status`
    intent here means NO id was present. Policies are answered from the SAMPLE
    FAQ (grounded in data/faq.json), never invented by the model.
    """
    classification = gemini_module.classify_message(text)
    if not classification:
        return None

    intent = classification.get("intent")
    language = classification.get("language")

    if intent == "order_status":
        return {
            "intent": "order_status_missing_id",
            "reply": _localized(ORDER_ID_REQUEST_REPLY, language),
            "source": "gemini:fixed",
        }
    if intent in POLICY_INTENTS:
        # Grounded in the SAMPLE FAQ: return that topic's sample answer, our own
        # text rephrased into the customer's language. If the topic has no stored
        # answer, let the deterministic branch decide (never invent one here).
        sample = faq_module.get_answer_by_id(intent)
        if not sample:
            return None
        return {
            "intent": intent,
            "reply": _localized(sample, language),
            "source": "gemini:policy_sample",
        }
    if intent == "greeting":
        return {
            "intent": "greeting",
            "reply": _localized(GREETING_REPLY, language),
            "source": "gemini:fixed",
        }
    if intent == "ambiguous":
        return {
            "intent": "ambiguous",
            "reply": _localized(CLARIFICATION_REPLY, language),
            "source": "gemini:fixed",
        }
    if intent == "other":
        return {
            "intent": "fallback",
            "reply": _localized(FALLBACK_REPLY, language),
            "source": "gemini:fixed",
        }
    # Unexpected value -> let the deterministic assistant decide.
    return None


def respond(text, db_path=None, faq_path=None):
    """Decide intent and produce an answer from approved sources.

    Returns a dict:
        {"intent": ..., "reply": ..., "source": ...}
    source marks WHERE the answer came from, which keeps the data lineage
    auditable (and is handy for the optional Gemini layer later).
    """
    text = (text or "").strip()
    if not text:
        return {"intent": "empty", "reply": FALLBACK_REPLY, "source": "none"}

    # 1) Order status — highest priority when an order id is present.
    match = ORDER_ID_RE.search(text)
    if match:
        # Normalize any accepted variation to the canonical ORD-#### form
        # that the SQLite table actually stores (the lookup stays unchanged).
        order_id = f"ORD-{match.group(1)}"
        return {
            "intent": "order_status",
            "reply": describe_order(order_id, db_path),
            "source": "orders_db",
        }

    # 2) OPTIONAL Gemini branch - understand free-form / multilingual phrasing.
    #    Only runs when the feature is enabled and a key exists; it may only
    #    classify intent + language and rephrase our OWN fixed messages. Order
    #    facts already came from SQLite above, and unapproved FAQ placeholders
    #    are never quoted (see _respond_via_gemini). On ANY failure this returns
    #    None and we fall through to the deterministic assistant below, without
    #    ever pretending Gemini answered.
    if gemini_module.is_enabled():
        # Farewell is resolved DETERMINISTICALLY before we consult the model,
        # so a goodbye behaves IDENTICALLY whether Gemini is enabled or disabled
        # (rule 5b below). Gemini has no `farewell` intent: left to the model,
        # "bye" is classified `other` and answered with the generic fallback.
        # An order id was already handled above by rule 1, and the whole-word +
        # short-message guards in _looks_like_farewell keep false positives
        # (a bare "thank you", a long sentence merely mentioning "goodbye") out.
        if _looks_like_farewell(text):
            return {"intent": "farewell", "reply": FAREWELL_REPLY, "source": "fixed"}
        gemini_result = _respond_via_gemini(text)
        if gemini_result is not None:
            return gemini_result

    # 3) Order-status wording (English / Urdu / Roman Urdu) but NO order id ->
    #    ask for the order id. Never guess an order or invent a status.
    tokens = _match_tokens(text)
    if _is_order_status_no_id(tokens):
        return {
            "intent": "order_status_missing_id",
            "reply": ORDER_ID_REQUEST_REPLY,
            "source": "none",
        }

    # 4) Policy question (any language). data/faq.json now holds activated
    #    SAMPLE demonstration policies, so we answer with that topic's sample
    #    text (never an invented policy). The `source` marks it as sample data
    #    (faq_kb:<topic>:sample) for auditable lineage. A policy request that
    #    cannot be pinned to a single confident topic asks which one.
    topic = _detect_policy_topic(text, tokens, faq_path)
    if topic:
        sample = faq_module.get_answer_by_id(topic, faq_path)
        if sample:
            return {
                "intent": "faq",
                "reply": sample,
                "source": f"faq_kb:{topic}:sample",
            }
    if (POLICY_ACTION_TOKENS | POLICY_GENERIC_TOKENS) & set(tokens):
        # Clearly a policy request, but no single confident topic matched.
        # A concrete policy subject we have NO FAQ topic for (e.g. "warranty")
        # is an UNSUPPORTED question -> fall through to the honest fallback
        # below, do NOT ask "tell me more". Only a vague policy ask with no
        # specific unmapped subject (e.g. just "policy") gets the clarification.
        if not (UNSUPPORTED_POLICY_TOPICS & set(tokens)):
            return {
                "intent": "ambiguous",
                "reply": CLARIFICATION_REPLY,
                "source": "none",
            }

    # 5) Greeting (whole-word; 'nahi' is never a greeting).
    if _looks_like_greeting(text):
        return {"intent": "greeting", "reply": GREETING_REPLY, "source": "fixed"}

    # 5b) Farewell (whole-word; a bare 'thank you' or a long sentence that
    #     merely mentions 'goodbye' is NOT treated as leaving).
    if _looks_like_farewell(text):
        return {"intent": "farewell", "reply": FAREWELL_REPLY, "source": "fixed"}

    # 6) Honest fallback.
    return {"intent": "fallback", "reply": FALLBACK_REPLY, "source": "none"}
