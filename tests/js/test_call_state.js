/*
  tests/js/test_call_state.js
  ────────────────────────────
  Head-LESS practical tests for the SIMULATED-CALL controller (static/js/call.js).

  These exercise the pure state machine — lifecycle/greeting, barge-in, mute,
  end-call teardown, the duplicate-message guard, and especially the
  STALE-RESPONSE protection (an older /api/chat reply must never be spoken after
  a newer one or after the call ends). No browser, no network, and NO new
  dependencies: only Node's built-in `assert` is used.

  Run:   node tests/js/test_call_state.js
*/

"use strict";

const assert = require("assert");
const path = require("path");

const callModule = require(path.join(__dirname, "..", "..", "static", "js", "call.js"));
const { createCallController, STATES } = callModule;

// Let pending promise .then callbacks run to completion.
function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

/** Build a controller with recording stubs so the logic runs in isolation. */
function makeCtx(overrides) {
  const clock = { t: 0 };

  const spoken = [];
  const ttsStops = { n: 0 };
  let speaking = false;
  const tts = {
    speak: function (text) { speaking = true; spoken.push(text); },
    stop: function () { ttsStops.n += 1; speaking = false; },
    isSpeaking: function () { return speaking; },
  };

  const rec = {
    counts: { start: 0, stop: 0, abort: 0 },
    start: function () { this.counts.start += 1; },
    stop: function () { this.counts.stop += 1; },
    abort: function () { this.counts.abort += 1; },
  };

  const user = [];
  const bot = [];
  const system = [];
  const status = [];
  const stateLog = [];
  const transcript = {
    addUser: function (t) { user.push(t); },
    addBot: function (t) { bot.push(t); },
    addSystem: function (t) { system.push(t); },
  };

  const pending = []; // manual deferreds so we can resolve replies out of order
  const chat = function (msg) {
    return new Promise(function (resolve, reject) {
      pending.push({ msg: msg, resolve: resolve, reject: reject });
    });
  };

  let toSeq = 0; // id counter for the controllable setTimeout stub
  const timeouts = [];

  const timers = {
    now: function () { return clock.t; },
    setInterval: function () { return 123; },
    clearInterval: function () {},
    // Controllable merge timers: finals are buffered until we run them.
    setTimeout: function (fn) { const id = ++toSeq; timeouts.push({ id: id, fn: fn }); return id; },
    clearTimeout: function (id) {
      const i = timeouts.findIndex(function (x) { return x.id === id; });
      if (i >= 0) timeouts.splice(i, 1);
    },
  };
  function runDueTimers() {
    const due = timeouts.slice();
    timeouts.length = 0;
    due.forEach(function (x) { x.fn(); });
  }
  /**
   * Run merge timers the way real time would: repeatedly, advancing the clock
   * until the BOUNDED merge wait has fully elapsed. A turn that does not look
   * finished yet (no sentence terminator) is now extended rather than cut in
   * half, so a single pass is no longer enough to submit it.
   */
  function drainTimers() {
    for (let pass = 0; pass < 10; pass++) {
      clock.t += 2500; // past maxMergeMs (2400ms) -> the cap is reached
      const due = timeouts.slice();
      timeouts.length = 0;
      if (!due.length) break;
      due.forEach(function (x) { x.fn(); });
    }
  }

  const options = Object.assign(
    {
      tts: tts,
      recognition: rec,
      transcript: transcript,
      status: function (t, err) { status.push({ t: t, err: err }); },
      state: function (s, d) { stateLog.push({ s: s, d: d }); },
      getLang: function () { return "en-US"; },
      chat: chat,
      timers: timers,
    },
    overrides || {}
  );

  const controller = createCallController(options);
  return {
    controller: controller, clock: clock, spoken: spoken, ttsStops: ttsStops,
    rec: rec, user: user, bot: bot, system: system, status: status,
    stateLog: stateLog, pending: pending, isSpeaking: function () { return speaking; },
    runDueTimers: runDueTimers, drainTimers: drainTimers,
  };
}

// ── 1) Lifecycle: greeting is spoken, then the call resumes listening ──
async function testLifecycleAndGreeting() {
  const c = makeCtx();
  assert.strictEqual(c.controller.getState().state, STATES.IDLE);

  c.controller.startCall();
  assert.ok(c.spoken.length >= 1, "a greeting is spoken on start");
  assert.strictEqual(c.controller.getState().state, STATES.SPEAKING);
  assert.strictEqual(c.rec.counts.start, 0, "recognition is not armed while greeting plays");

  c.controller.onTtsEnded(); // greeting finished
  assert.strictEqual(c.controller.getState().state, STATES.LISTENING);
  assert.strictEqual(c.rec.counts.start, 1, "listening starts exactly one session");
}

