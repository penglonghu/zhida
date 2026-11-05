#!/bin/sh
set -e

# 嵌入模型是运行时必需的大文件，交付时随包挂载，构建镜像时不打包。
# 这里做防御性检查：模型缺失直接报错退出，避免带病启动。
MODEL_DIR="/app/models/bge-small-zh"
if [ ! -f "$MODEL_DIR/config_sentence_transformers.json" ]; then
    echo "错误：未找到嵌入模型 $MODEL_DIR" >&2
    echo "请先运行 python download_bge.py 下载模型，或用 volume 把 models 目录挂载进容器。" >&2
    exit 1
fi

exec python main.py
