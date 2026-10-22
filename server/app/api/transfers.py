import time

from fastapi import APIRouter, HTTPException, Request

from .. import codes, config, db, ratelimit, storage

router = APIRouter(prefix="/api")


def _client_ip(request: Request) -> str:
    # Behind a reverse proxy every request looks like it comes from the proxy, so
    # the left-most forwarded address is used when present.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _now() -> float:
    return time.time()


def _get_transfer(token: str):
    row = db.one("SELECT * FROM transfer WHERE token = ?", (token,))
    if row is None:
        raise HTTPException(status_code=404, detail="传输不存在或已失效")
    if row["expires_at"] <= _now():
        raise HTTPException(status_code=410, detail="传输已过期")
    return row


def _authorize(row, key: str | None, role: str) -> None:
    if not key:
        raise HTTPException(status_code=401, detail="缺少访问密钥")
    if role == "sender" and key != row["sender_key"]:
        raise HTTPException(status_code=403, detail="发送方密钥无效")
    if role == "receiver" and (row["receiver_key"] is None or key != row["receiver_key"]):
        raise HTTPException(status_code=403, detail="接收方密钥无效")


def _files_of(transfer_id: str) -> list[dict]:
    rows = db.query(
        "SELECT idx, name, size, mime, received, sha256, status FROM file WHERE transfer_id = ? ORDER BY idx",
        (transfer_id,),
    )
    return [
        {
            "idx": r["idx"],
            "name": r["name"],
            "size": r["size"],
            "mime": r["mime"],
            "received": r["received"],
            "sha256": r["sha256"],
            "status": r["status"],
        }
        for r in rows
    ]


def _payload(row) -> dict:
    return {
        "token": row["token"],
        "code": row["code"],
        "transport": row["transport"],
        "status": row["status"],
        "created_at": row["created_at"],
        "code_expires_at": row["code_expires_at"],
        "expires_at": row["expires_at"],
        "joined": row["receiver_key"] is not None,
        "files": _files_of(row["id"]),
        "stun": config.STUN_URLS,
        "p2p_chunk_size": config.P2P_CHUNK_SIZE,
        "relay_chunk_size": config.RELAY_CHUNK_SIZE,
        "p2p_timeout_ms": config.P2P_TIMEOUT_MS,
    }


@router.get("/config")
def get_config():
    return {
        "stun": config.STUN_URLS,
        "max_file_size": config.MAX_FILE_SIZE,
        "max_files": config.MAX_FILES,
        "p2p_chunk_size": config.P2P_CHUNK_SIZE,
        "relay_chunk_size": config.RELAY_CHUNK_SIZE,
        "p2p_timeout_ms": config.P2P_TIMEOUT_MS,
        "retention_hours": config.RETENTION_HOURS,
    }


@router.post("/transfers")
async def create_transfer(request: Request):
    body = await request.json()
    files = body.get("files") or []
    if not files:
        raise HTTPException(status_code=400, detail="没有文件")
    if len(files) > config.MAX_FILES:
        raise HTTPException(status_code=400, detail=f"文件数量超过上限 {config.MAX_FILES}")

    total = 0
    cleaned = []
    for item in files:
        name = str(item.get("name") or "").strip()
        size = item.get("size")
        if not name or not isinstance(size, int) or size < 0:
            raise HTTPException(status_code=400, detail="文件信息不完整")
        if size > config.MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail=f"{name} 超过单文件上限")
        total += size
        cleaned.append(
            {
                "name": name[:255],
                "size": size,
                "mime": str(item.get("mime") or "")[:120],
            }
        )

    if storage.used_bytes() + total > config.QUOTA_BYTES:
        raise HTTPException(status_code=507, detail="服务器存储空间不足，请稍后再试")

    now = _now()
    transfer_id = codes.new_token()
    token = codes.new_token()
    sender_key = codes.new_key()
    code = codes.new_code()
    expires_at = now + config.RETENTION_HOURS * 3600

    with db.tx() as conn:
        conn.execute(
            "INSERT INTO transfer (id, code, token, sender_key, receiver_key, transport, status,"
            " created_at, code_expires_at, expires_at) VALUES (?,?,?,?,NULL,'pending','waiting',?,?,?)",
            (transfer_id, code, token, sender_key, now, now + config.CODE_TTL_MIN * 60, expires_at),
        )
        for idx, item in enumerate(cleaned):
            conn.execute(
                "INSERT INTO file (transfer_id, idx, name, size, mime) VALUES (?,?,?,?,?)",
                (transfer_id, idx, item["name"], item["size"], item["mime"]),
            )

    storage.prepare(transfer_id, len(cleaned))

    row = db.one("SELECT * FROM transfer WHERE id = ?", (transfer_id,))
    out = _payload(row)
    out["sender_key"] = sender_key
    return out


