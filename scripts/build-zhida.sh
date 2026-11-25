#!/usr/bin/env bash
# 一键构建：依赖层做成命名基础镜像，代码变更只重建应用层（秒级）。
#
# 用法：
#   ./scripts/build-zhida.sh                # 依赖未变则跳过基础层，只重建应用层
#   ./scripts/build-zhida.sh --up           # 构建后重建并启动 zhida 容器
#   ./scripts/build-zhida.sh --rebuild-base # 强制重建依赖基础镜像
set -euo pipefail

cd "$(dirname "$0")/.."

# 读取 .env 中的构建期变量（不导出到运行环境，仅本脚本使用）
for var in PIP_INDEX_URL TORCH_INDEX_URL BASE_IMAGE APP_IMAGE; do
    if [[ -f .env ]]; then
        value="$(grep -E "^${var}=" .env | tail -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//; s/[[:space:]]*$//' || true)"
        if [[ -n "${value}" ]]; then
            printf -v "$var" '%s' "$value"
        fi
    fi
done

BASE_IMAGE="${BASE_IMAGE:-fde-demo-zhida-base:latest}"
APP_IMAGE="${APP_IMAGE:-fde-demo-zhida:latest}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
TORCH_INDEX_URL="${TORCH_INDEX_URL:-https://mirrors.aliyun.com/pytorch-wheels/cpu/}"

# 构建期让容器直接出网：本机代理（Privoxy/SOCKS）抖动时不影响 pip/apt。
# 镜像拉取仍走 Docker Desktop 自己的代理设置。
BYPASS=(--build-arg NO_PROXY='*' --build-arg HTTP_PROXY= --build-arg HTTPS_PROXY=)

deps_hash() {
    cat requirements.txt Dockerfile.base | openssl dgst -sha256 | awk '{print $NF}'
}

WANT_HASH="$(deps_hash)"
HAVE_HASH=""
if [[ "${1:-}" != "--rebuild-base" ]]; then
    HAVE_HASH="$(docker image inspect "$BASE_IMAGE" --format '{{ index .Config.Labels "zhida.deps-hash" }}' 2>/dev/null || true)"
fi

if [[ "$HAVE_HASH" != "$WANT_HASH" ]]; then
    if [[ -z "$HAVE_HASH" ]] && docker image inspect "$APP_IMAGE" >/dev/null 2>&1; then
        # 首次迁移：复用现有应用镜像里已装好的依赖，避免重新下载
        echo "[build] 未找到基础镜像，从现有 $APP_IMAGE 复用依赖层"
        printf 'FROM %s\n' "$APP_IMAGE" | docker build \
            --label "zhida.deps-hash=$WANT_HASH" -t "$BASE_IMAGE" -
    else
        echo "[build] 依赖层需要重建 -> $BASE_IMAGE"
        echo "[build] PIP_INDEX_URL=$PIP_INDEX_URL"
        echo "[build] TORCH_INDEX_URL=$TORCH_INDEX_URL"
        docker build -f Dockerfile.base \
            --build-arg PIP_INDEX_URL="$PIP_INDEX_URL" \
            --build-arg TORCH_INDEX_URL="$TORCH_INDEX_URL" \
            --label "zhida.deps-hash=$WANT_HASH" \
            "${BYPASS[@]}" \
            -t "$BASE_IMAGE" .
    fi
else
    echo "[build] 依赖层未变化，跳过（${BASE_IMAGE}）"
fi

echo "[build] 构建应用层 -> $APP_IMAGE"
docker build -f Dockerfile --build-arg BASE_IMAGE="$BASE_IMAGE" -t "$APP_IMAGE" .

echo "[build] 完成：$APP_IMAGE"

if [[ "${1:-}" == "--up" || "${2:-}" == "--up" ]]; then
    docker compose up -d --force-recreate zhida
    echo "[build] 已重启 zhida"
fi
