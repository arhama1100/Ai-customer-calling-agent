/*
  tests/js/test_call_glue.js
  ───────────────────────────
  Head-LESS tests for the BROWSER HALF of static/js/call.js (mountBrowserCall) -
  the part tests/js/test_call_state.js cannot reach because it touches the DOM
  and the Web Speech globals.

  Why this file exists: the reported bug ("the greeting plays, then the call
  stays on Listening and never answers") lives entirely in that glue - whether a
  recognition session is really running, whether onresult/onerror/onend behave,
  and whether the call can recover. No automated test covered any of it.

  These tests install a FAKE browser (document / window / SpeechRecognition /
  speechSynthesis / fetch / timers / Date.now) and drive it through a virtual
  clock. They reproduce browser EVENT SEQUENCES with mocks - they are NOT a real
  microphone test, and nothing here proves that a physical mic works.

  Run:   node tests/js/test_call_glue.js
*/

"use strict";

const assert = require("assert");
const path = require("path");

const CALL_PATH = path.join(__dirname, "..", "..", "static", "js", "call.js");

// Let pending promise .then callbacks run to completion.
function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

function makeElement(id) {
  const el = {
    id: id,
    textContent: "",
    value: "",
    disabled: false,
    checked: false,
    className: "",
    classes: [],
    attrs: {},
    listeners: {},
    children: [],
    scrollTop: 0,
    scrollHeight: 100,
    addEventListener: function (type, fn) { this.listeners[type] = fn; },
    setAttribute: function (k, v) { this.attrs[k] = v; },
    appendChild: function (child) { this.children.push(child); return child; },
    removeChild: function (child) {
      const i = this.children.indexOf(child);
      if (i >= 0) this.children.splice(i, 1);
      return child;
    },
  };
  // A FUNCTIONAL classList (Task 4/5 code toggles "error"), still harmless for
  // every older test that only watches textContent.
  el.classList = {
    add: function (c) { if (el.classes.indexOf(c) === -1) el.classes.push(c); },
    remove: function (c) {
      const i = el.classes.indexOf(c);
      if (i >= 0) el.classes.splice(i, 1);
    },
    toggle: function (c, on) { if (on) el.classList.add(c); else el.classList.remove(c); },
  };
  return el;
}

/**
 * Install the fake browser, load call.js (which auto-mounts), and hand back a
 * control surface for driving and observing the glue.
 */
