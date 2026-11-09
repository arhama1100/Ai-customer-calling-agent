"""
app/assistant.py
────────────────
Routes a customer message to the right APPROVED data source. This is the
brain of the text/voice chat, and it is intentionally deterministic.

Routing rules (checked in order):
1a. Explicit request to speak with a HUMAN (English / Roman Urdu / Urdu script)
    -> honest handoff message, resolved BEFORE every other rule including the
    optional model. A mixed request ("...about order ORD-1001") still goes to
    the handoff so the human ask is never silently dropped; the handoff reply
    itself invites the customer to ask Nova the concrete question directly.
1. Contains an order id (e.g. ORD-1002)  -> answer from PostgreSQL (orders.py)
1b. Business-hours question (English / Roman Urdu / Urdu script) -> the SAMPLE
    schedule from data/faq.json, resolved BEFORE the optional model so the
    answer is identical with Gemini on or off, and the model is never asked
    and can never invent opening times.
2. OPTIONAL Gemini branch (only when GEMINI_ENABLED + a key are set): a plain
   farewell is first answered deterministically (so "bye" behaves the SAME on
   and off - Gemini has no `farewell` intent), then we classify the message's
   intent + language, route order questions by asking for an id, and answer a
   policy question from the SAMPLE demonstration FAQ (rephrased into the
   customer's language). A verdict the model is NOT sure about ("other",
   "ambiguous", or needs_clarification) no longer decides anything: the
   deterministic rules below run first, and the model's hesitation is only used
   if they fail too (so a shaky classification can neither invent an answer nor
   discard a correct rule match). Any failure here falls through to the rules
   below (and never pretends Gemini answered).
3. Order-status wording in Urdu / Roman Urdu with NO id -> ask for the order id
4. Matches the FAQ -> return that topic's SAMPLE policy answer (source marks it
   as demonstration data, e.g. faq_kb:<topic>:sample). An ambiguous policy
   request with no single clear topic -> ask which one; a request naming a
   specific policy we have no topic for (e.g. a warranty) -> honest fallback.
   NEVER invent a policy.
5. A plain greeting or farewell        -> fixed greeting / farewell reply
6. Nothing matched                       -> honest fallback (never invents)

Hard rules: this module NEVER fabricates an order status or a policy. Order
facts come ONLY from PostgreSQL. Policy answers come ONLY from data/faq.json, which
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
    "from your confirmation (the digits that follow ORD) and I'll look it up."
)

# When the customer clearly gave an order number but it did NOT resolve to a
# confident, complete id (e.g. a partial "one two three", or digits split by a
# mishearing) we ask them to REPEAT it rather than guess or pad a number. This
# keeps the hard rule intact: an order id is only ever used when we actually
# heard it, never invented.
ORDER_ID_REPEAT_REPLY = (
    "Sorry, I didn't catch your full order number. Could you please repeat "
    "it? It's the four digits that follow ORD, said one at a time."
)

# ── Multilingual routing vocabulary (Phase E) ──────────────────────────────
# A message is treated as an ORDER-STATUS request only when it references the
# customer's OWN order/parcel (ORDER_REF_TOKENS) AND carries a status/arrival
# cue (ORDER_STATUS_CUES) AND is not actually a POLICY request (see
# POLICY_ACTION_TOKENS). Order ids are matched earlier and hit PostgreSQL.
ORDER_REF_TOKENS = {
    "order", "orders", "parcel", "package", "shipment", "consignment",
    "tracking",
    "آرڈر", "پارسل", "پیکج", "پیکیج", "شپمنٹ",
}

# Status / arrival cues across English, Roman Urdu and Urdu script. Bare verbs
# like "deliver / shipped / received" describe THIS order here, so "my parcel
# was not delivered" is an order-status question, while "how long does delivery
# take" (no order reference) stays on the shipping-policy path.
#
# A cue alone is NEVER enough: _is_order_status_no_id requires an order/parcel
# REFERENCE plus a cue, so everyday words like "when" or "get" cannot turn an
# unrelated sentence into an order question ("when do you open", "do you accept
# apple pay" have no order reference and stay on their own paths).
ORDER_STATUS_CUES = {
    # English
    "where", "status", "track", "tracking", "arrived", "arrive", "reached",
    "reach", "here", "delivered", "deliver", "delivery", "shipped", "ship",
    "dispatched", "dispatch", "received", "receive", "coming", "pending",
    # Natural rephrasings that carry no "status/where/track" wording at all
    # ("when will I get my order", "any update on my shipment", "can you tell
    # me about my order") - previously these fell through to the fallback.
    "update", "updates", "when", "get", "got", "tell", "came", "come",
    "yet", "still", "sent", "moving", "progress",
    # Roman Urdu
    "kahan", "kahaan", "kab", "pohncha", "pohnche", "pohancha", "pohanche",
    "pohnch", "aya", "aaya", "aayi", "hua", "hui", "hogia", "hogaya", "bata",
    "batao", "mila", "milega", "mili", "kab", "khaka", "dikhao", "check",
    # "aa gaya" / "ho gaya" ("has it come") - the natural spoken form, which
    # carries none of the words above.
    "gaya", "gayi", "aayega", "aegi", "ayega",
    # Urdu script
    "کہاں", "کب", "اسٹیٹس", "ٹریکنگ", "پہنچ", "پہنچا", "پہنچے", "آیا",
    "آئی", "ہوا", "ہوئی", "بھیجا", "بھیج", "دیا", "گیا", "ملا", "ملی", "ملے",
    "باتائیں", "بتائیں", "دکھائیں", "اپڈیٹ", "کیا", "کچھ", "حالت", "صورتحال",
}

# Words that mean the customer wants to CANCEL / RETURN / CHANGE / PAY, i.e. a
# POLICY question. If any appear we route to the policy branch, never to "your
# order status", even when the message also mentions an order. Kept here purely
# for ROUTING - faq.json content is never modified.
POLICY_ACTION_TOKENS = {
    "cancel", "cancelled", "cancellation", "return", "returns", "refunded",
    "refund", "refundable", "exchange", "change", "changed", "changes",
    "modify", "address", "payment", "pay", "paid", "card", "invoice",
    # Softer ways of asking to stop an order that use no "cancel" wording at all
    # ("please stop my order", "I don't want it anymore", "remove my order").
    "stop", "remove", "deleted", "delete", "manat",
    "منسوخ", "رد", "واپس", "واپسی", "تبدیل", "بدل", "ادائیگی", "بل", "بند", "رک",
    "کنسل", "پیسے", "پیسہ",
    "wapas", "wapsi", "tabdeel", "badalna", "ada", "adaigi", "paisa",
    "karwana", "karwa", "band", "rok", "paise",
}

# Urdu-script / Roman-Urdu policy cues that the English-only faq.json keywords
# cannot match. Maps a policy topic id to cue words; used to decide WHICH sample
# policy topic to answer (the answer text itself is fetched from faq.json by id).
POLICY_TOPIC_CUES = {
    "cancellation": {"cancel", "cancellation", "منسوخ", "رد", "karwana", "karwa",
                     "stop", "remove", "delete", "manat", "band", "rok", "بند", "رک",
                     "کنسل"},
    "returns": {"return", "returns", "refund", "exchange", "واپس", "واپسی",
                "wapas", "wapsi"},
    "changes": {"change", "modify", "address", "edit", "تبدیل", "بدل",
                "tabdeel", "badalna", "update", "location", "shifted", "wrong",
                "galat", "theek", "غلط", "ٹھیک", "پتہ"},
    "payment": {"payment", "pay", "card", "credit", "ada", "adaigi", "paisa",
                "ادائیگی", "بل", "invoice", "charged", "charge", "cash", "cod",
                "emi", "کیش", "رقم", "پیسے", "پیسہ", "paise"},
    # Shipping is normally caught by the English faq.json keywords, but Urdu
    # script and Roman Urdu have no English keyword to hit, so the topic needs
    # its own cues. Listed LAST so a tie always prefers a more specific topic.
    "shipping": {"ship", "shipping", "courier", "deliver", "delivery", "trseel",
                 "tarseel", "delivered", "شپنگ", "ترسیل", "ترسیلات", "ڈیلیوری",
                 "کورئیر", "پوسٹ", "شپ"},
}

# Two words that must BOTH appear for a topic, in any order or distance. Needed
# because a customer can describe a return without ever saying "return": "can I
# send this item back to you". Requiring the pair (never "back" alone) keeps an
# unrelated "I will come back later" from being read as a refund request.
POLICY_PHRASE_TOPICS = [
    ({"back", "send"}, "returns"),
    ({"back", "sent"}, "returns"),
    ({"back", "money"}, "returns"),
    ({"back", "amount"}, "returns"),
    ({"back", "refund"}, "returns"),
    # "theek kar dein" (please fix it) is a change REQUEST, not a status ask,
    # even though the sentence also mentions the order. Neither word alone is
    # safe: "theek" also means "fine" and "kar" is an everyday helper verb.
    ({"theek", "kar"}, "changes"),
    ({"theek", "kardo"}, "changes"),
    ({"galat", "kar"}, "changes"),
]

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

# ── Business-hours vocabulary ─────────────────────────────────────────────
# Three tiers so the routing stays safe:
# STRONG words only ever appear in an hours question (Urdu "kab band" style
# asks are carried by WEAK+CONTEXT, not listed here).
BUSINESS_HOURS_STRONG_TOKENS = {
    # English
    "hours", "timing", "timings", "opening", "closing",
    # Roman Urdu
    "khulti", "khulta", "kholtay", "kholta", "kholen", "kholne", "khulne",
    "baje",  # "kitne" deliberately NOT here: "kitne din" asks a shipping
             # duration; only "kitne baje" (o'clock) names time, and "baje"
             # carries that on its own.
    # Urdu script
    "کھلتی", "کھولتی", "کھولتا", "بجے", "ٹائمنگ", "اوقات",
}
# WEAK words that also live in other intents ("closed order", "band kar do"
# = cancel). They count ONLY together with a CONTEXT question/time word.
BUSINESS_HOURS_WEAK_TOKENS = {
    "open", "opens", "opened", "close", "closes", "closed",
    "band", "shop", "store", "dukaan", "dukan",
    "بند", "دکان", "شاپ", "اسٹور",
}
# CONTEXT: the question/time wording that turns a weak word into an hours ask.
BUSINESS_HOURS_CONTEXT_TOKENS = {
    "when", "what", "time", "today", "tomorrow", "day", "days", "week",
    "kab", "aaj", "kal", "ab", "kya", "are", "does", "is",
    "کب", "آج", "کل", "ab", "کیا",
}
# SHOP: an explicit reference to the shop itself. "band" means both "closed"
# and "stop (cancel)", so a message that carries cancellation wording counts as
# an hours question only when the SHOP is what is being talked about
# ("apki shop kab band hoti hai") - "order band" never reaches this, it is
# already excluded by the order-reference guard in _is_business_hours.
BUSINESS_HOURS_SHOP_TOKENS = {
    "shop", "store", "dukaan", "dukan", "market",
    "دکان", "شاپ", "اسٹور", "مرکز",
}

# ── Human-agent request vocabulary (Task 2) ─────────────────────────────
# Two tiers, mirroring the business-hours design above: STRONG words name a
# HUMAN directly, so they pair with any request verb (want/need/speak/talk...).
# WEAK words (agent/support/team/staff/someone) also live in ordinary
# questions ("who is my delivery agent?", "I want support for this"), so they
# count ONLY next to an explicit communication verb (speak/talk/connect/
# transfer/baat/بات) - never on want/need alone. Both tiers require a human
# REFERENCE plus a COMMUNICATION cue, so no single everyday word handoffs.
HUMAN_AGENT_STRONG_REF_TOKENS = {
    # English
    "human", "person", "representative", "representatives",
    # Roman Urdu
    "insaan", "insan", "numainda", "numainde", "shakhs",
    # Urdu script
    "انسان", "انسانی", "شخص", "نمائندہ", "نمائندے", "نمائندوں",
}
HUMAN_AGENT_WEAK_REF_TOKENS = {
    # English (job-role words that also appear in unrelated questions)
    "agent", "agents", "staff", "team", "someone", "support",
    # Roman Urdu loanword
    "customer",
    # Urdu script
    "ایجنٹ", "ٹیم", "عملہ", "کسٹمر", "سپورٹ",
}
HUMAN_AGENT_CONNECT_TOKENS = {
    # English communication verbs - "contact" is deliberately NOT here (an
    # "agent se contact" style Urdu ask is covered by the script form below,
    # while "how do I contact the courier?" must stay a shipping question).
    "speak", "speaks", "speaking", "talk", "talks", "talking",
    "connect", "connects", "connecting", "transfer", "transfers",
    "transferring",
    # Roman Urdu
    "baat", "karwao", "karwaao", "karwa", "karwaye",
    # Urdu script
    "بات", "رابطہ", "کروائیں", "کروا", "کروائے",
}
HUMAN_AGENT_WANT_TOKENS = {
    # Only ever decisive with a STRONG human noun (see _is_human_agent_request)
    "want", "wants", "need", "needs",
}
# B1 fix - the WEAK tier is split so a CAPABILITY question about the team
# ("what languages does your support team speak?") is not read as a request TO
# be connected: TRANSFER verbs (connect/transfer/karwao/رابطہ/کروائیں) are
# decisive for WEAK nouns on their own, while a TALK verb (speak/talk/baat/
# بات) only counts when the message also names WHO the talk is FOR (a
# REQUESTER: "me", "mujhe", or the desire forms "karni/karna/کرنی" - Roman
# Urdu "baat karni hai" states the request with an implied first person).
# STRONG human nouns keep the full CONNECT set (plus WANT) exactly as before,
# so every genuine request tested above still routes.
HUMAN_AGENT_TRANSFER_TOKENS = {
    # English
    "connect", "connects", "connecting", "transfer", "transfers",
    "transferring",
    # Roman Urdu
    "karwao", "karwaao", "karwa", "karwaye",
    # Urdu script
    "رابطہ", "کروائیں", "کروا", "کروائے",
}
HUMAN_AGENT_TALK_TOKENS = {
    # English
    "speak", "speaks", "speaking", "talk", "talks", "talking",
    # Roman Urdu
    "baat",
    # Urdu script
    "بات",
}
HUMAN_AGENT_REQUESTER_TOKENS = {
    # Who the conversation is to be with / for - spoken first person or the
    # desire construction that carries it in Roman Urdu / Urdu script.
    "me", "myself", "us", "mujhe", "mujhko",
    "karni", "karna", "karana", "chahta", "chahti",
    "کرنی", "کرنے", "چاہیے",
}

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

# Gratitude is its OWN intent, kept deliberately separate from farewell: a bare
# "thank you" thanks us, it does not end the call (the customer may well ask
# another question next). Matched WHOLE-word / whole-phrase exactly like the
# greeting and farewell tests above, so 'thanks' inside a longer sentence is
# never mistaken for a standalone thank-you, and a message that ALSO carries a
# real request ("thanks, what is my order status?") is answered by that request
# first - the gratitude rule only fires when nothing else matched.
GRATITUDE_TOKENS = {
    "thank", "thanks", "thx",
    "shukriya", "shukria", "shukriyaan", "ehsan",
}
GRATITUDE_PHRASES = {
    "thank you", "thank you so much", "thanks a lot", "thank you very much",
    "many thanks", "lots of thanks", "thanks so much", "thank you both",
    "big thanks", "thanks alot",
    "bohat shukriya", "bahut shukriya", "bohat shukria", "aap ka shukriya",
    "shukriya bohat", "many many thanks",
}
GRATITUDE_URDU_PHRASES = {
    "شکریہ", "بہت شکریہ", "آپ کا شکریہ", "ہزار ہزار شکریہ",
}

GREETING_REPLY = (
    "Hello! I can help with order status, shipping, returns, cancellations, "
    "and payments. What would you like to know?"
)

FAREWELL_REPLY = (
    "Thank you for contacting us! Have a great day. Goodbye!"
)

# Kept short on purpose: this one is SPOKEN back over the voice call after every
# thank-you, and it invites the next question instead of closing the call.
GRATITUDE_REPLY = (
    "You're welcome! Is there anything else I can help you with?"
)

# Approved fixed wording for a thank-you in Urdu script, used when the message
# itself is already Urdu (see _respond_gratitude). Like every other fixed reply
# this is our own pre-approved text and never model-invented.
GRATITUDE_REPLY_URDU = "آپ کا شکریہ! کیا میں آپ کی اور کوئی مدد کر سکتا ہوں?"

FALLBACK_REPLY = (
    "I'm not sure how to help with that yet. I can check an order status "
    "(just tell me your order number) or answer questions about shipping, "
    "returns, cancellations, and payments. I'm an automated assistant, so if "
    "you'd rather speak with a human, just ask and I'll be honest about what "
    "this demo can and cannot do."
)

# Honest handoff wording (Task 2). Hard rules: it admits Nova is automated, it
# states plainly that NO live transfer exists in this demo and that NO person
# has been contacted (nothing here happens silently behind the scenes), and it
# never invents a phone number, email, or support channel - it only points at
# an official contact IF the customer was given one by the business. It closes
# by offering what Nova CAN actually do, so a mixed request loses nothing.
HUMAN_AGENT_HANDOFF_REPLY = (
    "I'm Nova AI, an automated assistant - I can't transfer you to a live "
    "human agent, because live agent transfer is not available in this demo "
    "and no person has been contacted or notified. If the business gave you "
    "an official support contact, please use that channel. I can still help "
    "right here: share your order number and I'll check its status, or ask "
    "about shipping, returns, cancellations, payments, and business hours."
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
    'see you later' / 'allah hafiz' / 'khuda hafiz'. A long sentence merely
    mentioning 'goodbye' does NOT match. A bare 'thank you' (no bye/goodbye) is
    NOT leaving either - since Phase E.2 it is answered by its own gratitude
    intent instead of the farewell or the fallback.
    """
    tokens = _match_tokens(text)
    if not tokens or len(tokens) > 4:
        return False
    if " ".join(tokens) in FAREWELL_PHRASES:
        return True
    return any(tok in FAREWELL_TOKENS for tok in tokens)


