import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))

MAX_FILE_SIZE = _env_int("MAX_FILE_SIZE", 2 * 1024**3)
MAX_FILES = _env_int("MAX_FILES", 20)
QUOTA_BYTES = _env_int("QUOTA_BYTES", 10 * 1024**3)

CODE_TTL_MIN = _env_int("CODE_TTL_MIN", 30)
RETENTION_HOURS = _env_int("RETENTION_HOURS", 24)
CLEANUP_INTERVAL_SEC = _env_int("CLEANUP_INTERVAL_SEC", 300)

JOIN_LIMIT_PER_MIN = _env_int("JOIN_LIMIT_PER_MIN", 10)
JOIN_LIMIT_PER_HOUR = _env_int("JOIN_LIMIT_PER_HOUR", 60)

P2P_TIMEOUT_MS = _env_int("P2P_TIMEOUT_MS", 8000)
P2P_CHUNK_SIZE = _env_int("P2P_CHUNK_SIZE", 16 * 1024)
RELAY_CHUNK_SIZE = _env_int("RELAY_CHUNK_SIZE", 4 * 1024 * 1024)

STUN_URLS = [
    u.strip()
    for u in os.getenv(
        "STUN_URLS",
        "stun:stun.l.google.com:19302,stun:stun.cloudflare.com:3478,stun:stun.qq.com:3478",
    ).split(",")
    if u.strip()
]

DB_PATH = DATA_DIR / "transfer.db"
FILES_DIR = DATA_DIR / "files"

# The Android shell serves its UI from http://localhost, so its requests to the
# server's IP are cross-origin and must be allowed explicitly.
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost,https://localhost,capacitor://localhost").split(",")
    if o.strip()
]