function mountFake(opts) {
  opts = opts || {};
  const clock = { t: 10000 }; // nonzero, like a real Date.now()
  const timeouts = [];
  const intervals = [];
  let seq = 0;

  // Globals are installed as configurable properties and RESTORED afterwards:
  // Node exposes some of them (navigator) through getters, so plain assignment
  // would throw under "use strict" and leave the real environment mutated.
  const savedProps = [];
  function patch(name, value) {
    savedProps.push({ name: name, desc: Object.getOwnPropertyDescriptor(globalThis, name) });
    Object.defineProperty(globalThis, name, { value: value, writable: true, configurable: true });
  }
  function restoreAll() {
    savedProps.reverse().forEach(function (p) {
      if (p.desc) Object.defineProperty(globalThis, p.name, p.desc);
      else delete globalThis[p.name];
    });
    savedProps.length = 0;
  }

  const els = {};
  ["call-start", "call-end", "call-mute", "call-duration", "call-state",
    "call-status", "call-orb", "mic-btn", "stt-lang",
    // Task 4/5 panel: transcript, consent checkbox, session status line.
    "call-transcript", "call-consent", "call-session"].forEach(function (id) {
    els[id] = makeElement(id);
  });
  els["stt-lang"].value = opts.lang || "en-US";
  els["call-consent"].checked = !!opts.consent; // consent OFF unless asked

  const domEvents = [];
  const document = {
    readyState: "complete",
    getElementById: function (id) { return els[id] || null; },
    createElement: function (tag) { return makeElement(tag); },
    addEventListener: function (type, fn) {
      (document._l[type] = document._l[type] || []).push(fn);
    },
    dispatchEvent: function (ev) {
      domEvents.push(ev && ev.type);
      (document._l[ev && ev.type] || []).forEach(function (fn) { try { fn(ev); } catch (e) {} });
      return true;
    },
    _l: {},
  };

  // ── Fake recognition ──
  const recInstances = [];
  function FakeSpeechRecognition() {
    this.lang = "";
    this.continuous = false;
    this.interimResults = false;
    this.maxAlternatives = 1;
    this.onstart = this.onresult = this.onerror = this.onend = null;
    this.starts = 0;
    this.stops = 0;
    this.aborts = 0;
    recInstances.push(this);
  }
  FakeSpeechRecognition.prototype.start = function () {
    this.starts += 1;
    if (opts.startThrows) {
      const err = new Error("fake: microphone busy");
      err.name = opts.startThrowName || "NotReadableError";
      throw err;
    }
    if (!opts.noOnStart) {
      if (this.onstart) this.onstart(); // Chrome fires it async; sync is fine here
    }
  };
  FakeSpeechRecognition.prototype.stop = function () { this.stops += 1; };
  FakeSpeechRecognition.prototype.abort = function () { this.aborts += 1; };

  const speech = { speaking: [], pending: [] };
  // Build the query string so tests can exercise the two diagnostic levels
  // independently: ?voicediag (stages + lengths) and ?voicetext (recognized text).
  var search = "";
  if (opts.diag) search += (search ? "&" : "?") + "voicediag=1";
  if (opts.diagText) search += (search ? "&" : "?") + "voicetext=1";
  const window = {
    SpeechRecognition: opts.noRecognition ? undefined : FakeSpeechRecognition,
    webkitSpeechRecognition: opts.noRecognition ? undefined : FakeSpeechRecognition,
    location: { search: search },
    speechSynthesis: undefined,
  };

  // ── Fake TTS module (a bare global, exactly like the real one) ──
  const spoken = [];
  let ttsStops = 0;
  let ttsListener = null;
  if (opts.tts) {
    patch("TTS", {
      supported: true,
      speak: function (text) { spoken.push(text); },
      stop: function () { ttsStops += 1; },
      isSpeaking: function () { return opts.staleSpeakingFlag === false ? false : true; },
      onChange: function (fn) { ttsListener = fn; return function () {}; },
    });
  }
  function emitTts(state) { if (ttsListener) ttsListener(state); }

  // ── Fake fetch: records the exact body, answers with the real contract ──
  // Routes: /api/chat as before; the Task 5 session endpoints answer like the
  // real Flask routes (start returns a valid hex id; end confirms completion).
  const sent = [];
  const FAKE_SESSION_ID = "0123456789abcdef0123456789abcdef";
  const fetchImpl = function (url, init) {
    sent.push({ url: url, body: init && init.body });
    if (url === "/api/session/start") {
      return Promise.resolve(opts.sessionStartFails
        ? { ok: false, status: 500, json: function () { return Promise.resolve({}); } }
        : { ok: true, json: function () {
            return Promise.resolve({
              session_id: FAKE_SESSION_ID, consent: true, status: "active",
              started_at: "2026-11-03T00:00:00+00:00", notice: "demo",
            });
          } });
    }
    if (/^\/api\/session\/[0-9a-f]{32}\/end$/.test(url)) {
      return Promise.resolve(opts.sessionEndFails
        ? { ok: false, status: 500, json: function () { return Promise.resolve({}); } }
        : { ok: true, json: function () {
            return Promise.resolve({
              session_id: url.split("/")[3], status: "completed",
              outcome: "resolved", summary: "saved",
            });
          } });
    }
    return Promise.resolve({
      ok: true,
      json: function () {
        return Promise.resolve(opts.chatReply || {
          intent: "order_status_missing_id",
          reply: "Please share your order number.",
          source: "none",
        });
      },
    });
  };
  if (opts.fetchFails) {
    patch("fetch", function () { return Promise.reject(new TypeError("fake network")); });
  } else {
    patch("fetch", fetchImpl);
  }

  // ── Fake timers + clock ──
  patch("setTimeout", function (fn, ms) {
    const t = { id: ++seq, fn: fn, at: clock.t + (ms || 0) };
    timeouts.push(t);
    return t.id;
  });
  patch("clearTimeout", function (id) {
    const i = timeouts.findIndex(function (x) { return x.id === id; });
    if (i >= 0) timeouts.splice(i, 1);
  });
  patch("setInterval", function (fn, ms) {
    const iv = { id: ++seq, fn: fn, ms: ms || 1, next: clock.t + (ms || 1) };
    intervals.push(iv);
    return iv.id;
  });
  patch("clearInterval", function (id) {
    const i = intervals.findIndex(function (x) { return x.id === id; });
    if (i >= 0) intervals.splice(i, 1);
  });
  const realDateNow = Date.now;
  Date.now = function () { return clock.t; };

  patch("navigator", {
    language: "en-US",
    mediaDevices: opts.micWarmup
      ? { getUserMedia: function () { return Promise.resolve({ getTracks: function () { return [{ stop: function () {} }]; } }); } }
      : undefined,
  });

  // The module binds `root` from window at load time and auto-mounts.
  patch("window", window);
  patch("document", document);
  if (opts.silentConsole) {
    // Diagnostics write to the console by design; keep the suite output clean.
    patch("console", { log: function () {}, warn: function () {}, error: function () {} });
  }
  delete require.cache[require.resolve(CALL_PATH)];
  const api = require(CALL_PATH);

  /** Move the virtual clock forward, firing due timeouts and interval ticks. */
  function advance(ms) {
    const target = clock.t + ms;
    for (let guard = 0; guard < 2000; guard++) {
      let next = null;
      timeouts.forEach(function (t) { if (!next || t.at < next.at) next = { at: t.at, to: t }; });
      intervals.forEach(function (iv) { if (!next || iv.next < next.at) next = { at: iv.next, iv: iv }; });
      if (!next || next.at > target) { clock.t = target; return; }
      clock.t = next.at;
      if (next.iv) {
        next.iv.next = next.at + next.iv.ms;
        next.iv.fn();
      } else {
        const i = timeouts.indexOf(next.to);
        if (i >= 0) timeouts.splice(i, 1);
        next.to.fn();
      }
    }
  }

  function latestRec() { return recInstances[recInstances.length - 1] || null; }
  function totalStarts() {
    return recInstances.reduce(function (n, r) { return n + r.starts; }, 0);
  }
  function totalAborts() {
    return recInstances.reduce(function (n, r) { return n + r.aborts; }, 0);
  }

  /** Emit one recognition result the way Chrome delivers it. */
  function emitResult(text, isFinal) {
    const r = latestRec();
    if (!r || !r.onresult) return;
    const res = [{ transcript: text }];
    res.isFinal = !!isFinal;
    const results = [res];
    results.length = 1;
    r.onresult({ resultIndex: 0, results: results });
  }
  function emitError(code) {
    const r = latestRec();
    if (r && r.onerror) r.onerror({ error: code });
  }
  function emitEnd() {
    const r = latestRec();
    if (r && r.onend) r.onend();
  }

  function statusText() { return els["call-status"].textContent; }
  function stateText() { return els["call-state"].textContent; }
  function sessionText() { return els["call-session"].textContent; }
  /** Rendered transcript rows as "Label :: text" strings, in DOM order. */
  function transcriptRows() {
    return els["call-transcript"].children.map(function (row) {
      return row.children.map(function (cell) { return cell.textContent; }).join(" :: ");
    });
  }
  function sessionCalls() {
    return sent.filter(function (s) { return s.url.indexOf("/api/session") === 0; });
  }

  function cleanup() {
    restoreAll();
    Date.now = realDateNow;
    require.cache && delete require.cache[require.resolve(CALL_PATH)];
  }

  return {
    api: api,
    advance: advance,
    clickStart: function () { els["call-start"].listeners.click(); },
    clickEnd: function () { els["call-end"].listeners.click(); },
    clickMute: function () { els["call-mute"].listeners.click(); },
    // Change the shared Voice-language <select> the way a user picking an option
    // does: set its value, then fire the change event the glue listens for.
    changeLang: function (tag) {
      els["stt-lang"].value = tag;
      if (els["stt-lang"].listeners.change) els["stt-lang"].listeners.change();
    },
    emitResult: emitResult,
    emitError: emitError,
    emitEnd: emitEnd,
    emitTts: emitTts,
    latestRec: latestRec,
    totalStarts: totalStarts,
    totalAborts: totalAborts,
    statusText: statusText,
    stateText: stateText,
    sessionText: sessionText,
    transcriptRows: transcriptRows,
    sessionCalls: sessionCalls,
    domEvents: domEvents,
    FAKE_SESSION_ID: FAKE_SESSION_ID,
    spoken: spoken,
    sent: sent,
    ttsStops: function () { return ttsStops; },
    recInstances: recInstances,
    els: els,
    cleanup: cleanup,
    diag: function () { return (window.__voiceDiag && window.__voiceDiag()) || []; },
  };
}