// ── 2) Stale-response protection: only the newest reply is ever spoken ──
async function testStaleResponseProtection() {
  const c = makeCtx();
  c.controller.startCall();
  c.controller.onTtsEnded();

  c.controller.submitUtterance("where is ORD-1");
  assert.strictEqual(c.pending.length, 1);
  // Barge-in with a second utterance supersedes the first request.
  c.controller.submitUtterance("shipping time");
  assert.strictEqual(c.pending.length, 2);

  // Newest (B) resolves FIRST -> it should be spoken.
  c.pending[1].resolve({ intent: "faq", reply: "RB-shipping", source: "faq_kb:shipping:sample" });
  await flush();
  assert.ok(c.bot.includes("RB-shipping"), "latest reply is spoken");
  assert.strictEqual(c.controller.getState().state, STATES.SPEAKING);

  // Older (A) resolves LATE -> must be ignored (no render, no speech).
  const botCountAfterB = c.bot.length;
  c.pending[0].resolve({ intent: "order_status", reply: "RA-stale", source: "orders_db" });
  await flush();
  assert.ok(!c.bot.includes("RA-stale"), "stale reply must NOT be spoken");
  assert.strictEqual(c.bot.length, botCountAfterB, "stale reply adds nothing to the transcript");
}

// ── 3) Mute stops capture and ignores speech; unmute resumes once ──
async function testMuteBehavior() {
  const c = makeCtx();
  c.controller.startCall();
  c.controller.onTtsEnded(); // LISTENING, start = 1

  c.controller.toggleMute();
  assert.strictEqual(c.controller.getState().state, STATES.MUTED);
  assert.strictEqual(c.controller.getState().muted, true);
  assert.ok(c.rec.counts.stop >= 1, "mute stops the recognition session (capture, not just ignore)");

  const pendingBefore = c.pending.length;
  c.controller.submitUtterance("ignored while muted");
  c.controller.handleSpeech("muted speech", true);
  assert.strictEqual(c.pending.length, pendingBefore, "no message is sent while muted");
  assert.ok(!c.user.includes("ignored while muted"), "muted utterance is not recorded");

  c.controller.toggleMute(); // unmute
  assert.strictEqual(c.controller.getState().state, STATES.LISTENING);
  assert.strictEqual(c.controller.getState().muted, false);
  assert.strictEqual(c.rec.counts.start, 2, "unmute resumes recognition exactly once (no duplicate session)");
}

// ── 4) End call tears everything down and invalidates a pending reply ──
async function testEndCallInvalidatesPending() {
  const c = makeCtx();
  c.controller.startCall();
  c.controller.onTtsEnded();
  c.controller.submitUtterance("hello there");
  assert.strictEqual(c.pending.length, 1);

  c.controller.endCall();
  assert.strictEqual(c.controller.getState().state, STATES.ENDED);
  assert.strictEqual(c.controller.getState().callActive, false);
  assert.ok(c.rec.counts.abort >= 1, "end aborts recognition");
  assert.ok(c.ttsStops.n >= 1, "end cancels speech synthesis");

  const botBefore = c.bot.length;
  c.pending[0].resolve({ intent: "greeting", reply: "should-not-speak", source: "fixed" });
  await flush();
  assert.ok(!c.bot.includes("should-not-speak"), "a reply arriving after end is dropped");
  assert.strictEqual(c.bot.length, botBefore, "nothing is rendered after the call ended");
}

// ── 5) Duplicate-message guard suppresses a same-text resend in-window ──
async function testDuplicateMessageGuard() {
  const c = makeCtx({ duplicateWindowMs: 1500 });
  c.controller.startCall();
  c.controller.onTtsEnded();

  c.controller.submitUtterance("same text");
  const first = c.pending.length;
  c.controller.submitUtterance("same text"); // identical, within window -> ignored
  assert.strictEqual(c.pending.length, first, "duplicate within window is suppressed");

  c.clock.t = 2000; // beyond the window -> allowed again
  c.controller.submitUtterance("same text");
  assert.strictEqual(c.pending.length, first + 1, "same text after the window is accepted again");
}

