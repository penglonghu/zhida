"""测试替身：只实现被测代码用到的最小 Redis 子集，避免依赖真实 Redis。"""
import fnmatch


class FakeRedis:
    """内存版 redis.asyncio 子集（decode_responses=True 语义，值都是 str）。"""

    def __init__(self):
        self.data = {}

    async def hset(self, key, field=None, value=None, mapping=None):
        bucket = self.data.setdefault(key, {})
        if mapping:
            for k, v in mapping.items():
                bucket[k] = str(v)
        elif field is not None:
            bucket[field] = str(value)
        return 1

    async def hsetnx(self, key, field, value):
        bucket = self.data.setdefault(key, {})
        if field in bucket:
            return 0
        bucket[field] = str(value)
        return 1

    async def hgetall(self, key):
        return dict(self.data.get(key, {}))

    async def hincrby(self, key, field, amount=1):
        bucket = self.data.setdefault(key, {})
        new_value = int(bucket.get(field, 0)) + amount
        bucket[field] = str(new_value)
        return new_value

    async def exists(self, *keys):
        return sum(1 for key in keys if key in self.data)

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            if key in self.data:
                del self.data[key]
                removed += 1
        return removed

    async def scan_iter(self, match=None, count=None):
        for key in sorted(self.data):
            if match is None or fnmatch.fnmatch(key, match):
                yield key

    async def ping(self):
        return True


class BrokenRedis:
    """所有操作都抛连接异常，用于验证 fail-closed。"""

    def __getattr__(self, name):
        async def _boom(*args, **kwargs):
            raise ConnectionError("redis down")
        return _boom
