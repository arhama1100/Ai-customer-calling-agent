# AI Customer Calling Agent

A beginner-friendly customer-service "calling" agent for a **fictional** shop.
A customer types (or speaks) a question; the assistant answers **only** from
deterministic, local sources — order status from a SQLite database and topic
answers from a sample FAQ file. It never invents an answer, and it always
falls back to honest text chat when voice features are unavailable.

> Status: **Phases 0–6 plus the Gemini integration phases (B1–B4) are complete**
> — Flask API, SQLite orders, a DRAFT FAQ, a deterministic assistant, a plain
> HTML/CSS/JS frontend with browser STT/TTS, and an **optional Gemini
> natural-language layer that is implemented but OFF by default**
> (`GEMINI_ENABLED=false`). The current suite is **156 passing tests** (verified),
> and the **text-chat flow was confirmed end-to-end in a real browser**. Gemini
> only classifies intent/language and rephrases our own approved text; order
> status still comes **only** from SQLite, unapproved FAQ policies are **never**
> quoted as confirmed fact, and every Gemini interaction in the tests is mocked
> (no live API call has been made). Real microphone/audio output and live Gemini
> behaviour have **not** been verified — see
> [Known limitations](#known-limitations--future-improvements).

## Features
- **Text chat** that always works (the primary, guaranteed path).
- **Deterministic intent routing**: order id → SQLite, topic → FAQ, else an
  honest "I'm not sure" fallback. No fabricated order statuses or policies.
- **Voice input (STT)** via the browser Web Speech API (English / Urdu).
- **Spoken replies (TTS)** via `speechSynthesis`, with a user toggle.
- **Graceful degradation**: unsupported browser, denied mic, no speech, or a
  speech-service error each show a friendly message; text chat keeps working.
- **Modern, responsive blue-and-white UI** built with plain HTML/CSS/JS.
- **Automated tests** with pytest (backend, API, frontend-serving, and
  frontend-contract checks).

## Multilingual support (English / Urdu / Roman Urdu)
Customers can ask in **English**, **Urdu script** (e.g. "میرا آرڈر کہاں ہے") and
**Roman Urdu** (e.g. "mera order kahan hai").

- **Order status:** an order id (`ORD-1002`) is matched reliably from any of the
  three forms and answered from SQLite; Urdu/Roman wording *without* an id asks
  for the order number instead of guessing.
- **Free-form / mixed phrasing:** when Gemini is enabled it classifies the intent
  and language and rephrases our own approved replies into the customer's
  language.
- **Current limitations (honest):**
  - The **offline** (Gemini-off) policy matcher uses **English keywords only**,
    so an Urdu/Roman *policy* question falls through to the safe fallback rather
    than a policy answer — no wrong policy is ever produced.
  - **Reply localization** into Urdu needs Gemini ON; offline fixed replies stay
    in English (they can still be *spoken* in the selected language if the
    browser has that voice).

## Technology stack
| Layer | Choice |
|---|---|
| Backend | Python + Flask (REST API, app-factory pattern) |
| Database | SQLite (Python's built-in `sqlite3` — no extra install) |
| Frontend | Plain HTML / CSS / JavaScript (no framework, no build step) |
| Speech (browser) | Web Speech API (`SpeechRecognition`) + `speechSynthesis` |
| Tests | pytest |
| Optional (off by default) | Gemini layer via `google-genai` (`app/gemini.py`) — grounded, feature-flagged, mocked in tests |

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
.\.venv\Scripts\python.exe -m pytest
```

## Environment variables
All are read from a local `.env` (via `python-dotenv`) and have safe defaults,
so the app runs even without a `.env` file. **No secrets are committed.**

| Variable | Default | Purpose |
|---|---|---|
| `FLASK_PORT` | `5000` | Port for the local dev server |
| `FLASK_DEBUG` | `true` | Enable auto-reload (development only) |
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
| `GET` | `/api/health` | Liveness check |
| `GET` | `/api/orders/<order_id>` | Order lookup (200 found / 404 honest not-found) |
| `POST` | `/api/chat` | Chat: body `{"message": "..."}` → `{intent, reply, source}` |
| `GET` | `/` | The chat frontend |

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
- **`data/faq.json` contains DRAFT, placeholder wording** for a fictional shop
  (`_meta.status = "DRAFT — PENDING OWNER APPROVAL"`) and is **not** an approved
  real-world policy. Until the owner approves it, a policy question (shipping,
  returns, cancellation, changes, payment) returns a **"needs confirmation —
  please contact support"** notice and the draft wording is **never** presented
  as a confirmed answer — in **every** mode (Gemini ON, OFF, no key, or an API
  failure). See `docs/faq_owner_questions.md` for the owner worksheet.
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
data/       faq.json (draft FAQ) + generated shop.db (not tracked)
tests/      pytest suite (backend, API, frontend serving + contract)
run.py      start the dev server
```

## Security & deployment readiness
**Safe as-is for a local, single-user demo** (dev server bound to `127.0.0.1`,
fictional data, no real secrets). The following **must be addressed before any
public / production deployment**:

| Area | Current state (local demo) | Required before production |
|---|---|---|
| **Secrets** | `GEMINI_API_KEY` is read only from the environment; never hardcoded, logged, returned, or committed; `.env` is git-ignored | Keep keys out of source; use a managed secret store; rotate keys |
| **Debug mode** | `FLASK_DEBUG` defaults to **on** (reloader + interactive debugger) | Set `FLASK_DEBUG=false`; never expose the Werkzeug debugger |
| **Transport** | Plain HTTP on localhost | HTTPS is **mandatory** — the Web Speech API needs a secure context |
| **Server** | Flask dev server | A production WSGI server (gunicorn/uWSGI) behind a reverse proxy |
| **Auth / authz** | None — endpoints are open (demo) | Add authentication + per-customer authorization so a customer only ever sees **their own** order |
| **PII exposure** | `/api/orders/<id>` returns the stored row (incl. `customer_name`) for **fictional** data with no auth | Redact PII, scope lookups to the authenticated user, validate input |
| **Rate limiting** | None | Add throttling on `/api/chat` and `/api/orders` (and Gemini quota/budget caps) |
| **Input validation** | Empty / non-dict / malformed JSON is handled; `/api/chat` never 500s (safe JSON fallback) | Add a max message length and stricter validation for real traffic |
| **Error handling** | `/api/chat` returns `{intent, reply, source}` even on internal errors; `/api/orders` is not yet exception-guarded | Add a JSON error handler across the whole `/api` blueprint |
| **Persistence** | Single-file SQLite (`data/*.db`, git-ignored) | A managed, backed-up database for concurrency and durability |

## Known limitations & future improvements
**Limitations (honest):**
- **No real phone calling.** Despite the project name there is **no telephony /
  PSTN integration** — "calling" means the browser voice (STT/TTS) chat only.
- **Live Gemini is unverified.** The Gemini layer is fully **mocked in tests**
  and disabled by default; it has never been run against a real key here.
- **Voice hardware is unverified.** Real microphone input and audio output have
  not been exercised (see the Voice section above).
- **FAQ policies are unapproved placeholders**, so the assistant withholds them
  behind a "needs confirmation / contact support" notice (by design).
- **Offline multilingual is limited** to English policy keywords and English
  fixed replies (see the Multilingual section above).

**Future improvements:**
- Have the shop owner approve the FAQ (via `docs/faq_owner_questions.md`) and
  add Urdu / Roman Urdu keywords to `data/faq.json`.
- Add optional real telephony (e.g. a Twilio / voice-studio integration) as a
  genuinely separate phase.
- Add authentication, rate limiting, request-size limits, and a whole-blueprint
  JSON error handler for production hardening.
- Add a real (opt-in) browser end-to-end suite (e.g. Playwright) behind an
  explicit dependency decision.
- Optional cloud STT/TTS for broader language coverage beyond the browser.

## Notes
- The dev server binds to `127.0.0.1` (local only). For any real deployment you
  would use a production WSGI server and HTTPS (the Web Speech API needs a
  secure context) — see
  [Security & deployment readiness](#security--deployment-readiness).
