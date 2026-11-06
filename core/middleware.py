"""HTTP 中间件：trace / 审计日志 / 认证 / 限流。

全部用纯 ASGI 实现（不用 BaseHTTPMiddleware）：
BaseHTTPMiddleware 会缓冲响应 body，破坏 /stream-query 的 SSE 流式输出。
纯 ASGI 只包装 send、不碰 body，对流式安全。

注册顺序（Starlette add_middleware 后加的在外层，先执行）：
RateLimit(最内) -> Audit -> Auth -> Trace(最外) -> CORS
Auth 必须先于 Audit/RateLimit，后者才能拿到租户身份。
"""
import hmac
import logging
import time
import uuid
from collections import defaultdict, deque

from config import settings
from core.auth import parse_cookies, parse_users, verify_session_token
from core.tracing import set_trace_id

logger = logging.getLogger(__name__)


def _header_map(scope) -> dict:
    return {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}


def _api_key_tenants() -> dict:
    """API Key -> 租户映射。SERVICE_API_KEY 兼容映射到默认租户。"""
    mapping = {}
    if settings.service_api_key:
        mapping[settings.service_api_key] = settings.default_tenant_id
    for pair in settings.tenant_api_keys.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        tenant, key = (s.strip() for s in pair.split("=", 1))
        if tenant and key:
            mapping[key] = tenant
    return mapping


class TraceMiddleware:
    """生成/透传 trace_id，写入 contextvars 并回传响应头。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        trace_id = _header_map(scope).get("x-trace-id") or uuid.uuid4().hex[:16]
        set_trace_id(trace_id)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((b"x-trace-id", trace_id.encode()))
            await send(message)

        await self.app(scope, receive, send_wrapper)


class AuditMiddleware:
    """结构化访问审计日志：一次请求一行，含 trace_id / 耗时 / 状态码 / IP。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        method = scope.get("method", "?")
        path = scope.get("path", "?")
        client = scope.get("client")
        client_ip = client[0] if client else "-"
        status_holder = {"code": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            cost_ms = (time.perf_counter() - start) * 1000
            tenant_id = scope.get("state", {}).get("tenant_id", "-")
            logger.info(
                "audit method=%s path=%s status=%s tenant=%s ip=%s cost_ms=%.1f",
                method, path, status_holder["code"], tenant_id, client_ip, cost_ms,
            )


_PUBLIC_PATHS = ("/health", "/ready", "/login", "/logout", "/me", "/docs", "/openapi.json", "/redoc")


class AuthMiddleware:
    """认证 + 租户解析，两种凭证：

    - 浏览器：登录 Cookie（HMAC 签名 token，租户由账号配置决定）
    - 程序调用：Authorization: Bearer <key> 或 X-API-Key

    auth_enabled=False（POC/开发）时放行，并允许 X-Tenant-Id 指定租户，
    便于本地验证隔离；生产必须开启 auth，该头会被忽略。
    """

    def __init__(self, app):
        self.app = app
        self._key_tenants = _api_key_tenants()
        self._users = parse_users(settings.auth_users)
        if settings.auth_enabled and self._users and not settings.session_secret:
            logger.error("配置了 AUTH_USERS 但缺少 SESSION_SECRET，浏览器登录将不可用")

    def _resolve_tenant(self, supplied: str):
        for key, tenant in self._key_tenants.items():
            if hmac.compare_digest(supplied, key):
                return tenant
        return None

    def _apply_auth(self, headers: dict, state: dict) -> bool:
        """依次尝试 Cookie 与 API Key，成功则写 state。"""
        if settings.session_secret:
            token = parse_cookies(headers.get("cookie", "")).get(settings.cookie_name)
            if token:
                payload = verify_session_token(token, settings.session_secret)
                record = self._users.get(payload["u"]) if payload else None
                if record:
                    state["tenant_id"] = record[1]
                    state["username"] = payload["u"]
                    state["auth_method"] = "cookie"
                    return True

        auth = headers.get("authorization", "")
        supplied = auth[7:] if auth.startswith("Bearer ") else headers.get("x-api-key", "")
        tenant = self._resolve_tenant(supplied) if supplied else None
        if tenant is not None:
            state["tenant_id"] = tenant
            state["username"] = "-"
            state["auth_method"] = "apikey"
            return True
        return False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = _header_map(scope)
        state = scope.setdefault("state", {})
        path = scope.get("path", "")

        # 仅开发态允许用请求头指定租户；生产开启认证后必须由凭证决定
        if not settings.auth_enabled:
            state["tenant_id"] = headers.get("x-tenant-id") or settings.default_tenant_id
            state["username"] = "anonymous"
            state["auth_method"] = "dev"
            await self.app(scope, receive, send)
            return

        if path in _PUBLIC_PATHS or path.startswith("/docs/"):
            self._apply_auth(headers, state)   # /me 需要识别已登录用户，但不强制
            await self.app(scope, receive, send)
            return

        if not self._users and not self._key_tenants:
            # fail-closed：开了认证却没有任何凭证来源
            logger.error("AUTH_ENABLED=true 但未配置 AUTH_USERS 或 API Key，拒绝请求")
            await self._reject(send, 500, b'{"detail":"auth misconfigured"}')
            return

        if self._apply_auth(headers, state):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        logger.warning("认证失败 path=%s ip=%s", path, client[0] if client else "-")
        await self._reject(send, 401, b'{"detail":"unauthorized"}')

    async def _reject(self, send, status: int, body: bytes):
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})


