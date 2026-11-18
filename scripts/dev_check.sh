#!/usr/bin/env bash
# 知答 ZhiDa 本地一键体检
# 用法：bash scripts/dev_check.sh
# 目标：在"从没跑过这个项目"的机器上也给出明确的下一步提示。
# 退出码：0 = 无硬性错误（允许有提示项）；1 = 有硬性错误（.venv 缺失 / 文件缺失 / 语法编译失败等）。
set -euo pipefail

# 切到仓库根目录（本脚本所在目录的上一级），保证从任意目录调用都可运行
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PY="$ROOT_DIR/.venv/bin/python"
RUFF="$ROOT_DIR/.venv/bin/ruff"
ERRORS=0
WARNINGS=0

log_ok()   { printf '✅ %s\n' "$*"; }
log_warn() { printf '⚠️  %s\n' "$*"; WARNINGS=$((WARNINGS + 1)); }
log_err()  { printf '❌ %s\n' "$*"; ERRORS=$((ERRORS + 1)); }
section()  { printf '\n== %s ==\n' "$*"; }

section "1/7 Python 虚拟环境"
if [[ ! -x "$PY" ]]; then
    log_err "未找到可执行的 .venv/bin/python"
    echo "    修复：python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
else
    log_ok "虚拟环境存在：$("$PY" --version 2>&1)"
    if "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)'; then
        log_ok "Python 版本满足要求（>= 3.13）"
    else
        log_err "Python 版本过低（本项目要求 >= 3.13），请用新 Python 重建 .venv"
    fi
fi

section "2/7 必需文件"
for f in \
    main.py config.py schemas.py \
    requirements.txt requirements-dev.txt ruff.toml \
    .env.example Dockerfile docker-compose.yml \
    scripts/entrypoint.sh deploy/nginx.conf prompts/system.md chat.html; do
    if [[ -f "$ROOT_DIR/$f" ]]; then
        log_ok "存在：$f"
    else
        log_err "缺失：$f"
    fi
done

section "3/7 环境变量（.env）"
if [[ -f "$ROOT_DIR/.env" ]]; then
    log_ok "存在：.env"
    if grep -qE '^LLM_API_KEY=.+$' "$ROOT_DIR/.env"; then
        log_ok "LLM_API_KEY 已填写"
    else
        log_warn ".env 中 LLM_API_KEY 为空，问答会失败；请参考 .env.example 填写真实 Key"
    fi
else
    log_warn "未找到 .env —— 复制模板并填入真实 Key：cp .env.example .env"
fi

section "4/7 嵌入模型"
if [[ -d "$ROOT_DIR/models/bge-small-zh" ]]; then
    log_ok "存在：models/bge-small-zh"
else
    log_warn "未找到 models/bge-small-zh —— 首次使用请运行：.venv/bin/python download_bge.py（需联网）"
fi

section "5/7 docker compose 配置"
if ! command -v docker >/dev/null 2>&1; then
    log_warn "未安装 docker，跳过 compose 配置校验（仅容器部署时需要）"
elif ! docker compose version >/dev/null 2>&1; then
    log_warn "docker compose 插件不可用，跳过 compose 配置校验"
elif [[ ! -f "$ROOT_DIR/.env" ]]; then
    log_warn ".env 不存在，compose 的 env_file 无法解析，跳过校验（cp .env.example .env 后再试）"
elif docker compose config --quiet 2>/dev/null; then
    log_ok "docker compose 配置可解析（无需启动 daemon）"
else
    log_err "docker compose 配置有误，请运行 docker compose config 查看明细"
fi

section "6/7 语法编译（compileall）"
if [[ -x "$PY" ]]; then
    if "$PY" -m compileall -q core scripts main.py config.py schemas.py; then
        log_ok "compileall 通过（语法无误）"
    else
        log_err "compileall 失败，见上方报错"
    fi
else
    log_warn "跳过 compileall：.venv 不存在"
fi

section "7/7 静态检查（ruff）"
if [[ -x "$RUFF" ]]; then
    # --no-cache：避免在仓库里留下 .ruff_cache 目录
    if "$RUFF" check --no-cache .; then
        log_ok "ruff 检查通过"
    else
        log_err "ruff 检查未通过，见上方明细"
    fi
else
    log_warn "未安装 ruff，跳过；安装：pip install -r requirements-dev.txt"
fi

section "下一步"
cat <<'NEXT'
  1. 激活虚拟环境：        source .venv/bin/activate
  2. 准备配置：            cp .env.example .env    # 填入 LLM_API_KEY 等真实值
  3. 下载嵌入模型（首次）： .venv/bin/python download_bge.py
  4. 启动 Qdrant：         docker run -d -p 6333:6333 -v "$(pwd)/qdrant_storage:/qdrant/storage" qdrant/qdrant
  5. 启动服务：            .venv/bin/python main.py
  6. 打开页面：            chat.html（API 默认 http://127.0.0.1:8000）
NEXT

printf '\n================ 体检结果 ================\n'
if (( ERRORS > 0 )); then
    printf '硬性错误 %d 项，提示 %d 项；请先按上面的 ❌ 提示修复后重跑。\n' "$ERRORS" "$WARNINGS"
    exit 1
fi
printf '通过（提示 %d 项）。项目可以启动。\n' "$WARNINGS"
exit 0
