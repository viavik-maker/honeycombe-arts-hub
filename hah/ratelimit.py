"""In-memory sliding-window rate limits, keyed by (limit name, client key).

Kept per process, which is fine: the site runs as a single instance."""
import threading
import time
from collections import deque

# name: (max events, window in seconds)
LIMITS = {
    "public_form": (30, 60 * 60),
    "csp_report": (60, 60 * 60),
    "data_export": (10, 60 * 60),
}
MAX_KEYS = 20_000  # bound memory if someone sprays many addresses

_events = {}
_lock = threading.Lock()


def _window(name, key, now):
    limit, period = LIMITS[name]
    q = _events.get((name, key))
    if q is not None:
        while q and q[0] <= now - period:
            q.popleft()
    return q, limit


def blocked(name, key):
    """True if KEY has used up its allowance for NAME (doesn't count this call)."""
    with _lock:
        q, limit = _window(name, key, time.monotonic())
        return bool(q) and len(q) >= limit


def hit(name, key):
    """Record one event; returns False (and records nothing) if over the limit."""
    now = time.monotonic()
    with _lock:
        q, limit = _window(name, key, now)
        if q is None:
            if len(_events) >= MAX_KEYS:
                _evict(now)
            q = _events[(name, key)] = deque()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


# never dropped to make room: losing these would reset a lockout
KEEP = {"collection_pw", "staff_login_pair", "staff_login_email", "staff_totp", "staff_totp_user", "acct_login_pair",
        "acct_login_email"}


def _evict(now):
    """Make room: drop keys whose window has passed, then the least recently used of the rest (never KEEP ones)."""
    for k in [k for k, q in _events.items() if not q or q[-1] <= now - LIMITS[k[0]][1]]:
        del _events[k]
    if len(_events) >= MAX_KEYS:
        spare = sorted((q[-1], k) for k, q in _events.items() if k[0] not in KEEP)
        for _, k in spare[:max(1, len(spare) // 2)]:
            del _events[k]


def reset():
    with _lock:
        _events.clear()
