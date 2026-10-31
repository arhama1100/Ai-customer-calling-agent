# Missing FAQ — Shop-Owner Questionnaire

> **Purpose:** This worksheet lists customer questions that have **no approved
> answer yet**. The shop owner should fill in the blank "Approved answer" space
> under each topic. Only the owner's wording is authoritative.

---

## ⚠️ Status of the current knowledge base

- **All existing FAQ policies are still UNAPPROVED placeholders.** The answers in
  `data/faq.json` were created during project scaffolding and are marked
  `_meta.status = "DRAFT — PENDING OWNER APPROVAL"`. They must be reviewed and
  approved by the shop owner before the assistant treats them as policy.
- **`data/faq.json` is the ONLY source of truth for policy answers, and it has
  NOT been modified here.** Nothing in this document has been copied into it.
- **No answers are pre-filled or invented in this file** — the blanks are for the
  owner only. The assistant will never guess a policy, price, date, refund, or
  approval.
- Order **status** questions (e.g. "where is my order ORD-1002?") are answered
  from the SQLite orders database, **not** from this FAQ — they are intentionally
  excluded here.
- **The optional Gemini layer does not change any of this.** It is **OFF by
  default**, only classifies intent/language and rephrases our own approved text,
  and is **never** a source of policy facts. Until you approve a topic here, the
  assistant replies with a "needs confirmation — please contact support" notice
  instead of the draft answer, in **every** mode (Gemini on, off, no key, or an
  API failure). **Live Gemini behaviour and real microphone/audio output have not
  been verified** in this build (Gemini is mocked in tests; text chat was the
  path confirmed in a browser).

Each section below gives real customer phrasings in **English**, **Urdu script**,
and **Roman Urdu**. Please write one approved answer per topic.

---

## 1. Payment — charged but order not confirmed / duplicate charge 💳

**Customer questions**
- EN: "My payment was deducted but my order wasn't confirmed." / "I was charged twice for one order."
- اردو: "میری پیمنٹ کٹ گئی لیکن میرا آرڈر کنفرم نہیں ہوا۔" / "ایک آرڈر پر مجھ سے دو بار پیسے کٹ گئے۔"
- Roman: "Meri payment deduct ho gayi but order confirm nahi hua." / "Ek order par mujhe do baar charge kiya gaya."

**Approved answer (owner):**
```
<Fill in here — how the shop handles a payment that was deducted but not confirmed, and duplicate charges>
```

---

## 2. Delivery — late / not received / stuck 🚚

**Customer questions**
- EN: "My parcel hasn't arrived yet — where is it?" / "My delivery is late." / "The courier couldn't deliver."
- اردو: "میرا پارسل ابھی تک نہیں آیا، یہ کہاں ہے؟" / "میری ڈیلیوری تاخیر کا شکار ہے۔" / "کورئیر میرا سامان ڈیلیور نہیں کر سکا۔"
- Roman: "Mera parcel abhi tak nahi aaya, yeh kahan hai?" / "Meri delivery late hai." / "Courier mera saman deliver nahi kar saka."

**Approved answer (owner):**
```
<Fill in here — what the shop does when a delivery is late, not received, or a courier fails to deliver>
```

---

## 3. Damaged / defective / wrong item received 📦

**Customer questions**
- EN: "I received a damaged/defective product." / "You sent me the wrong item."
- اردو: "مجھے خراب / ٹوٹا ہوا سامان ملا ہے۔" / "آپ نے میرا غلط سامان بھیج دیا۔"
- Roman: "Mujhe kharab / toota hua saman mila hai." / "Aap ne meral ghalat saman bhej diya."

**Approved answer (owner):**
```
<Fill in here — replacement / refund / return process for damaged, defective, or wrong items>
```

---

## 4. Warranty / guarantee 🛡️

**Customer questions**
- EN: "Do your products come with a warranty?" / "Is there a guarantee on this item?"
- اردو: "کیا آپ کے مصنوعات کی وارنٹی ہوتی ہے؟" / "کیا اس آئٹم کی گارنٹی ہے؟"
- Roman: "Kya aap ke products ki warranty hoti hai?" / "Kya is item ki guarantee hai?"

**Approved answer (owner):**
```
<Fill in here — warranty / guarantee terms, if any>
```

---

## 5. Shipping cost / coverage / international 🌍