/** Start a call and get to the Listening state (TTS or no-TTS path). */
function toListening(b) {
  b.clickStart();
  if (b.spoken.length) {
    b.emitTts({ speaking: true });
    b.emitTts({ speaking: false }); // greeting finished -> listening
  }
  b.advance(1); // the no-TTS path hands back the floor on a 0ms timer
  return b;
}

// ── 1) Baseline: the glue really does arm recognition once the greeting ends ──
async function testListeningArmsOneSession() {
  const b = mountFake({ tts: true });
  try {
    toListening(b);
    assert.strictEqual(b.stateText(), "Listening", "the call reaches Listening");
    assert.strictEqual(b.totalStarts(), 1, "exactly one recognition session is armed");
    assert.ok(b.statusText().indexOf("Listening") === 0, "the status says listening");
    assert.ok(b.statusText().indexOf("en-US") !== -1,
      "the status names which language is listening (a mismatch must be visible)");
  } finally { b.cleanup(); }
}

// ── 2) THE REPORTED BUG: a session that dies WITHOUT firing onstart/onend left
//        recRunning stuck true, so every later start() answered "already
//        running" and the UI claimed Listening forever with nothing listening. ──
async function testSilentlyDeadSessionIsReArmed() {
  const b = mountFake({ tts: true });
  try {
    toListening(b);
    assert.strictEqual(b.totalStarts(), 1, "one session armed after the greeting");

    // The session goes dead without any event: no result, no error, no end.
    // (A backgrounded tab / refused speech service does exactly this.)
    b.advance(60000); // a minute of "Listening" with zero recognition events

    assert.ok(b.totalStarts() >= 2,
      "the watchdog notices the dead listener and arms a new one (starts=" + b.totalStarts() + ")");
    assert.ok(b.totalAborts() >= 1, "the stale session is aborted before re-arming");
    assert.strictEqual(b.stateText(), "Listening", "still listening - the call was not torn down");
  } finally { b.cleanup(); }
}