// ── 6) Barge-in: speech during AI audio cancels TTS, then captures the turn ──
async function testBargeInCancelsTts() {
  const c = makeCtx();
  c.controller.startCall(); // SPEAKING greeting (speaking = true)

  c.controller.handleSpeech("hold on a second", false); // interim interruption
  assert.ok(c.ttsStops.n >= 1, "interim barge-in promptly cancels TTS");
  assert.strictEqual(c.controller.getState().state, STATES.LISTENING);

  c.controller.handleSpeech("hold on a second", true); // the full utterance
  c.drainTimers(); // the bounded merge wait elapses -> submitted as one turn
  assert.strictEqual(c.pending.length, 1, "the interrupted utterance is then submitted");
}

// ── 7) Greeting FAILURE: if speech cannot start we stay honest and keep the
//      call alive (resume listening) instead of falsely claiming it played. ──
async function testGreetingFailure() {
  let started = 0;
  const thrown = { n: 0 };
  const throwingTts = {
    speak: function () { thrown.n += 1; throw new Error("no audio engine"); },
    stop: function () {},
    isSpeaking: function () { return false; },
  };
  const c = makeCtx({
    tts: throwingTts,
    recognition: {
      counts: { start: 0, stop: 0, abort: 0 },
      start: function () { this.counts.start += 1; started = this.counts.start; },
      stop: function () {},
      abort: function () {},
    },
  });

  c.controller.startCall();
  // speak() threw inside speakLine's try/catch -> we must NOT stay stuck in
  // Speaking; we report an error status and fall back to Listening.
  assert.strictEqual(thrown.n, 1, "greeting tried to speak once");
  assert.strictEqual(c.controller.getState().state, STATES.LISTENING,
    "a failed greeting still leaves the call listening");
  assert.ok(started >= 1, "recognition is armed after a failed greeting");
  assert.ok(c.status.some(function (s) { return s.err === true; }),
    "a failed greeting surfaces an error status (no false success)");
}

// ── 8) VOICE-ONLY: with NO transcript sink (the browser glue case) the call
//      still recognises, processes and speaks the assistant reply. ──
async function testHiddenTranscriptStillResponds() {
  const spoken = [];
  let speaking = false;
  const tts = {
    speak: function (t) { speaking = true; spoken.push(t); },
    stop: function () { speaking = false; },
    isSpeaking: function () { return speaking; },
  };
  const rec = {
    counts: { start: 0, stop: 0, abort: 0 },
    start: function () { this.counts.start += 1; },
    stop: function () {}, abort: function () {},
  };
  const pending = [];
  const controller = createCallController({
    tts: tts,
    recognition: rec,
    status: function () {},
    state: function () {},
    getLang: function () { return "en-US"; },
    chat: function (m) { return new Promise(function (res) { pending.push({ m: m, res: res }); }); },
    timers: { now: function () { return 0; }, setInterval: function () { return 1; }, clearInterval: function () {} },
    // NOTE: deliberately NO `transcript` option -> mirrors the voice-only UI.
  });

  controller.startCall();
  controller.onTtsEnded();
  controller.submitUtterance("where is my order");
  assert.strictEqual(pending.length, 1, "hiding the transcript must not stop the backend request");

  pending[0].res({ intent: "order_status", reply: "It is on the way.", source: "orders_db" });
  await flush();
  assert.ok(spoken.includes("It is on the way."), "the reply is still spoken aloud");
  assert.strictEqual(controller.getState().state, STATES.SPEAKING);
}

// ── 9) Self-echo guard: the AI's OWN line, fed back by the mic, is never
//      treated as a new customer message. ──
async function testAiEchoNotSubmitted() {
  const c = makeCtx();
  c.controller.startCall();
  c.controller.onTtsEnded();
  c.controller.submitUtterance("track my parcel");
  assert.strictEqual(c.pending.length, 1);

  c.pending[0].resolve({ intent: "order_status", reply: "Your parcel is on the way.", source: "orders_db" });
  await flush();
  // The reply has become lastSpoken. If the recognizer echoes it back verbatim,
  // the self-echo guard must suppress it (no second backend request).
  const before = c.pending.length;
  c.controller.handleSpeech("Your parcel is on the way.", true);
  assert.strictEqual(c.pending.length, before, "the AI's own echoed line is not resubmitted");
}

