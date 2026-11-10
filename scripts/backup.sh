#!/usr/bin/env bash
# 在线备份 Qdrant（快照）、Redis（RDB）与 .env。
# 用法：scripts/backup.sh [输出根目录]
# 环境变量：BACKUP_ROOT（默认 backups）、KEEP_DAYS（默认 7，0 表示不清理）
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_ROOT="${BACKUP_ROOT:-backups}"
KEEP_DAYS="${KEEP_DAYS:-7}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${1:-$BACKUP_ROOT/$STAMP}"
mkdir -p "$OUT"

echo "[1/4] 创建 Qdrant 快照..."
COLLECTION="$(docker compose exec -T zhida python -c \
    "from config import settings; print(settings.qdrant_collection_name)" | tail -n1 | tr -d '\r')"
SNAP="$(docker compose exec -T zhida python - "$COLLECTION" <<'PY' | tail -n1 | tr -d '\r'
import sys
from qdrant_client import QdrantClient
from config import settings

client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port, timeout=180)
print(client.create_snapshot(sys.argv[1]).name)
PY
)"
docker compose cp "qdrant:/qdrant/snapshots/$COLLECTION/$SNAP" "$OUT/qdrant.snapshot"
# 拷出后删除容器内快照，避免反复备份堆积磁盘
docker compose exec -T zhida python - "$COLLECTION" "$SNAP" <<'PY' >/dev/null
import sys
from qdrant_client import QdrantClient
from config import settings

client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port, timeout=180)
client.delete_snapshot(sys.argv[1], sys.argv[2])
PY

echo "[2/4] 保存 Redis RDB..."
docker compose exec -T redis redis-cli SAVE >/dev/null
docker compose cp redis:/data/dump.rdb "$OUT/redis-dump.rdb"

echo "[3/4] 备份配置..."
if [ -f .env ]; then
    cp .env "$OUT/env.backup"
    chmod 600 "$OUT/env.backup"
fi

echo "[4/4] 写 manifest 与校验和..."
{
    echo "created_at=$(date -Iseconds 2>/dev/null || date)"
    echo "collection=$COLLECTION"
    echo "qdrant_snapshot=$SNAP"
    echo "keep_days=$KEEP_DAYS"
} > "$OUT/manifest.txt"
if command -v sha256sum >/dev/null 2>&1; then
    (cd "$OUT" && sha256sum qdrant.snapshot redis-dump.rdb env.backup > SHA256SUMS 2>/dev/null || \
        sha256sum qdrant.snapshot redis-dump.rdb > SHA256SUMS)
else
    (cd "$OUT" && shasum -a 256 qdrant.snapshot redis-dump.rdb env.backup > SHA256SUMS 2>/dev/null || \
        shasum -a 256 qdrant.snapshot redis-dump.rdb > SHA256SUMS)
fi

if [ "$KEEP_DAYS" != "0" ] && [ -d "$BACKUP_ROOT" ]; then
    find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -exec rm -rf {} + 2>/dev/null || true
fi

echo "备份完成：$OUT ($(du -sh "$OUT" | cut -f1))"
