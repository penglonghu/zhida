"""账号存储：密码哈希、租户绑定与版本号，存在 Redis 中。

版本号用于让已签发的会话立即失效：改密 / 改租户时 HINCRBY，删号直接 DEL。
认证中间件每次请求读取当前账号并比对版本，因此不做进程内缓存。
"""
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from config import settings
from core.auth import parse_users
from core.redis_client import get_redis

_REQUIRED_FIELDS = ("password_hash", "tenant", "version")


class UserStoreError(Exception):
    """账号存储相关错误的基类。"""


class UserExistsError(UserStoreError):
    def __init__(self, username: str):
        super().__init__(f"账号已存在: {username}")
        self.username = username


class UserNotFoundError(UserStoreError):
    def __init__(self, username: str):
        super().__init__(f"账号不存在: {username}")
        self.username = username


@dataclass(frozen=True)
class UserRecord:
    username: str
    password_hash: str
    tenant: str
    version: int
    updated_at: int


def _key_prefix() -> str:
    return f"{settings.redis_prefix}:auth:user:"


def _key(username: str) -> str:
    return f"{_key_prefix()}{username}"


def _now() -> int:
    return int(time.time())


def _validate(username: str, tenant: Optional[str] = None) -> None:
    if not username or not username.strip():
        raise ValueError("用户名不能为空")
    if tenant is not None and not tenant.strip():
        raise ValueError("租户不能为空")


async def get_user(username: str, *, client=None) -> Optional[UserRecord]:
    if not username:
        return None
    data = await (client or get_redis()).hgetall(_key(username))
    if not data or any(field not in data for field in _REQUIRED_FIELDS):
        return None
    try:
        version = int(data["version"])
        updated_at = int(data.get("updated_at") or 0)
    except (TypeError, ValueError):
        return None
    return UserRecord(
        username=username,
        password_hash=data["password_hash"],
        tenant=data["tenant"],
        version=version,
        updated_at=updated_at,
    )


async def list_users(*, client=None) -> List[UserRecord]:
    redis = client or get_redis()
    prefix = _key_prefix()
    users: List[UserRecord] = []
    async for key in redis.scan_iter(match=f"{prefix}*", count=100):
        username = key[len(prefix):]
        record = await get_user(username, client=redis)
        if record is not None:
            users.append(record)
    return sorted(users, key=lambda user: user.username)


async def add_user(username: str, password_hash: str, tenant: str,
                   *, force: bool = False, client=None) -> int:
    _validate(username, tenant)
    if not password_hash:
        raise ValueError("密码哈希不能为空")
    redis = client or get_redis()
    exists = await redis.exists(_key(username))
    if exists and not force:
        raise UserExistsError(username)
    version = await redis.hincrby(_key(username), "version", 1) if exists else 1
    await redis.hset(_key(username), mapping={
        "password_hash": password_hash,
        "tenant": tenant,
        "version": version,
        "updated_at": _now(),
    })
    return version


async def set_password(username: str, password_hash: str, *, client=None) -> int:
    _validate(username)
    if not password_hash:
        raise ValueError("密码哈希不能为空")
    redis = client or get_redis()
    if not await redis.exists(_key(username)):
        raise UserNotFoundError(username)
    version = await redis.hincrby(_key(username), "version", 1)
    await redis.hset(_key(username), mapping={
        "password_hash": password_hash,
        "updated_at": _now(),
    })
    return version


async def set_tenant(username: str, tenant: str, *, client=None) -> int:
    _validate(username, tenant)
    redis = client or get_redis()
    if not await redis.exists(_key(username)):
        raise UserNotFoundError(username)
    version = await redis.hincrby(_key(username), "version", 1)
    await redis.hset(_key(username), mapping={
        "tenant": tenant,
        "updated_at": _now(),
    })
    return version


async def remove_user(username: str, *, client=None) -> None:
    _validate(username)
    redis = client or get_redis()
    if not await redis.exists(_key(username)):
        raise UserNotFoundError(username)
    await redis.delete(_key(username))


async def seed_from_env(spec: str, *, client=None) -> int:
    """仅当账号库为空时从 AUTH_USERS 导入；HSETNX 保证多 worker 不覆盖。"""
    redis = client or get_redis()
    async for _ in redis.scan_iter(match=f"{_key_prefix()}*", count=1):
        return 0
    imported = 0
    users: dict[str, Tuple[str, str]] = parse_users(spec)
    for username, (password_hash, tenant) in users.items():
        created = await redis.hsetnx(_key(username), "password_hash", password_hash)
        if created:
            await redis.hset(_key(username), mapping={
                "tenant": tenant,
                "version": 1,
                "updated_at": _now(),
            })
            imported += 1
    return imported