def _looks_like_gratitude(text):
    """Whole-word / whole-phrase gratitude test (token-based, never a substring).

    Uses the SAME short-message guard as greeting/farewell (<= 4 tokens), so only
    a genuine standalone thank-you counts: 'thanks' / 'thank you so much' /
    'bohat shukriya' / 'شکریہ'. A long sentence that merely mentions thanks
    ("thanks for letting me know about the delay in my shipment") is NOT gratitude
    and keeps its real intent, and 'shukriya' embedded in an unrelated word
    cannot fire because tokens are compared whole.
    """
    tokens = _match_tokens(text)
    if not tokens or len(tokens) > 4:
        return False
    joined = " ".join(tokens)
    if joined in GRATITUDE_PHRASES:
        return True
    # Urdu script is not lowercased away by the normalizer, so set-phrase forms
    # ("بہت شکریہ") are compared as a whole joined phrase too.
    if joined in GRATITUDE_URDU_PHRASES or any(t in GRATITUDE_URDU_PHRASES for t in tokens):
        return True
    return any(tok in GRATITUDE_TOKENS for tok in tokens)


def _respond_gratitude(text):
    """The one approved reply to a thank-you, in the language it was said in.

    Returns the fixed English wording for Roman/Latin scripts and the fixed Urdu
    script wording when the customer thanked us in Urdu. No order facts and no
    policy text are involved, so no live translation call is made.
    """
    if any("\u0600" <= ch <= "\u06ff" for ch in (text or "")):  # Urdu/Arabic script
        return {"intent": "gratitude", "reply": GRATITUDE_REPLY_URDU, "source": "fixed"}
    return {"intent": "gratitude", "reply": GRATITUDE_REPLY, "source": "fixed"}


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
    # A policy verb pair ("send ... back", "theek kar") outranks the status
    # reading too, because such a sentence usually ALSO names the order.
    if any(pair <= token_set for pair, _topic in POLICY_PHRASE_TOPICS):
        return False
    has_ref = bool(ORDER_REF_TOKENS & token_set)
    has_cue = bool(ORDER_STATUS_CUES & token_set)
    return has_ref and has_cue