// ── 3) start() that keeps throwing used to exhaust 3 quick retries and then stop
//        in SILENCE: the label stayed "Listening" and nothing ever retried. ──
async function testUnarmableMicrophoneIsHonestAndKeepsRetrying() {
  const b = mountFake({ tts: true, startThrows: true });
  try {
    toListening(b);
    b.advance(2000); // burn the quick retry ladder
    assert.ok(/microphone/i.test(b.statusText()),
      "the caller is told the microphone could not be opened, not shown a fake Listening (got: " + b.statusText() + ")");

    const before = b.totalStarts();
    b.advance(20000); // the watchdog keeps trying while the call is live
    assert.ok(b.totalStarts() > before,
      "re-arming keeps being attempted instead of giving up silently");
  } finally { b.cleanup(); }
}

// ── 4) An unsupported voice language (the call shares text chat's selector) must
//        be explained and must NOT strand the UI in a silent Listening state. ──
async function testUnsupportedLanguageIsExplainedNotFatal() {
  const b = mountFake({ tts: true, lang: "ur-PK" });
  try {
    toListening(b);
    b.emitError("language-not-supported");
    assert.notStrictEqual(b.stateText(), "Error",
      "an unsupported language does not tear the call down");
    assert.ok(/language/i.test(b.statusText()) && b.statusText().indexOf("ur-PK") !== -1,
      "the message names the language to change (got: " + b.statusText() + ")");

    b.advance(20000);
    assert.ok(b.totalStarts() >= 2, "the listener is re-armed rather than left dead");
  } finally { b.cleanup(); }
}

// ── 5) A run of silent sessions used to be swallowed entirely (soft-error), which
//        is indistinguishable from a broken mic. It now says so. ──
async function testRepeatedSilenceIsExplained() {
  const b = mountFake({ tts: true });
  try {
    toListening(b);
    for (let i = 0; i < 3; i++) {
      b.emitError("no-speech");
      b.emitEnd();          // Chrome ends the session after reporting no-speech
      b.advance(1000);      // let the re-arm beat run
    }
    assert.ok(/not heard anything/i.test(b.statusText()),
      "three silent sessions produce guidance (got: " + b.statusText() + ")");
    assert.strictEqual(b.stateText(), "Listening", "the call stays usable");
  } finally { b.cleanup(); }
}

// ── 6) A denied microphone stays a clear hard error (not stuck Listening). ──
async function testDeniedPermissionIsAFinalHonestError() {
  const b = mountFake({ tts: true });
  try {
    toListening(b);
    b.emitError("not-allowed");
    assert.strictEqual(b.stateText(), "Error", "permission denial surfaces as Error");
    assert.ok(/permission/i.test(b.statusText()), "and explains why");
  } finally { b.cleanup(); }
}

// ── 7) THE STALE-SPEAKING-FLAG TRAP: Chrome can leave speechSynthesis reporting
//        "speaking" long after the line ended. Cancelling speech on that stale
//        signal aborts the session carrying the customer's words, so nothing was
//        ever submitted. Barge-in must follow OUR OWN state, not the engine flag. ──
async function testStaleSpeakingFlagDoesNotSabotageTheTurn() {
  const b = mountFake({ tts: true }); // fake TTS.isSpeaking() is always true
  try {
    toListening(b);
    const stopsBefore = b.ttsStops();
    b.emitResult("What is the status of my order?", true);
    assert.strictEqual(b.ttsStops(), stopsBefore,
      "speech is NOT cancelled while the assistant is not actually speaking");

    b.advance(1);
    assert.strictEqual(b.sent.length, 1, "the turn still reaches /api/chat");
    const body = JSON.parse(b.sent[0].body);
    assert.strictEqual(body.message, "What is the status of my order?",
      "and it is forwarded whole, in the existing {message} format");
  } finally { b.cleanup(); }
}