// ── 10) A SPOKEN order number is captured and forwarded VERBATIM to /api/chat,
//        and the DB answer is spoken back (the number-recognition call path). ──
async function testSpokenOrderNumberForwarded() {
  const c = makeCtx();
  c.controller.startCall();
  c.controller.onTtsEnded(); // LISTENING

  // Interim alone must NOT submit (waits for the completed utterance).
  c.controller.handleSpeech("order number one zero zero two", false);
  assert.strictEqual(c.pending.length, 0, "an interim result is not submitted");

  // The final result is sent, unaltered, so the backend can normalize it.
  c.controller.handleSpeech("order number one zero zero two", true);
  c.drainTimers(); // flush the merge buffer -> one utterance is sent
  assert.strictEqual(c.pending.length, 1, "the final spoken order number is sent to chat");
  assert.strictEqual(
    c.pending[0].msg, "order number one zero zero two",
    "the transcript is forwarded verbatim (never mangled in the call layer)"
  );

  c.pending[0].resolve({
    intent: "order_status", source: "orders_db",
    reply: "Order ORD-1002 for 1 x Desk Lamp is currently shipped.",
  });
  await flush();
  assert.ok(c.spoken.some(function (t) { return t.indexOf("ORD-1002") !== -1; }),
    "the grounded DB answer is spoken aloud");
  assert.strictEqual(c.controller.getState().state, STATES.SPEAKING);
}

// ── 11) A bare number reply (after "please repeat your order number") is still
//        recognized as an utterance and sent — it must never be discarded. ──
async function testBareNumberReplyIsCaptured() {
  const c = makeCtx();
  c.controller.startCall();
  c.controller.onTtsEnded();

  c.controller.handleSpeech("one zero zero two", true);
  c.drainTimers();
  assert.strictEqual(c.pending.length, 1, "a number-only utterance is captured and sent");
  assert.strictEqual(c.pending[0].msg, "one zero zero two");
}

// ── 12) THE reported bug: a slowly dictated number arrives as MULTIPLE Chrome
//        "final" results. They must merge into ONE utterance so the backend
//        sees the complete number instead of a fragment that looks "unclear". ──
async function testFragmentedOrderNumberMerges() {
  const c = makeCtx();
  c.controller.startCall();
  c.controller.onTtsEnded();

  c.controller.handleSpeech("one zero zero", true);   // first fragment
  assert.strictEqual(c.pending.length, 0, "a lone fragment is not submitted yet");
  c.controller.handleSpeech("one", true);             // continuation within window
  assert.strictEqual(c.pending.length, 0, "still merging until the pause ends");
  c.drainTimers();                                     // bounded merge wait closes
  assert.strictEqual(c.pending.length, 1, "the merged utterance is sent once");
  assert.strictEqual(c.pending[0].msg, "one zero zero one", "fragments joined in order");
}

// ── 13) The AI's OWN line must not be read back as customer input -- even when
//        the mic re-hears only a FRAGMENT (a substring) of what it just said. ──
async function testEchoFragmentOfAiLineSuppressed() {
  const c = makeCtx();
  c.controller.startCall();          // greeting -> lastSpoken = greeting
  c.controller.onTtsEnded();
  c.controller.handleSpeech("Shop Support Assistant", true); // substring of greeting
  c.runDueTimers();
  assert.strictEqual(c.pending.length, 0, "a re-heard fragment of the AI's line is dropped");
}

// ── 14) Muting discards any half-spoken number still in the merge buffer. ──
async function testMuteDiscardsBufferedSpeech() {
  const c = makeCtx();
  c.controller.startCall();
  c.controller.onTtsEnded();
  c.controller.handleSpeech("one zero", true);  // buffered, not yet sent
  assert.strictEqual(c.pending.length, 0);
  c.controller.toggleMute();                     // muting discards the buffer
  c.runDueTimers();
  assert.strictEqual(c.pending.length, 0, "buffered speech is discarded on mute");
}

// ── 15) Ending the call discards the merge buffer too (no post-hangup submit). ──
async function testEndCallDiscardsBufferedSpeech() {
  const c = makeCtx();
  c.controller.startCall();
  c.controller.onTtsEnded();
  c.controller.handleSpeech("one zero", true);
  c.controller.endCall();
  c.runDueTimers();
  assert.strictEqual(c.pending.length, 0, "buffered speech is discarded on end-call");
}

