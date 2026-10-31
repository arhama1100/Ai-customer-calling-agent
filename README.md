# AI Customer Calling Agent

A beginner-friendly customer-service "calling" agent for a **fictional** shop.
A customer types (or speaks) a question; the assistant answers **only** from
deterministic, local sources — order status from a SQLite database and topic
answers from a sample FAQ file. It never invents an answer, and it always
falls back to honest text chat when voice features are unavailable.

> Status: **Phases 0–6, the Gemini integration phases (B1–B4), the
> hardening/multilingual phases (C–E.1), and the whole of Phase G — G1 (farewell
> consistency with Gemini on/off), G2 (activated sample FAQ policies +
> multilingual answering) and G3 (security hardening: request validation, safe
> JSON error handling, rate limiting, and production-safe configuration) — are
> complete** — a Flask API, SQLite orders, an FAQ of **demonstration sample
> policies**, a deterministic assistant, a plain HTML/CSS/JS frontend with browser
> STT/TTS, and an **optional Gemini natural-language layer that is implemented but
> OFF by default** (`GEMINI_ENABLED=false`). The current suite is **286 passing
> tests** (verified), and the **text-chat flow was confirmed end-to-end in a real
> browser** in an earlier phase. Gemini only classifies intent/language and
> rephrases our own approved text; order status still comes **only** from SQLite,
> policy answers come **only** from the sample FAQ (fictional demonstration data,
> clearly labelled, never invented), and every Gemini interaction in the tests is
> mocked (no live API call has been made). The new G3 security limits (rate
> limiting, body/message-size caps, JSON error handlers) are covered by automated
> tests, but **real microphone/audio behaviour and live Gemini remain unverified**
> — see [Known limitations](#known-limitations--future-improvements).

## Features
- **Text chat** that always works (the primary, guaranteed path).
- **Deterministic intent routing**: order id → SQLite, topic → FAQ, else an
  honest "I'm not sure" fallback. No fabricated order statuses or policies.
- **Sample policy answers**: a policy question (shipping, returns, cancellation,
  changes, payment) returns the matching answer from `data/faq.json`. These are
  **fictional demonstration policies**, clearly labelled in the UI footer and in
  the response `source` (`faq_kb:<topic>:sample`) — a real business must replace
  them with its own approved policies before customer use.
- **Greetings & farewells**: recognizes a greeting ("hi", "assalam o alaikum")
  and a farewell ("bye", "goodbye", "see you later", "Allah Hafiz", "Khuda
  Hafiz", "okay bye") with a friendly fixed reply — **identically whether Gemini
  is enabled or disabled** — while avoiding false positives (a bare "thank you",
  or "goodbye" buried inside a longer sentence, is *not* treated as leaving).
- **Voice input (STT)** via the browser Web Speech API (English / Urdu).
- **Spoken replies (TTS)** via `speechSynthesis`, with a user toggle.
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
- **Greetings & farewells** are recognized in English and Roman/Urdu forms
  (e.g. "khuda hafiz", "allah hafiz", "see you later") and answered with the
  same fixed reply whether Gemini is enabled or disabled.
- **Policy questions** are detected across English, Urdu script and Roman Urdu
  (keyword + cue matching) and routed to the right sample topic; an ambiguous
  "policy" ask with no single clear topic gets a short clarification question
  rather than a guess.
- **Current limitations (honest):**
  - Offline, a detected policy topic returns its **English** sample answer from
    `data/faq.json` (the wording is never invented). Urdu-script *shipping*
    wording is not yet auto-detected; ask shipping in English/Roman Urdu, or use
    Gemini for broader free-form coverage.
  - **Reply localization** into Urdu needs Gemini ON; offline fixed replies
    (greeting, farewell, the order-id request, clarification, and the sample
    policy text) stay in English (they can still be *spoken* in the selected
    language if the browser has that voice).

## Technology stack
| Layer | Choice |
|---|---|
| Backend | Python + Flask (REST API, app-factory pattern) |
| Database | SQLite (Python's built-in `sqlite3` — no extra install) |
| Frontend | Plain HTML / CSS / JavaScript (no framework, no build step) |
| Speech (browser) | Web Speech API (`SpeechRecognition`) + `speechSynthesis` |
| Tests | pytest |
| Optional (off by default) | Gemini layer via `google-genai` (`app/gemini.py`) — grounded, feature-flagged, mocked in tests |

## Architecture
A single request flows through clearly separated layers, and each layer keeps a
hard rule so no answer is ever invented:

```
browser (index.html + app.js + stt.js + tts.js)
      |  fetch POST /api/chat  {message}
      v
Flask API (app/routes.py)  -- validation & guards (Phase G3):
      |   body-size cap -> 413, message-length cap -> 413,
      |   rate limiter (app/rate_limit.py) -> 429, DB-error + JSON error handlers
      v
Deterministic assistant (app/assistant.py)  -- routes by intent:
      |- order id present  ------------> SQLite  (app/orders.py)  [source of truth]
      |- policy topic      ------------> sample FAQ (data/faq.json via app/faq.py)
      |- greeting / farewell / ambiguous -> fixed, friendly replies
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
- Git is optional (a repo was initialized locally; nothing is pushed).

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
```powershell
.\.venv\Scripts\python.exe -m pytest            # the full suite (currently 286 tests)
```
The suite is fully **offline**: a conftest autouse fixture clears the Gemini
environment for every test, so no live Gemini/network call is ever made and no
test touches the real `data/shop.db` (each test gets a throwaway seeded database
via `tmp_path`). Coverage includes the deterministic assistant, order lookups,
the sample-FAQ answering across English/Urdu/Roman Urdu, farewell/greeting
consistency with Gemini both ON and OFF, the API contract, frontend-serving and
frontend-contract checks, and the Phase G3 security checks (request validation,
JSON error handling, rate limiting, and safe config defaults).

Full verification battery used to sign off each phase:
```powershell
.\.venv\Scripts\python.exe -m pytest                     # all tests
.\.venv\Scripts\python.exe -m pip check                  # dependency integrity
.\.venv\Scripts\python.exe -W error::SyntaxWarning -m compileall -q app  # strict compile
node --check static/js/app.js                            # JS syntax (repeat for tts.js, stt.js)
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
- **Urdu (`ur-PK`)** recognition depends on a browser/OS voice pack; where it is
  missing, recognition may fall back or error, but the app stays usable.
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
static/     plain frontend (index.html, css/, js/ with app/tts/stt)
data/       faq.json (fictional sample/demonstration policies) + generated shop.db (not tracked)
tests/      pytest suite (backend, API, frontend serving + contract)
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
