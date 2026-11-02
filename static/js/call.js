/*
  static/js/call.js
  ─────────────────
  A browser-based SIMULATED support call layered on the exact same backend and
  voice pieces the text chat already uses. There is NO telephony: "call" means a
  hands-free voice loop of  mic -> /api/chat -> spoken reply, driven only by the
  browser Web Speech APIs.

  The call is a VOICE-ONLY experience: it shows a large animated orb + a short
  status line ("Listening…", "AI is speaking…"), NOT a chat transcript. It never
  writes into the text-chat log, so the Chat workspace stays independent.

  Architecture — two clearly separated halves:

  1) createCallController(deps)  — PURE, dependency-injected state machine.
     No DOM, no browser globals. It owns lifecycle states, the mute flag, the
     duplicate-message + self-echo guards, the barge-in decision, greeting audio
     sequencing, and (most importantly) the STALE-RESPONSE protection so an old
     /api/chat reply can never be spoken after a newer one. This is what
     tests/js/test_call_state.js exercises in plain Node (no dependencies).

  2) mountBrowserCall()  — thin glue. It builds ONE continuous SpeechRecognition
     session for the call (stt.js's one-shot "draft + manual Send" recognizer
     cannot support hands-free interruption), reuses the EXISTING global TTS
     module for speaking/stop, shows only orb+status (no transcript), and calls
     the SAME /api/chat with the SAME {message} -> {intent, reply, source}.

  IMPORTANT (greeting-audio fix): tts.js/stt.js expose their modules as top-level
  `const` globals, which are NOT properties of `window`. So we must reference the
  bare `TTS` (guarded by `typeof TTS !== "undefined"`), exactly like app.js does.
  Reading `window.TTS` is always undefined and silently skips real speech.
*/

