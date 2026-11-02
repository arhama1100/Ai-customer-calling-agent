/*
  static/js/nova.js
  ─────────────────
  Landing-page niceties for Nova AI. PROGRESSIVE ENHANCEMENT ONLY: every
  "Try ..." link is a real anchor, so scrolling works even without this file.
  All this adds is moving keyboard focus into the actual control after the
  smooth scroll begins, so "Try Voice Agent" / "Try Text Chat" land the user
  ON the working experience (Start Call button / chat input), not just near it.

  It never touches the call controller, chat pipeline, or any state.
*/
(function () {
  "use strict";

  function focusAfterClick(linkId, targetId) {
    var link = document.getElementById(linkId);
    if (!link) return;
    link.addEventListener("click", function () {
      var target = document.getElementById(targetId);
      if (!target || target.disabled) return; // honest: focus only a usable control
      // Let the CSS smooth scroll start, then focus WITHOUT causing a second jump.
      window.setTimeout(function () {
        try { target.focus({ preventScroll: true }); } catch (e) { target.focus(); }
      }, 420);
    });
  }

  focusAfterClick("try-voice", "call-start"); // hero "Try Voice Agent" -> Start Call
  focusAfterClick("try-text", "message");     // "Try Text Chat" -> chat input
})();
