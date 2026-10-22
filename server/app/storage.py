import os
import shutil
import time
from pathlib import Path

from . import config, db


def transfer_dir(transfer_id: str) -> Path:
    return config.FILES_DIR / transfer_id


def part_path(transfer_id: str, idx: int) -> Path:
    return transfer_dir(transfer_id) / f"{idx}.part"


def used_bytes() -> int:
    total = 0
    root = config.FILES_DIR
    if not root.exists():
        return 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def prepare(transfer_id: str, count: int) -> None:
    d = transfer_dir(transfer_id)
    d.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        part_path(transfer_id, i).touch(exist_ok=True)


def write_chunk(transfer_id: str, idx: int, offset: int, data: bytes) -> int:
    """Write data at offset, truncating anything past the write.

    Truncation matters on resume: a client that re-sends from an earlier offset
    must not leave stale bytes from an aborted attempt beyond the new end.
    """
    path = part_path(transfer_id, idx)
    with open(path, "r+b") as fh:
        fh.seek(offset)
        fh.write(data)
        fh.truncate(offset + len(data))
    return offset + len(data)


def size_of(transfer_id: str, idx: int) -> int:
    path = part_path(transfer_id, idx)
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def remove_transfer(transfer_id: str) -> None:
    shutil.rmtree(transfer_dir(transfer_id), ignore_errors=True)


def cleanup_once() -> int:
    now = time.time()
    rows = db.query("SELECT id FROM transfer WHERE expires_at <= ?", (now,))
    removed = 0
    for row in rows:
        remove_transfer(row["id"])
        removed += 1
    if rows:
        with db.tx() as conn:
            conn.execute("DELETE FROM file WHERE transfer_id IN (SELECT id FROM transfer WHERE expires_at <= ?)", (now,))
            conn.execute("DELETE FROM transfer WHERE expires_at <= ?", (now,))
    return removed
