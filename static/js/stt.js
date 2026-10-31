/*
  static/js/stt.js
  ────────────────
  Browser speech recognition (STT) using the built-in Web Speech API.
  No npm packages, no server involvement — pure browser capability.

  Flow:  mic click -> listen -> final transcript -> ChatApp.sendMessage(text)

  Fallback rules (project policy — text chat must ALWAYS work):
  - Browser without SpeechRecognition -> mic stays disabled, hint shown.
  - Microphone permission denied        -> friendly message, text chat intact.
  - No speech detected                  -> gentle message, ready to retry.
  - Any recognition error               -> message shown, never crashes the app.
*/

const STT = (function () {
  // The API is prefixed in Chrome/Edge; the standard name may exist later.
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;

  const supported = Boolean(SpeechRecognition);

  // ── Language config ──
  // BCP-47 tags. "en-US" is universally supported; "ur-PK" (Urdu as spoken
  // in Pakistan) works only where the browser/OS provides the voice pack —
  // we detect and warn instead of failing silently.
  const LANGS = {
    "en-US": "English",
    "ur-PK": "اردو (Urdu)",
  };
  const LANG_STORAGE_KEY = "stt-lang";

  let currentLang =
    localStorage.getItem(LANG_STORAGE_KEY) || // remember user's last choice
    (navigator.language && navigator.language.toLowerCase().startsWith("ur")
      ? "ur-PK"
      : "en-US");

  let recognition = null;
  let listening = false;
  let ui = null; // set by init(): {button, langSelect}

  /** Create (or recreate) the recognition instance for the current language. */
  function buildRecognizer() {
    if (!supported) return null;

    const rec = new SpeechRecognition();
    rec.lang = currentLang; // use the SELECTED language as-is (never replaced)
    rec.interimResults = true; // stream partial results so words appear live
    rec.maxAlternatives = 1;
    rec.continuous = false; // one utterance per click

    let finalTranscript = ""; // accumulated FINAL text for this session

    rec.onstart = () => {
      listening = true;
      finalTranscript = ""; // fresh session -> no stale carry-over
      renderMicState();
      ChatApp.setStatus(
        `Listening — speak now (${LANGS[currentLang]}, ${rec.lang})…`
      );
    };

    rec.onresult = (event) => {
      let interim = "";
      // Iterate ONLY the new results, starting at event.resultIndex.
      // (Reading results[0] blindly can resend a stale/first segment.)
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const res = event.results[i];
        const txt = res[0].transcript;
        if (res.isFinal) finalTranscript += txt;
        else interim += txt;
      }

      // Show what the mic hears, live, in the input box (nothing sent yet).
      const live = (finalTranscript + interim).trim();
      ChatApp.setDraft(live);

      // On the FINAL result: stop and let the user review/correct, then Send.
      const last = event.results[event.results.length - 1];
      if (last && last.isFinal) {
        const finalText = finalTranscript.trim();
        if (finalText) {
          // Put the unchanged transcript in the input; the user presses Send,
          // which routes it through ChatApp.sendMessage() to /api/chat.
          ChatApp.setDraft(finalText);
          ChatApp.setStatus("Here's what I heard — edit if needed, then press Send.");
        } else {
          ChatApp.setStatus("I didn't catch that. Tap the mic and try again.");
        }
        try {
          rec.stop();
        } catch (e) {
          /* already ending */
        }
      }
    };

    rec.onerror = (event) => {
      // See: https://developer.mozilla.org/docs/Web/API/SpeechRecognition/error
      let hint;
      let isError = true; // most recognition failures are shown in red
      switch (event.error) {
        case "not-allowed":
        case "service-not-allowed":
          hint =
            "Microphone permission was denied — allow it in the browser, or keep typing.";
          break;
        case "no-speech":
          hint = "I didn't hear anything. Tap the mic and try again.";
          isError = false; // gentle prompt, not a real error
          break;
        case "audio-capture":
          hint = "No microphone was found — connect one, or keep typing.";
          break;
        case "network":
          hint =
            "Speech recognition needs the browser's speech service, but the network request failed. Check your connection or type your question.";
          break;
        case "language-not-supported":
          hint =
            "This browser can't recognize the selected language — try the other one, or type.";
          break;
        case "aborted":
          hint = ""; // user cancelled; no message needed
          break;
        default:
          hint = `Speech recognition error (${event.error}) — typing still works.`;
      }
      if (hint) ChatApp.setStatus(hint, isError);
    };

    rec.onend = () => {
      listening = false;
      renderMicState();
    };

    return rec;
  }

  /** Update the mic button to reflect idle/listening state and support. */
  function renderMicState() {
    if (!ui || !ui.button) return;
    if (!supported) {
      ui.button.disabled = true;
      ui.button.classList.remove("listening");
      ui.button.textContent = "🎤";
      return;
    }
    ui.button.disabled = false;
    ui.button.textContent = "🎤";
    // The .listening class drives the blue pulse animation in style.css.
    ui.button.classList.toggle("listening", listening);
    ui.button.title = listening ? "Stop listening" : "Speak your question";
  }

  /** Switch recognition language (called from the language <select>). */
  function setLanguage(tag) {
    if (!LANGS[tag]) return; // unknown tag -> ignore, never silently change
    currentLang = tag;
    localStorage.setItem(LANG_STORAGE_KEY, tag);
    if (listening && recognition) {
      recognition.abort(); // rebuild below will restart on next click
    }
    recognition = buildRecognizer();
  }

  /** Start or stop listening (called by the mic button). */
  function toggle() {
    if (!supported) return; // never breaks text chat
    if (listening) {
      if (recognition) recognition.stop();
      return;
    }
    try {
      // First call triggers the browser's microphone permission prompt.
      recognition = recognition || buildRecognizer();
      recognition.start();
    } catch (err) {
      // start() throws if called while already running, etc. — recover.
      ChatApp.setStatus("Could not start the microphone — typing still works.");
      listening = false;
      renderMicState();
    }
  }

  /**
   * Wire STT to the DOM. Called from app.js after ChatApp is exposed.
   * Returns false (and leaves the mic disabled) when unsupported.
   */
  function init(domRefs) {
    ui = domRefs;
    recognition = buildRecognizer();

    // Populate + sync the language selector (works even if STT unsupported).
    if (ui.langSelect) {
      ui.langSelect.innerHTML = "";
      Object.entries(LANGS).forEach(([tag, label]) => {
        const opt = document.createElement("option");
        opt.value = tag;
        opt.textContent = label;
        ui.langSelect.appendChild(opt);
      });
      ui.langSelect.value = currentLang;
      ui.langSelect.addEventListener("change", () =>
        setLanguage(ui.langSelect.value)
      );
    }

    renderMicState();

    if (!supported) {
      ChatApp.setStatus(
        "Voice input isn't available in this browser — please type your question."
      );
      if (ui.langSelect) ui.langSelect.disabled = true;
      return false;
    }

    ui.button.addEventListener("click", toggle);
    return true;
  }

  return { supported, init, toggle, setLanguage };
})();
