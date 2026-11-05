FROM python:3.14

WORKDIR /app

# 先复制依赖，利用 docker 层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p logs && chmod +x scripts/entrypoint.sh

EXPOSE 8000

# 用 python main.py 而非 uvicorn 命令行：main.py 里 log_config=None 才能生效
# （否则 uvicorn 默认日志配置会覆盖我们 core/logging.py 的 trace_id 格式）。
ENTRYPOINT ["scripts/entrypoint.sh"]