// ── 8) Full pipeline through the glue: recognized sentence -> /api/chat -> reply
//        handed to the SHARED TTS engine, exactly like Text Chat's backend. ──
async function testSpokenTurnIsAnsweredAndSpoken() {
  const b = mountFake({ tts: true });
  try {
    toListening(b);
    b.emitResult("Where is my parcel?", true);
    b.advance(1);
    await flush(); // the reply arrives on a promise
    assert.strictEqual(b.sent.length, 1, "one request was made");
    assert.strictEqual(b.sent[0].url, "/api/chat", "against the existing endpoint");
    assert.ok(b.spoken.some(function (t) { return t === "Please share your order number."; }),
      "the backend reply is spoken back through the existing TTS module");
    assert.strictEqual(b.stateText(), "Speaking", "and the call reports it is speaking");
  } finally { b.cleanup(); }
}

// ── 9) Interim results still must not submit anything (barge-in only), and the
//        merge timer still owns the final turn. ──
async function testInterimAloneNeverSubmits() {
  const b = mountFake({ tts: true });
  try {
    toListening(b);
    b.emitResult("what is the", false);
    b.advance(3000);
    assert.strictEqual(b.sent.length, 0, "an interim is never sent as a turn");
    b.emitResult("what is the status of my order?", true);
    b.advance(3000);
    assert.strictEqual(b.sent.length, 1, "the final result is sent");
  } finally { b.cleanup(); }
}

// ── 10) Diagnostics are opt-in and PRIVACY SAFE: with ?voicediag=1 the trace is
//        recorded, and it never contains the words the customer spoke. ──
async function testDiagnosticsAreOnByDefaultOffAndLeakNoSpeech() {
  const quiet = mountFake({ tts: true, diag: false, silentConsole: true });
  try {
    toListening(quiet);
    quiet.emitResult("my order 1002 please", true);
    quiet.advance(1);
    assert.deepStrictEqual(quiet.diag(), [], "no diagnostics are recorded unless asked for");
  } finally { quiet.cleanup(); }

  const b = mountFake({ tts: true, diag: true, silentConsole: true });
  try {
    toListening(b);
    b.emitResult("one zero zero two is my order", true);
    b.advance(3000); // the bounded merge wait closes -> the turn is submitted
    await flush();
    const rows = b.diag();
    assert.ok(rows.length > 0, "with ?voicediag=1 the pipeline is traced");
    const text = rows.join("\n");
    assert.ok(/recognition-result/.test(text) && /submit/.test(text) && /chat-response/.test(text),
      "the trace covers recognition -> submit -> API stages");
    assert.ok(/chat-request len=29/.test(text), "the turn reaches the client with its length");
    assert.strictEqual(text.indexOf("one zero zero two"), -1,
      "the trace must not contain recognized words (customer data)");
    assert.ok(/len=29/.test(text), "it carries lengths instead");
  } finally { b.cleanup(); }
}

// ── 11) Muting must stop the mic and the watchdog must not fight it. ──
async function testMuteStopsReArming() {
  const b = mountFake({ tts: true });
  try {
    toListening(b);
    b.clickMute();
    assert.strictEqual(b.stateText(), "Muted", "muting is reflected");
    const before = b.totalStarts();
    b.advance(60000);
    assert.strictEqual(b.totalStarts(), before, "the watchdog never arms a mic during a mute");
    b.clickMute(); // unmute
    b.advance(3000);
    assert.ok(b.totalStarts() > before, "unmuting listens again");
  } finally { b.cleanup(); }
}

// ── 12) Ending a call must not leave the watchdog re-arming a ghost session. ──
async function testEndCallStopsEverything() {
  const b = mountFake({ tts: true });
  try {
    toListening(b);
    b.clickEnd();
    const before = b.totalStarts();
    b.advance(60000);
    assert.strictEqual(b.totalStarts(), before, "no recognition is armed after the call ended");
    assert.strictEqual(b.stateText(), "Ended", "and the UI says the call ended");
  } finally { b.cleanup(); }
}

