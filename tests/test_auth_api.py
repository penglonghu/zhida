"""认证链路的接口测试：TestClient + FakeRedis，不连接外部服务。

关键覆盖：账号版本导致旧会话立即失效、存储故障 fail-closed、API Key 不依赖账号存储。
"""
import asyncio
import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

import main
from config import settings
from core.auth import hash_password, verify_session_token
from core.user_store import add_user, remove_user, set_password, set_tenant
from tests.fakes import BrokenRedis, FakeRedis

PASSWORD = "Kx7-rain-Forest"
NEW_PASSWORD = "Lq9-steady-Bridge"


def _hash(password=PASSWORD):
    return hash_password(password, iterations=1000)


def _login(client, password=PASSWORD, username="alice"):
    return client.post("/login", json={"username": username, "password": password})


class StubRetriever:
    def list_documents(self, tenant_id, kb_id):
        return []


@pytest.fixture
def env(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr("core.user_store.get_redis", lambda: redis)
    monkeypatch.setattr(main, "retriever", StubRetriever())
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    monkeypatch.setattr(settings, "auth_users", "")
    monkeypatch.setattr(settings, "tenant_api_keys", "tenantA=keyA")
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    monkeypatch.setattr(settings, "redis_enabled", False)
    monkeypatch.setattr(settings, "default_tenant_id", "default")
    monkeypatch.setattr(settings, "default_kb_id", "default")
    asyncio.run(add_user("alice", _hash(), "tenantA", client=redis))
    with TestClient(main.app) as client:
        yield client, redis


def test_login_success_and_me(env):
    client, _ = env

    resp = _login(client)

    assert resp.status_code == 200
    assert resp.json()["tenant"] == "tenantA"
    assert settings.cookie_name in resp.cookies
    me = client.get("/me").json()
    assert me["authenticated"] is True
    assert me["username"] == "alice"
    assert me["tenant"] == "tenantA"


def test_wrong_password_rejected(env):
    client, _ = env

    resp = _login(client, password="Wrong-pass-123")

    assert resp.status_code == 401
    assert settings.cookie_name not in resp.cookies


def test_unknown_user_looks_like_wrong_password(env):
    client, _ = env

    resp = _login(client, username="ghost", password="Wrong-pass-123")

    assert resp.status_code == 401
    assert resp.json()["detail"] == "用户名或密码错误"


def test_unchanged_cookie_still_works(env):
    client, _ = env
    assert _login(client).status_code == 200

    assert client.get("/documents").status_code == 200


def test_change_password_invalidates_old_cookie(env):
    client, redis = env
    assert _login(client).status_code == 200
    asyncio.run(set_password("alice", _hash(NEW_PASSWORD), client=redis))

    assert client.get("/documents").status_code == 401

    client.cookies.clear()
    assert _login(client, password=PASSWORD).status_code == 401
    assert _login(client, password=NEW_PASSWORD).status_code == 200


def test_change_tenant_invalidates_and_new_scope(env):
    client, redis = env
    assert _login(client).status_code == 200
    asyncio.run(set_tenant("alice", "tenantB", client=redis))

    assert client.get("/documents").status_code == 401

    client.cookies.clear()
    resp = _login(client)
    assert resp.status_code == 200
    assert client.get("/me").json()["tenant"] == "tenantB"


def test_remove_user_invalidates_cookie(env):
    client, redis = env
    assert _login(client).status_code == 200
    asyncio.run(remove_user("alice", client=redis))

    assert client.get("/documents").status_code == 401
    assert _login(client).status_code == 401


def test_old_token_without_version_rejected(env):
    client, _ = env
    payload = {"u": "alice", "exp": int(time.time()) + 60}
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(
        hmac.new(b"test-secret", body.encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    client.cookies.set(settings.cookie_name, f"{body}.{sig}")

    assert client.get("/documents").status_code == 401


def test_expired_token_rejected(env):
    client, _ = env
    token = main.create_session_token("alice", "test-secret", -1, 1)
    client.cookies.set(settings.cookie_name, token)

    assert client.get("/documents").status_code == 401


def test_api_key_does_not_depend_on_user_store(env, monkeypatch):
    client, _ = env
    monkeypatch.setattr("core.user_store.get_redis", lambda: BrokenRedis())

    resp = client.get("/documents", headers={"X-API-Key": "keyA"})

    assert resp.status_code == 200


def test_store_down_login_returns_503(env, monkeypatch):
    client, _ = env
    monkeypatch.setattr("core.user_store.get_redis", lambda: BrokenRedis())

    assert _login(client).status_code == 503


def test_store_down_cookie_request_returns_503(env, monkeypatch):
    client, _ = env
    assert _login(client).status_code == 200
    monkeypatch.setattr("core.user_store.get_redis", lambda: BrokenRedis())

    assert client.get("/documents").status_code == 503


def test_store_down_me_is_unauthenticated_not_503(env, monkeypatch):
    client, _ = env
    monkeypatch.setattr("core.user_store.get_redis", lambda: BrokenRedis())

    resp = client.get("/me")

    assert resp.status_code == 200
    assert resp.json()["authenticated"] is False


def test_auth_disabled_dev_header(env, monkeypatch):
    client, _ = env
    monkeypatch.setattr(settings, "auth_enabled", False)
    # 开发态租户头只影响 state，这里用 /me 验证
    resp = client.get("/me", headers={"X-Tenant-Id": "dev"})

    assert resp.status_code == 200
    assert resp.json()["authenticated"] is True
    assert resp.json()["tenant"] == "dev"


def test_ready_checks_redis_when_auth_enabled(env, monkeypatch, tmp_path):
    client, _ = env
    monkeypatch.setattr(settings, "redis_enabled", False)

    class ReadyRetriever:
        class client:
            @staticmethod
            def collection_exists(name):
                return True

    monkeypatch.setattr(main, "retriever", ReadyRetriever())
    monkeypatch.setattr(settings, "embed_model_name", str(tmp_path))
    (tmp_path / "config_sentence_transformers.json").write_text("{}")
    monkeypatch.setattr("core.redis_client.get_redis", lambda: BrokenRedis())

    resp = client.get("/ready")

    assert resp.status_code == 503
    assert resp.json()["checks"]["redis"].startswith("down")


def test_token_version_roundtrip(env):
    token = main.create_session_token("alice", "test-secret", 60, 7)
    payload = verify_session_token(token, "test-secret")
    assert payload["v"] == 7
