"""一次性迁移：给旧数据回填 tenant_id / kb_id（默认 scope）。

单 collection 隔离上线前入库的 point 没有这两个字段，带 filter 的检索会
查不到它们。升级后执行一次即可；新数据入库时已自动写入。

用法：
    python scripts/migrate_isolation.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from core.rag_retriever import retriever


def main():
    updated = retriever.migrate_scope_fields()
    print(
        f"回填完成：{updated} 个 point -> "
        f"tenant_id={settings.default_tenant_id}, kb_id={settings.default_kb_id}"
    )


if __name__ == "__main__":
    main()