def _is_human_agent_request(tokens):
    """True when the message explicitly asks to speak with a HUMAN (English,
    Roman Urdu or Urdu script).

    Two tiers keep ordinary mentions out:
    - a STRONG human noun (human/person/representative/انسان/نمائندے) plus a
      communicate cue OR a plain want/need is a handoff request;
    - a WEAK role noun (agent/support/team/staff/someone) needs a TRANSFER
      verb (connect/transfer/karwao/رابطہ), or a TALK verb (speak/talk/baat)
      ONLY when a REQUESTER ("me", "mujhe", "karni") names who the talk is
      for - so "I want support for this", "who is my delivery agent?" and
      "what languages does your support team speak?" keep their own paths;
    - with neither tier the message is not a handoff at all.

    B2: for THIS test only, hyphenated compounds are split and possessive
    apostrophes removed ("human-agent" -> human + agent; "agent's" -> agents)
    so a hyphen or 's cannot hide a request. Every other router still sees the
    original tokens, so no other path changes behavior.
    """
    expanded = []
    for tok in tokens:
        for part in tok.replace("\u2019", "'").split("-"):
            part = part.replace("'", "")
            if part:
                expanded.append(part)
    token_set = set(expanded)
    has_connect = bool(HUMAN_AGENT_CONNECT_TOKENS & token_set)
    has_want = bool(HUMAN_AGENT_WANT_TOKENS & token_set)
    if HUMAN_AGENT_STRONG_REF_TOKENS & token_set:
        return has_connect or has_want
    if HUMAN_AGENT_WEAK_REF_TOKENS & token_set:
        if HUMAN_AGENT_TRANSFER_TOKENS & token_set:
            return True
        return bool(HUMAN_AGENT_TALK_TOKENS & token_set
                    and HUMAN_AGENT_REQUESTER_TOKENS & token_set)
    return False