// ── 13) THE LANGUAGE-SWITCH GAP: the <select> drove rec.lang only at the next
//        natural re-arm, so switching languages left a session listening in the
//        OLD language for up to ~6s. Changing the language must re-arm NOW. ──
async function testChangeVoiceLanguageReArmsRecognition() {
  const b = mountFake({ tts: true, lang: "en-US" });
  try {
    toListening(b);
    assert.strictEqual(b.latestRec().lang, "en-US", "armed in the initially selected language");
    const startsBefore = b.totalStarts();
    const abortsBefore = b.totalAborts();

    b.changeLang("ur-PK");

    assert.strictEqual(b.latestRec().lang, "ur-PK",
      "the recognizer now listens in the language that was just chosen");
    assert.ok(b.totalAborts() > abortsBefore, "the stale-language session is aborted");
    assert.ok(b.totalStarts() > startsBefore, "a fresh session is armed in the new language");
    assert.ok(b.statusText().indexOf("ur-PK") !== -1,
      "the status label names the new listening language (got: " + b.statusText() + ")");
  } finally { b.cleanup(); }
}

// ── 14) Recognized-text diagnostics are a SEPARATE opt-in. ?voicediag stays
//        lengths-only (words never logged); only ?voicetext prints what the mic
//        heard, to the console, so Urdu/Roman-Urdu STT output can be diagnosed. ──
async function testRecognizedTextIsSeparateOptIn() {
  const b1 = mountFake({ tts: true, diag: true, silentConsole: true });
  try {
    toListening(b1);
    b1.emitResult("shukriya my friend", true);
    b1.advance(3000);
    await flush();
    const t1 = b1.diag().join("\n");
    assert.ok(/recognition-result/.test(t1), "?voicediag traces the stages");
    assert.strictEqual(t1.indexOf("shukriya"), -1, "?voicediag alone never logs the words");
  } finally { b1.cleanup(); }

  const b2 = mountFake({ tts: true, diagText: true, silentConsole: true });
  try {
    toListening(b2);
    b2.emitResult("thank you so much", true);
    b2.advance(3000);
    await flush();
    const t2 = b2.diag().join("\n");
    assert.ok(/recognition-result[^\n]*"thank you so much"/.test(t2),
      "?voicetext logs exactly what the microphone heard");
    assert.ok(/chat-request[^\n]*"thank you so much"/.test(t2),
      "and the precise text submitted to /api/chat");
  } finally { b2.cleanup(); }
}

// ── 15) Task 2 (B4): a human-agent handoff reply from /api/chat must reach the
//        shared TTS speak function exactly like any other answer. The ask is
//        recognized by the BACKEND (covered in pytest); this proves the voice
//        glue neither drops nor rewrites the honest handoff text. ──
async function testHumanAgentHandoffReplyIsSpoken() {
  const handoff = "I'm Nova AI, an automated assistant - I can't transfer you " +
    "to a live human agent in this demo, and no person has been contacted.";
  const b = mountFake({
    tts: true,
    chatReply: { intent: "human_agent_request", reply: handoff, source: "fixed" },
  });
  try {
    toListening(b);
    b.emitResult("I want to speak to a human", true);
    // No terminal punctuation: like tests 10/14, the turn closes when the
    // bounded merge window expires, not on the next tick.
    b.advance(3000);
    await flush(); // the reply arrives on a promise
    assert.strictEqual(b.sent.length, 1, "the spoken ask is sent to /api/chat once");
    assert.strictEqual(b.sent[0].url, "/api/chat", "through the existing endpoint");
    assert.strictEqual(JSON.parse(b.sent[0].body).message, "I want to speak to a human",
      "with the unchanged {message} request body");
    assert.ok(b.spoken.indexOf(handoff) !== -1,
      "the handoff reply is handed to the shared TTS module VERBATIM (not rewritten)");
    assert.strictEqual(b.stateText(), "Speaking", "and the call reports it is speaking");
  } finally { b.cleanup(); }
}

// ── 16) Task 4: the call shows a VISIBLE, speaker-labeled transcript in strict
//        chronological order - system events, the greeting, what the customer
//        said, and the spoken reply all appear as rendered lines. ──
async function testCallTranscriptIsVisibleLabeledAndOrdered() {
  const b = mountFake({ tts: true });
  try {
    toListening(b);
    b.emitResult("where is my order?", true); // terminator -> sends at once
    b.advance(1);
    await flush(); // reply rendered + spoken
    b.emitTts({ speaking: true });
    b.emitTts({ speaking: false }); // reply finished -> listening again
    b.emitResult("and the return policy?", true);
    b.advance(1);
    await flush();
    b.clickEnd();

    const rows = b.transcriptRows();
    assert.strictEqual(rows[1], "System :: Call started", "call start is labeled");
    assert.ok(rows[2].indexOf("Nova AI ::") === 0 && /Hello! This is Nova AI/.test(rows[2]),
      "the greeting appears labeled as Nova AI (got: " + rows[2] + ")");
    assert.strictEqual(rows[3], "You :: where is my order?",
      "recognized SPEECH appears in the transcript with a clear speaker label");
    assert.strictEqual(rows[4], "Nova AI :: Please share your order number.",
      "the spoken reply is ALSO shown as text (voice responses appear in the transcript)");
    assert.strictEqual(rows[5], "You :: and the return policy?", "chronological order holds");
    assert.strictEqual(rows[rows.length - 1], "System :: Call ended", "and the end is recorded");
  } finally { b.cleanup(); }
}

