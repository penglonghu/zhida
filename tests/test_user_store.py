"""账号存储层单测：用 FakeRedis，不连接真实 Redis。"""
import asyncio

import pytest

from core.user_store import (
    UserExistsError,
    UserNotFoundError,
    add_user,
    get_user,
    list_users,
    remove_user,
    seed_from_env,
    set_password,
    set_tenant,
)
from tests.fakes import FakeRedis


def run(coro):
    return asyncio.run(coro)


def test_add_user_then_get():
    redis = FakeRedis()
    run(add_user("alice", "hash-1", "tenantA", client=redis))

    user = run(get_user("alice", client=redis))

    assert user.username == "alice"
    assert user.password_hash == "hash-1"
    assert user.tenant == "tenantA"
    assert user.version == 1
    assert user.updated_at > 0


def test_add_duplicate_raises_and_keeps_record():
    redis = FakeRedis()
    run(add_user("alice", "hash-1", "tenantA", client=redis))

    with pytest.raises(UserExistsError):
        run(add_user("alice", "hash-2", "tenantB", client=redis))

    user = run(get_user("alice", client=redis))
    assert (user.password_hash, user.tenant, user.version) == ("hash-1", "tenantA", 1)


def test_add_force_overwrites_and_bumps_version():
    redis = FakeRedis()
    run(add_user("alice", "hash-1", "tenantA", client=redis))

    run(add_user("alice", "hash-2", "tenantB", force=True, client=redis))

    user = run(get_user("alice", client=redis))
    assert (user.password_hash, user.tenant, user.version) == ("hash-2", "tenantB", 2)


def test_set_password_bumps_version_and_keeps_tenant():
    redis = FakeRedis()
    run(add_user("alice", "hash-1", "tenantA", client=redis))

    version = run(set_password("alice", "hash-2", client=redis))

    user = run(get_user("alice", client=redis))
    assert version == 2
    assert (user.password_hash, user.tenant, user.version) == ("hash-2", "tenantA", 2)


def test_set_tenant_bumps_version_and_keeps_password():
    redis = FakeRedis()
    run(add_user("alice", "hash-1", "tenantA", client=redis))

    run(set_tenant("alice", "tenantB", client=redis))

    user = run(get_user("alice", client=redis))
    assert (user.password_hash, user.tenant, user.version) == ("hash-1", "tenantB", 2)


def test_missing_user_raises():
    redis = FakeRedis()

    with pytest.raises(UserNotFoundError):
        run(set_password("ghost", "hash", client=redis))
    with pytest.raises(UserNotFoundError):
        run(set_tenant("ghost", "tenantA", client=redis))
    with pytest.raises(UserNotFoundError):
        run(remove_user("ghost", client=redis))


def test_remove_user():
    redis = FakeRedis()
    run(add_user("alice", "hash-1", "tenantA", client=redis))

    run(remove_user("alice", client=redis))

    assert run(get_user("alice", client=redis)) is None


def test_list_users_sorted():
    redis = FakeRedis()
    run(add_user("bob", "h", "t", client=redis))
    run(add_user("alice", "h", "t", client=redis))

    users = run(list_users(client=redis))

    assert [u.username for u in users] == ["alice", "bob"]


def test_empty_username_or_tenant_rejected():
    redis = FakeRedis()

    with pytest.raises(ValueError):
        run(add_user("", "h", "tenantA", client=redis))
    with pytest.raises(ValueError):
        run(add_user("alice", "h", "  ", client=redis))


def test_incomplete_record_is_treated_as_missing():
    redis = FakeRedis()
    redis.data["zhida:auth:user:alice"] = {"password_hash": "h"}

    assert run(get_user("alice", client=redis)) is None


def test_seed_imports_only_when_empty():
    redis = FakeRedis()
    spec = "alice:hash-a:tenantA,bob:hash-b:tenantB"

    imported = run(seed_from_env(spec, client=redis))

    assert imported == 2
    assert run(get_user("alice", client=redis)).tenant == "tenantA"
    assert run(get_user("bob", client=redis)).version == 1


def test_seed_skips_when_store_has_users():
    redis = FakeRedis()
    run(add_user("carol", "h", "t", client=redis))

    imported = run(seed_from_env("alice:hash-a:tenantA", client=redis))

    assert imported == 0
    assert run(get_user("alice", client=redis)) is None


def test_seed_is_idempotent_and_does_not_override_changes():
    redis = FakeRedis()
    spec = "alice:hash-a:tenantA"
    run(seed_from_env(spec, client=redis))
    run(set_password("alice", "new-hash", client=redis))

    imported = run(seed_from_env(spec, client=redis))

    user = run(get_user("alice", client=redis))
    assert imported == 0
    assert (user.password_hash, user.version) == ("new-hash", 2)
