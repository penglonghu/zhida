"""真实 Redis 集成测试：验证账号存储的种子导入与版本自增。

Redis 不可达时自动跳过；使用随机前缀，测试结束清理，不碰业务数据。
"""
import asyncio
import socket
import uuid

import pytest
import redis.asyncio as aioredis

from config import settings
from core import user_store


def _redis_up() -> bool:
    try:
        with socket.create_connection((settings.redis_host, settings.redis_port), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _redis_up(), reason="Redis not reachable")


def test_account_store_against_real_redis():
    asyncio.run(_scenario())


async def _scenario():
    prefix = f"zhida_it_{uuid.uuid4().hex[:8]}"
    original_prefix = settings.redis_prefix
    settings.redis_prefix = prefix
    client = aioredis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        decode_responses=True,
    )
    try:
        assert await user_store.seed_from_env("alice:hash-a:tenantA", client=client) == 1
        alice = await user_store.get_user("alice", client=client)
        assert (alice.tenant, alice.version) == ("tenantA", 1)

        assert await user_store.set_password("alice", "hash-b", client=client) == 2
        alice = await user_store.get_user("alice", client=client)
        assert alice.password_hash == "hash-b"

        # 已有账号时，种子不再覆盖或新增
        spec = "alice:hash-a:tenantA,bob:hash-b:tenantB"
        assert await user_store.seed_from_env(spec, client=client) == 0
        assert await user_store.get_user("bob", client=client) is None

        await user_store.remove_user("alice", client=client)
        assert await user_store.get_user("alice", client=client) is None
    finally:
        keys = [key async for key in client.scan_iter(match=f"{prefix}:*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()
        settings.redis_prefix = original_prefix
