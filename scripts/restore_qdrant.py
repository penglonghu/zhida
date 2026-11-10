"""把快照上传到 Qdrant 覆盖指定 collection（由 scripts/restore.sh 调用）。"""
import sys

import httpx

from config import settings


def main():
    path = sys.argv[1]
    collection = sys.argv[2] if len(sys.argv) > 2 else settings.qdrant_collection_name
    url = (
        f"http://{settings.qdrant_host}:{settings.qdrant_port}"
        f"/collections/{collection}/snapshots/upload"
    )
    with open(path, "rb") as f:
        resp = httpx.post(
            url,
            params={"priority": "snapshot"},
            files={"snapshot": ("snapshot", f, "application/octet-stream")},
            timeout=600,
        )
    resp.raise_for_status()
    print(f"Qdrant 恢复完成：collection={collection}, status={resp.status_code}")


if __name__ == "__main__":
    main()
