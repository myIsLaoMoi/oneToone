import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _create(client, files):
    r = client.post("/api/transfers", json={"files": files})
    assert r.status_code == 200, r.text
    return r.json()


def _join(client, code):
    return client.post("/api/transfers/join", json={"code": code})


def _upload(client, token, key, idx, data, offset=0):
    return client.post(
        f"/api/transfers/{token}/chunk",
        params={"key": key, "idx": idx, "offset": offset},
        content=data,
    )


def test_create_join_and_download(client):
    payload = _create(client, [{"name": "报告.txt", "size": 11, "mime": "text/plain"}])
    assert payload["code"].isdigit() and len(payload["code"]) == 6
    assert payload["joined"] is False
    token, sender_key = payload["token"], payload["sender_key"]

    joined = _join(client, payload["code"])
    assert joined.status_code == 200
    receiver_key = joined.json()["receiver_key"]
    assert joined.json()["joined"] is True

    body = "hello world".encode()
    assert _upload(client, token, sender_key, 0, body).status_code == 200

    dl = client.get(f"/d/{token}/0", params={"key": receiver_key})
    assert dl.status_code == 200
    assert dl.content == body
    assert dl.headers["content-disposition"].startswith("attachment;")
    assert "filename*=UTF-8''" in dl.headers["content-disposition"]


def test_code_is_reusable_for_the_same_receiver(client):
    payload = _create(client, [{"name": "a.bin", "size": 4}])
    first = _join(client, payload["code"])
    assert first.status_code == 200
    # A refresh or a second device must get the same receiver identity back.
    again = _join(client, payload["code"])
    assert again.status_code == 200
    assert again.json()["receiver_key"] == first.json()["receiver_key"]


def test_join_is_rate_limited(client):
    codes = [_create(client, [{"name": f"{i}.bin", "size": 1}])["code"] for i in range(3)]
    for _ in range(10):
        _join(client, "999999")
    blocked = _join(client, codes[0])
    assert blocked.status_code == 429


def test_unknown_code_rejected(client):
    assert _join(client, "000000").status_code == 404


def test_keys_are_scoped(client):
    payload = _create(client, [{"name": "a.bin", "size": 4}])
    token, sender_key = payload["token"], payload["sender_key"]
    receiver_key = _join(client, payload["code"]).json()["receiver_key"]

    assert _upload(client, token, receiver_key, 0, b"abcd").status_code == 403
    assert _upload(client, token, "", 0, b"abcd").status_code == 401
    assert client.get(f"/d/{token}/0", params={"key": "bogus"}).status_code == 403
    assert client.get(f"/api/transfers/{token}", params={"key": sender_key}).status_code == 200


def test_offset_must_be_contiguous(client):
    payload = _create(client, [{"name": "a.bin", "size": 12}])
    token, key = payload["token"], payload["sender_key"]
    assert _upload(client, token, key, 0, b"abcd").status_code == 200
    gap = _upload(client, token, key, 0, b"efgh", offset=6)
    assert gap.status_code == 409
    ok = _upload(client, token, key, 0, b"efgh", offset=4)
    assert ok.status_code == 200
    assert ok.json()["received"] == 8


def test_resume_truncates_stale_bytes(client):
    payload = _create(client, [{"name": "a.bin", "size": 8}])
    token, key = payload["token"], payload["sender_key"]
    _upload(client, token, key, 0, b"abcdefgh")
    # A client that restarts from 0 must leave exactly 4 bytes, not 8.
    resumed = _upload(client, token, key, 0, b"wxyz", offset=0)
    assert resumed.status_code == 200
    assert resumed.json()["received"] == 4
    dl = client.get(f"/d/{token}/0", params={"key": key})
    assert dl.status_code == 409  # not marked uploaded any more


def test_size_limit_enforced(client):
    r = client.post("/api/transfers", json={"files": [{"name": "big.bin", "size": 2 * 1024 * 1024}]})
    assert r.status_code == 413


def test_file_count_limit(client):
    files = [{"name": f"{i}.bin", "size": 1} for i in range(6)]
    assert client.post("/api/transfers", json={"files": files}).status_code == 400


def test_empty_file_list(client):
    assert client.post("/api/transfers", json={"files": []}).status_code == 400


def test_quota_enforced(client):
    # MAX_FILE_SIZE is 1MB, so spread the payload across several files.
    ok = _create(
        client,
        [{"name": f"{i}.bin", "size": 1024 * 1024} for i in range(5)],
    )
    token, key = ok["token"], ok["sender_key"]
    for idx in range(5):
        assert _upload(client, token, key, idx, b"x" * (1024 * 1024)).status_code == 200
    r = client.post("/api/transfers", json={"files": [{"name": "b.bin", "size": 1024 * 1024}]})
    assert r.status_code == 507


def test_range_requests(client):
    payload = _create(client, [{"name": "a.bin", "size": 10}])
    token, key = payload["token"], payload["sender_key"]
    _upload(client, token, key, 0, b"0123456789")

    part = client.get(f"/d/{token}/0", params={"key": key}, headers={"Range": "bytes=2-5"})
    assert part.status_code == 206
    assert part.content == b"2345"
    assert part.headers["content-range"] == "bytes 2-5/10"

    tail = client.get(f"/d/{token}/0", params={"key": key}, headers={"Range": "bytes=-3"})
    assert tail.status_code == 206
    assert tail.content == b"789"

    head = client.head(f"/d/{token}/0", params={"key": key})
    assert head.headers["accept-ranges"] == "bytes"
    assert head.headers["content-length"] == "10"


def test_switch_relay_and_progress(client):
    payload = _create(client, [{"name": "a.bin", "size": 4}])
    token, sender_key = payload["token"], payload["sender_key"]
    receiver_key = _join(client, payload["code"]).json()["receiver_key"]

    r = client.post(f"/api/transfers/{token}/switch-relay", params={"key": sender_key}, json={"reason": "timeout"})
    assert r.json()["transport"] == "relay"

    p = client.post(
        f"/api/transfers/{token}/progress",
        params={"key": receiver_key},
        json={"files": [{"idx": 0, "received": 2, "sha256": "ab" * 32}]},
    )
    assert p.status_code == 200
    state = client.get(f"/api/transfers/{token}", params={"key": sender_key}).json()
    assert state["files"][0]["received"] == 2
    assert state["files"][0]["sha256"] == "ab" * 32


def test_delete_requires_sender(client):
    payload = _create(client, [{"name": "a.bin", "size": 4}])
    token = payload["token"]
    receiver_key = _join(client, payload["code"]).json()["receiver_key"]
    assert client.delete(f"/api/transfers/{token}", params={"key": receiver_key}).status_code == 403
    assert client.delete(f"/api/transfers/{token}", params={"key": payload["sender_key"]}).status_code == 200
    assert client.get(f"/api/transfers/{token}", params={"key": payload["sender_key"]}).status_code == 404


def test_path_traversal_in_filename_is_inert(client, tmp_path):
    payload = _create(client, [{"name": "../../etc/passwd", "size": 3}])
    token, key = payload["token"], payload["sender_key"]
    assert _upload(client, token, key, 0, b"abc").status_code == 200
    stored = list((tmp_path / "files").rglob("*.part"))
    assert len(stored) == 1
    assert stored[0].name == "0.part"