def _is_business_hours(tokens):
    """True when the message asks about opening/closing times (English, Roman
    Urdu or Urdu script).

    Guards keep every existing path intact:
    - a message that references the customer's OWN order/parcel is never an
      hours question here ("Mera order kab deliver hoga?" stays on the order
      path, "why is my parcel closed" stays a status question);
    - STRONG hours words alone are decisive;
    - WEAK words (open/close/band/shop) need a question/time CONTEXT word, and
      when cancellation wording ("band") is present the message must also name
      the SHOP itself, so "refund kab milega" stays returns and cancel wording
      stays cancellation.
    """
    token_set = set(tokens)
    if ORDER_REF_TOKENS & token_set:
        return False
    if BUSINESS_HOURS_STRONG_TOKENS & token_set:
        return True
    if (BUSINESS_HOURS_WEAK_TOKENS & token_set
            and BUSINESS_HOURS_CONTEXT_TOKENS & token_set):
        if POLICY_ACTION_TOKENS & token_set:
            return bool(BUSINESS_HOURS_SHOP_TOKENS & token_set)
        return True
    return False


# ── Spoken order-number normalization ──────────────────────────────────────
# The browser's speech recognition (and typed text chat) frequently delivers an
# order number as WORDS — "one zero zero two", "order number one two three",
# "one hundred twenty-three" — or with the word "order"/"order number" instead
# of the literal ORD token, or (Urdu) using Arabic-Indic digit glyphs. None of
# those forms match ORDER_ID_RE, so a spoken number used to fall through to the
# generic fallback: the reported "the AI does not recognize the order number"
# bug. We normalize ONLY the number words into digits, and we attach them to an
# order ONLY when the surrounding words clearly mark an order reference; we
# NEVER pad, guess or invent an id. An incomplete/ambiguous number -> ask repeat.
_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_NUMBER_SCALES = {"hundred": 100, "thousand": 1000, "million": 1000000}
# Arabic-Indic (٠١٢٣٤٥٦٧٨٩) and Extended/Persian-Urdu (۰۱۲۳۴۵۶۷۸۹) digits.
_DIGIT_GLYPHS = {
    "\u0660": "0", "\u0661": "1", "\u0662": "2", "\u0663": "3", "\u0664": "4",
    "\u0665": "5", "\u0666": "6", "\u0667": "7", "\u0668": "8", "\u0669": "9",
    "\u06f0": "0", "\u06f1": "1", "\u06f2": "2", "\u06f3": "3", "\u06f4": "4",
    "\u06f5": "5", "\u06f6": "6", "\u06f7": "7", "\u06f8": "8", "\u06f9": "9",
}
# Words that make a number reading an ORDER reference (reuse the existing
# order-reference vocabulary plus the literal id stem and the word "number").
_ORDER_NUMBER_CUES = ORDER_REF_TOKENS | {"ord", "order", "number"}


