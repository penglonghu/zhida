"""登录账号与会话：PBKDF2 密码哈希 + HMAC 签名 Cookie。

- 账号来自配置 AUTH_USERS（"用户名:密码哈希:租户,用户名2:..."），
  密码只存哈希，不存明文；用 scripts/hash_password.py 生成。
- 登录成功下发 HMAC-SHA256 签名的无状态 token（没有服务端 session 表），
  token 只放用户名和过期时间；租户每次请求从当前账号配置里取，
  删账号/改租户立即生效。
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Dict, Optional, Tuple

PBKDF2_ITERATIONS = 260_000

# 用户不存在时也跑一次校验，避免通过响应时间枚举账号
_DUMMY_HASH = ""


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    # 用 | 分隔：Docker Compose 会插值 .env 里的 $VAR，$ 格式会被吞掉
    return f"pbkdf2_sha256|{iterations}|{salt.hex()}|{dk.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        sep = "|" if "|" in encoded else "$"
        algo, iterations, salt_hex, hash_hex = encoded.split(sep)
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if not _DUMMY_HASH:
        _DUMMY_HASH = hash_password("zhida-dummy-password")
    return _DUMMY_HASH


def parse_users(spec: str) -> Dict[str, Tuple[str, str]]:
    """解析 AUTH_USERS -> {username: (password_hash, tenant)}。格式错误项跳过。"""
    users: Dict[str, Tuple[str, str]] = {}
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":", 2)
        if len(parts) != 3:
            continue
        username, pwd_hash, tenant = (p.strip() for p in parts)
        if username and pwd_hash and tenant:
            users[username] = (pwd_hash, tenant)
    return users


def authenticate(users: Dict[str, Tuple[str, str]], username: str,
                 password: str) -> Optional[str]:
    """校验成功返回租户，失败返回 None。"""
    record = users.get(username)
    if record is None:
        verify_password(password, _dummy_hash())
        return None
    pwd_hash, tenant = record
    return tenant if verify_password(password, pwd_hash) else None


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def create_session_token(username: str, secret: str, ttl_seconds: int) -> str:
    payload = {"u": username, "exp": int(time.time()) + ttl_seconds}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_session_token(token: str, secret: str) -> Optional[dict]:
    try:
        body, sig = token.split(".", 1)
        expected = _b64e(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64d(body))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        if not payload.get("u"):
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def parse_cookies(header: str) -> Dict[str, str]:
    cookies: Dict[str, str] = {}
    for part in header.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        cookies[key.strip()] = value.strip()
    return cookies
