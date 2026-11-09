# AI Customer Calling Agent

A customer-service demo agent for a **fictional** shop: customers type or
speak, and the assistant answers only from local, deterministic sources —
order status from PostgreSQL and policies from a sample FAQ file — never inventing
an answer.

![Python](https://img.shields.io/badge/python-3.x-blue)
![Flask](https://img.shields.io/badge/flask-REST-lightgrey)
![Tests](https://img.shields.io/badge/tests-536%20Python%20%C2%B7%2047%20JS-brightgreen)
![Gemini](https://img.shields.io/badge/Gemini-optional%2C%20off%20by%20default-orange)

> **Not a phone system.** Despite the name, there is no telephony — "calling"
> means a simulated voice call in the browser. This is a mentor-guided
> internship project, not production-ready software
> ([details](#limitations-and-security)).

## Contents

[Overview](#overview) · [Features](#features) · [How it works](#how-it-works) ·
[Voice call and session recording](#voice-call-and-session-recording) ·
[Tech stack](#tech-stack) · [Quick start](#quick-start) ·
[Run tests](#run-tests) · [Configuration](#configuration) ·
[API reference](#api-reference) · [Limitations and security](#limitations-and-security) ·
[Manual testing](#manual-testing-browser-microphone--speaker) ·
[Project structure](#project-structure) · [Future improvements](#future-improvements) ·
[License](#license)

## Overview

A Flask + Neon PostgreSQL backend serves a plain HTML/CSS/JS frontend where a virtual
shop assistant tracks orders and answers policy questions. The same
`/api/chat` endpoint powers both text chat and a browser voice-call mode
(speech recognition + synthesis via the Web Speech API). An optional Gemini
layer can classify intent and rephrase approved text — it is **off by
default**, and it can never fetch or fabricate an order or policy fact. All
shop data, policies, and sample numbers are fictional demonstration content.

## Features

- **Text chat** that always works — the primary, guaranteed path.
- **Simulated voice calls** — hands-free mode with spoken greeting, barge-in,
  digit-by-digit order-number buffering, mute, and graceful fallback to text.
- **Live call transcript** — every typed and recognized message and every
  Nova AI reply appear on screen in order, with clear "You" / "Nova AI" labels;
  voice replies are shown too, and a duplicate recognition callback never
  double-records a turn.
- **Consented session records** — a simulated call can be stored locally (a
  unique session id, start/end time, the transcript, detected intents, an
  honest outcome — `resolved` / `unresolved` / `escalated` / `no_conversation`
  — a concise summary, and a human-follow-up flag). **Recording is opt-in:**
  with consent declined nothing is persisted and the call still works.
- **Staff history view** — recent sessions with date, time, intent, outcome and
  summary; the list deliberately omits the raw transcript.
- **Order tracking** — status answers come only from the PostgreSQL database.
- **Sample FAQs** — shipping, returns, cancellation, changes, payment, answered
  verbatim from `data/faq.json` (fictional demonstration policies).
- **Multilingual typed input** — English, Urdu script, and Roman Urdu;
  greetings, farewells, and gratitude ("Thank you" / "Shukriya") get friendly
  fixed replies.
- **Optional Gemini integration** — feature-flagged, mocked in every test,
  never a source of facts.
- **Hardened local API** — body/message-size caps, JSON error handlers, and an
  in-memory rate limiter.

## How it works

```
Browser UI ──▶ Flask API ──▶ Assistant (intent router)
(text chat +                  │
 voice call ── same ──────────┤
 /api/chat ──────────────────▶├── order id ─────▶ PostgreSQL (source of truth)
                              ├── policy topic ──▶ sample FAQ (data/faq.json)
                              ├── greeting / farewell / gratitude / fallback
                              └── (optional) Gemini: classify + rephrase only
                                        ▼
                              {intent, reply, source}
```

Every reply carries a `source` field (e.g. `orders_db`, `faq_kb:returns:sample`,
`fixed`, `none`), keeping the data lineage auditable.

## Voice call and session recording

The voice call is a **browser simulation**, not a telephone call. The pipeline
(`static/js/stt.js`, `static/js/tts.js`, `static/js/call.js`) works like this:

- **Voice input (STT)** — the browser's `SpeechRecognition` transcribes speech
  into a customer turn. It requests the microphone permission, supports
  `en-US` and `ur-PK` where the browser provides them, ignores empty or
  whitespace-only results, and never submits a recognized turn twice. If
  recognition is unsupported, denied, or errors out, the UI says so and
  **typing remains a full fallback**.
- **Assistant** — the recognized (or typed) text is sent to the same
  `/api/chat` endpoint that text chat uses; the reply is the deterministic,
  grounded answer described above.
- **Voice output (TTS)** — `speechSynthesis` speaks Nova's reply without
  overlapping prior speech, and stops when the call ends. Browsers without
  `speechSynthesis` still see every reply in the on-screen transcript.
- **Call states** — the controller shows clear **Listening / Processing /
  Speaking** states and keeps only one recognition session active at a time.

When a call ends, a session **record** can be saved. The transcript and the
record are created only after the customer ticks the consent box (the server
additionally requires a strict `consent: true`); the stored record holds the
turn-by-turn transcript, detected intents, an honest outcome
(`resolved` / `unresolved` / `escalated` / `no_conversation`), a concise
summary, and a human-follow-up flag. Nothing is stored without consent. All
records live in Neon PostgreSQL in deployed use. The legacy local SQLite file
can be migrated with the included utility. There is no real telephony or live
human transfer.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python + Flask (app-factory pattern) |
| Database | Neon PostgreSQL via Psycopg 3 and a bounded connection pool |
| Hosting | Vercel Python Function; static assets served from its CDN |
| Frontend | Plain HTML / CSS / JS — no framework, no build step |
| Speech | Browser Web Speech API (`SpeechRecognition` + `speechSynthesis`) |
| Optional NLP | Gemini via `google-genai`, off by default (`GEMINI_ENABLED=false`) |
| Tests | pytest + dependency-free Node scripts for the voice-call JS |

## Quick start

Prerequisites: **Python 3.x** (developed on 3.14) and a modern browser —
**Chrome or Edge** for voice features; text chat works everywhere.

```powershell
# From the project root
py -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt

# Optional for a new demo database: this inserts six fictional orders.
# It is a manual step; the app never seeds data automatically.
.\.venv\Scripts\python.exe -m app.seed

# Start the server (binds to 127.0.0.1:5000)
.\.venv\Scripts\python.exe run.py
```

Open **http://127.0.0.1:5000/** — health check at `/api/health` returns
`{"status": "ok"}`. Database-backed features require `DATABASE_URL` in the
process environment (see [Configuration](#configuration)).

Try it: ask *“What is the status of order ORD-1002?”*, *“What is your return
policy?”*, or *“Thank you”*.

## Run tests

```powershell
.\.venv\Scripts\python.exe -m pytest        # fully offline; SQLite test adapter
node tests/js/test_call_state.js             # voice-call state machine
node tests/js/test_call_glue.js              # browser glue via a fake DOM
```

The Python suite never makes a network call (Gemini is stubbed in every test)
and each test runs against a throwaway database. The JS tests exercise the
voice-call logic headlessly against a mocked browser — **they do not verify a
real microphone or speaker.** Real mic/audio behaviour still needs manual
testing in Chrome/Edge (see below).

## Configuration

Settings come from environment variables; see [.env.example](.env.example) for
placeholders. For local development, python-dotenv loads `.env` if present;
Vercel supplies variables through Project Settings. Set `DATABASE_URL` in your
shell locally or in Vercel's Environment Variables for deployed use.
In PowerShell, for the current terminal session, use `$env:DATABASE_URL = "<Neon pooled URL>"`.

- **Neon:** create a PostgreSQL project and use its pooled connection string
  (the hostname contains `-pooler`). Set it as `DATABASE_URL`; SSL is required
  by the application. Never commit the real URL.
- **Migration:** install dependencies, set `DATABASE_URL`, then run
  `\.venv\Scripts\python.exe scripts\migrate_sqlite_to_neon.py`. It reads
  `data/shop.db` in read-only mode, copies both tables in one transaction,
  avoids replacing existing destination rows, and verifies every column and
  row count before declaring success. It never deletes the source file.
- **Schema/sample data:** `python -m app.seed` initializes both tables and
  upserts the six fictional orders; it does not run automatically at app
  startup. Do not run it against production unless those demo orders are
  wanted there.

- **Gemini (optional, off by default):** to enable free-form phrasing and reply
  translation, set `GEMINI_ENABLED=true` and `GEMINI_API_KEY` in your process
  environment locally or in the hosting provider. The key is read from the
  environment at runtime and is never logged, committed, or sent to the
  frontend. With the flag off, the deterministic assistant handles everything
  and no external call is made.
- Server (`FLASK_PORT`, `FLASK_HOST`, `FLASK_DEBUG`), input caps
  (`MAX_CONTENT_LENGTH`, `MAX_MESSAGE_LENGTH`) and rate limiting
  (`RATE_LIMIT_*`) have safe defaults defined in `app/config.py` and need no
  setup for local use.

## API reference

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Chat frontend |
| `GET` | `/api/health` | Liveness → `{"status": "ok"}` |
| `GET` | `/api/orders/<order_id>` | Order lookup from PostgreSQL |
| `POST` | `/api/chat` | Chat endpoint used by text chat **and** the voice call |
| `POST` | `/api/session/start` | Open a **consented** simulated-call record (`{"consent": true, "channel": "voice"\|"text"}`); without consent returns `session_id: null` and stores nothing |
| `POST` | `/api/session/<id>/end` | Close a session with its turns → sanitized transcript, intents, honest outcome + summary |
| `GET` | `/api/sessions` | Staff history — metadata + summary only, no raw transcript |
| `GET` | `/api/session/<id>` | One full consented session record, transcript included |

`/api/chat` request/response:

```json
{ "message": "What is the status of order ORD-1002?" }
```

```json
{
  "intent": "order_status",
  "reply": "Order ORD-1002 for 1 x Desk Lamp is currently shipped.",
  "source": "orders_db"
}
```

`intent` is one of: `order_status`, `order_status_missing_id`,
`order_status_unclear`, `business_hours`, `faq`, `human_agent_request`,
`greeting`, `farewell`, `gratitude`, `ambiguous`, `empty`, `fallback` (an
honest "I'm not sure" — never invented). `human_agent_request` returns the
honest handoff wording — it never claims a live transfer or that anyone was
notified.

<details>
<summary>Errors and limits (Phase G3)</summary>

Every `/api` error is a JSON object `{"error": "...", "message": "..."}` — no
stack traces or secrets, ever.

| Situation | Status | `error` |
|---|---|---|
| Chat message longer than `MAX_MESSAGE_LENGTH` | 413 | `message_too_long` |
| Body larger than `MAX_CONTENT_LENGTH` | 413 | `payload_too_large` |
| Over the per-client rate limit | 429 | `rate_limited` (+ `Retry-After`) |
| Wrong method / unmatched `/api` route | 405 / 404 | `method_not_allowed` / `not_found` |
| PostgreSQL lookup fails | 500 | `storage_error` |
| Any other server error | 500 | `internal_error` |

An empty, missing, or malformed message is deliberately *not* an error: it
returns the friendly `intent: "empty"` contract with status 200.

</details>

## Limitations and security

- **Fictional data only** — sample orders and FAQ policies are demonstration
  content, labelled in the UI and in each response's `source`; replace them
  before any real use.
- **No real telephony** — browser voice simulation only, no PSTN integration.
- **Human-agent transfer is simulated** — when a customer asks for a human, the
  honest handoff reply is returned and the request is flagged "requires staff
  follow-up" in the session record. **No real agent is notified or contacted
  and no support phone number or email is invented.**
- **Recording is consent-gated** — a transcript/session record is stored only
  when the customer ticks the consent box (the server also requires a strict
  `consent: true`). If consent is declined nothing is persisted and the call
  still works. Records live in Neon PostgreSQL in deployed use.
- **Speech recognition is browser-dependent** — English speech works in
  Chrome/Edge; **spoken Urdu/Roman Urdu is unreliable or unavailable** (no
  Roman-Urdu speech model exists, and `ur-PK` voice packs are usually missing
  on desktop). *Typing* English, Urdu script or Roman Urdu all work. Text chat
  is the guaranteed fallback.
- **Live Gemini unverified** — implemented and fully mocked in tests, but never
  run against a real key.
- **Not production-ready** — no authentication or authorization; safe as a
  local, loopback, single-user demo only.

<details>
<summary>Security & deployment readiness (before any public launch)</summary>

| Area | Current state | Remaining for production |
|---|---|---|
| Secrets | `GEMINI_API_KEY` read only from the environment; `.env` git-ignored | Managed secret store, key rotation |
| Debug mode | Off by default; only the local dev launcher enables it | Keep `FLASK_DEBUG` false when deployed |
| Transport | Plain HTTP on `127.0.0.1` | HTTPS (also required by the Web Speech API) |
| Server | Flask dev server | WSGI server behind a reverse proxy |
| Auth / PII | None; `/api/orders/<id>` returns the stored row and ids are sequential | Auth + per-customer ownership checks; redact `customer_name`; non-enumerable ids |
| Rate limiting | In-memory, per-process; resets on restart and is not shared across Vercel instances | Shared persistent store (e.g. Redis/Upstash), Gemini budget caps |
| Persistence | Neon PostgreSQL with parameterized queries | Backups, monitoring, and migration rehearsal |

</details>

<details>
<summary>Voice diagnostics (local development only)</summary>

Open the page with `?voicediag=1` to log voice-pipeline stages (lengths and
codes only, never words) to the browser console, and add the separate
`?voicetext=1` flag to also preview what the microphone actually recognized.
Run `__voiceDiag()` in the console to dump the recorded lines. Nothing is sent
or stored; neither flag is meant for real customers. Full behaviour notes:
`static/js/call.js`.

</details>

## Manual testing (browser microphone / speaker)

The automated JS suites run against a **mocked** browser and cannot prove real
audio behaviour. To verify a simulated voice call for yourself:

1. Start the server (`.\.venv\Scripts\python.exe run.py`) and open
   **http://127.0.0.1:5000/** in **Chrome or Edge** (the Web Speech API needs a
   secure/loopback origin and mic permission).
2. In the call panel, allow the microphone when prompted. Speak after the
   greeting; the Listening / Processing / Speaking states and the on-screen
   transcript should update, and Nova's reply should be spoken.
3. Toggle the **recording consent** checkbox: with it off, the status says the
   call is not stored; with it on, ending the call adds a row to the **History**
   section (date, time, intent, outcome, summary).
4. Ask for a human agent to confirm the honest handoff reply and the
   "requires staff follow-up" label — no real person is contacted.

If your browser lacks speech recognition or synthesis, the call degrades to a
working text-only session; that path is covered by the automated tests.

## Project structure

```
app/        Flask backend (assistant, routes, orders, sessions, FAQ, Gemini, config)
static/     Frontend — index.html, css/, js/ (app.js, stt.js, tts.js, call.js, sessions.js)
data/       faq.json (unchanged sample policies) + legacy shop.db migration source (ignored)
tests/      pytest suite + js/ (headless Node tests for the voice call)
docs/       FAQ owner-questions worksheet for collecting real policies
run.py      Local dev server launcher
index.py    Vercel WSGI entry point
scripts/    PostgreSQL schema setup and SQLite-to-Neon migration commands
```

Session records are handled by `app/sessions.py` (schema, honest outcome and
summary derivation, consent gating) exposed through the four `/api/session*`
endpoints, and rendered in the staff History panel by `static/js/sessions.js`.

## Vercel deployment

Vercel detects the WSGI app exposed by the root index.py entry point. Its build
step runs build.py, which copies the existing static frontend into public/static
for Vercel's CDN. No database connection or sample-data seed runs at startup.

1. Create a Neon PostgreSQL project and copy its pooled connection string from
   the Neon dashboard. Use the hostname containing -pooler and keep SSL enabled.
2. Import this repository as a Vercel project. Vercel detects Flask and reads
   requirements.txt plus pyproject.toml.
3. In Vercel Project Settings → Environment Variables, add DATABASE_URL for
   Preview and Production (and Development if you use Vercel CLI). Paste the
   Neon pooled URL directly into Vercel; never commit it.
4. To migrate existing local data, set DATABASE_URL in your local shell and run
   scripts/migrate_sqlite_to_neon.py from the project root. The utility reads
   data/shop.db read-only and reports exact verified row counts for orders and
   call_sessions. A second run is safe and also checks every copied field.
5. For a new empty Neon database instead, run scripts/init_postgres.py. This
   creates the schema but inserts no orders. Seed fictional orders manually
   only when you explicitly want demo data.
6. Redeploy on Vercel after setting its variable. Check /api/health and test an
   order lookup and session flow. The live Neon migration/connectivity check
   must be completed with your Neon URL; the offline test suite cannot prove
   access to your cloud database.

The limiter remains in memory, so Vercel instances do not share counts and
counts reset when an instance restarts. For production throttling, use a shared
persistent service such as Upstash Redis.

## Future improvements

- Replace demonstration policies with an owner-approved set
  (worksheet: `docs/faq_owner_questions.md`).
- Real telephony (e.g. Twilio) as a genuinely separate phase.
- Authentication + per-customer authorization and a shared rate-limit store.
- Cloud STT/TTS for broader spoken-language coverage.
- A real browser end-to-end suite (e.g. Playwright).

## License

No license file is currently included in this repository. If you plan to open
source it, add a `LICENSE` (and update this section) before publication.