def _number_tokens(text):
    """Split a message into lowercase tokens for NUMBER scanning.

    Normalizes Unicode, swaps Arabic-Indic/Urdu digit glyphs for ASCII, turns
    separators (hyphen/slash) and punctuation into spaces so "twenty-three" and
    "1002" tokenize cleanly, and keeps letters from every script intact.
    """
    t = unicodedata.normalize("NFKC", text).lower()
    for glyph, d in _DIGIT_GLYPHS.items():
        t = t.replace(glyph, d)
    t = re.sub(r"[-\u2013\u2014_/]", " ", t)   # – —
    t = re.sub(r"[^\w\s]", " ", t)
    return [w for w in re.sub(r"\s+", " ", t).split() if w]


def _is_number_token(tok):
    return tok.isdigit() or tok in _NUMBER_WORDS or tok in _NUMBER_SCALES or tok == "and"


def _parse_compound(core):
    """Value of a scale-bearing phrase: 'one hundred twenty three' -> 123,
    'one thousand two' -> 1002, 'twenty three' -> 23."""
    total = 0
    current = 0
    for tok in core:
        if tok in _NUMBER_SCALES:
            scale = _NUMBER_SCALES[tok]
            if scale == 100:
                current = (current or 1) * 100
            else:
                total += (current or 1) * scale
                current = 0
        elif tok.isdigit():
            current = current * 10 + int(tok)
        else:
            current += _NUMBER_WORDS.get(tok, 0)
    return total + current