// ── 17) Task 5 F: consent DEFAULTS OFF. An unrecorded call still works fully,
//        but not a single session request is ever made and the note says so. ──
async function testConsentOffStoresNothingAndSaysSo() {
  const b = mountFake({ tts: true }); // consent checkbox left unchecked
  try {
    toListening(b);
    assert.ok(/not stored/i.test(b.sessionText()),
      "the panel states honestly that nothing is being stored (got: " + b.sessionText() + ")");
    b.emitResult("where is my order?", true);
    b.advance(1);
    await flush();
    b.clickEnd();
    await flush();
    assert.strictEqual(b.sessionCalls().length, 0,
      "declined consent makes ZERO session API calls - nothing is persisted");
    assert.strictEqual(b.sent.length, 1, "only the /api/chat turn ran");
  } finally { b.cleanup(); }
}

// ── 18) Task 5 full consent flow: start -> real turn -> end stores the exact
//        transcript turn with the BACKEND intent; a second End click cannot
//        re-send it; the history view is notified via a DOM event. ──
async function testConsentOnRecordsSessionTurnsWithIntents() {
  const b = mountFake({
    tts: true,
    consent: true,
    chatReply: {
      intent: "faq",
      reply: "Returns are accepted within 14 days of delivery.",
      source: "faq_kb:returns:sample",
    },
  });
  try {
    toListening(b);
    b.emitResult("what is your return policy?", true);
    b.advance(1);
    await flush(); // reply rendered, spoken, and recorded with its intent
    b.clickEnd();
    await flush();
    await flush(); // the end POST resolves through two .then hops

    const calls = b.sessionCalls();
    assert.strictEqual(calls.length, 2, "one start + one end, no more");
    assert.strictEqual(calls[0].url, "/api/session/start");
    assert.deepStrictEqual(JSON.parse(calls[0].body), { consent: true, channel: "voice" },
      "start carries the explicit consent and the voice channel");
    assert.strictEqual(calls[1].url, "/api/session/" + b.FAKE_SESSION_ID + "/end",
      "end targets the id the server issued");
    const body = JSON.parse(calls[1].body);
    assert.strictEqual(body.turns.length, 1,
      "exactly ONE recorded turn - the greeting is shown but never recorded as customer speech");
    assert.strictEqual(body.turns[0].customer, "what is your return policy?");
    assert.strictEqual(body.turns[0].reply, "Returns are accepted within 14 days of delivery.");
    assert.strictEqual(body.turns[0].intent, "faq",
      "the REAL backend intent is stored, so the summary cannot invent topics");
    assert.ok(b.domEvents.indexOf("nova:session-saved") !== -1,
      "the staff history view is notified to refresh");
    assert.ok(/saved/i.test(b.sessionText()), "and the panel says the summary was saved");

    b.clickEnd(); // a second End click must NEVER duplicate the record
    await flush();
    assert.strictEqual(b.sessionCalls().length, 2, "the record is claimed exactly once");
  } finally { b.cleanup(); }
}

// ── 19) No-duplicate guarantee at the voice layer: if recognition fires the
//        SAME final again inside the dedupe window, it is submitted AND
//        rendered exactly once. ──
async function testRepeatedRecognitionCallbackDoesNotDuplicate() {
  const b = mountFake({ tts: true });
  try {
    toListening(b);
    b.emitResult("where is my parcel?", true);
    b.advance(1);
    await flush(); // answered and spoken

    // Chrome re-delivering the same final segment (the classic duplicate):
    b.emitResult("where is my parcel?", true);
    b.advance(800); // under the 1500ms duplicate window
    await flush();

    assert.strictEqual(b.sent.length, 1, "the duplicate is NOT sent to /api/chat");
    const userRows = b.transcriptRows().filter(function (r) {
      return r.indexOf("You ::") === 0;
    });
    assert.strictEqual(userRows.length, 1, "and it is NOT rendered twice in the transcript");
  } finally { b.cleanup(); }
}

// ── 20) Task 3 A, unsupported browser: without SpeechRecognition the Start
//        button stays disabled and the reason is stated - text paths untouched. ──
async function testUnsupportedRecognitionDisablesCallHonestly() {
  const b = mountFake({ tts: true, noRecognition: true });
  try {
    assert.strictEqual(b.els["call-start"].disabled, true,
      "a browser without speech recognition cannot start a voice call");
    assert.ok(/speech recognition/i.test(b.statusText()),
      "and the status names the real reason (got: " + b.statusText() + ")");
    assert.strictEqual(b.stateText(), "Idle", "the call never pretends to be live");
  } finally { b.cleanup(); }
}

