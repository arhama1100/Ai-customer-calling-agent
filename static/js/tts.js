/*
  static/js/tts.js
  ────────────────
  Browser text-to-speech using the built-in speechSynthesis API.

  Fallback rule (project policy): if TTS is unavailable or blocked, replies
  simply show as text — nothing else may break. So every function here fails
  quietly and the chat never depends on it.

  Language: we try to use a voice for the requested language (e.g. "en-US",
  "ur-PK"). If the browser has no matching voice we do NOT pretend otherwise —
  we fall back to whatever the browser provides, so Urdu is only spoken when an
  Urdu voice is actually installed.

  Single-active model: only one reply can speak at a time. speak() always
  cancels any current/queued utterance first (no overlap), and we track state so
  the UI can toggle the SAME reply between "Speak" and "Stop".
*/

const TTS = (function () {
  // Does this browser support speech synthesis at all?
  const supported = typeof window !== "undefined" && "speechSynthesis" in window;

  // Speech state for the one active utterance.
  let speaking = false; // we requested / are playing speech
  let currentText = null; // the exact reply text that is active
  const listeners = []; // UI callbacks: ({ speaking, text }) => void

  function emit() {
    const state = { speaking, text: currentText };
    listeners.forEach((fn) => {
      try {
        fn(state);
      } catch (err) {
        /* a broken listener must never break speech */
      }
    });
  }

  /** Subscribe to start/stop changes. Returns an unsubscribe function. */
  function onChange(fn) {
    listeners.push(fn);
    return () => {
      const i = listeners.indexOf(fn);
      if (i >= 0) listeners.splice(i, 1);
    };
  }

  /**
   * Find a browser voice matching a BCP-47 tag (e.g. "en-US", "ur-PK").
   * Prefers an exact language match, then any voice sharing the 2-letter
   * prefix ("en" / "ur"). Returns null when nothing matches.
   */
  function voiceFor(lang) {
    if (!supported || !lang) return null;
    const wanted = String(lang).replace("_", "-");
    const prefix = wanted.slice(0, 2).toLowerCase();
    const voices = window.speechSynthesis.getVoices() || [];
    return (
      voices.find((v) => v.lang && v.lang.replace("_", "-") === wanted) ||
      voices.find((v) => v.lang && v.lang.toLowerCase().startsWith(prefix)) ||
      null
    );
  }

  /**
   * Speak `text` aloud (cancels any current speech first, so clicks never
   * overlap or stack). `lang` is optional ("en-US" / "ur-PK"); when a matching
   * voice exists it is used, otherwise the browser default speaks.
   */
  function speak(text, lang) {
    if (!supported || !text || !String(text).trim()) return; // empty/whitespace
    const myText = String(text);
    try {
      // Stop + clear anything still playing or queued, then speak this reply.
      window.speechSynthesis.cancel();

      const utterance = new SpeechSynthesisUtterance(myText);
      utterance.rate = 1;
      if (lang) {
        const voice = voiceFor(lang);
        if (voice) utterance.voice = voice; // only if the browser actually has it
        utterance.lang = (voice && voice.lang) || lang;
      }

      // Reliable state via utterance events. Guard on myText so a late
      // end/error from a PREVIOUS (already replaced) utterance can't wipe
      // the new active state.
      utterance.onstart = () => {
        speaking = true;
        emit();
      };
      utterance.onend = () => {
        if (currentText === myText) {
          speaking = false;
          currentText = null;
          emit();
        }
      };
      utterance.onerror = () => {
        if (currentText === myText) {
          speaking = false;
          currentText = null;
          emit();
        }
      };

      // Set state synchronously so a fast second click toggles to "stop".
      speaking = true;
      currentText = myText;
      emit();

      window.speechSynthesis.speak(utterance);
    } catch (err) {
      // Silent failure by design: TTS is an enhancement, not a requirement.
      console.warn("TTS failed:", err);
      speaking = false;
      currentText = null;
      emit();
    }
  }

  /** Cancel ongoing speech and clear the queue immediately. */
  function stop() {
    if (supported) {
      try {
        window.speechSynthesis.cancel();
      } catch (err) {
        console.warn("TTS stop failed:", err);
      }
    }
    speaking = false;
    currentText = null;
    emit();
  }

  /**
   * Is speech active? Pass `text` to ask "is THIS reply the active one?".
   * Combines our tracked state with the browser's live speaking/pending flags
   * so the answer stays correct even if an event is delayed.
   */
  function isSpeaking(text) {
    if (!supported) return false;
    const live = window.speechSynthesis.speaking || window.speechSynthesis.pending;
    const active = speaking || live;
    if (typeof text === "string") return active && currentText === text;
    return active;
  }

  // Chrome loads the voice list asynchronously; this warms it so the very
  // first click can already pick an Urdu/English voice. Harmless elsewhere.
  if (supported && "onvoiceschanged" in window.speechSynthesis) {
    window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
  }

  return { supported, speak, stop, isSpeaking, onChange };
})();
