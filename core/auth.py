"""登录账号与会话：PBKDF2 密码哈希 + HMAC 签名 Cookie。

- 账号存 Redis（见 core/user_store.py），AUTH_USERS 仅作为空库时的种子；
  运维用 scripts/manage_users.py 增删改，密码只存哈希不存明文。
- 登录成功下发 HMAC-SHA256 签名的无状态 token（没有服务端 session 表），
  token 放用户名、账号版本与过期时间；租户与版本每次请求从账号存储读取，
  改密/改租户/删号后旧 token 立即失效。
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

MIN_PASSWORD_LENGTH = 10

# 常见弱口令（小写比较）。改这里即可扩展，本期不做外部配置。
_WEAK_PASSWORDS = {
    "password", "password123", "passw0rd", "12345678", "123456789",
    "1234567890", "qwerty123", "qwertyuiop", "letmein", "admin123",
    "abc123456", "iloveyou", "welcome1", "zhida", "zhida123",
    "1111111111", "0000000000",
}


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


def verify_login(password: str, password_hash: Optional[str]) -> bool:
    """校验登录密码。账号不存在时也跑一次 PBKDF2，避免时间侧信道枚举账号。"""
    if password_hash is None:
        verify_password(password, _dummy_hash())
        return False
    return verify_password(password, password_hash)


def validate_password(password: str, username: str) -> Optional[str]:
    """校验密码强度，返回中文错误原因；通过返回 None。"""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"密码至少 {MIN_PASSWORD_LENGTH} 位（当前 {len(password)} 位）"
    lowered = password.lower()
    if lowered in _WEAK_PASSWORDS:
        return "密码过于常见，请更换"
    if lowered == username.lower():
        return "密码不能与用户名相同"
    if len(username) >= 3 and username.lower() in lowered:
        return "密码不能包含用户名"
    return None


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def create_session_token(username: str, secret: str, ttl_seconds: int,
                         version: int) -> str:
    payload = {"u": username, "v": version, "exp": int(time.time()) + ttl_seconds}
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
        # 改造前签发的 token 没有版本号，一律失效，迫使重新登录
        if "v" not in payload:
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