// ── 21) Task 3 B, graceful text-only fallback: with NO speechSynthesis at all
//        the call still runs, and every reply still appears in the transcript. ──
async function testNoTtsCallStillWorksAsTextOnly() {
  const b = mountFake({}); // no TTS global at all
  try {
    b.clickStart();
    b.advance(5); // the no-audio path hands the floor back on a 0ms timer
    assert.strictEqual(b.spoken.length, 0, "nothing was (or claims to be) spoken");
    assert.strictEqual(b.stateText(), "Listening", "the call still reaches Listening");

    b.emitResult("hello there?", true);
    b.advance(1);
    await flush();
    b.advance(5); // reply "spoken" -> without audio, back to listening
    const rows = b.transcriptRows();
    assert.ok(rows.indexOf("Nova AI :: Please share your order number.") !== -1,
      "the reply still reaches the VISIBLE transcript (text-only fallback)");
    assert.strictEqual(b.spoken.length, 0, "and still no fake audio claim");
  } finally { b.cleanup(); }
}

// ── 22) A failing session API NEVER breaks the call: the failure is stated
//        honestly, no end-save is attempted for a session that never started,
//        and the conversation itself continues normally. ──
async function testSessionApiFailureDoesNotBreakTheCall() {
  const b = mountFake({ tts: true, consent: true, sessionStartFails: true });
  try {
    toListening(b);
    await flush(); // the rejected start POST settles on a later microtask
    assert.ok(/could not start/i.test(b.sessionText()),
      "the recording failure is admitted openly (got: " + b.sessionText() + ")");
    b.emitResult("where is my order?", true);
    b.advance(1);
    await flush();
    assert.ok(b.spoken.length >= 1, "the call keeps answering normally");
    b.clickEnd();
    await flush();
    const urls = b.sessionCalls().map(function (c) { return c.url; });
    assert.deepStrictEqual(urls, ["/api/session/start"],
      "no end-save is attempted for a session that never started");
  } finally { b.cleanup(); }
}

const tests = [
  ["1) listening arms one session", testListeningArmsOneSession],
  ["2) silently dead session is re-armed", testSilentlyDeadSessionIsReArmed],
  ["3) unarmable mic is honest and keeps retrying", testUnarmableMicrophoneIsHonestAndKeepsRetrying],
  ["4) unsupported language is explained, not fatal", testUnsupportedLanguageIsExplainedNotFatal],
  ["5) repeated silence is explained", testRepeatedSilenceIsExplained],
  ["6) denied permission is a final honest error", testDeniedPermissionIsAFinalHonestError],
  ["7) stale speaking flag does not sabotage the turn", testStaleSpeakingFlagDoesNotSabotageTheTurn],
  ["8) spoken turn is answered and spoken", testSpokenTurnIsAnsweredAndSpoken],
  ["9) interim alone never submits", testInterimAloneNeverSubmits],
  ["10) diagnostics opt-in and leak no speech", testDiagnosticsAreOnByDefaultOffAndLeakNoSpeech],
  ["11) mute stops re-arming", testMuteStopsReArming],
  ["12) end call stops everything", testEndCallStopsEverything],
  ["13) changing voice language re-arms recognition", testChangeVoiceLanguageReArmsRecognition],
  ["14) recognized-text diagnostics are a separate opt-in", testRecognizedTextIsSeparateOptIn],
  ["15) human-agent handoff reply is spoken verbatim", testHumanAgentHandoffReplyIsSpoken],
  ["16) call transcript is visible, labeled, chronological", testCallTranscriptIsVisibleLabeledAndOrdered],
  ["17) consent OFF stores nothing and says so", testConsentOffStoresNothingAndSaysSo],
  ["18) consent ON records turns with real intents, once", testConsentOnRecordsSessionTurnsWithIntents],
  ["19) repeated recognition callback never duplicates", testRepeatedRecognitionCallbackDoesNotDuplicate],
  ["20) unsupported recognition disables the call honestly", testUnsupportedRecognitionDisablesCallHonestly],
  ["21) no-TTS browsers still get a working text-only call", testNoTtsCallStillWorksAsTextOnly],
  ["22) session API failure never breaks the call", testSessionApiFailureDoesNotBreakTheCall],
];

(async function run() {
  let pass = 0;
  let fail = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log("PASS  " + name);
      pass += 1;
    } catch (err) {
      fail += 1;
      console.log("FAIL  " + name + " :: " + (err && err.message));
    }
  }
  console.log("\n" + pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
})();
