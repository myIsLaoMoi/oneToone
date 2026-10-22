from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def test_signaling_relays_between_peers(client):
    payload = client.post("/api/transfers", json={"files": [{"name": "a.bin", "size": 4}]}).json()
    token, sender_key = payload["token"], payload["sender_key"]
    receiver_key = client.post("/api/transfers/join", json={"code": payload["code"]}).json()["receiver_key"]

    with client.websocket_connect(f"/ws/{token}?role=sender&key={sender_key}") as sender:
        with client.websocket_connect(f"/ws/{token}?role=receiver&key={receiver_key}") as receiver:
            assert sender.receive_json()["type"] == "peer-present"
            assert receiver.receive_json()["type"] == "peer-present"

            sender.send_json({"type": "signal", "data": {"sdp": "offer"}})
            relayed = receiver.receive_json()
            assert relayed["type"] == "signal"
            assert relayed["from"] == "sender"
            assert relayed["data"]["sdp"] == "offer"

            sender.send_json({"type": "ping"})
            assert sender.receive_json()["type"] == "pong"


def test_bad_key_is_rejected(client):
    payload = client.post("/api/transfers", json={"files": [{"name": "a.bin", "size": 4}]}).json()
    token = payload["token"]
    try:
        with client.websocket_connect(f"/ws/{token}?role=sender&key=wrong"):
            raise AssertionError("connection should have been closed")
    except Exception as exc:
        assert isinstance(exc, WebSocketDisconnect) or "4403" in str(exc)


def test_peer_left_notification(client):
    payload = client.post("/api/transfers", json={"files": [{"name": "a.bin", "size": 4}]}).json()
    token, sender_key = payload["token"], payload["sender_key"]
    receiver_key = client.post("/api/transfers/join", json={"code": payload["code"]}).json()["receiver_key"]

    with client.websocket_connect(f"/ws/{token}?role=sender&key={sender_key}") as sender:
        with client.websocket_connect(f"/ws/{token}?role=receiver&key={receiver_key}") as receiver:
            sender.receive_json()
            receiver.receive_json()
            receiver.close()
        left = sender.receive_json()
        assert left["type"] == "peer-left"
        assert left["role"] == "receiver"
