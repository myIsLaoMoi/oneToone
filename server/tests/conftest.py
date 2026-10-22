import importlib
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MAX_FILE_SIZE", str(1024 * 1024))
    monkeypatch.setenv("QUOTA_BYTES", str(5 * 1024 * 1024))
    monkeypatch.setenv("MAX_FILES", "5")
    monkeypatch.setenv("CODE_TTL_MIN", "30")

    for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    app_module = importlib.import_module("app.main")
    ratelimit = importlib.import_module("app.ratelimit")
    ratelimit.reset()
    with TestClient(app_module.app) as c:
        yield c