def _group_to_digits(group):
    """Turn one run of number tokens into its digit string.

    A pure digit-word sequence reads digit-by-digit (an order id is spoken that
    way): 'one zero zero two' -> '1002'. A scale phrase is read as its value:
    'one hundred twenty three' -> '123'.
    """
    core = [t for t in group if t != "and"]
    if any(t in _NUMBER_SCALES for t in core):
        return str(_parse_compound(core))
    seq = ""
    for tok in core:
        value = _NUMBER_WORDS.get(tok, -1)
        if tok.isdigit():
            seq += tok
        elif 0 <= value <= 9:
            seq += str(value)
        else:
            return str(_parse_compound(core))  # tens/teens mixed -> value form
    return seq


def _digit_groups(tokens):
    """Return confident digit strings found in the token stream.

    Only maximal runs of number tokens become candidates. A single common
    number word ('one', 'five') or a lone literal digit is intentionally
    IGNORED so filler words are never mistaken for an order number.
    """
    groups = []
    cur = []
    for tok in tokens:
        if _is_number_token(tok):
            cur.append(tok)
        elif cur:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)

    out = []
    for group in groups:
        core = [t for t in group if t != "and"]
        if not core:
            continue
        if len(core) == 1:
            tok = core[0]
            # Keep a literal multi-digit token ("1002"); drop a lone number WORD
            # or single digit ("one", "5") so everyday filler is not mistaken
            # for an order number.
            if tok.isdigit() and len(tok) >= 2:
                digits = tok
            else:
                continue
        else:
            digits = _group_to_digits(group)
        if digits and digits.isdigit() and len(digits) >= 2:
            out.append(digits)
    return out


def _resolve_order_id(text):
    """Decide whether a message names a specific order, and which canonical id.

    Returns one of:
      ("ORD-####", "found")    - a confident id: hand to the database lookup.
      (None, "uncertain")      - an order number was clearly given but did not
                                 resolve to a complete id -> ask to repeat.
      (None, "none")           - no order-number signal; continue normal routing.
    """
    # 1) The trusted literal form (ORD-1002 / ord 1002 / order ord 1002 ...) —
    #    unchanged, so every existing spoken variation that keeps the ORD stem
    #    still behaves exactly as before.
    match = ORDER_ID_RE.search(text)
    if match:
        return f"ORD-{match.group(1)}", "found"

    tokens = _number_tokens(text)
    token_set = set(tokens)
    has_order_cue = bool(_ORDER_NUMBER_CUES & token_set)
    # A reply that is ONLY a number counts as an order reference too: it is what
    # the customer says right after we ask them to repeat the number in a call,
    # so those digits must be usable rather than fall through to the fallback.
    number_only = bool(tokens) and all(_is_number_token(t) for t in tokens)
    if not (has_order_cue or number_only):
        return None, "none"  # no order reference at all -> never treat a stray
                             # number as an order (no guessing).

    groups = _digit_groups(tokens)
    if not groups:
        return None, "none"  # an order reference with no number -> the existing
                            # "please share your order number" path handles it.

    best = max(groups, key=len)
    if len(best) == 4 and best.isdigit():
        return f"ORD-{best}", "found"

    # A number was given next to an order reference but it is not a clean
    # 4-digit id. If the message is actually a POLICY action (cancel/return/
    # refund ...), leave it to the policy branch instead of hijacking it with a
    # "repeat your number" prompt. Otherwise the number was unclear -> ask.
    if POLICY_ACTION_TOKENS & token_set:
        return None, "none"
    return None, "uncertain"


