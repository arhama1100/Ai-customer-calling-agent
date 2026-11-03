/*
  static/js/sessions.js
  ─────────────────────
  The staff "Simulated session history" view (Task 5 D).

  Reads /api/sessions (metadata + honest summaries ONLY - the raw transcript
  is deliberately not listed there) and renders one card per stored session:
  date, time, channel, status, detected intents, outcome, summary, and a
  clear "Requires staff follow-up" label when the customer asked for a human.

  Honesty rules enforced here (not just server-side):
  - These are browser-SIMULATED calls. Nothing was phoned, no agent notified.
  - Only consented sessions exist at all; the empty state says so.
  - Every string is written with textContent - never innerHTML.
*/

(function () {
  "use strict";

  // Browser-only module: stay inert outside a page (Node tests never load it).
  if (typeof document === "undefined" || !document.getElementById) {
    return;
  }

  var listEl = document.getElementById("history-list");
  var refreshBtn = document.getElementById("history-refresh");
  if (!listEl) {
    return; // page has no history section
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text; // XSS-safe by construction
    return node;
  }

  function fmtTime(iso) {
    if (!iso) return "—";
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      return d.toLocaleString();
    } catch (err) {
      return iso;
    }
  }

  var OUTCOME_LABEL = {
    resolved: "Answered (see summary)",
    unresolved: "Not fully answered",
    escalated: "Human agent requested",
    no_conversation: "No substantive exchange",
  };

  function renderSession(item) {
    var card = el("article", "history-card");
    var head = el("div", "history-head");
    head.appendChild(
      el(
        "span",
        "history-when",
        fmtTime(item.started_at) + (item.ended_at ? " → " + fmtTime(item.ended_at) : " → (still open)")
      )
    );
    head.appendChild(el("span", "history-channel", (item.channel || "voice") + " · " + (item.status || "?")));
    if (item.human_followup) {
      // Explicitly LABELED as needing action; explicitly NOT claiming anyone was told.
      head.appendChild(
        el("span", "history-followup", "Requires staff follow-up (no agent was auto-notified)")
      );
    }
    card.appendChild(head);

    var meta = el("div", "history-meta");
    meta.appendChild(el("span", "history-intent", "Intents: " + (item.primary_intent || "none")));
    meta.appendChild(
      el("span", "history-outcome", "Outcome: " + (OUTCOME_LABEL[item.outcome] || item.outcome || "pending"))
    );
    card.appendChild(meta);

    if (item.summary) {
      card.appendChild(el("p", "history-summary", item.summary));
    }
    return card;
  }

  function setStatus(text, isError) {
    listEl.textContent = "";
    var p = el("p", "history-note" + (isError ? " error" : ""), text);
    listEl.appendChild(p);
  }

  function load() {
    setStatus("Loading stored sessions…", false);
    fetch("/api/sessions")
      .then(function (r) {
        if (!r.ok) throw new Error("server error (" + r.status + ")");
        return r.json();
      })
      .then(function (data) {
        var items = (data && data.sessions) || [];
        listEl.textContent = ""; // replace, never append twice (no duplicates on refresh)
        if (!items.length) {
          setStatus(
            "No stored sessions yet. Sessions appear here only after a customer " +
              "starts a simulated call with the recording box ticked.",
            false
          );
          return;
        }
        items.forEach(function (item) {
          listEl.appendChild(renderSession(item));
        });
      })
      .catch(function () {
        setStatus("The session history could not be loaded just now.", true);
      });
  }

  if (refreshBtn) {
    refreshBtn.addEventListener("click", load);
  }
  // call.js announces every saved session so the fresh record appears without
  // the staff having to press Refresh.
  if (document.addEventListener) {
    document.addEventListener("nova:session-saved", load);
  }
  load();
})();
