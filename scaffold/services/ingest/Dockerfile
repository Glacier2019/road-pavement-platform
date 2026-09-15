# M2 接入服务镜像（骨架版）
# 说明：模块自己带 Dockerfile 是"接入五件套"的第一件（见 MODULE-ONBOARDING.md）
#
# ⚠ 构建上下文是 scaffold 根（见 docker-compose.skeleton.yml 的 build.context），
#   不是 services/ingest/ —— 因为契约③写入收口后本服务依赖 M3（packages/rpdao），
#   必须能 COPY 到它。直接 docker build 时的正确写法：
#     docker build -f services/ingest/Dockerfile -t rp-ingest .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PYTHONPATH=/app

WORKDIR /app

COPY services/ingest/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# M3 对象域数据访问层：装到 /app/rpdao，由 PYTHONPATH=/app 使其可 import。
# 收口后本服务**不再自带连接池、不再有裸 SQL**，读写一律经契约③。
COPY packages/rpdao /app/rpdao

COPY services/ingest/models.py services/ingest/violations.py services/ingest/app.py ./

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
