"""TraceID 上下文：用 contextvars 在单次请求内传递 trace_id。

contextvars 与 asyncio 兼容：每个请求跑在自己的 task 里，set 的值
不会串到别的并发请求，日志 Filter 读到的永远是当前请求的 trace_id。
"""
import contextvars

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")


def set_trace_id(trace_id: str) -> None:
    trace_id_var.set(trace_id)


def get_trace_id() -> str:
    return trace_id_var.get()
