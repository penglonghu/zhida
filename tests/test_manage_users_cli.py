"""运维 CLI 测试：直接调用 main(argv, password_provider)，不启子进程。"""
import asyncio
import logging

import pytest

import scripts.manage_users as cli
from core.auth import verify_password
from core.user_store import get_user
from tests.fakes import FakeRedis

PASSWORD = "Kx7-rain-Forest"
NEW_PASSWORD = "Lq9-steady-Bridge"


@pytest.fixture
def env(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr("core.user_store.get_redis", lambda: redis)
    monkeypatch.setattr(cli, "setup_logging", lambda *a, **k: None)
    return redis


def _provider(*values):
    seq = iter(values)
    return lambda _prompt: next(seq)


def _run(argv, provider=None):
    def _no_prompt(_prompt):
        raise AssertionError("不应提示密码")

    return cli.main(argv, password_provider=provider or _no_prompt)


def _user(redis, username="alice"):
    return asyncio.run(get_user(username, client=redis))


def test_add_success(env, caplog):
    with caplog.at_level(logging.INFO, logger="audit"):
        code = _run(["add", "--user", "alice", "--tenant", "tenantA"],
                    _provider(PASSWORD, PASSWORD))

    assert code == 0
    user = _user(env)
    assert user.tenant == "tenantA"
    assert verify_password(PASSWORD, user.password_hash)
    assert "action=add" in caplog.text and "result=ok" in caplog.text
    assert PASSWORD not in caplog.text
    assert user.password_hash not in caplog.text


def test_add_duplicate_conflicts(env, caplog):
    _run(["add", "--user", "alice", "--tenant", "tenantA"], _provider(PASSWORD, PASSWORD))

    with caplog.at_level(logging.INFO, logger="audit"):
        code = _run(["add", "--user", "alice", "--tenant", "tenantB"],
                    _provider(NEW_PASSWORD, NEW_PASSWORD))

    assert code == 4
    user = _user(env)
    assert user.tenant == "tenantA" and user.version == 1
    assert "result=conflict" in caplog.text


def test_add_force_overwrites(env):
    _run(["add", "--user", "alice", "--tenant", "tenantA"], _provider(PASSWORD, PASSWORD))

    code = _run(["add", "--user", "alice", "--tenant", "tenantB", "--force"],
                _provider(NEW_PASSWORD, NEW_PASSWORD))

    assert code == 0
    user = _user(env)
    assert user.tenant == "tenantB" and user.version == 2
    assert verify_password(NEW_PASSWORD, user.password_hash)


def test_password_mismatch_is_usage_error(env):
    code = _run(["add", "--user", "alice", "--tenant", "tenantA"],
                _provider(PASSWORD, "different-pw"))

    assert code == 2
    assert _user(env) is None


def test_weak_password_rejected_without_writing(env, caplog):
    with caplog.at_level(logging.INFO, logger="audit"):
        code = _run(["add", "--user", "alice", "--tenant", "tenantA"],
                    _provider("password123", "password123"))

    assert code == 5
    assert _user(env) is None
    assert "result=weak_password" in caplog.text
    assert "password123" not in caplog.text


def test_empty_tenant_is_usage_error(env):
    code = _run(["add", "--user", "alice", "--tenant", "  "],
                _provider(PASSWORD, PASSWORD))

    assert code == 2
    assert _user(env) is None


def test_passwd_success(env):
    _run(["add", "--user", "alice", "--tenant", "tenantA"], _provider(PASSWORD, PASSWORD))

    code = _run(["passwd", "--user", "alice"], _provider(NEW_PASSWORD, NEW_PASSWORD))

    user = _user(env)
    assert code == 0
    assert user.version == 2
    assert verify_password(NEW_PASSWORD, user.password_hash)


def test_passwd_unknown_user(env):
    code = _run(["passwd", "--user", "ghost"], _provider(NEW_PASSWORD, NEW_PASSWORD))

    assert code == 3
    assert _user(env, "ghost") is None


def test_set_tenant(env):
    _run(["add", "--user", "alice", "--tenant", "tenantA"], _provider(PASSWORD, PASSWORD))

    code = _run(["set-tenant", "--user", "alice", "--tenant", "tenantB"])

    user = _user(env)
    assert code == 0
    assert user.tenant == "tenantB" and user.version == 2


def test_set_tenant_unknown_user(env):
    assert _run(["set-tenant", "--user", "ghost", "--tenant", "tenantB"]) == 3


def test_remove_user(env):
    _run(["add", "--user", "alice", "--tenant", "tenantA"], _provider(PASSWORD, PASSWORD))

    assert _run(["remove", "--user", "alice"]) == 0
    assert _user(env) is None


def test_remove_unknown_user(env):
    assert _run(["remove", "--user", "ghost"]) == 3


def test_list_hides_hash(env, capsys):
    _run(["add", "--user", "alice", "--tenant", "tenantA"], _provider(PASSWORD, PASSWORD))

    assert _run(["list"], _provider()) == 0

    out = capsys.readouterr().out
    user = _user(env)
    assert "alice" in out and "tenantA" in out
    assert user.password_hash not in out
