"""HTTP 中间件：trace / 审计日志 / 认证 / 限流。

全部用纯 ASGI 实现（不用 BaseHTTPMiddleware）：
BaseHTTPMiddleware 会缓冲响应 body，破坏 /stream-query 的 SSE 流式输出。
纯 ASGI 只包装 send、不碰 body，对流式安全。

注册顺序（Starlette add_middleware 后加的在外层，先执行）：
RateLimit(最内) -> Auth -> Audit -> Trace(最外) -> CORS
"""
import logging
import time
import uuid
from collections import defaultdict, deque

from config import settings
from core.tracing import set_trace_id

logger = logging.getLogger(__name__)


def _header_map(scope) -> dict:
    return {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}


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
            logger.info(
                "audit method=%s path=%s status=%s ip=%s cost_ms=%.1f",
                method, path, status_holder["code"], client_ip, cost_ms,
            )


class AuthMiddleware:
    """API Key 认证：Authorization: Bearer <key> 或 X-API-Key。auth_enabled=False 时放行。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not settings.auth_enabled or not settings.service_api_key:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in ("/health", "/docs", "/openapi.json", "/redoc") or path.startswith("/docs/"):
            await self.app(scope, receive, send)
            return

        headers = _header_map(scope)
        auth = headers.get("authorization", "")
        supplied = auth[7:] if auth.startswith("Bearer ") else headers.get("x-api-key", "")

        if supplied == settings.service_api_key:
            await self.app(scope, receive, send)
            return

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


class RateLimitMiddleware:
    """内存滑动窗口限流（按客户端 IP），标准库实现，零新增依赖。

    注意：与会话存储同理，内存实现只对单实例有效，多实例要换 Redis。
    """

    def __init__(self, app):
        self.app = app
        self._hits = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not settings.rate_limit_enabled:
            await self.app(scope, receive, send)
            return
        if scope.get("path", "") == "/health":
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_ip = client[0] if client else "-"
        now = time.time()
        q = self._hits[client_ip]
        while q and now - q[0] > settings.rate_limit_window_seconds:
            q.popleft()
        if len(q) >= settings.rate_limit_max_requests:
            await self._reject(send, 429, b'{"detail":"too many requests"}')
            return
        q.append(now)
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
