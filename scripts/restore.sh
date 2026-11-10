#!/usr/bin/env bash
# 从 scripts/backup.sh 生成的目录恢复 Qdrant 与 Redis。
# 用法：scripts/restore.sh <备份目录> [--yes]
# 注意：会覆盖当前知识库与会话数据；.env 不会自动覆盖，请人工核对。
set -euo pipefail
cd "$(dirname "$0")/.."

DIR="${1:-}"
YES="${2:-}"
if [ -z "$DIR" ] || [ ! -d "$DIR" ]; then
    echo "用法：$0 <备份目录> [--yes]" >&2
    exit 1
fi
for f in qdrant.snapshot redis-dump.rdb; do
    [ -f "$DIR/$f" ] || { echo "缺少文件：$DIR/$f" >&2; exit 1; }
done

if [ "$YES" != "--yes" ]; then
    read -r -p "将覆盖当前 Qdrant/Redis 数据，输入 restore 继续: " ans
    [ "$ans" = "restore" ] || { echo "已取消"; exit 1; }
fi

DIR_ABS="$(cd "$DIR" && pwd)"

echo "[1/4] 停止应用，避免缓存和写入干扰..."
docker compose stop zhida

echo "[2/4] 恢复 Qdrant..."
# 脚本从宿主机挂载：恢复时不依赖当前镜像里是否有该文件
docker compose run --rm --no-deps -T --entrypoint python \
    -v "$DIR_ABS:/backup:ro" \
    -v "$PWD/scripts:/restore-scripts:ro" \
    -e PYTHONPATH=/app \
    zhida /restore-scripts/restore_qdrant.py /backup/qdrant.snapshot

echo "[3/4] 恢复 Redis..."
docker compose stop redis
docker compose cp "$DIR/redis-dump.rdb" redis:/data/dump.rdb
docker compose start redis

echo "[4/4] 启动应用并等待就绪..."
docker compose start zhida
for _ in $(seq 1 30); do
    if docker compose exec -T zhida python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3)" \
        >/dev/null 2>&1; then
        echo "恢复完成，/ready 正常"
        docker compose exec -T zhida python - <<'PY'
from qdrant_client import QdrantClient
from config import settings

client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
print("points:", client.count(settings.qdrant_collection_name).count)
PY
        exit 0
    fi
    sleep 3
done

echo "警告：/ready 未在 90 秒内就绪，请检查日志" >&2
exit 1