def _detect_policy_topic(text, tokens, faq_path):
    """Pick a policy topic id (shipping/returns/cancellation/changes/payment) or
    None. POLICY_TOPIC_CUES is the primary detector and now covers English as
    well as Roman Urdu and Urdu script (faq.json's keywords are English-only and
    are consulted only afterwards). POLICY_PHRASE_TOPICS catches a topic that no
    single word names ("send this back"). This ONLY decides which SAMPLE topic to
    answer; the answer text is fetched from faq.json by id in respond() (never
    invented here).

    The cue score is checked FIRST, before the FAQ keywords, because the FAQ
    matches keywords as substrings: "any update on my shipment" contains "ship"
    and used to win the shipping answer even though the customer was describing
    their own order. Order-reference messages are already intercepted earlier by
    rule 3, so anything reaching here has no confident order reference, and a
    specific cue ("update my location") outranks a loose keyword hit.
    """
    token_set = set(tokens)
    best_topic, best_score = None, 0
    for topic, cues in POLICY_TOPIC_CUES.items():
        score = len(token_set & cues)
        if score > best_score:
            best_topic, best_score = topic, score
    if best_topic:
        return best_topic
    # No single cue word, but a pair of words can still name the topic
    # unambiguously ("send ... back", "money back").
    for pair, pair_topic in POLICY_PHRASE_TOPICS:
        if pair <= token_set:
            return pair_topic
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


def _model_order_id_is_verifiable(order_id, text):
    """Accept a model-claimed order id ONLY if the customer actually said it.

    The classifier is told to leave `order_id` empty unless the customer stated a
    number, but a model can still invent one, so we never look up an id on the
    model's word alone. The digits are re-checked against the customer's OWN
    message using the same number normalization as the spoken-number path, so
    "one zero zero two" and "1002" both count as stated. Anything else is
    discarded and we ask the customer for their number instead - PostgreSQL stays the
    only source of order facts.
    """
    if not order_id:
        return None
    digits = "".join(ch for ch in str(order_id) if ch.isdigit())
    if len(digits) != 4:
        return None
    if digits in _digit_groups(_number_tokens(text)):
        return f"ORD-{digits}"
    return None


