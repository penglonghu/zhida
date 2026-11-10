"""Prometheus 指标定义与 HTTP 采集。

指标口径按 RED 方法：Rate（请求量）、Errors（错误率）、Duration（延迟）。
业务侧补充 LLM 调用、检索耗时、文档入库与问答结果，便于判断
「是模型慢、检索慢，还是服务本身慢」。

为什么用 prometheus_client 而不是手写文本格式：
- 输出是 Prometheus 事实标准，客户现有监控（Prometheus/夜莺）可直接抓
- 直方图分桶、标签、累加计数、线程安全都已处理好，避免自定义格式出错
- 自带进程级指标（CPU/内存/GC），省掉额外采集
单进程 uvicorn 下无多进程聚合问题（多 worker 才需要 multiprocess 模式，本项目未用）。
"""
import logging
import time

from prometheus_client import Counter, Gauge, Histogram

logger = logging.getLogger(__name__)

# ===== HTTP（RED）=====
HTTP_REQUESTS = Counter(
    "zhida_http_requests_total",
    "HTTP 请求总数",
    ["method", "route", "status"],
)
HTTP_DURATION = Histogram(
    "zhida_http_request_duration_seconds",
    "HTTP 请求耗时（秒，含流式响应全程）",
    ["route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)
HTTP_IN_FLIGHT = Gauge(
    "zhida_http_in_flight_requests",
    "正在处理中的请求数",
)

# ===== 依赖调用 =====
LLM_REQUESTS = Counter(
    "zhida_llm_requests_total",
    "LLM 调用次数",
    ["operation", "status"],   # operation: chat/stream/rewrite/verify；status: ok/error/cancelled
)
LLM_DURATION = Histogram(
    "zhida_llm_request_duration_seconds",
    "LLM 调用耗时（秒，含重试）",
    ["operation"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120),
)
RETRIEVAL_DURATION = Histogram(
    "zhida_retrieval_duration_seconds",
    "检索耗时（秒，含嵌入 + 召回 + 融合）",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

# ===== 业务 =====
DOCUMENTS_UPLOADED = Counter(
    "zhida_documents_uploaded_total",
    "成功入库的文档数",
)
CHUNKS_ADDED = Counter(
    "zhida_document_chunks_total",
    "成功入库的文本块数",
)
ANSWERS = Counter(
    "zhida_answers_total",
    "完成的问答数",
    ["result"],   # answered / no_answer（资料未覆盖）
)

# ===== 就绪状态（/ready 每次检查后更新，供告警与看板使用）=====
READY = Gauge("zhida_ready", "服务整体就绪 1/0")
COMPONENT_READY = Gauge(
    "zhida_component_ready",
    "依赖组件就绪 1/0",
    ["component"],   # qdrant / embedding_model / redis
)

# 延迟标签只认白名单路由：任意 404 路径若原样作标签，会被扫描器制造高基数，
# 拖慢 Prometheus 采集并放大内存（标签基数爆炸是监控最常见的坑）。
KNOWN_ROUTES = {
    "/", "/login", "/logout", "/me", "/query", "/stream-query",
    "/upload-doc", "/clear-collection", "/clear-session",
    "/knowledge-bases", "/list-chunks",
    "/health", "/ready", "/metrics", "/docs", "/openapi.json", "/redoc",
}


def _route_label(scope) -> str:
    # Starlette 路由匹配成功后会把 Route 对象写进 scope["route"]，
    # 用它的模板路径（如 /query）而不是原始 URL，天然避免高基数。
    route = scope.get("route")
    if route is not None:
        path = getattr(route, "path", None)
        if path:
            return path
    path = scope.get("path", "")
    return path if path in KNOWN_ROUTES else "other"


class MetricsMiddleware:
    """最外层 HTTP 指标采集中间件（纯 ASGI，不缓冲 body，不破坏 SSE）。

    必须注册为最外层（最后 add_middleware）：这样认证 401、限流 429、
    路由 404 等被内层中间件/路由短路的请求也能被统计，不会漏计错误率。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "?")
        status_holder = {"code": 0}
        start = time.perf_counter()
        HTTP_IN_FLIGHT.inc()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            HTTP_IN_FLIGHT.dec()
            route = _route_label(scope)
            HTTP_REQUESTS.labels(method, route, str(status_holder["code"])).inc()
            HTTP_DURATION.labels(route).observe(time.perf_counter() - start)