// ── 16) FIRST-TURN FIX: an echo of the greeting must not be MERGED into the
//        customer's real utterance. Screening happens per fragment, before the
//        merge buffer, so the backend receives the clean number only.
async function testEchoDoesNotPolluteMergedNumber() {
  const c = makeCtx();
  c.controller.startCall();          // greeting -> lastSpoken
  c.controller.onTtsEnded();

  c.controller.handleSpeech("How can I help you today?", true); // re-heared AI line
  c.controller.handleSpeech("one zero zero one", true);          // the real words
  c.drainTimers();

  assert.strictEqual(c.pending.length, 1, "exactly one turn is sent");
  assert.strictEqual(
    c.pending[0].msg, "one zero zero one",
    "the echoed AI question is stripped and the number survives intact",
  );
}

// ── 17) Removing the session-end flush must NOT strand input: the merge window
//        alone still guarantees a lone fragment reaches the backend (so it can
//        honestly ask for a repeat instead of going silent). The wait is now
//        BOUNDED: it extends past one window, but never forever.
async function testLoneFragmentStillSentAfterWindow() {
  const c = makeCtx();
  c.controller.startCall();
  c.controller.onTtsEnded();

  c.controller.handleSpeech("one zero zero", true);
  assert.strictEqual(c.pending.length, 0, "waits briefly for the rest of the number");
  c.runDueTimers();
  assert.strictEqual(c.pending.length, 0, "an unfinished turn extends the wait once");
  c.drainTimers();
  assert.strictEqual(c.pending.length, 1, "the fragment is still sent after the bounded wait");
  assert.strictEqual(c.pending[0].msg, "one zero zero");
}

// ── 19) THE reported voice bug: a normal SENTENCE spoken with a natural pause
//        was cut into two turns ("What is the status" / "of my order?"), each of
//        which the backend could only answer with its honest "I'm not sure" line
//        - while the same sentence typed in Text Chat worked. The pause must not
//        end the turn: the fragments belong together as ONE request. ──
async function testSentenceAcrossNaturalPauseStaysOneTurn() {
  const c = makeCtx();
  c.controller.startCall();
  c.controller.onTtsEnded();

  c.controller.handleSpeech("What is the status", true);   // final, then a breath
  c.runDueTimers();                                         // one merge window passes
  assert.strictEqual(c.pending.length, 0, "half a question is not submitted yet");

  c.controller.handleSpeech("of my order?", true);          // the speaker continues
  assert.strictEqual(c.pending.length, 1, "the completed sentence goes as one turn");
  assert.strictEqual(c.pending[0].msg, "What is the status of my order?");

  c.drainTimers();
  assert.strictEqual(c.pending.length, 1, "and it is never sent a second time");
}

// ── 20) The other side of the same fix: waiting must not make the call slow.
//        Recognition punctuated the utterance -> it IS complete, so it goes
//        immediately, without burning the merge window at all. ──
async function testCompletedSentenceIsSentImmediately() {
  const c = makeCtx();
  c.controller.startCall();
  c.controller.onTtsEnded();

  c.controller.handleSpeech("Where is my order?", true);
  assert.strictEqual(c.pending.length, 1, "a punctuated turn needs no waiting window");
  assert.strictEqual(c.pending[0].msg, "Where is my order?");
  c.runDueTimers();
  assert.strictEqual(c.pending.length, 1, "no stale timer resends it");
}

// ── 21) Bounded, generic, and content-blind: however long an UNPUNCTUATED turn
//        takes, it is still delivered once the cap is reached, and the option is
//        configurable for tests. Nothing about a specific question is encoded. ──
async function testUnfinishedTurnIsBoundedByConfigurableCap() {
  const c = makeCtx({ utteranceBufferMs: 100, maxMergeMs: 200 });
  c.controller.startCall();
  c.controller.onTtsEnded();

  c.controller.handleSpeech("tell me about my", true);
  c.drainTimers();
  assert.strictEqual(c.pending.length, 1, "an unfinished turn is still delivered");
  assert.strictEqual(c.pending[0].msg, "tell me about my");
}

