import time
from collections import defaultdict, deque

from . import config

_attempts: dict[str, deque] = defaultdict(deque)
_last_prune = 0.0


def _prune(now: float) -> None:
    global _last_prune
    if now - _last_prune < 60:
        return
    _last_prune = now
    for ip in list(_attempts):
        hits = _attempts[ip]
        while hits and now - hits[0] > 3600:
            hits.popleft()
        if not hits:
            _attempts.pop(ip, None)


def allow_join(ip: str) -> bool:
    """Guard the 6-digit code space against enumeration."""
    now = time.time()
    _prune(now)
    hits = _attempts[ip]
    while hits and now - hits[0] > 3600:
        hits.popleft()

    last_minute = sum(1 for t in hits if now - t <= 60)
    if last_minute >= config.JOIN_LIMIT_PER_MIN:
        return False
    if len(hits) >= config.JOIN_LIMIT_PER_HOUR:
        return False

    hits.append(now)
    return True


def reset() -> None:
    _attempts.clear()
