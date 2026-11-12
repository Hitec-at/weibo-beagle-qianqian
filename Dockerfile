# 单镜像打包：SQLite 快照烤进镜像，云上只读，不挂持久卷。
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 仅拷贝运行时需要的代码与数据快照；.env（含 Cookie）由 .dockerignore 排除。
# fetch_weibo 提供视频直链按需刷新所需的限速与接口请求逻辑。
COPY --chown=appuser:appuser config.py filters.py memes.py store.py fetch_weibo.py ./
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser data ./data

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
