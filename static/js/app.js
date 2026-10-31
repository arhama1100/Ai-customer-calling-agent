/*
  static/js/app.js
  ────────────────
  Wires the plain chat UI to the backend:

  user types  ->  POST /api/chat  ->  {intent, reply, source}  ->  shown + spoken

  Voice input (STT) is added in Phase 5 (static/js/stt.js) and will reuse the
  same sendMessage() path — that is why the send logic lives here once.
*/

// ── Element references (looked up once) ──
const logEl = document.getElementById("log");
const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("message");
const micBtnEl = document.getElementById("mic-btn");
const sendBtnEl = document.getElementById("send-btn");
const speakToggleEl = document.getElementById("speak-toggle");
const stopSpeakEl = document.getElementById("stop-speak");
const statusEl = document.getElementById("status");
const typingEl = document.getElementById("typing");

/**
 * Language to speak replies in: the user's selected voice language (the same
 * English/Urdu choice used for the mic), falling back to the browser language.
 */
function getPreferredLang() {
  const sel = document.getElementById("stt-lang");
  return (sel && sel.value) || navigator.language || "en-US";
}

// ── Keep every reply's Speak button in sync with the single active utterance ──
// Each reply registers a tiny refresh() here; whenever TTS starts/stops
// (including natural end or the Stop Speaking button) all buttons update.
const speakRefreshers = [];
function refreshSpeakButtons() {
  speakRefreshers.forEach((fn) => fn());
}
if (typeof TTS !== "undefined" && TTS.supported) {
  TTS.onChange(refreshSpeakButtons);
}

/** Add one bubble to the conversation log. */
function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`; // "user" or "bot"
  div.textContent = text; // textContent (not innerHTML) avoids HTML injection

  // Assistant replies get a visible Speak button (only if the browser can
  // speak). It TOGGLES: if this reply is already speaking, clicking stops it;
  // otherwise it starts. The reply text is captured in the closure (never
  // rebuilt as HTML), so this stays safe and simple.
  if (role === "bot" && typeof TTS !== "undefined" && TTS.supported) {
    const actions = document.createElement("div");
    actions.className = "msg-actions";

    const btn = document.createElement("button");
    btn.type = "button"; // never a form submit
    btn.className = "speak-btn";

    // Reflect the current state on THIS button's label + accessibility text.
    const refresh = () => {
      const active = TTS.isSpeaking(text);
      btn.textContent = active ? "⏹ Stop Speaking" : "🔊 Speak Response";
      btn.setAttribute(
        "aria-label",
        active ? "Stop speaking this reply" : "Read this reply aloud"
      );
      btn.classList.toggle("speaking", active);
    };

    btn.addEventListener("click", () => {
      if (TTS.isSpeaking(text)) {
        TTS.stop(); // same reply active -> stop, do NOT restart
      } else {
        TTS.speak(text, getPreferredLang()); // starts (auto-cancels any other)
      }
      refresh();
    });

    refresh(); // initial label (idle -> "Speak Response")
    actions.appendChild(btn);
    div.appendChild(actions);
    speakRefreshers.push(refresh); // global TTS changes update this button too
  }

  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight; // keep newest message visible
}

/**
 * Show a short status line (used for errors and STT feedback).
 * Pass isError=true to render it in the error color.
 */
function setStatus(text, isError) {
  statusEl.textContent = text || "";
  statusEl.classList.toggle("error", Boolean(isError));
}

/** Toggle the "working" UI: typing dots + disabled send/mic during a request. */
function setBusy(busy) {
  if (typingEl) typingEl.hidden = !busy;
  if (sendBtnEl) sendBtnEl.disabled = busy;
  if (inputEl) inputEl.disabled = busy;
}

/**
 * Put text into the input box WITHOUT sending it.
 * Used by STT so you can see exactly what the mic heard and correct it
 * before pressing Send.
 */
function setDraft(text) {
  inputEl.value = text || "";
  inputEl.focus();
}

/**
 * Send a user message to the backend and render + speak the reply.
 * Shared by the text form (now) and the mic input (Phase 5).
 */
async function sendMessage(text) {
  const message = (text || "").trim();
  if (!message) return;

  addMessage("user", message);
  inputEl.value = "";
  setStatus("");
  setBusy(true); // show typing dots, disable send/input while awaiting

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });

    if (!response.ok) {
      // The API returns consistent JSON errors (413 too long, 429 rate limited,
      // 500, ...). Show the server's friendly message when present so the user
      // gets real guidance instead of a bare status number.
      let detail = `server error (${response.status})`;
      try {
        const errBody = await response.json();
        if (errBody && typeof errBody.message === "string" && errBody.message.trim()) {
          detail = errBody.message.trim();
        }
      } catch (parseErr) {
        // Not JSON (e.g. a proxy error page) - keep the status-based detail.
      }
      throw new Error(detail);
    }

    const data = await response.json(); // {intent, reply, source}
    // Validate the reply shape BEFORE rendering, so a malformed response can
    // never paint "undefined"/invalid content into the chat.
    if (!data || typeof data.reply !== "string" || data.reply.trim() === "") {
      throw new Error("the assistant returned an invalid reply");
    }
    setBusy(false);
    addMessage("bot", data.reply);

    // Speak only if the user left the toggle on and the browser can; honor the
    // selected language (same choice as the per-reply Speak button).
    if (speakToggleEl.checked) {
      TTS.speak(data.reply, getPreferredLang());
    }
  } catch (err) {
    // Honest error: never fake an assistant answer. Covers a network failure
    // AND API errors (the friendly message is thrown above for non-2xx replies).
    setBusy(false);
    setStatus(`The assistant could not answer just now: ${err.message}. You can still type.`, true);
  } finally {
    setBusy(false);
    inputEl.focus();
  }
}

// ── Form submit: the primary text-chat path (works with speech unavailable) ──
formEl.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(inputEl.value);
});

// ── Tell the user if the browser cannot speak (text chat still fully works) ──
if (!TTS.supported) {
  setStatus("Note: this browser can't speak replies, but text chat works fine.");
  speakToggleEl.checked = false;
  speakToggleEl.disabled = true;
  if (stopSpeakEl) stopSpeakEl.disabled = true;
}

// ── Global "Stop Speaking": cancels any speech immediately ──
if (stopSpeakEl) {
  stopSpeakEl.addEventListener("click", () => {
    TTS.stop();
    setStatus("");
  });
}

// ── Friendly first message so the chat is never empty ──
addMessage(
  "bot",
  "Hi! Ask me about an order (for example ORD-1002) or a topic like shipping, " +
    "returns, cancellation, or payment. Type below or tap the mic."
);

// Expose the shared chat API. STT uses setDraft (show heard text) and, if the
// user sends, sendMessage runs through this same path with the exact text.
window.ChatApp = {
  sendMessage,
  addMessage,
  setStatus,
  setDraft,
  micButton: micBtnEl,
};

// ── Voice input (Phase 5): wire the mic button + language selector ──
// STT is optional; if the browser lacks SpeechRecognition, init() returns
// false, the mic stays disabled, and text chat keeps working exactly as before.
if (typeof STT !== "undefined") {
  STT.init({
    button: micBtnEl,
    langSelect: document.getElementById("stt-lang"),
  });
}
