from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers.system_router import _get_system_kv, system

pytestmark = pytest.mark.unit


def test_discovery_endpoint_is_public(monkeypatch):
    monkeypatch.setattr("server.routers.system_router.get_version", lambda: "0.7.1.dev0")

    app = FastAPI()
    app.include_router(system, prefix="/api")
    response = TestClient(app).get("/api/system/discovery")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Yuxi"
    assert payload["version"] == "0.7.1.dev0"
    assert payload["api_prefix"] == "/api"
    assert payload["capabilities"]["cli"]["browser_login"] is True
    assert payload["capabilities"]["cli"]["api_key_auth"] is True
    assert payload["capabilities"]["cli"]["kb_upload"] is True
    assert payload["endpoints"]["cli_auth_sessions"] == "/api/auth/cli/sessions"


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeSession:
    """记录 execute 收到的语句，用于断言查询是按 key 而不是按主键取的。"""

    def __init__(self, row=None):
        self._row = row
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _FakeResult(self._row)

    async def get(self, *args, **kwargs):  # pragma: no cover - 不应被调用
        raise AssertionError("_get_system_kv 不应使用 db.get()（主键是整型 id，传字符串会 500）")


async def test_get_system_kv_queries_by_key_not_primary_key():
    """回归：曾误用 db.get(SystemKV, 'model_routing')，asyncpg 抛
    DataError: 'str' object cannot be interpreted as an integer，模型路由接口 500。"""
    sentinel = object()
    session = _FakeSession(row=sentinel)

    row = await _get_system_kv(session, "model_routing")

    assert row is sentinel
    assert len(session.statements) == 1
    compiled = str(session.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "system_kv.key" in compiled
    assert "model_routing" in compiled
    # 关键：不能再退化成按主键 id 过滤
    assert "system_kv.id =" not in compiled


async def test_get_system_kv_returns_none_when_missing():
    session = _FakeSession(row=None)
    assert await _get_system_kv(session, "model_routing") is None
