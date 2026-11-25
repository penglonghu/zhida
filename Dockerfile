# 应用层镜像：只复制代码，依赖来自 Dockerfile.base 构建的命名基础镜像。
# 用 scripts/build-zhida.sh 构建（会自动准备基础镜像）。
ARG BASE_IMAGE=fde-demo-zhida-base:latest
FROM ${BASE_IMAGE}

WORKDIR /app

COPY . .

RUN mkdir -p logs && chmod +x scripts/entrypoint.sh

EXPOSE 8000

# 用 python main.py 而非 uvicorn 命令行：main.py 里 log_config=None 才能生效
# （否则 uvicorn 默认日志配置会覆盖我们 core/logging.py 的 trace_id 格式）。
ENTRYPOINT ["scripts/entrypoint.sh"]