def _respond_via_gemini(text, db_path=None):
    """Use Gemini to classify intent + language, then return a GROUNDED result.

    Returns ``(result, classification)``:
      result        the grounded answer dict, or None when the model has no
                    actionable verdict (disabled, a timeout, a rogue label, or
                    simply "other"/"ambiguous"). None means "the deterministic
                    rules below decide", so a model hesitation can no longer
                    discard an answer the rules already understand.
      classification  the validated classification (or None), remembered only so
                    the final step can ask a clarifying question when the model
                    said the message was too vague, instead of shrugging.

    Order ids were already handled by the reliable rules + database lookup above,
    so an `order_status` intent here usually means no id was resolved: we ask for
    it. A model-claimed id is used only when the customer's own words contain it
    (see _model_order_id_is_verifiable). Policies are answered from the SAMPLE
    FAQ (grounded in data/faq.json), never invented by the model.
    """
    classification = gemini_module.classify_message(text)
    if not classification:
        return None, None

    intent = classification.get("intent")
    language = classification.get("language")

    # LOW CONFIDENCE: the model itself says this is too vague to route safely, so
    # we ask a short question instead of acting on a shaky classification.
    if classification.get("needs_clarification"):
        return {
            "intent": "ambiguous",
            "reply": _localized(CLARIFICATION_REPLY, language),
            "source": "gemini:fixed",
        }, classification

    if intent == "order_status":
        verified = _model_order_id_is_verifiable(
            classification.get("order_id"), text)
        if verified:
            # The model only pointed at a number the customer already said; the
            # status itself still comes straight from PostgreSQL.
            return {
                "intent": "order_status",
                "reply": describe_order(verified, db_path),
                "source": "orders_db",
            }, classification
        return {
            "intent": "order_status_missing_id",
            "reply": _localized(ORDER_ID_REQUEST_REPLY, language),
            "source": "gemini:fixed",
        }, classification
    if intent in POLICY_INTENTS:
        # Grounded in the SAMPLE FAQ: return that topic's sample answer, our own
        # text rephrased into the customer's language. If the topic has no stored
        # answer, let the deterministic branch decide (never invent one here).
        sample = faq_module.get_answer_by_id(intent)
        if not sample:
            return None, classification
        return {
            "intent": intent,
            "reply": _localized(sample, language),
            "source": "gemini:policy_sample",
        }, classification
    if intent == "greeting":
        return {
            "intent": "greeting",
            "reply": _localized(GREETING_REPLY, language),
            "source": "gemini:fixed",
        }, classification
    # "ambiguous" / "other" / an unexpected value: NOT actionable on its own.
    # Fall through to the deterministic rules, which understand far more than the
    # model's two "not sure" labels; the hint is only used if they too fail.
    return None, classification


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

    # 1a) Explicit human-agent request (any supported language) -> the honest
    #     handoff wording above. First rule of all, before order ids and
    #     before the optional model, so a mixed request ("I want to speak to
    #     a human about order ORD-1001") can never silently drop the human
    #     ask; the reply itself invites the concrete question straight back.
    #     Deterministic fixed text - no model call, no invented contact info.
    tokens = _match_tokens(text)
    if _is_human_agent_request(tokens):
        return {
            "intent": "human_agent_request",
            "reply": HUMAN_AGENT_HANDOFF_REPLY,
            "source": "fixed",
        }

    # 1) Order status — highest priority when an order id is present. This also
    #    resolves a SPOKEN order number ("one zero zero two", "order number
    #    1002") into the canonical ORD-#### form; the database lookup itself is
    #    unchanged. An unclear/partial number asks the customer to repeat it
    #    instead of inventing an id (see _resolve_order_id).
    order_id, order_status = _resolve_order_id(text)
    if order_status == "found":
        return {
            "intent": "order_status",
            "reply": describe_order(order_id, db_path),
            "source": "orders_db",
        }
    if order_status == "uncertain":
        return {
            "intent": "order_status_unclear",
            "reply": ORDER_ID_REPEAT_REPLY,
            "source": "none",
        }

    # 1b) Business-hours question (any supported language) -> the SAMPLE schedule
    #     from data/faq.json, VERBATIM, before the optional model is consulted:
    #     the answer is identical with Gemini enabled or disabled, and no model
    #     can ever invent opening times. Same precedence pattern as farewell and
    #     gratitude above. If the topic were ever missing from faq.json, this
    #     falls through honestly to the existing routing (never guesses).
    if _is_business_hours(tokens):
        sample = faq_module.get_answer_by_id("business_hours", faq_path)
        if sample:
            return {
                "intent": "business_hours",
                "reply": sample,
                "source": "faq_kb:business_hours:sample",
            }

    # 2) OPTIONAL Gemini branch - understand free-form / multilingual phrasing.
    #    Only runs when the feature is enabled and a key exists; it may only
    #    classify intent + language and rephrase our OWN fixed messages. Order
    #    facts already came from PostgreSQL above, and unapproved FAQ placeholders
    #    are never quoted (see _respond_via_gemini). On ANY failure this returns
    #    None and we fall through to the deterministic assistant below, without
    #    ever pretending Gemini answered.
    #    An order id was already handled above by rule 1, and the whole-word and
    #    short-message guards in _looks_like_farewell keep false positives (a bare
    #    "thank you", a long sentence merely mentioning "goodbye") out.
    model_hint = None
    if gemini_module.is_enabled():
        # Farewell is resolved DETERMINISTICALLY before we consult the model,
        # so a goodbye behaves IDENTICALLY whether Gemini is enabled or disabled
        # (rule 5b below). Gemini has no `farewell` intent: left to the model,
        # "bye" is classified `other` and answered with the generic fallback.
        if _looks_like_farewell(text):
            return {"intent": "farewell", "reply": FAREWELL_REPLY, "source": "fixed"}
        # Same treatment for a thank-you (rule 5c below): resolved from our own
        # vocabulary, never from the model, and it costs no Gemini call.
        if _looks_like_gratitude(text):
            return _respond_gratitude(text)
        gemini_result, model_hint = _respond_via_gemini(text, db_path)
        if gemini_result is not None:
            return gemini_result

    # 3) Order-status wording (English / Urdu / Roman Urdu) but NO order id ->
    #    ask for the order id. Never guess an order or invent a status.
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

    # 5c) Gratitude (whole-word; short-message guard as above). Reached only when
    #      rules 1-4 found no order reference, no order id and no policy topic,
    #      so "thanks, what is my order status?" was already answered as an order
    #      question and only a STANDALONE thank-you gets here. Kept distinct from
    #      farewell: thanking us does not end the call.
    if _looks_like_gratitude(text):
        return _respond_gratitude(text)

    # 6) Nothing matched. If we did consult the model and it could not pin the
    #    message down either, use THAT honestly: a request it found too vague
    #    gets a short clarifying question, an unrelated one gets the same
    #    fallback a disabled model would give. Neither invents an intent.
    if model_hint:
        hint_language = model_hint.get("language")
        if model_hint.get("intent") == "ambiguous":
            return {
                "intent": "ambiguous",
                "reply": _localized(CLARIFICATION_REPLY, hint_language),
                "source": "gemini:fixed",
            }
        if model_hint.get("intent") == "other":
            return {
                "intent": "fallback",
                "reply": _localized(FALLBACK_REPLY, hint_language),
                "source": "gemini:fixed",
            }

    # 7) Honest fallback (no model consulted, or it gave no usable verdict).
    return {"intent": "fallback", "reply": FALLBACK_REPLY, "source": "none"}
