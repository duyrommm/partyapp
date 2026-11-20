import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["MC_KEY"] = "k"
os.environ.pop("DATABASE_URL", None)
os.environ["STATE_FILE"] = "/tmp/_pa_unused.json"

import app as A  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

MC = {"x-mc-key": "k"}


@pytest.fixture
def clock(monkeypatch):
    off = [0.0]
    monkeypatch.setattr(A, "mono", lambda: time.monotonic() + off[0])
    return off


@pytest.fixture
def c(tmp_path, monkeypatch, clock):
    monkeypatch.setattr(A, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(A, "DATABASE_URL", "")
    A.S.clear(); A.S.update(A.new_state())
    A.CONNS.clear(); A.ONLINE.clear()
    with TestClient(A.app) as client:
        yield client


def join(c, name, group="it-legal", **kw):
    r = c.post("/api/join", json={"name": name, "group": group, **kw})
    return r


def mc(c, **body):
    return c.post("/api/mc", json=body, headers=MC)


def until(ws, pred, n=60):
    """Đọc tin nhắn tới khi pred(msg) đúng."""
    for _ in range(n):
        m = ws.receive_json()
        if pred(m):
            return m
    raise AssertionError("không thấy tin nhắn mong đợi")


def state_of(ws, pred=lambda m: True):
    return until(ws, lambda m: m.get("t") == "state" and pred(m))
