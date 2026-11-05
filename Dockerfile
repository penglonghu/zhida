FROM python:3.14

WORKDIR /app

# pip 镜像源：默认清华（国内直连可用），客户内网可通过 build-arg 覆盖为自建源
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
# CPU 版 torch 源：RAG 用 CPU 推理，避免拉 ~2GB 的 CUDA nvidia 依赖
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

# 先复制依赖，利用 docker 层缓存
COPY requirements.txt .

# 先装 CPU 版 torch（体积小、无 nvidia 依赖），再装其余依赖；网络慢加大超时
RUN pip install --no-cache-dir --timeout 300 torch \
      --index-url ${TORCH_INDEX_URL} --extra-index-url ${PIP_INDEX_URL} && \
    pip install --no-cache-dir --timeout 300 -r requirements.txt -i ${PIP_INDEX_URL}

COPY . .

RUN mkdir -p logs && chmod +x scripts/entrypoint.sh

EXPOSE 8000

# 用 python main.py 而非 uvicorn 命令行：main.py 里 log_config=None 才能生效
# （否则 uvicorn 默认日志配置会覆盖我们 core/logging.py 的 trace_id 格式）。
ENTRYPOINT ["scripts/entrypoint.sh"]
