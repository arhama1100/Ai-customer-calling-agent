# AI Customer Calling Agent

A customer-service demo agent for a **fictional** shop: customers type or
speak, and the assistant answers only from local, deterministic sources —
order status from SQLite and policies from a sample FAQ file — never inventing
an answer.

![Python](https://img.shields.io/badge/python-3.x-blue)
![Flask](https://img.shields.io/badge/flask-REST-lightgrey)
![Tests](https://img.shields.io/badge/tests-431%20Python%20%C2%B7%2037%20JS-brightgreen)
![Gemini](https://img.shields.io/badge/Gemini-optional%2C%20off%20by%20default-orange)

> **Not a phone system.** Despite the name, there is no telephony — "calling"
> means a simulated voice call in the browser. This is a mentor-guided
> internship project, not production-ready software
> ([details](#limitations-and-security)).

## Contents

[Overview](#overview) · [Features](#features) · [How it works](#how-it-works) ·
[Tech stack](#tech-stack) · [Quick start](#quick-start) ·
[Run tests](#run-tests) · [Configuration](#configuration) ·
[API reference](#api-reference) · [Limitations and security](#limitations-and-security) ·
[Project structure](#project-structure) · [Future improvements](#future-improvements) ·
[License](#license)

## Overview

A Flask + SQLite backend serves a plain HTML/CSS/JS frontend where a virtual
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
- **Order tracking** — status answers come only from the SQLite database.
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
 /api/chat ──────────────────▶├── order id ─────▶ SQLite (source of truth)
                              ├── policy topic ──▶ sample FAQ (data/faq.json)
                              ├── greeting / farewell / gratitude / fallback
                              └── (optional) Gemini: classify + rephrase only
                                        ▼
                              {intent, reply, source}
```

Every reply carries a `source` field (e.g. `orders_db`, `faq_kb:returns:sample`,
`fixed`, `none`), keeping the data lineage auditable.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python + Flask (app-factory pattern) |
| Database | SQLite (built-in `sqlite3`) |
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

# First run only: create the local database with fictional sample orders
.\.venv\Scripts\python.exe -m app.seed

# Start the server (binds to 127.0.0.1:5000)
.\.venv\Scripts\python.exe run.py
```

Open **http://127.0.0.1:5000/** — health check at `/api/health` returns
`{"status": "ok"}`. A `.env` file is optional; all defaults work without it
(see [Configuration](#configuration)).

Try it: ask *“What is the status of order ORD-1002?”*, *“What is your return
policy?”*, or *“Thank you”*.

## Run tests

```powershell
.\.venv\Scripts\python.exe -m pytest        # 431 Python tests (fully offline)
node tests/js/test_call_state.js            # 23 — voice-call state machine
node tests/js/test_call_glue.js             # 14 — browser glue via a fake DOM
```

The Python suite never makes a network call (Gemini is stubbed in every test)
and each test runs against a throwaway database. The JS tests exercise the
voice-call logic headlessly — **real microphone/audio behaviour still needs
manual testing in Chrome/Edge.**

## Configuration

All settings come from environment variables (optionally via a local `.env`,
never committed) and have safe defaults — see [.env.example](.env.example).

- **Gemini (optional, off by default):** to enable free-form phrasing and reply
  translation, copy `.env.example` to `.env`, set `GEMINI_ENABLED=true` and put
  your key in `GEMINI_API_KEY` — locally only. The key is read from the
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
| `GET` | `/api/orders/<order_id>` | Order lookup from SQLite |
| `POST` | `/api/chat` | Chat endpoint used by text chat **and** the voice call |

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
`order_status_unclear`, `faq`, `greeting`, `farewell`, `gratitude`,
`ambiguous`, `empty`, `fallback` (an honest "I'm not sure" — never invented).

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
| SQLite lookup fails | 500 | `storage_error` |
| Any other server error | 500 | `internal_error` |

An empty, missing, or malformed message is deliberately *not* an error: it
returns the friendly `intent: "empty"` contract with status 200.

</details>

## Limitations and security

- **Fictional data only** — sample orders and FAQ policies are demonstration
  content, labelled in the UI and in each response's `source`; replace them
  before any real use.
- **No real telephony** — browser voice simulation only, no PSTN integration.
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
| Rate limiting | In-memory, per-process | Shared store (e.g. Redis), Gemini budget caps |
| Persistence | Single-file SQLite, parameterized queries | Managed, backed-up database |

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

## Project structure

```
app/        Flask backend (assistant, routes, orders, FAQ, Gemini, config)
static/     Frontend — index.html, css/, js/ (app.js, stt.js, tts.js, call.js)
data/       faq.json (fictional sample policies) + generated shop.db (not tracked)
tests/      pytest suite + js/ (headless Node tests for the voice call)
docs/       FAQ owner-questions worksheet for collecting real policies
run.py      Local dev server launcher
```

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
