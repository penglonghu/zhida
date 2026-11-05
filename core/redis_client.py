"""Redis 连接单例：多轮会话与限流的共享状态后端。

为什么需要 Redis：进程内存态（dict / deque）只活在单个进程里。
多 worker / 多实例时，每个进程各存一份，会话会「串台」（请求打到
不同实例读不到同一会话）、限流会「失效」（各实例独立计数）。
Redis 是独立于应用进程的外部存储，所有实例读写同一份数据，
这才是横向扩展的前提。

懒加载：只在 redis_enabled=True 且首次访问时才建立连接，
单实例 / POC 不装 Redis 也能照常启动运行。
"""
import logging

import redis.asyncio as aioredis

from config import settings

logger = logging.getLogger(__name__)

_client = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            decode_responses=True,  # 读写返回 str 而非 bytes，JSON 直接可用
        )
        logger.info(
            "Redis 已连接 host=%s:%s db=%s",
            settings.redis_host, settings.redis_port, settings.redis_db,
        )
    return _client
