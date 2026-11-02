# AI Customer Calling Agent

A beginner-friendly customer-service "calling" agent for a **fictional** shop.
A customer types (or speaks) a question; the assistant answers **only** from
deterministic, local sources — order status from a SQLite database and topic
answers from a sample FAQ file. It never invents an answer, and it always
falls back to honest text chat when voice features are unavailable.

> Status: **a mentor-guided internship project, several phases complete.** A
> Flask API, SQLite orders, an FAQ of **demonstration sample policies**, a
> deterministic assistant, and a plain HTML/CSS/JS frontend with browser STT/TTS
> are in place — including a **simulated browser voice-call mode**
> (`static/js/call.js`) and an **optional Gemini natural-language layer that is
> implemented but OFF by default** (`GEMINI_ENABLED=false`). The current suite is
> **431 passing Python tests plus 37 headless JavaScript tests** (verified).
> Order status comes **only** from SQLite, policy answers **only** from the
> sample FAQ (fictional demonstration data, clearly labelled, never invented),
> and Gemini only classifies intent/language and rephrases our own approved text.
> Every Gemini interaction in the tests is mocked (no live API call has been
> made). The deterministic assistant understands **English, Urdu script and
> Roman Urdu** (typed) for order status, the five policy topics, greetings,
> farewells and **gratitude** ("Thank you" / "Shukriya" → a "You're welcome"
> reply). The G3 security limits (rate limiting, body/message-size caps, JSON
> error handlers) are covered by automated tests, but **real microphone/audio
> behaviour, spoken Urdu / Roman Urdu recognition, and live Gemini remain
> unverified** — see [Voice](#voice--limitations--fallback-important) and
> [Known limitations](#known-limitations--future-improvements).

## Features
- **Text chat** that always works (the primary, guaranteed path).
- **Deterministic intent routing**: order id → SQLite, topic → FAQ, else an
  honest "I'm not sure" fallback. No fabricated order statuses or policies.
- **Sample policy answers**: a policy question (shipping, returns, cancellation,
  changes, payment) returns the matching answer from `data/faq.json`. These are
  **fictional demonstration policies**, clearly labelled in the UI footer and in
  the response `source` (`faq_kb:<topic>:sample`) — a real business must replace
  them with its own approved policies before customer use.
- **Greetings, farewells & gratitude**: recognizes a greeting ("hi", "assalam o
  alaikum"), a farewell ("bye", "goodbye", "see you later", "Allah Hafiz", "Khuda
  Hafiz", "okay bye"), and a standalone thank-you ("thank you", "thanks", "shukriya",
  "bohat shukriya", "شکریہ") with a friendly fixed reply — each **identically
  whether Gemini is enabled or disabled** — while avoiding false positives:
  "goodbye" buried in a longer sentence is *not* treated as leaving, and a
  thank-you that also carries a real question ("thanks, what is my order
  status?") is answered as that question, not as gratitude.
- **Text-chat voice input (STT)** via the browser Web Speech API (`en-US` /
  `ur-PK`). Roman Urdu is a *written* form with **no browser speech model**, so
  it is supported for **typed** input, not spoken recognition.
- **Spoken replies (TTS)** via `speechSynthesis`, with a user toggle.
- **Simulated browser voice-call mode** (`static/js/call.js`): a hands-free call
  over one continuous recognition session — spoken greeting, **barge-in**,
  end-of-turn speech **buffering** (including an order number dictated digit by
  digit), mute, honest end-of-call teardown, stale-response protection, a
  **liveness watchdog** that re-arms a silently-dead microphone, immediate
  re-arming when the Voice-language changes, and graceful handling of every
  `onerror` code. It posts the **same `/api/chat` request and gets the same
  backend intent detection** as text chat, so voice and text are equivalent.
- **Graceful degradation**: unsupported browser, denied mic, no speech, or a
  speech-service error each show a friendly message; text chat keeps working.
- **Modern, responsive blue-and-white UI** built with plain HTML/CSS/JS.
- **Hardened API (Phase G3)**: a request-body size cap and a chat message-length
  cap, consistent **safe JSON errors** (`400/405/413/429/500`) with no leaked
  stack traces or secrets, a lightweight **in-memory rate limiter** on
  `/api/chat` and `/api/orders`, a **database-error guard** on order lookup, and a
  **production-safe config default** (debug **off** unless explicitly enabled, and
  loopback host binding). The malformed-JSON → friendly-200 and internal-error →
  `{intent, reply, source}` contracts are preserved, not broken.
- **Automated tests** with pytest (backend, API, frontend-serving, frontend-contract
  checks, and G3 security/validation/rate-limit/config checks).

## Multilingual support (English / Urdu / Roman Urdu)
Customers can ask in **English**, **Urdu script** (e.g. "میرا آرڈر کہاں ہے") and
**Roman Urdu** (e.g. "mera order kahan hai").

- **Order status:** an order id (`ORD-1002`) is matched reliably from any of the
  three forms and answered from SQLite; Urdu/Roman wording *without* an id asks
  for the order number instead of guessing.
- **Free-form / mixed phrasing:** when Gemini is enabled it classifies the intent
  and language and rephrases our own approved replies into the customer's
  language.
- **Greetings, farewells & gratitude** are recognized in English and Roman/Urdu
  forms (e.g. "khuda hafiz", "allah hafiz", "see you later"; "shukriya", "bohat
  shukriya", "شکریہ") and answered with the same fixed reply whether Gemini is
  enabled or disabled. A thank-you is a **standalone** intent: it never overrides
  a message that also asks a real order/policy question.
- **Policy questions** are detected across English, Urdu script and Roman Urdu
  (keyword + cue matching) and routed to the right sample topic; an ambiguous
  "policy" ask with no single clear topic gets a short clarification question
  rather than a guess.
- **Current limitations (honest):**
  - **Spoken Urdu and Roman Urdu are not reliably recognized.** Browser speech
    recognition (`ur-PK`) depends on a browser/OS voice pack that is absent from
    most desktop Chrome/Edge installs, and **Roman Urdu has no speech model at
    all** (it is Urdu written in Latin letters). *Typing* English, Urdu script or
    Roman Urdu all work; *speaking* them may not (see
    [Voice](#voice--limitations--fallback-important)).
  - Offline, a detected policy topic returns its **English** sample answer from
    `data/faq.json` (the wording is never invented). Urdu-script *shipping*
    wording is not yet auto-detected; ask shipping in English/Roman Urdu, or use
    Gemini for broader free-form coverage.
  - **Reply localization** into Urdu needs Gemini ON; offline fixed replies
    (greeting, farewell, the order-id request, clarification, and the sample
    policy text) stay in English — except gratitude, which has its own fixed
    Urdu-script reply when the thank-you was written in Urdu script (they can
    still be *spoken* in the selected language if the browser has that voice).

## Technology stack
| Layer | Choice |
|---|---|
| Backend | Python + Flask (REST API, app-factory pattern) |
| Database | SQLite (Python's built-in `sqlite3` — no extra install) |
| Frontend | Plain HTML / CSS / JavaScript (no framework, no build step) |
| Speech (browser) | Web Speech API (`SpeechRecognition`) + `speechSynthesis` |
| Tests | pytest (Python) + dependency-free Node scripts (headless JS for `call.js`) |
| Optional (off by default) | Gemini layer via `google-genai` (`app/gemini.py`) — grounded, feature-flagged, mocked in tests |

## Architecture
A single request flows through clearly separated layers, and each layer keeps a
hard rule so no answer is ever invented:

```
browser:  index.html + app.js (text chat, mic STT, TTS)
                     + call.js (simulated voice call: continuous STT,
                       turn buffering, barge-in, mute, TTS)   [both -> same /api/chat]
      |  fetch POST /api/chat  {message}
      v
Flask API (app/routes.py)  -- validation & guards (Phase G3):
      |   body-size cap -> 413, message-length cap -> 413,
      |   rate limiter (app/rate_limit.py) -> 429, DB-error + JSON error handlers
      v
Deterministic assistant (app/assistant.py)  -- routes by intent:
      |- order id present  ------------> SQLite  (app/orders.py)  [source of truth]
      |- policy topic      ------------> sample FAQ (data/faq.json via app/faq.py)
      |- greeting / farewell / gratitude / ambiguous -> fixed, friendly replies
      |- no match          ------------> honest fallback (never invents)
      ^
      +- OPTIONAL Gemini (app/gemini.py, OFF by default): only CLASSIFIES
         intent/language and REPHRASES the approved text above - it can never
         fetch or fabricate an order/policy fact.
      v
   {intent, reply, source}  --> shown in the chat and (optionally) spoken (TTS)
```

The `source` field records where every answer came from (e.g. `orders_db`,
`faq_kb:returns:sample`, `gemini:policy_sample`, `fixed`, `none`), keeping the
data lineage auditable. The app is built with the Flask **app-factory** pattern
(`app/__init__.py: create_app`), so tests create an isolated instance over a
throwaway database and never touch `data/shop.db`.

## Prerequisites
- **Python 3.x** on your machine (this project was developed and tested on
  **3.14**; the core stack has no compiled dependencies).
- A modern desktop browser. **Voice input (STT) works best in Chrome or Edge**;
  other browsers may lack the Web Speech API — text chat still fully works.
- Git (optional) — the project is tracked in a Git repository; `.env`,
  `data/*.db`, and `.venv/` are excluded via `.gitignore`.

## Setup (Windows PowerShell)
```powershell
# From the project root:
py -m venv .venv                                   # create the virtual env
.\.venv\Scripts\pip.exe install -r requirements.txt  # Flask, dotenv, pytest, google-genai (Gemini, off by default)

# Optional: create your own local config (the defaults work without it)
Copy-Item .env.example .env
```

## Run the server
```powershell
.\.venv\Scripts\python.exe run.py
```
Then open **http://127.0.0.1:5000/** in your browser.
Health check: **http://127.0.0.1:5000/api/health** → `{"status": "ok"}`.

## Seed the sample database (first run)
`data/shop.db` is generated locally and is **not** tracked by Git. Create /
refresh it with the fictional sample orders:
```powershell
.\.venv\Scripts\python.exe -m app.seed
```

## Run the tests
**Python** (backend, API, frontend serving + contract):
```powershell
.\.venv\Scripts\python.exe -m pytest            # the full suite (currently 431 tests)
```
**JavaScript** (the simulated-call controller and its browser glue, headless —
no browser, no dependencies, no network):
```powershell
node tests/js/test_call_state.js               # pure state machine (23 tests)
node tests/js/test_call_glue.js                # browser half via a fake DOM + Web Speech + virtual clock (14 tests)
node --check static/js/call.js                 # syntax check (repeat for app.js, stt.js, tts.js)
```
The JS tests inject stub recognizers/TTS/fetch and a virtual clock, so they
exercise the voice-call **logic** (lifecycle & greeting, barge-in, turn
buffering, mute, end-call teardown, stale-response protection, the liveness
watchdog, the language switch, and the `/api/chat` request shape) — **not** a
real microphone. They are non-vacuous: reverting a fix makes the matching tests
fail.

The Python suite is fully **offline**: a conftest autouse fixture clears the Gemini
environment for every test, so no live Gemini/network call is ever made and no
test touches the real `data/shop.db` (each test gets a throwaway seeded database
via `tmp_path`). Coverage includes the deterministic assistant, order lookups,
the sample-FAQ answering across English/Urdu/Roman Urdu, greeting/farewell/
gratitude consistency with Gemini both ON and OFF, voice-vs-text equivalence at
the backend, the API contract, frontend-serving and frontend-contract checks, and
the Phase G3 security checks (request validation, JSON error handling, rate
limiting, and safe config defaults).

Full verification battery used to sign off each phase:
```powershell
.\.venv\Scripts\python.exe -m pytest                     # all Python tests
node tests/js/test_call_state.js                         # JS call state machine
node tests/js/test_call_glue.js                          # JS browser glue
.\.venv\Scripts\python.exe -m pip check                  # dependency integrity
.\.venv\Scripts\python.exe -W error::SyntaxWarning -m compileall -q app  # strict compile
node --check static/js/app.js                            # JS syntax (repeat for tts.js, stt.js, call.js)
```

> Automated checks never exercise a real browser microphone or audible speech, so
> **voice (STT/TTS) still needs manual browser testing** in Chrome/Edge (see the
> Voice section above); this is reported honestly rather than claimed as tested.

## Environment variables
All are read from a local `.env` (via `python-dotenv`) and have safe defaults,
so the app runs even without a `.env` file. **No secrets are committed.**

| Variable | Default | Purpose |
|---|---|---|
| `FLASK_PORT` | `5000` | Port for the local dev server |
| `FLASK_HOST` | `127.0.0.1` | Host to bind to — **loopback by default**, so a local run is never exposed on the LAN (override deliberately) |
| `FLASK_DEBUG` | `false` | Debug/auto-reload. **Off by default (safe for production)**; `run.py` (the local dev launcher) enables it unless you set `FLASK_DEBUG=false` |
| `MAX_CONTENT_LENGTH` | `16384` | Hard cap on any request body in bytes; an oversized body is rejected with `413` before the views run |
| `MAX_MESSAGE_LENGTH` | `500` | Cap on the `/api/chat` message length in characters; longer returns a `413` JSON error (`0` disables) |
| `RATE_LIMIT_ENABLED` | `true` | In-memory request throttling on `/api/chat` and `/api/orders` (**disabled in the test suite**) |
| `RATE_LIMIT_MAX` | `30` | Maximum requests per client per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Sliding-window length in seconds |
| `GEMINI_ENABLED` | `false` | Turn the optional Gemini layer on (default off) |
| `GEMINI_API_KEY` | *(unset)* | Secret — set only in local `.env`; never logged/committed |
| `GEMINI_MODEL` | `gemini-flash-latest` | Gemini model name (non-secret) |
| `GEMINI_TIMEOUT` | `10` | Per-request timeout in seconds (protects against a slow API) |

> Gemini is **off by default**: with `GEMINI_ENABLED=false` (or no key set) the
> app uses its deterministic assistant and makes no external call. To enable it,
> copy `.env.example` to `.env`, set `GEMINI_ENABLED=true`, and add your key to
> `GEMINI_API_KEY` **only in `.env`** (never commit it). The key is read from the
> environment at runtime and is never logged or sent to the frontend.

## API endpoints
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Liveness check → `{"status": "ok"}` |
| `GET` | `/api/orders/<order_id>` | Order lookup: **200** found (row from SQLite) / **404** honest not-found / **500** safe JSON on a DB error / **429** when rate-limited |
| `POST` | `/api/chat` | Chat: body `{"message": "..."}` → **200** `{intent, reply, source}`; **413** too long, **429** rate-limited (see below) |
| `GET` | `/` | The chat frontend |

**`/api/chat` contract.** Request `{"message": "<text>"}`; the response is always
`{"intent", "reply", "source"}`. Possible `intent` values: `order_status` (a
matched id, answered from SQLite), `order_status_missing_id` (order wording but
no id — asks for the number), `order_status_unclear` (an incomplete / ambiguous
spoken number — asks to repeat it), `faq` (shipping / returns / cancellation /
changes / payment), `greeting`, `farewell`, `gratitude`, `ambiguous` (a short
clarifying question), `empty`, and `fallback` (an honest "I'm not sure" — never
invented). The voice call posts this same endpoint and reads the same fields, so
voice and text share one contract.

**Errors & limits (Phase G3).** Every `/api` error is a consistent JSON object
`{"error": "...", "message": "..."}` with an appropriate status code, and a real
server error is logged server-side but **never** returned with a stack trace or
secret. Non-`/api` (frontend) requests keep Flask's normal HTML behaviour.

| Situation | Status | Body `error` |
|---|---|---|
| Empty / missing / non-string `message`, or a malformed JSON body | `200` | *(none — answered with the friendly `intent:"empty"` contract, deliberately unchanged)* |
| Chat message longer than `MAX_MESSAGE_LENGTH` | `413` | `message_too_long` |
| Request body larger than `MAX_CONTENT_LENGTH` | `413` | `payload_too_large` |
| Over the per-client rate limit (`/api/chat`, `/api/orders`) | `429` | `rate_limited` (with a `Retry-After` header) |
| Wrong HTTP method / unmatched `/api` route | `405` / `404` | `method_not_allowed` / `not_found` |
| SQLite lookup fails | `500` | `storage_error` |
| Any other unexpected server error | `500` | `internal_error` |

The frontend surfaces the friendly `message` for these errors; text chat keeps working.

## Voice — limitations & fallback (important)
- **Text chat always works** — voice is an enhancement, never a requirement.
- **STT** uses the browser Web Speech API. If it is unsupported (e.g. Firefox)
  or the microphone permission is denied, the mic button is disabled/ignored and
  a message prompts the user to type. Recognition also needs a reachable speech
  service and a secure context (local `http://127.0.0.1` counts as secure).
- **TTS** uses `speechSynthesis`. If unavailable or blocked, replies appear as
  text only — nothing breaks. Both the auto-spoken reply and the per-reply Speak
  button use the **selected voice language** (`en-US` / `ur-PK`), falling back to
  the browser default when no matching voice is installed.
- **Urdu (`ur-PK`)** recognition depends on a browser/OS voice pack that is
  missing on most desktop Chrome/Edge installs; where it is absent, recognition
  raises `language-not-supported` (or simply hears nothing) and the call says so
  instead of failing silently. **Roman Urdu cannot be recognized by speech at
  all** — it is a *written* form. Type Urdu or Roman Urdu in the text box (the
  backend reads both) rather than speaking it.
- **Simulated voice call** (`static/js/call.js`) shares the same language
  selector and `/api/chat`; changing the Voice language re-arms the running
  recognition immediately, and a liveness watchdog re-arms a microphone session
  that dies without an event. Greeting, barge-in, turn buffering, mute and
  end-of-call teardown are covered by the headless JS tests.
- **Local diagnostics (opt-in, dev only):** open the page with `?voicediag=1` to
  log pipeline stages (recognition start/result, buffer submit, request/response,
  state changes) to the browser console — lengths and codes only, never the
  words. Add `?voicetext=1` (a **separate** flag) to also print a short preview of
  the recognized text to the **console only**, to debug what the mic actually
  heard; run `__voiceDiag()` in the console to dump the recorded lines. Nothing is
  sent or stored, and neither flag should be used with real customers.
- **Not yet hardware-verified:** automated checks confirmed the controls exist
  and the text flow works, but **real microphone capture (STT) and audio playback
  (TTS) were not exercised** — test those manually in Chrome/Edge before relying
  on voice.

## Data, determinism & privacy
- **All sample orders are fictional** (fake names, IDs, and items — see
  `app/seed.py`). There is no real customer data.
- **Order status comes only from SQLite** (`app/orders.py`, used by `/api/chat`
  and `/api/orders`): the assistant reads the stored row and never fabricates a
  status, date, item, or quantity. Topic/policy answers come only from
  `data/faq.json`.
- **`data/faq.json` holds FICTIONAL demonstration sample policies**
  (`_meta.status = "DEMONSTRATION SAMPLE POLICIES …"`), activated so customers
  get direct, helpful answers instead of a "needs confirmation" notice. They are
  **not** a verified real-world shop policy: the sample label is carried in the
  response `source` (`faq_kb:<topic>:sample`) and stated in the UI footer, and a
  real business must replace them with its own approved policies. Policy wording
  is read **verbatim** from this file and is **never** invented (with Gemini ON,
  OFF, no key, or an API failure the answer still comes from this same file).
  `docs/faq_owner_questions.md` is the worksheet for collecting a real owner's
  approved answers.
- `.env`, the SQLite `*.db`, and `.venv/` are excluded from Git via `.gitignore`.

## Project layout
```
app/        Flask backend
  config.py       reads .env (port, debug, Gemini flag/model/timeout defaults)
  database.py     SQLite connection + schema
  seed.py         fictional sample orders
  orders.py       order lookups (source of truth for status)
  faq.py          FAQ lookup (source of truth for topic answers)
  gemini.py       optional, feature-flagged Gemini wrapper (off by default)
  assistant.py    intent router (deterministic + optional Gemini grounding)
  routes.py       REST API (/api/health, /api/orders, /api/chat)
  __init__.py     app factory + serves the frontend at "/"
static/     plain frontend (index.html, css/, js/: app.js, tts.js, stt.js, call.js)
data/       faq.json (fictional sample/demonstration policies) + generated shop.db (not tracked)
tests/      pytest suite (backend, API, frontend serving + contract) + js/ (headless Node tests for call.js)
run.py      start the dev server
```

## Security & deployment readiness
**Safe as-is for a local, single-user demo** (dev server bound to `127.0.0.1`,
fictional data, no real secrets). The following **must be addressed before any
public / production deployment**:

| Area | Current state (after Phase G3) | Remaining before a real production launch |
|---|---|---|
| **Secrets** | `GEMINI_API_KEY` is read only from the environment; never hardcoded, logged, returned, or committed; `.env` is git-ignored | Use a managed secret store; rotate keys; keep `.env` out of images/logs |
| **Debug mode** | **Off by default** (`FLASK_DEBUG=false`); only `run.py` (local loopback dev) turns it on, so a production `create_app()` never exposes the Werkzeug debugger | Ensure `FLASK_DEBUG` is unset/false in the deployed environment |
| **Transport** | Plain HTTP on `127.0.0.1` (loopback) | HTTPS is **mandatory** — the Web Speech API needs a secure context |
| **Server** | Flask dev server for local use | A production WSGI server (gunicorn/uWSGI) behind a reverse proxy |
| **Auth / authz** | None — endpoints are open (demo, fictional data) | Add authentication + per-customer authorization so a customer only ever sees **their own** order |
| **PII exposure** | `/api/orders/<id>` still returns the stored row (incl. `customer_name`) for **fictional** demo data, and order ids are sequential with no auth | Redact `customer_name`, use non-enumerable ids (or verify ownership), require auth on the raw order endpoint |
| **Rate limiting** | **Added**: in-memory sliding-window throttle on `/api/chat` and `/api/orders` (`429` + `Retry-After`) | It is per-process (not shared across workers/restart); move to a shared store (e.g. Redis) and add Gemini budget caps for real traffic |
| **Input validation** | **Added**: `MAX_CONTENT_LENGTH` body cap, `MAX_MESSAGE_LENGTH` cap, non-string `message` coercion, and consistent JSON errors; `/api/chat` still never 500s | Tune limits for real traffic; add schema validation if the API grows |
| **Error handling** | **Added**: JSON error handlers across `/api` (HTTP + unexpected `Exception`) that log server-side and return safe messages with proper status codes; DB-error guard on order lookup | Structured logging/monitoring and operator error alerting |
| **Persistence** | Single-file SQLite (`data/*.db`, git-ignored) with parameterized queries (no SQL injection) | A managed, backed-up database for concurrency and durability; define a backup policy |

## Known limitations & future improvements
**Limitations (honest):**
- **No real phone calling.** Despite the project name there is **no telephony /
  PSTN integration** — "calling" means the browser voice (STT/TTS) chat only.
- **Live Gemini is unverified.** The Gemini layer is fully **mocked in tests**
  and disabled by default; it has never been run against a real key here.
- **Voice hardware is unverified.** Real microphone input and audio output have
  not been exercised (see the Voice section above).
- **Spoken Urdu / Roman Urdu recognition is unreliable.** Browser STT has no
  Roman-Urdu speech model and usually no `ur-PK` voice pack on desktop, so Urdu
  spoken aloud may not be recognized; English speech and *typed* Urdu / Roman
  Urdu work. This is a browser limitation, not something the backend can force.
- **FAQ policies are FICTIONAL demonstration samples**, not owner-approved
  real-world policy. They are answered directly but labelled as sample data (UI
  footer + `source`); replace them before any real customer use.
- **Offline (Gemini-off) replies stay in English.** Multilingual *detection*
  covers order-status and the five policy topics across English, Urdu script and
  Roman Urdu, but the offline fixed replies — and translating an answer into
  Urdu — depend on Gemini being enabled (see the Multilingual section above).
- **Rate limiting is in-memory and per-process (Phase G3).** Throttling counters
  live in one Python process and are **not** shared across multiple workers or
  server restarts, so behind a multi-process deployment the effective global
  limit scales with the number of processes. That is fine for this single-process
  loopback demo; a real deployment needs a shared store (e.g. Redis).
- **Order ids are sequential and there is no auth.** A caller could enumerate
  `ORD-1001`, `ORD-1002`, … and the raw `/api/orders/<id>` endpoint returns the
  stored `customer_name`. Harmless for this **fictional** demo dataset, but it
  **must** be changed (non-enumerable ids / ownership check / auth) before any
  real customer data is used.

**Future improvements:**
- Replace the demonstration sample policies in `data/faq.json` with a real
  owner-approved policy set (worksheet: `docs/faq_owner_questions.md`) and add
  Urdu / Roman Urdu answer text / keywords.
- Add optional real telephony (e.g. a Twilio / voice-studio integration) as a
  genuinely separate phase.
- (G3 already shipped in-memory rate limiting, request-size limits, and whole-API
  JSON error handlers.) For production: move rate limiting to a shared store
  (Redis), add authentication + per-customer authorization, and add a managed,
  backed-up database.
- Add a real (opt-in) browser end-to-end suite (e.g. Playwright) behind an
  explicit dependency decision.
- Optional cloud STT/TTS for broader language coverage beyond the browser.

## Notes
- The dev server binds to `127.0.0.1` (local only). For any real deployment you
  would use a production WSGI server and HTTPS (the Web Speech API needs a
  secure context) — see
  [Security & deployment readiness](#security--deployment-readiness).
