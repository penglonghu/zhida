"""集中式日志配置：文件 + 控制台双输出，幂等。

各模块用 logging.getLogger(__name__) 拿自己的 logger，自动带上模块名，
取代原来 core 里散落的 print()。
"""
import logging
import sys
from pathlib import Path

_configured = False


def setup_logging(log_level: str = "INFO") -> None:
    global _configured
    if _configured:
        return

    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    file_handler = logging.FileHandler(log_dir / "service.log", encoding="utf-8")
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(log_level.upper())
    root.addHandler(file_handler)
    root.addHandler(stream_handler)

    _configured = True