**Customer questions**
- EN: "How much is shipping?" / "Do you offer free shipping?" / "Do you ship outside the country?"
- اردو: "شپنگ کا کتنا چارج ہے؟" / "کیا فری شپنگ ہے؟" / "کیا آپ بیرون ملک شپنگ کرتے ہیں؟"
- Roman: "Shipping ka kitna charge hai?" / "Kya free shipping hai?" / "Kya aap baeeron mulk shipping karte hain?"

**Approved answer (owner):**
```
<Fill in here — shipping fees, free-shipping threshold (if any), and areas / countries covered>
```

---

## 6. Return specifics — return shipping / refund not received ↩️

**Customer questions**
- EN: "Who pays for return shipping?" / "My refund hasn't arrived yet." / "Can I return an opened item?"
- اردو: "واپسی کی شپنگ کون ادا کرے گا؟" / "میرا رفند ابھی تک نہیں آیا۔" / "کیا میں کھولا ہوا سامان واپس کر سکتا ہوں؟"
- Roman: "Wapsi ki shipping kaun ada karega?" / "Mera refund abhi tak nahi aaya." / "Kya main khola hua saman wapis kar sakta hoon?"

**Approved answer (owner):**
```
<Fill in here — who pays return shipping, refund timing after return, and whether opened items can be returned>
```

---

## 7. Cancellation — refund after cancelling ✖️

**Customer questions**
- EN: "If I cancel, when will I get my refund?" / "How do I cancel and get money back?"
- اردو: "اگر میں آرڈر کینسل کروں تو پیسے کب واپس ملیں گے؟" / "کینسل کر کے رقم کیسے واپس ملے گی؟"
- Roman: "Agar main order cancel karoon to paise kab wapis milenge?" / "Cancel kar ke raqam kaise wapis milegi?"

**Approved answer (owner):**
```
<Fill in here — refund method and timing for a cancelled order>
```

---

## 8. Order confirmation / invoice / receipt 🧾

**Customer questions**
- EN: "I didn't get an order confirmation / invoice." / "Can I get a receipt?"
- اردو: "مجھے آرڈر کی تصدیق / انوائس نہیں ملی۔" / "کیا مجھے رسائی مل سکتی ہے؟"
- Roman: "Mujhe order ki confirmation / invoice nahi mili." / "Kya mujhe receipt mil sakti hai?"

**Approved answer (owner):**
```
<Fill in here — how customers receive / request order confirmation, invoice, or receipt>
```

---

## 9. Contact support channel / hours ☎️

> The existing `changes` topic says "please contact support" but never defines how.

**Customer questions**
- EN: "How do I contact support?" / "What are your support hours?"
- اردو: "میں سپورٹ سے کیسے رابطہ کروں؟" / "آپ کے سپورٹ کے اوقات کیا ہیں؟"
- Roman: "Main support se kaise rabta karoon?" / "Aap ke support ke auqaat kya hain?"

**Approved answer (owner):**
```
<Fill in here — support channel(s) (phone / email / WhatsApp), and hours of operation>
```

---

## 10. Multilingual keywords for the EXISTING approved topics 🌐

The **current** `data/faq.json` topics (shipping, returns, cancellation, changes,
payment) exist but their **keywords are English-only**, so Urdu-script and Roman
Urdu phrasings don't match the deterministic keyword matcher. Please confirm the
preferred handling (owner decision — do not change the file until approved):

- EN (existing topics): shipping/delivery, returns/refunds, cancellation, order
  changes, payment methods.
- اردو (sample phrasings to add as keywords): "شپنگ"، "ڈیلیوری"، "واپسی"،
  "رفند"، "منسوخ"، "تبدیلی"، "پیمنٹ"
- Roman (sample phrasings to add as keywords): "shipping", "delivery", "wapsi",
  "refund", "cancel", "change", "payment"

**Owner decision / approved keywords:**
```
<Fill in here — whether to add Urdu / Roman keywords to faq.json, and the exact terms>
```

---

## How to complete this worksheet
1. Fill the "Approved answer (owner)" block for each topic you want the assistant
   to answer.
2. Leave a topic blank if there is no policy for it yet — the assistant will keep
   asking for clarification or route to support rather than invent an answer.
3. Once the answers are provided, they can be reviewed and (with your approval)
   added to `data/faq.json` in a later step.
