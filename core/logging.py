"""集中式日志配置：文件 + 控制台双输出，幂等，自动注入 trace_id。

各模块用 logging.getLogger(__name__) 拿自己的 logger，自动带上模块名。
trace_id 通过 TraceFormatter 从 contextvars 读取，写入每条日志记录，
让现场排错时能把同一请求散落各处的日志串起来。
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import settings
from core.tracing import get_trace_id

_configured = False


class TraceFormatter(logging.Formatter):
    """格式化时注入当前请求的 trace_id（contextvars 读取，无请求时补 "-"）。

    用 Formatter 而非 Filter 的原因：logger 的 filter 只对「该 logger 直接
    记录」的 record 生效，子 logger 传播上来的 record 不会经过父 logger 的
    filter；而 formatter 挂在 handler 上，每条经过的 record 都会命中。
    """
    def format(self, record: logging.LogRecord) -> str:
        record.trace_id = get_trace_id()
        return super().format(record)


def setup_logging(log_level: str = "INFO") -> None:
    global _configured
    if _configured:
        return

    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = TraceFormatter(
        "%(asctime)s | %(levelname)s | trace=%(trace_id)s | %(name)s | %(message)s"
    )

    file_handler = RotatingFileHandler(
        log_dir / "service.log",
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(log_level.upper())
    root.addHandler(file_handler)
    root.addHandler(stream_handler)

    _configured = True