@router.post("/transfers/join")
async def join_transfer(request: Request):
    body = await request.json()
    code = str(body.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="请输入验证码")

    client = _client_ip(request)
    if not ratelimit.allow_join(client):
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")

    row = db.one("SELECT * FROM transfer WHERE code = ?", (code,))
    if row is None or row["expires_at"] <= _now():
        raise HTTPException(status_code=404, detail="验证码无效或已过期")

    # A transfer has exactly one receiver identity. The code stays usable until
    # the transfer expires so the receiver can resume from another device or
    # after a refresh, and every join hands out the same receiver key.
    receiver_key = row["receiver_key"]
    if receiver_key is None:
        receiver_key = codes.new_key()
        with db.tx() as conn:
            conn.execute(
                "UPDATE transfer SET receiver_key = ?, status = 'joined' WHERE id = ? AND receiver_key IS NULL",
                (receiver_key, row["id"]),
            )
        row = db.one("SELECT * FROM transfer WHERE id = ?", (row["id"],))
        receiver_key = row["receiver_key"]

    out = _payload(row)
    out["receiver_key"] = receiver_key
    return out


@router.get("/transfers/{token}")
def get_transfer(token: str, key: str = ""):
    row = _get_transfer(token)
    if key not in (row["sender_key"], row["receiver_key"]):
        raise HTTPException(status_code=403, detail="密钥无效")
    return _payload(row)


@router.post("/transfers/{token}/chunk")
async def upload_chunk(token: str, idx: int, request: Request, key: str = "", offset: int = 0):
    row = _get_transfer(token)
    _authorize(row, key, "sender")

    frow = db.one("SELECT * FROM file WHERE transfer_id = ? AND idx = ?", (row["id"], idx))
    if frow is None:
        raise HTTPException(status_code=404, detail="文件不存在")

    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="空数据块")
    if offset + len(data) > frow["size"]:
        raise HTTPException(status_code=400, detail="数据超出文件声明大小")
    current = storage.size_of(row["id"], idx)
    if offset > current:
        raise HTTPException(
            status_code=409,
            detail=f"偏移不连续：服务器已有 {current} 字节，请求从 {offset} 开始",
        )

    written = storage.write_chunk(row["id"], idx, offset, data)
    with db.tx() as conn:
        conn.execute(
            "UPDATE file SET received = ? WHERE transfer_id = ? AND idx = ?",
            (written, row["id"], idx),
        )
        if written >= frow["size"]:
            conn.execute(
                "UPDATE file SET status = 'uploaded' WHERE transfer_id = ? AND idx = ?",
                (row["id"], idx),
            )
        else:
            # A resumed write can truncate a file that was previously complete.
            conn.execute(
                "UPDATE file SET status = 'uploading' WHERE transfer_id = ? AND idx = ?",
                (row["id"], idx),
            )
        conn.execute(
            "UPDATE transfer SET transport = 'relay', status = 'relaying' WHERE id = ? AND transport != 'relay'",
            (row["id"],),
        )
    return {"idx": idx, "received": written, "size": frow["size"]}


@router.post("/transfers/{token}/progress")
async def report_progress(token: str, request: Request, key: str = ""):
    """Receiver-side progress report, used for relay resume and P2P verification."""
    row = _get_transfer(token)
    _authorize(row, key, "receiver")
    body = await request.json()
    files = body.get("files") or []
    with db.tx() as conn:
        for item in files:
            idx = item.get("idx")
            received = item.get("received")
            sha = item.get("sha256")
            if not isinstance(idx, int) or not isinstance(received, int):
                continue
            conn.execute(
                "UPDATE file SET received = ? WHERE transfer_id = ? AND idx = ?",
                (received, row["id"], idx),
            )
            if sha:
                conn.execute(
                    "UPDATE file SET sha256 = ? WHERE transfer_id = ? AND idx = ?",
                    (str(sha)[:64], row["id"], idx),
                )
    return {"ok": True}


@router.post("/transfers/{token}/verify")
async def verify_hashes(token: str, request: Request, key: str = ""):
    row = _get_transfer(token)
    _authorize(row, key, "sender")
    body = await request.json()
    files = body.get("files") or []
    with db.tx() as conn:
        for item in files:
            idx = item.get("idx")
            sha = item.get("sha256")
            if isinstance(idx, int) and sha:
                conn.execute(
                    "UPDATE file SET sha256 = ? WHERE transfer_id = ? AND idx = ?",
                    (str(sha)[:64], row["id"], idx),
                )
    return {"ok": True, "files": _files_of(row["id"])}


@router.post("/transfers/{token}/switch-relay")
async def switch_relay(token: str, request: Request, key: str = ""):
    row = _get_transfer(token)
    if key not in (row["sender_key"], row["receiver_key"]):
        raise HTTPException(status_code=403, detail="密钥无效")
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    reason = str((body or {}).get("reason") or "")[:120]
    with db.tx() as conn:
        conn.execute(
            "UPDATE transfer SET transport = 'relay', status = 'relaying' WHERE id = ? AND transport != 'relay'",
            (row["id"],),
        )
    return {"ok": True, "transport": "relay", "reason": reason}


@router.delete("/transfers/{token}")
def delete_transfer(token: str, key: str = ""):
    row = _get_transfer(token)
    if key != row["sender_key"]:
        raise HTTPException(status_code=403, detail="仅发送方可取消")
    storage.remove_transfer(row["id"])
    with db.tx() as conn:
        conn.execute("DELETE FROM file WHERE transfer_id = ?", (row["id"],))
        conn.execute("DELETE FROM transfer WHERE id = ?", (row["id"],))
    return {"ok": True}