class MemoryRateLimiter:
    """内存滑动窗口限流（按限流身份）：记录每次请求时间戳，精确到毫秒边界。

    仅单实例有效——多 worker/实例各记各的，限流会失效。
    """

    def __init__(self, max_requests: int = None, window_seconds: int = None):
        self.max_requests = max_requests if max_requests is not None else settings.rate_limit_max_requests
        self.window_seconds = window_seconds if window_seconds is not None else settings.rate_limit_window_seconds
        self._hits = defaultdict(deque)

    async def allow(self, identity: str) -> bool:
        now = time.time()
        q = self._hits[identity]
        while q and now - q[0] > self.window_seconds:
            q.popleft()
        if len(q) >= self.max_requests:
            return False
        q.append(now)
        return True


class RedisRateLimiter:
    """Redis 固定窗口限流（按限流身份）：INCR 计数，首次计数时设置窗口过期。

    多实例共享同一个计数器，天然全局生效。固定窗口在窗口边界处可能有
    少量突刺（上一窗口末尾 + 下一窗口开头的请求都算进新窗口），但对
    问答服务足够；实现比滑动窗口（ZSET 删过期成员）简单得多。
    """

    # 原子 INCR + 首次 EXPIRE：两条命令若拆开，多实例并发时可能都读到
    # current>1 而都不设过期，导致 key 永不过期。Lua 在服务端一次完成。
    _INCR_LUA = """
    local current = redis.call('INCR', KEYS[1])
    if current == 1 then
        redis.call('EXPIRE', KEYS[1], ARGV[1])
    end
    return current
    """

    def __init__(self, max_requests: int = None, window_seconds: int = None):
        from core.redis_client import get_redis

        self.max_requests = max_requests if max_requests is not None else settings.rate_limit_max_requests
        self.window_seconds = window_seconds if window_seconds is not None else settings.rate_limit_window_seconds
        self._redis = get_redis()
        self._incr_script = self._redis.register_script(self._INCR_LUA)

    def _key(self, identity: str) -> str:
        return f"{settings.redis_prefix}:ratelimit:{identity}"

    async def allow(self, identity: str) -> bool:
        current = await self._incr_script(keys=[self._key(identity)], args=[self.window_seconds])
        return int(current) <= self.max_requests


class RateLimitMiddleware:
    """限流中间件：按 redis_enabled 选择内存滑动窗口或 Redis 固定窗口。"""

    def __init__(self, app):
        self.app = app
        self._limiter = RedisRateLimiter() if settings.redis_enabled else MemoryRateLimiter()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not settings.rate_limit_enabled:
            await self.app(scope, receive, send)
            return
        if scope.get("path", "") in ("/health", "/ready"):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_ip = client[0] if client else "-"
        tenant_id = scope.get("state", {}).get("tenant_id")
        if settings.auth_enabled and tenant_id:
            identity = f"tenant:{tenant_id}"
        else:
            identity = f"ip:{client_ip}"
        try:
            allowed = await self._limiter.allow(identity)
        except Exception:
            # fail-open：限流是防护性功能，Redis 挂了宁可放行，也不能把整个服务搞挂。
            logger.warning("限流后端不可用，fail-open 放行（identity=%s）", identity, exc_info=True)
            allowed = True
        if not allowed:
            await self._reject(send, 429, b'{"detail":"too many requests"}')
            return
        await self.app(scope, receive, send)

    async def _reject(self, send, status: int, body: bytes):
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})