// ── 22) Barge-in follows OUR OWN state, not the engine's flag. The recording TTS
//        stub here is still reporting isSpeaking() === true after the greeting
//        ended, which is exactly the stale state Chrome leaves speechSynthesis in
//        (speaking/pending can stay true long after a line finishes). Cancelling
//        speech on that stale signal aborts the recognition session that is
//        carrying the customer's words, so the turn never arrived at all.
async function testStaleEngineFlagDoesNotCancelAudio() {
  const c = makeCtx();
  c.controller.startCall();
  c.controller.onTtsEnded(); // LISTENING now, greeting audio over
  assert.strictEqual(c.isSpeaking(), true, "the engine still claims it is speaking");

  const before = c.ttsStops.n;
  c.controller.handleSpeech("where is my order", true);
  assert.strictEqual(c.ttsStops.n, before, "no speech is cancelled outside the Speaking state");

  c.drainTimers();
  assert.strictEqual(c.pending.length, 1, "and the customer's turn still reaches the backend");
}

// ── 23) The diagnostic hook shows WHERE a turn stops (speech -> buffer -> submit
//        -> reply), names the reason for a drop, and never carries the words
//        themselves. It is a seer: omit it and behaviour is identical. ──
async function testEventTraceShowsPipelineStages() {
  const events = [];
  const c = makeCtx({
    onEvent: function (stage, detail) { events.push(stage + (detail ? " " + detail : "")); },
  });
  c.controller.startCall();
  c.controller.onTtsEnded();

  c.controller.handleSpeech("where is my order", true);
  c.drainTimers();
  c.pending[0].resolve({ intent: "order_status_missing_id", reply: "Please share it.", source: "none" });
  await flush();

  const trace = events.join("|");
  assert.ok(/speech final=true/.test(trace), "recognition is traced");
  assert.ok(/buffered len=17/.test(trace), "the buffer stage is traced by length");
  assert.ok(/submit len=17/.test(trace), "the submission is traced");
  assert.ok(/reply len=16/.test(trace), "and so is the backend answer");
  assert.strictEqual(trace.indexOf("where is my order"), -1,
    "no recognized words are ever recorded, only lengths");

  c.controller.submitUtterance("   ");
  assert.ok(/submit-dropped reason=empty/.test(events.join("|")),
    "a refusal to send is explained, not silent");

  // Inert by default: a controller with no onEvent still answers normally.
  const plain = makeCtx();
  plain.controller.startCall();
  plain.controller.onTtsEnded();
  plain.controller.handleSpeech("where is my order", true);
  plain.drainTimers();
  assert.strictEqual(plain.pending.length, 1, "omitting the hook changes nothing");
}

// ── 18) The explicit boundary hook sends the pending turn immediately and does
//        not leave the merge timer behind to send it a second time.
async function testFlushSpeechSubmitsPendingTurnOnce() {
  const c = makeCtx();
  c.controller.startCall();
  c.controller.onTtsEnded();

  c.controller.handleSpeech("what is your return policy", true);
  assert.strictEqual(c.pending.length, 0, "buffered until the window ends");
  c.controller.flushSpeech();
  assert.strictEqual(c.pending.length, 1, "flush sends the pending turn now");
  assert.strictEqual(c.pending[0].msg, "what is your return policy");
  c.runDueTimers();
  assert.strictEqual(c.pending.length, 1, "the cancelled timer must not resend it");
}

(async function run() {
  const tests = [
    testLifecycleAndGreeting,
    testStaleResponseProtection,
    testMuteBehavior,
    testEndCallInvalidatesPending,
    testDuplicateMessageGuard,
    testBargeInCancelsTts,
    testGreetingFailure,
    testHiddenTranscriptStillResponds,
    testAiEchoNotSubmitted,
    testSpokenOrderNumberForwarded,
    testBareNumberReplyIsCaptured,
    testFragmentedOrderNumberMerges,
    testEchoFragmentOfAiLineSuppressed,
    testMuteDiscardsBufferedSpeech,
    testEndCallDiscardsBufferedSpeech,
    testEchoDoesNotPolluteMergedNumber,
    testLoneFragmentStillSentAfterWindow,
    testFlushSpeechSubmitsPendingTurnOnce,
    testSentenceAcrossNaturalPauseStaysOneTurn,
    testCompletedSentenceIsSentImmediately,
    testUnfinishedTurnIsBoundedByConfigurableCap,
    testStaleEngineFlagDoesNotCancelAudio,
    testEventTraceShowsPipelineStages,
  ];
  let pass = 0;
  let fail = 0;
  for (const fn of tests) {
    try {
      await fn();
      console.log("PASS  " + fn.name);
      pass += 1;
    } catch (err) {
      fail += 1;
      console.log("FAIL  " + fn.name + " :: " + (err && err.message));
    }
  }
  console.log("\n" + pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
})();
