import mimetypes
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from .. import db, storage

router = APIRouter()


def _parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    if not header or not header.startswith("bytes="):
        return None
    spec = header[len("bytes="):].split(",")[0].strip()
    if "-" not in spec:
        return None
    start_s, end_s = spec.split("-", 1)
    try:
        if start_s == "":
            length = int(end_s)
            if length <= 0:
                return None
            return max(size - length, 0), size - 1
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        return None
    return start, min(end, size - 1)


def _content_disposition(name: str) -> str:
    ascii_fallback = name.encode("ascii", "replace").decode("ascii").replace('"', "'")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(name)}"


@router.get("/d/{token}/{idx}")
def download(token: str, idx: int, request: Request, key: str = ""):
    row = db.one("SELECT * FROM transfer WHERE token = ?", (token,))
    if row is None:
        raise HTTPException(status_code=404, detail="传输不存在")
    if key not in (row["sender_key"], row["receiver_key"]):
        raise HTTPException(status_code=403, detail="密钥无效")
    frow = db.one("SELECT * FROM file WHERE transfer_id = ? AND idx = ?", (row["id"], idx))
    if frow is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    if frow["status"] != "uploaded":
        raise HTTPException(status_code=409, detail="文件尚未上传完成")

    path = storage.part_path(row["id"], idx)
    if not path.exists():
        raise HTTPException(status_code=410, detail="文件已被清理")
    size = path.stat().st_size
    mime = frow["mime"] or mimetypes.guess_type(frow["name"])[0] or "application/octet-stream"

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": _content_disposition(frow["name"]),
        "X-Content-Type-Options": "nosniff",
    }

    rng = _parse_range(request.headers.get("range"), size)
    if rng is None:
        headers["Content-Length"] = str(size)
        return FileResponse(path, media_type=mime, headers=headers)

    start, end = rng
    length = end - start + 1

    def body():
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(length)
    return StreamingResponse(body(), status_code=206, media_type=mime, headers=headers)


@router.head("/d/{token}/{idx}")
def download_head(token: str, idx: int, key: str = ""):
    row = db.one("SELECT * FROM transfer WHERE token = ?", (token,))
    if row is None:
        raise HTTPException(status_code=404, detail="传输不存在")
    if key not in (row["sender_key"], row["receiver_key"]):
        raise HTTPException(status_code=403, detail="密钥无效")
    frow = db.one("SELECT * FROM file WHERE transfer_id = ? AND idx = ?", (row["id"], idx))
    if frow is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    return Response(
        status_code=200,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(storage.size_of(row["id"], idx)),
        },
    )
