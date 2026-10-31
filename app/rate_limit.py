"""
app/rate_limit.py
─────────────────
A tiny, dependency-free in-memory rate limiter for the public API (Phase G3).

Why in-memory and hand-rolled?
- The project policy (CRITICAL RULES / G3.3) says avoid unnecessary
  dependencies and keep things lightweight for an internship-scale demo. A
  production service would use a shared store (Redis) behind a load balancer;
  that is deliberately NOT introduced here.

How it works:
- We keep a per-key list of recent request timestamps and allow at most
  ``max_requests`` inside a sliding ``window_seconds`` window. The key is chosen
  by the caller, e.g. ``"chat:127.0.0.1"`` (endpoint + client IP).

Known limitations (documented in the README + final report):
- Counts live in this process only. They are NOT shared across multiple workers
  or server restarts, so the effective global limit scales with the number of
  processes. This is acceptable for a single-process local demonstration.
- The store is pruned lazily per key on each check; a caller with a very large
  number of distinct keys could grow memory. For a loopback demo this is a
  non-issue, but a real deployment should use a proper backend.
- We use ``time.monotonic()`` so the window is immune to wall-clock changes.
"""

import threading
import time
from collections import deque

# One lock guards the whole shared map. The critical section is tiny (a couple
# of deque pops/appends), so contention is negligible for this app's scale.
_LOCK = threading.Lock()
# key -> deque[monotonic timestamp], oldest first
_hits = {}


def check(key, max_requests, window_seconds):
    """Record a request for ``key`` and say whether it is allowed.

    Returns a tuple ``(allowed, retry_after_seconds)``:
    - ``(True, 0.0)``  -> this request is within the limit (and was recorded).
    - ``(False, n)``   -> too many requests; retry in about ``n`` seconds.

    ``max_requests`` <= 0 or ``window_seconds`` <= 0 disables limiting (always
    allowed), which is how the tests opt out without touching this module.
    """
    if max_requests is None or max_requests <= 0 or not window_seconds or window_seconds <= 0:
        return True, 0.0

    now = time.monotonic()
    with _LOCK:
        hits = _hits.get(key)
        if hits is None:
            hits = deque()
            _hits[key] = hits

        # Drop timestamps that have slid out of the window.
        cutoff = now - window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()

        if len(hits) >= max_requests:
            # Time until the OLDEST still-counted hit leaves the window.
            retry_after = window_seconds - (now - hits[0])
            return False, max(0.0, retry_after)

        hits.append(now)
        return True, 0.0


def reset():
    """Clear all recorded hits (used between tests to avoid shared state)."""
    with _LOCK:
        _hits.clear()