(function (root, factory) {
  var api = factory();

  // Node / tests: export the API (createCallController, STATES, mountBrowserCall).
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  // Browser: expose a namespace.
  if (root) {
    root.CallCenter = api;
  }

  // Auto-mount the browser glue only inside a real browser (never in Node),
  // and only after the DOM is parsed so every element exists.
  if (typeof document !== "undefined" && root && root === window) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", api.mountBrowserCall);
    } else {
      api.mountBrowserCall();
    }
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  // ── Lifecycle states ──
  var STATES = {
    IDLE: "Idle",
    STARTING: "Starting",
    LISTENING: "Listening",
    PROCESSING: "Processing",
    SPEAKING: "Speaking",
    MUTED: "Muted",
    ENDING: "Ending",
    ENDED: "Ended",
    ERROR: "Error",
  };

  var DEFAULT_GREETING =
    "Hello! This is the Shop Support Assistant. How can I help you today?";

  function normStr(s) {
    return String(s || "").toLowerCase().replace(/\s+/g, " ").trim();
  }

  /**
   * The pure call controller. Every external touch point is injected so the
   * logic can be unit-tested head-lessly. Sensible no-op defaults keep it safe.
   */
  function createCallController(deps) {
    deps = deps || {};

    var tts =
      deps.tts || { speak: function () {}, stop: function () {}, isSpeaking: function () { return false; } };
    var recognition =
      deps.recognition || { start: function () {}, stop: function () {}, abort: function () {} };
    // The transcript sink is OPTIONAL. The browser glue deliberately passes NO
    // transcript (voice-only, hidden), while the Node tests pass a recorder to
    // observe behavior. Either way the call still processes and responds.
    var transcript =
      deps.transcript || { addUser: function () {}, addBot: function () {}, addSystem: function () {} };
    var status = deps.status || function () {};
    var publishState = deps.state || function () {};
    var getLang = deps.getLang || function () { return "en-US"; };
    var chat = deps.chat || null; // (message) => Promise<{intent, reply, source}>
    // Optional pipeline observer for diagnostics/tests. It is a SEER, never a
    // decision maker: no behaviour depends on it, and the default is inert. It
    // receives a stage name plus safe metrics (lengths, flags) - never the
    // customer's words, because an order number is customer data.
    var onEvent = deps.onEvent || function () {};

    var timers = deps.timers || {};
    var setIntervalFn = timers.setInterval || (typeof setInterval !== "undefined" ? setInterval : null);
    var clearIntervalFn = timers.clearInterval || (typeof clearInterval !== "undefined" ? clearInterval : null);
    var now = timers.now || (typeof Date !== "undefined" ? function () { return Date.now(); } : function () { return 0; });
    var setTimeoutFn = timers.setTimeout || (typeof setTimeout !== "undefined" ? setTimeout : null);
    var clearTimeoutFn = timers.clearTimeout || (typeof clearTimeout !== "undefined" ? clearTimeout : null);

    var greeting = typeof deps.greeting === "string" ? deps.greeting : DEFAULT_GREETING;
    var duplicateWindowMs = typeof deps.duplicateWindowMs === "number" ? deps.duplicateWindowMs : 1500;
    var interruptMinChars = typeof deps.interruptMinChars === "number" ? deps.interruptMinChars : 3;
    // A number dictated digit-by-digit arrives as SEVERAL recognition "final"
    // results (there are natural pauses between the digits). Sending each one on
    // its own would make the backend see a fragment like "one zero zero" and
    // report an incomplete number. We MERGE finals that arrive within this short
    // window into ONE utterance before sending. It is purely timing-based, so it
    // works for every order number with no per-id special-casing.
    var utteranceBufferMs = typeof deps.utteranceBufferMs === "number" ? deps.utteranceBufferMs : 750;
    // The same window also cut ordinary SENTENCES in half: "What is the status"
    // + (natural pause) + "of my order?" were submitted as two turns, each of
    // which the backend could only answer with its honest "I'm not sure" line,
    // while the identical typed sentence worked fine in Text Chat. So an
    // unterminated buffer (no . ? !) is NOT final yet: the window is extended
    // until this TOTAL time has passed since the FIRST fragment, then whatever
    // we have is sent so nothing is ever stranded. Bounded, generic, and no
    // question-specific text is hard-coded anywhere.
    var maxMergeMs = typeof deps.maxMergeMs === "number" ? deps.maxMergeMs : 2400;

    // ── Mutable call state ──
    var state = STATES.IDLE;
    var callId = 0; // bumped on start AND end: any reply from a dead call is stale
    var requestSeq = 0; // bumped per submitted utterance: only the newest wins
    var callActive = false;
    var muted = false;
    var durationStart = 0;
    var durationId = null;
    var lastUtterance = "";
    var lastUtteranceAt = 0;
    var lastSpoken = ""; // the AI's current/recent line -> self-echo suppression
    // Merge window for consecutive recognition finals (see utteranceBufferMs).
    var speechBuf = "";
    var speechBufTimer = null;
    var speechBufFirstAt = null; // when the FIRST buffered fragment arrived

    function currentDuration() {
      return durationStart ? Math.floor((now() - durationStart) / 1000) : 0;
    }
    function setState(next) {
      state = next;
      publishState(state, currentDuration());
    }
    function publishDuration() {
      publishState(state, currentDuration());
    }

    /**
     * True when `text` IS the AI's last line, or a contiguous fragment of it --
     * i.e. the microphone re-hearing our own speaker output rather than the
     * customer actually talking. Checked per fragment AND on the merged turn.
     */
    function isEchoOfSpokenLine(text) {
      if (!lastSpoken) return false;
      var ns = normStr(text);
      if (!ns) return false;
      var ls = normStr(lastSpoken);
      return ns === ls || (ns.length >= 4 && ls.indexOf(ns) !== -1);
    }

    /** Discard any merged-but-not-yet-sent speech and cancel its pending timer. */
    function clearSpeechBuffer() {
      if (speechBufTimer !== null && clearTimeoutFn) {
        try { clearTimeoutFn(speechBufTimer); } catch (e) { /* ignore */ }
      }
      speechBufTimer = null;
      speechBuf = "";
      speechBufFirstAt = null;
    }

    /**
     * True when Chrome closed the utterance as a complete sentence. Recognition
     * adds terminal punctuation to a finished phrase, so "of my order?" ends a
     * turn while "What is the status" plainly does not. Latin and Urdu marks are
     * both recognised; nothing about the CONTENT is inspected.
     */
    function endsCompletedSentence(text) {
      return /[.!?\u060f\u061f]\s*$/.test(String(text || "").trim());
    }

    /**
     * Send the accumulated finals as ONE utterance.
     * `force` (explicit host/UI flush, or no scheduler) always submits.
     * Otherwise an incomplete-looking turn is extended until maxMergeMs has
     * passed since its first fragment, so a sentence spoken across a natural
     * pause still reaches the backend whole.
     */
    function flushSpeechBuffer(force) {
      if (speechBufTimer !== null && clearTimeoutFn) {
        try { clearTimeoutFn(speechBufTimer); } catch (e) { /* ignore */ }
        speechBufTimer = null;
      }
      if (!speechBuf) return;
      if (!force && !endsCompletedSentence(speechBuf) && speechBufFirstAt !== null) {
        var waited = now() - speechBufFirstAt;
        if (waited < maxMergeMs) {
          // Still inside the allowed merge time and no sentence terminator:
          // keep waiting for the rest instead of submitting half a question.
          speechBufTimer = setTimeoutFn ? setTimeoutFn(function () {
            speechBufTimer = null;
            flushSpeechBuffer(false);
          }, Math.max(1, Math.min(utteranceBufferMs, maxMergeMs - waited))) : null;
          if (speechBufTimer === null) submitSpeechBuffer(); // no scheduler -> send
          return;
        }
      }
      submitSpeechBuffer();
    }

    /** Hand the merged turn to the backend and reset the buffer. */
    function submitSpeechBuffer() {
      var text = speechBuf;
      speechBuf = "";
      speechBufFirstAt = null;
      if (text) submitUtterance(text);
    }

    /** (Re)arm the short merge timer so a burst of finals becomes one turn. */
    function scheduleFlush() {
      if (speechBufTimer !== null && clearTimeoutFn) {
        try { clearTimeoutFn(speechBufTimer); } catch (e) { /* ignore */ }
        speechBufTimer = null;
      }
      if (setTimeoutFn) {
        speechBufTimer = setTimeoutFn(function () {
          speechBufTimer = null;
          flushSpeechBuffer(false);
        }, utteranceBufferMs);
      } else {
        flushSpeechBuffer(true); // no scheduler available -> submit immediately
      }
    }

    /** Move back to Listening and (re)arm exactly one recognition session. */
    function resumeListening() {
      if (!callActive) return;
      if (muted) {
        setState(STATES.MUTED);
        return;
      }
      setState(STATES.LISTENING);
      try { recognition.start(); } catch (e) { /* ignore */ }
    }

    /**
     * Speak a line and enter the Speaking state. If speech cannot start at all
     * (throws / no engine) we do NOT claim success: we surface a status and
     * resume listening so the call keeps working.
     */
    function speakLine(text) {
      lastSpoken = String(text || "").trim();
      setState(STATES.SPEAKING);
      try {
        tts.speak(text, getLang());
      } catch (e) {
        status("Audio playback failed — continuing in listening mode.", true);
        resumeListening();
      }
    }

    /** Begin a call: start the timer, then speak the greeting. */
    function startCall() {
      if (callActive) {
        return false; // a call is already running -> never start a 2nd session
      }
      callId += 1;
      callActive = true;
      muted = false;
      requestSeq = 0;
      lastUtterance = "";
      lastUtteranceAt = 0;
      lastSpoken = "";
      clearSpeechBuffer();
      durationStart = now();

      transcript.addSystem("— Call started —");
      status("Starting call…", false);
      setState(STATES.STARTING);

      if (setIntervalFn) {
        durationId = setIntervalFn(publishDuration, 1000);
      }

      // Greeting is spoken THROUGH the controller so "Speaking" is a real state.
      // Recognition is NOT started yet; it begins only once the greeting has
      // actually ended (the host calls onTtsEnded), so the greeting is never cut
      // off by mic startup or mistaken for customer speech.
      transcript.addBot(greeting);
      speakLine(greeting);
      return true;
    }

    /** End the call and guarantee no audio / recognition / timer leaks. */
    function endCall() {
      callActive = false;
      muted = false;
      callId += 1; // invalidate any in-flight /api/chat reply
      requestSeq += 1;
      setState(STATES.ENDING);

      try { recognition.abort(); } catch (e) { /* ignore */ }
      try { tts.stop(); } catch (e) { /* ignore */ }
      if (durationId !== null && clearIntervalFn) {
        clearIntervalFn(durationId);
      }
      durationId = null;
      durationStart = 0;
      lastSpoken = "";
      clearSpeechBuffer();

      transcript.addSystem("— Call ended —");
      status("Call ended.", false);
      setState(STATES.ENDED);
      return true;
    }

    /**
     * Mute stops the microphone CAPTURE (recognition.stop), not merely the
     * handling of text, and unmute restarts it only for a still-live call.
     */
    function toggleMute() {
      if (!callActive) {
        return; // nothing to mute outside a call
      }
      if (!muted) {
        muted = true;
        setState(STATES.MUTED);
        status("Muted — microphone off.", false);
        clearSpeechBuffer(); // drop any half-spoken number the moment we mute
        try { recognition.stop(); } catch (e) { /* ignore */ }
      } else {
        muted = false;
        if (state === STATES.MUTED) {
          status("Unmuted — listening.", false);
          resumeListening();
        }
      }
    }

    /**
     * Called by the host when recognition produces text. `isFinal` marks a
     * completed utterance. Handles barge-in and (for finals) submission.
     */
    function handleSpeech(text, isFinal) {
      var raw = (text || "").trim();
      if (!callActive || muted || !raw) {
        onEvent("speech-dropped", "reason=" + (!callActive ? "not-in-call" : muted ? "muted" : "empty"));
        return; // ignore outside a live, unmuted call
      }
      onEvent("speech", "final=" + !!isFinal + " len=" + raw.length);
      // Barge-in: the customer spoke while the AI was talking -> cancel promptly.
      // A minimum length avoids treating noise / the AI's own tail word as one.
      // Gated on OUR OWN Speaking state, not just the engine's flag: Chrome can
      // leave speechSynthesis.speaking/pending stuck true long after a line has
      // finished, and cancelling synthesis on that stale signal aborts the very
      // recognition session that is delivering the customer's words - the call
      // then sits at "Listening" forever with nothing ever submitted.
      if (state === STATES.SPEAKING && tts.isSpeaking() && raw.length >= interruptMinChars) {
        onEvent("barge-in", "len=" + raw.length);
        try { tts.stop(); } catch (e) { /* ignore */ }
        setState(STATES.LISTENING);
      }
      if (!isFinal) {
        return; // interim only drives barge-in; wait for the full utterance
      }
      // Screen out a re-heard fragment of the AI's OWN line BEFORE it enters the
      // merge buffer. Otherwise an echo of the greeting gets concatenated with the
      // customer's real words and the combined turn is what the backend receives.
      if (isEchoOfSpokenLine(raw)) {
        onEvent("echo-dropped", "len=" + raw.length);
        return;
      }
      // Accumulate this final result and (re)arm the merge timer, so a number
      // split across several finals ("one zero zero" + "one") is sent whole.
      if (!speechBuf) speechBufFirstAt = now();
      speechBuf = speechBuf ? speechBuf + " " + raw : raw;
      onEvent("buffered", "len=" + speechBuf.length);
      // Recognition punctuated the turn -> it is a complete sentence, so send it
      // now instead of making the caller wait out the merge window.
      if (endsCompletedSentence(speechBuf)) {
        flushSpeechBuffer(true);
        return;
      }
      scheduleFlush();
    }

    /** Send one customer utterance to /api/chat and speak the LATEST reply. */
    function submitUtterance(text) {
      var message = (text || "").trim();
      if (!message || !callActive || muted) {
        onEvent("submit-dropped", "reason=" + (!message ? "empty" : !callActive ? "not-in-call" : "muted"));
        return; // empty / muted / not in a call -> never send
      }
      var t = now();
      if (message === lastUtterance && t - lastUtteranceAt < duplicateWindowMs) {
        onEvent("submit-dropped", "reason=duplicate");
        return; // duplicate-message guard
      }
      // Echo guard: never treat the AI's OWN speech as customer input. A re-heard
      // fragment of what the assistant just said is a contiguous substring of its
      // last line, so a speaker->mic echo (e.g. of a spoken prompt) is dropped.
      if (isEchoOfSpokenLine(message)) {
        onEvent("submit-dropped", "reason=echo len=" + message.length);
        return;
      }
      lastUtterance = message;
      lastUtteranceAt = t;

      transcript.addUser(message);
      requestSeq += 1;
      var myCall = callId;
      var mySeq = requestSeq;
      onEvent("submit", "len=" + message.length + " seq=" + mySeq);
      setState(STATES.PROCESSING);

      if (!chat) {
        onEvent("no-chat-client");
        resumeListening();
        return;
      }

      Promise.resolve(chat(message)).then(function (data) {
        // STALE-RESPONSE PROTECTION: ignore anything from a dead call or from a
        // request that a newer one has already superseded.
        if (!callActive || myCall !== callId || mySeq !== requestSeq) {
          onEvent("reply-ignored", "reason=stale seq=" + mySeq);
          return;
        }
        if (!data || typeof data.reply !== "string" || data.reply.trim() === "") {
          status("The assistant could not answer just now.", true);
          resumeListening();
          return;
        }
        onEvent("reply", "len=" + data.reply.length);
        transcript.addBot(data.reply); // no-op in the voice-only browser glue
        speakLine(data.reply);
      }).catch(function () {
        if (!callActive || myCall !== callId || mySeq !== requestSeq) {
          onEvent("reply-ignored", "reason=stale-after-error seq=" + mySeq);
          return; // stale — a newer request owns the turn
        }
        onEvent("chat-error");
        status("Connection problem — please try again.", true);
        resumeListening();
      });
    }

    /** The host notifies us when the current utterance has finished speaking. */
    function onTtsEnded() {
      if (!callActive) {
        return; // ended/errored mid-speech -> do not resume the mic
      }
      if (state === STATES.PROCESSING) {
        return; // a request is still in flight; its reply will re-speak
      }
      if (state === STATES.SPEAKING) {
        // The greeting/reply finished -> hand the floor back to the customer.
        resumeListening();
      }
    }

    /** Surface a hard error (e.g. denied mic) and fully tear the call down. */
    function onError(message) {
      callActive = false;
      muted = false;
      callId += 1;
      requestSeq += 1;
      if (durationId !== null && clearIntervalFn) {
        clearIntervalFn(durationId);
      }
      durationId = null;
      durationStart = 0;
      lastSpoken = "";
      clearSpeechBuffer();
      try { recognition.abort(); } catch (e) { /* ignore */ }
      try { tts.stop(); } catch (e) { /* ignore */ }
      status(message, true);
      setState(STATES.ERROR);
    }

    /** Snapshot for the UI and for tests. */
    function getState() {
      return {
        state: state,
        callActive: callActive,
        muted: muted,
        duration: currentDuration(),
        callId: callId,
        requestSeq: requestSeq,
      };
    }

    return {
      startCall: startCall,
      endCall: endCall,
      toggleMute: toggleMute,
      handleSpeech: handleSpeech,
      submitUtterance: submitUtterance,
      onTtsEnded: onTtsEnded,
      onError: onError,
      flushSpeech: function () { flushSpeechBuffer(true); },
      getState: getState,
    };
  }

  // ──────────────────────────────────────────────────────────────────────────
  //  Browser glue: the only place that touches DOM / browser globals.
  // ──────────────────────────────────────────────────────────────────────────
  function mountBrowserCall() {
    if (typeof document === "undefined") {
      return null; // safety: never runs in Node
    }

    var startBtn = document.getElementById("call-start");
    var endBtn = document.getElementById("call-end");
    var muteBtn = document.getElementById("call-mute");
    var durationEl = document.getElementById("call-duration");
    var stateEl = document.getElementById("call-state");
    var statusEl = document.getElementById("call-status");
    var orbEl = document.getElementById("call-orb");
    var micBtn = document.getElementById("mic-btn");
    var langSel = document.getElementById("stt-lang");
    if (!startBtn || !endBtn || !muteBtn) {
      return null; // call UI not present on this page
    }

    var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

    // Resolve the EXISTING TTS module the same way app.js does. NOTE: it is a
    // top-level `const`, so it is NOT on window — referencing window.TTS here is
    // exactly what made the greeting silent. Use the bare binding instead.
    var TTSMod = (typeof TTS !== "undefined") ? TTS : null;
    var ttsSupported = !!(TTSMod && TTSMod.supported);

    // The voice-only call shows NO transcript: the controller is given no
    // transcript sink, so its addBot/addUser/addSystem calls are inert no-ops
    // and nothing ever reaches the chat log.
    var errText = null; // last hard error message, so we don't clobber it
    var lastPublishedState = null; // diagnostics: log each transition once
    // The duration ticker repaints the status line EVERY second, which used to
    // erase any real message (a recognition error, a mic hint) a moment after it
    // appeared - so the caller only ever saw "Listening". An explicit message is
    // now held until the next genuine state change.
    var hintHeld = false;
    function status(text, isError) {
      if (!statusEl) return;
      hintHeld = true;
      if (isError) {
        errText = text;
        statusEl.textContent = text;
        statusEl.classList.add("error");
      } else {
        errText = null;
        statusEl.textContent = text;
        statusEl.classList.remove("error");
      }
    }

    // ── TEMPORARY voice diagnostics ─────────────────────────────────────────
    // Opt-in ONLY: add ?voicediag=1 to the page URL. It answers the one question
    // automated tests cannot - WHERE the real browser pipeline stops: does a
    // recognition session start, does onresult fire, does the buffer submit, does
    // /api/chat answer. It records stage names, codes, lengths and state changes
    // - never the words heard (an order number is customer data), never a secret.
    //
    // To ALSO see WHAT the microphone actually heard (needed to diagnose whether
    // the browser returns Urdu / Roman Urdu / garbled text), add ?voicetext=1 (or
    // ?voicediag=text). That is deliberately a SEPARATE, second opt-in: it writes
    // a short preview of the recognized text to the browser CONSOLE ONLY - it is
    // never sent anywhere or stored - for local debugging on the demo's FICTIONAL
    // data. Do not enable it against real customers.
    // To remove: delete this block and every dlog(...) call site, plus window
    // __voiceDiag below.
    var VOICE_BUILD = "voice-2026-11-02-a"; // which build the browser really has
    var diagOn = false;
    var textDiag = false;
    var diagRows = [];
    try {
      var search = window.location.search || "";
      diagOn = /[?&]voicediag\b/.test(search) || /[?&]voicetext\b/.test(search);
      textDiag = /[?&]voicetext\b/.test(search) || /[?&]voicediag=text\b/.test(search);
    } catch (e) { diagOn = false; textDiag = false; }
    function dlog(stage, detail) {
      if (!diagOn) return;
      var line = "[voice " + VOICE_BUILD + "] " + stage + (detail === undefined ? "" : " " + detail);
      diagRows.push(line);
      if (diagRows.length > 500) diagRows.shift(); // bounded: a long call can't grow forever
      try { console.log(line); } catch (e) { /* no console: keep the call running */ }
    }
    // A short, single-line preview of recognized text - ONLY for ?voicetext, and
    // bounded so a dictated number or a long sentence is not dumped wholesale.
    function heardPreview(text) {
      if (!textDiag) return "";
      var t = String(text || "").replace(/\s+/g, " ").trim();
      if (t.length > 80) t = t.slice(0, 80) + "…";
      return ' "' + t + '"';
    }
    try {
      // Console: __voiceDiag() -> every recorded line, for pasting back as evidence.
      window.__voiceDiag = function () { return diagRows.slice(); };
    } catch (e) { /* non-browser */ }

    // ── /api/chat client — EXACT existing request/response format ──
    function chatClient(message) {
      dlog("chat-request", "len=" + String(message || "").length + heardPreview(message));
      return fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: message }),
      }).then(function (r) {
        if (!r.ok) {
          return r.json()
            .catch(function () { return {}; })
            .then(function (b) {
              var detail =
                b && typeof b.message === "string" && b.message.trim()
                  ? b.message.trim()
                  : "server error (" + r.status + ")";
              dlog("chat-http-error", "status=" + r.status);
              throw new Error(detail);
            });
        }
        return r.json(); // {intent, reply, source}
      }).then(function (data) {
        // intent/source are our own labels, not customer content.
        dlog("chat-response", "intent=" + (data && data.intent) + " source=" + (data && data.source));
        return data;
      });
    }

    var controller; // assigned below; the handlers below close over it

    // ── TTS adapter: reuse the single global TTS module (no 2nd engine) ──
    function hasVoiceFor(lang) {
      // Informational only: report when a chosen language has no installed
      // voice so the user is not left wondering why audio is silent.
      if (!ttsSupported || !window.speechSynthesis) return true;
      try {
        var voices = window.speechSynthesis.getVoices() || [];
        if (!voices.length) return true; // not loaded yet: don't block/claim fail
        var prefix = String(lang || "").slice(0, 2).toLowerCase();
        if (!prefix) return true;
        return voices.some(function (v) {
          return v.lang && v.lang.toLowerCase().indexOf(prefix) === 0;
        });
      } catch (e) {
        return true;
      }
    }

    var ttsAdapter = {
      speak: function (text, lang) {
        if (ttsSupported) {
          if (!hasVoiceFor(lang)) {
            status("No installed voice for that language — trying the default voice.", false);
          }
          TTSMod.speak(text, lang); // real speech; auto-cancels any prior utterance
        } else {
          // No speech engine at all: be honest, then advance so the call runs.
          status("This browser can't speak replies — the call continues without audio.", false);
          setTimeout(function () { controller.onTtsEnded(); }, 0);
        }
      },
      stop: function () { if (ttsSupported) TTSMod.stop(); },
      isSpeaking: function () {
        return !!(ttsSupported && TTSMod.isSpeaking && TTSMod.isSpeaking());
      },
    };

    // Detect end-of-speech (greeting or reply finished) -> resume listening.
    if (ttsSupported) {
      var prevSpeaking = false;
      TTSMod.onChange(function (st) {
        var nowSpeaking = !!st.speaking;
        if (prevSpeaking && !nowSpeaking) {
          controller.onTtsEnded();
        }
        prevSpeaking = nowSpeaking;
      });
    }

    // ── Recognition adapter: ONE continuous session for the whole call ──
    var rec = null;
    var recRunning = false;
    // rec.start() -> onstart is ASYNCHRONOUS, so `recRunning` alone cannot stop a
    // second start() landing in that gap (Chrome throws InvalidStateError and the
    // call is left "Listening" with no session behind it). This flags the request.
    var recStartPending = false;
    var recStartAttempts = 0;
    // Chrome also refuses a start() fired immediately after a session's end(); a
    // short beat between the two is the standard, reliable re-arm.
    var recRestartDelayMs = 250;
    // Liveness facts the two flags above CANNOT tell us: whether a session is
    // really there and producing anything. See startLivenessWatchdog().
    var lastRecActivityAt = 0;
    var noSpeechStreak = 0;
    // Longer than Chrome's own ~6s silence timeout, so a healthy-but-quiet
    // session always ends by itself before this ever concludes one is dead.
    var LIVENESS_GRACE_MS = 8000;
    var livenessId = null;

    function preferredLang() {
      return (langSel && langSel.value) || navigator.language || "en-US";
    }

    function safeStart() {
      if (!rec || recRunning || recStartPending || !controller) return; // no duplicate session
      var s = controller.getState();
      if (!s.callActive || s.muted) return; // never arm outside a live, unmuted call
      recStartPending = true;
      recStartAttempts += 1;
      dlog("start-requested", "lang=" + preferredLang() + " attempt=" + recStartAttempts);
      try {
        rec.lang = preferredLang();
        rec.start();
        return;
      } catch (e) {
        dlog("start-threw", String(e && e.name) + ": " + String(e && e.message));
        // Thrown when a previous session is still winding down. Retry shortly so a
        // live call is never left without a listener; bounded to avoid a spin.
        if (recStartAttempts <= 3) {
          setTimeout(function () {
            recStartPending = false;
            safeStart();
          }, recRestartDelayMs);
        } else {
          // Out of quick retries. This used to be SILENT: the UI stayed on
          // "Listening" with no recognizer behind it and no event left to revive
          // it. Say so, and let the liveness watchdog keep trying - the listener
          // is either working or visibly not, never quietly pretended.
          recStartPending = false;
          recStartAttempts = 0; // free again: a later start gets a fresh budget
          status("The microphone is still busy - still trying…", false);
        }
      }
    }

    /**
     * Recognition LIVENESS watchdog.
     *
     * recRunning / recStartPending exist to stop a second session stacking on a
     * first, and ONLY onstart / onend / abort clear them. So any session that
     * dies without delivering those events - the tab was backgrounded, the speech
     * service refused, a start() raced the warm-up mic release and threw - left
     * every later safeStart() answering "a session already exists" while nothing
     * was listening. The call then sat at "Listening" indefinitely and no words
     * could ever reach /api/chat. This verifies the claim instead of trusting it:
     * while we SAY we are listening, a real session must be producing events.
     */
    function startLivenessWatchdog() {
      if (!SpeechRecognition || livenessId !== null) return;
      livenessId = setInterval(function () {
        if (!controller) return;
        var s = controller.getState();
        // Only while the customer is meant to be talking: no expectations during
        // the greeting, a reply, processing, mute or after the call.
        if (!s.callActive || s.muted || s.state !== STATES.LISTENING) return;
        if (recRunning || recStartPending) {
          if (Date.now() - lastRecActivityAt < LIVENESS_GRACE_MS) return; // alive enough
          dlog("watchdog", "no recognition event - treating the session as dead");
          recognitionAdapter.abort(); // clears the stale flags, safely
        }
        dlog("watchdog", "re-arming recognition");
        safeStart();
      }, 1500);
    }

    function buildRec() {
      if (!SpeechRecognition) return null;
      var r = new SpeechRecognition();
      r.continuous = true;      // keep listening across the call
      r.interimResults = true;  // partials enable prompt barge-in
      r.maxAlternatives = 1;
      r.lang = preferredLang();

      r.onstart = function () {
        recRunning = true;
        recStartPending = false;
        recStartAttempts = 0; // we are genuinely listening again -> reset retries
        lastRecActivityAt = Date.now();
        dlog("recognition-start", "lang=" + r.lang);
      };

      r.onresult = function (ev) {
        lastRecActivityAt = Date.now();
        noSpeechStreak = 0; // the microphone IS delivering sound
        // Iterate ONLY new results from resultIndex (stale-segment guard, like stt.js).
        for (var i = ev.resultIndex; i < ev.results.length; i++) {
          var res = ev.results[i];
          var txt = res[0].transcript;
          // The preview is empty unless ?voicetext is set, so the default trace
          // still carries lengths only (never the words).
          dlog("recognition-result", "final=" + !!res.isFinal + " len=" + String(txt || "").length + (res.isFinal ? heardPreview(txt) : ""));
          controller.handleSpeech(txt, res.isFinal);
        }
      };

      r.onerror = function (ev) {
        lastRecActivityAt = Date.now();
        dlog("recognition-error", String(ev.error));
        if (ev.error === "not-allowed" || ev.error === "service-not-allowed") {
          controller.onError(
            "Microphone permission was denied, so voice calling can't run. You can still use text chat below."
          );
        } else if (ev.error === "audio-capture") {
          controller.onError(
            "No microphone was found, so voice calling can't run. You can still use text chat below."
          );
        } else if (ev.error === "no-speech" || ev.error === "aborted") {
          // Soft: onend restarts if the call is still live. But a run of silent
          // sessions has to be SAID, not swallowed: it is the difference between
          // "nobody spoke" and "this listener cannot hear the language you are
          // using", and without a hint the caller only sees "Listening".
          if (ev.error === "no-speech") {
            noSpeechStreak += 1;
            if (noSpeechStreak >= 3) {
              noSpeechStreak = 0;
              status("I have not heard anything yet - check the microphone, and that the Voice language ("
                + preferredLang() + ") matches how you speak.", false);
            }
          }
        } else if (ev.error === "language-not-supported") {
          // The call shares text chat's Voice language, and this browser has no
          // engine for it. Keep the call alive and name the fix instead of
          // tearing it down as an unexplained error. NOTE: browser speech
          // recognition has NO "Roman Urdu" model (Roman Urdu is a way of
          // WRITING Urdu, not a spoken language), and Urdu (ur-PK) voice packs
          // are absent from most desktop Chrome/Edge installs - so the practical
          // fallback is to TYPE Urdu or Roman Urdu, which the backend reads.
          status("This browser cannot recognize the selected voice language ("
            + r.lang + "). Pick another Voice language, or type English, Urdu or "
            + "Roman Urdu in the text box below - I understand all three.", true);
        } else if (ev.error === "network") {
          status("Speech recognition could not reach the browser's speech service - check your connection. Text chat still works.", true);
        } else {
          controller.onError(
            "Speech recognition error (" + ev.error + ") — voice calling paused. Text chat still works."
          );
        }
      };

      r.onend = function () {
        recRunning = false;
        recStartPending = false;
        lastRecActivityAt = Date.now();
        dlog("recognition-end");
        var s = controller.getState();
        // Auto-restart only for a live, UNMUTED call; never stack sessions.
        if (s.callActive && !s.muted) {
          // Do NOT flush the merge buffer here. A session can end on a pause
          // *between digits* of a dictated number; flushing would send the
          // fragment "one zero zero" on its own. The merge timer already
          // guarantees the turn is sent, and this lets the rest of the number
          // still arrive on the next session and join it.
          setTimeout(function () {
            var st = controller.getState();
            if (st.callActive && !st.muted) safeStart();
          }, recRestartDelayMs);
        }
      };
      return r;
    }

    var recognitionAdapter = {
      start: safeStart,
      stop: function () {
        if (rec) { try { rec.stop(); } catch (e) {} }
        recStartPending = false;
      },
      abort: function () {
        recRunning = false;
        recStartPending = false;
        recStartAttempts = 0;
        if (rec) { try { rec.abort(); } catch (e) {} }
      },
    };

    // ── Build the pure controller with the browser adapters wired in ──
    // NOTE: no `transcript` is passed -> the call renders NO bubbles (voice-only).
    controller = createCallController({
      tts: ttsAdapter,
      recognition: recognitionAdapter,
      status: status,
      state: publishState,
      getLang: preferredLang,
      chat: chatClient,
      onEvent: dlog, // sees the pipeline stages; changes nothing
    });
    // From now on a "Listening" claim is checked, never trusted blindly.
    startLivenessWatchdog();
    dlog("mounted", "build=" + VOICE_BUILD + " recognition=" + (SpeechRecognition ? "yes" : "no")
      + " tts=" + (ttsSupported ? "yes" : "no") + " lang=" + preferredLang());

    // ── UI: reflect the current state on the voice-only call bar ──
    var STATE_LABEL = {
      Idle: "",
      Starting: "Starting call…",
      Listening: "Listening…",
      Processing: "Thinking…",
      Speaking: "AI is speaking…",
      Muted: "Muted — microphone off.",
      Ending: "Ending call…",
      Ended: "Call ended.",
      Error: "Voice call error.",
    };

    function fmt(sec) {
      var m = Math.floor(sec / 60);
      var s = sec % 60;
      return (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
    }

    function publishState(st, secs) {
      if (stateEl) stateEl.textContent = st;
      if (durationEl) durationEl.textContent = fmt(secs || 0);
      if (orbEl) orbEl.setAttribute("data-state", st);

      var inCall = st !== STATES.IDLE && st !== STATES.ENDED && st !== STATES.ERROR;
      startBtn.disabled = inCall || !SpeechRecognition;
      endBtn.disabled = !inCall;
      muteBtn.disabled = !inCall;
      muteBtn.textContent = st === STATES.MUTED ? "🔊 Unmute" : "🔇 Mute";
      // Disable the standalone text-chat mic during a call so two recognizers
      // never run at once (prevents duplicate recognition sessions).
      if (micBtn) micBtn.disabled = inCall;

      // Brief status only (never conversation text). Preserve a real error
      // message while in the Error state; otherwise show the per-state hint.
      if (statusEl) {
        if (st === STATES.ERROR && errText) {
          statusEl.textContent = errText;
          statusEl.classList.add("error");
        } else if (hintHeld && st === lastPublishedState) {
          // No state change: a real message outranks the per-second label.
        } else {
          hintHeld = false;
          errText = null;
          var label = STATE_LABEL[st] || "";
          // Naming the listener while it listens: the call shares text chat's
          // Voice language, and a mismatch (or an unsupported one) is otherwise
          // invisible - it just looks like a microphone that never answers.
          if (st === STATES.LISTENING) label += " · " + preferredLang();
          statusEl.textContent = label;
          statusEl.classList.remove("error");
        }
      }

      // Diagnostics record transitions once, not the once-a-second duration tick.
      if (st !== lastPublishedState) {
        lastPublishedState = st;
        dlog("state", st + " secs=" + (secs || 0));
      }
    }

    // Warm up speech synthesis inside the click gesture (Chrome voice/timing
    // quirks). This only nudges the EXISTING engine; it never speaks itself.
    function warmUpSpeech() {
      if (!ttsSupported || !window.speechSynthesis) return;
      try {
        window.speechSynthesis.getVoices();
        if (window.speechSynthesis.paused) window.speechSynthesis.resume();
      } catch (e) { /* ignore */ }
    }

    // Prime the CAPTURE device inside the same click gesture, while the greeting
    // is still playing. Opening the mic for the very first time only *after* the
    // greeting is what lost the first turn: the permission prompt, device spin-up
    // and echo-cancellation convergence all landed on top of the customer's first
    // words, so they were clipped. Tracks are released immediately; the call's own
    // recognizer opens the real session once the greeting has ended.
    function warmUpMicrophone() {
      var md = navigator.mediaDevices;
      if (!md || !md.getUserMedia) return; // older browsers: recognizer still works
      try {
        md.getUserMedia({ audio: true }).then(function (stream) {
          dlog("mic-warmup", "granted tracks=" + stream.getTracks().length);
          stream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} });
        }).catch(function (err) {
          // Denied or unavailable: say nothing here. The recognizer reports the
          // real reason through onerror, so messaging stays accurate and single.
          dlog("mic-warmup", "denied/unavailable: " + String(err && err.name));
        });
      } catch (e) { /* ignore */ }
    }

    startBtn.addEventListener("click", function () {
      warmUpSpeech();
      warmUpMicrophone();
      controller.startCall();
    });
    endBtn.addEventListener("click", function () { controller.endCall(); });
    muteBtn.addEventListener("click", function () { controller.toggleMute(); });

    // Changing the Voice language must actually change RECOGNITION, not just a
    // stored preference. The call shares this <select> with text chat; rec.lang
    // is read fresh on every safeStart(), but a session that is ALREADY running
    // would otherwise keep listening in the old language until it timed out on
    // its own (up to ~6s). Aborting here forces an immediate re-arm in the new
    // language and repaints the label, so what the caller selected is what is
    // actually listening.
    if (langSel) {
      langSel.addEventListener("change", function () {
        dlog("lang-change", "to=" + preferredLang());
        var s = controller.getState();
        if (s.callActive && !s.muted) {
          recognitionAdapter.abort(); // clears the stale flags
          safeStart();                // rebuilds with rec.lang = the NEW language
          hintHeld = false;           // let the standard label show the new lang
          publishState(s.state, 0);
        }
      });
    }

    // Initial paint + honest support messaging.
    rec = buildRec();
    publishState(STATES.IDLE, 0);
    if (!SpeechRecognition) {
      startBtn.disabled = true;
      if (stateEl) stateEl.textContent = STATES.IDLE;
      status("Voice calling needs a browser with speech recognition (Chrome/Edge). Text chat below works everywhere.", false);
    }

    return controller; // handy for manual/console testing
  }

  return {
    STATES: STATES,
    createCallController: createCallController,
    mountBrowserCall: mountBrowserCall,
  };
});
