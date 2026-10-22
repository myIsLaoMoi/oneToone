import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import db

log = logging.getLogger(__name__)
router = APIRouter()

ROLES = ("sender", "receiver")


class Room:
    def __init__(self) -> None:
        self.socks: dict[str, WebSocket] = {}
        self.lock = asyncio.Lock()

    def other(self, role: str) -> WebSocket | None:
        for r, ws in self.socks.items():
            if r != role:
                return ws
        return None


rooms: dict[str, Room] = {}


async def _send(ws: WebSocket, payload: dict) -> None:
    try:
        await ws.send_text(json.dumps(payload))
    except Exception:
        pass


@router.websocket("/ws/{token}")
async def signaling(ws: WebSocket, token: str, role: str = "", key: str = ""):
    if role not in ROLES:
        await ws.close(code=4400)
        return

    row = db.one("SELECT * FROM transfer WHERE token = ?", (token,))
    if row is None:
        await ws.close(code=4404)
        return
    expected = row["sender_key"] if role == "sender" else row["receiver_key"]
    if not expected or key != expected:
        await ws.close(code=4403)
        return

    await ws.accept()
    transfer_id = row["id"]
    room = rooms.setdefault(transfer_id, Room())

    async with room.lock:
        previous = room.socks.get(role)
        room.socks[role] = ws
    if previous is not None:
        await _send(previous, {"type": "replaced"})
        try:
            await previous.close(code=4409)
        except Exception:
            pass

    peer = room.other(role)
    if peer is not None:
        await _send(ws, {"type": "peer-present"})
        await _send(peer, {"type": "peer-present"})

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = msg.get("type")

            if kind == "ping":
                await _send(ws, {"type": "pong"})
                continue

            if kind in ("signal", "p2p-failed", "p2p-ready", "progress"):
                target = room.other(role)
                if target is None:
                    await _send(ws, {"type": "peer-absent"})
                    continue
                msg["from"] = role
                await _send(target, msg)
                if kind == "p2p-failed":
                    with db.tx() as conn:
                        conn.execute(
                            "UPDATE transfer SET transport = 'relay', status = 'relaying'"
                            " WHERE id = ? AND transport != 'relay'",
                            (transfer_id,),
                        )
                continue
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("signaling error")
    finally:
        async with room.lock:
            if room.socks.get(role) is ws:
                room.socks.pop(role, None)
        peer = room.other(role)
        if peer is not None:
            await _send(peer, {"type": "peer-left", "role": role})
        if not room.socks:
            rooms.pop(transfer_id, None)
