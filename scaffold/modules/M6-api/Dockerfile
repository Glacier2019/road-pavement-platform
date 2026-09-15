# M6 统一数据出口镜像（骨架版）
#
# ⚠ 构建上下文是 scaffold 根（见 docker-compose.skeleton.yml 的 build.context），
#   不是 modules/M6-api/ —— 因为本服务依赖 M3（modules/M3-rpdao），必须能 COPY 到它。
#   直接 docker build 时的正确写法：
#     docker build -f modules/M6-api/Dockerfile -t rp-api .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PYTHONPATH=/app

WORKDIR /app

COPY modules/M6-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# M3 对象域数据访问层：装到 /app/rpdao，由 PYTHONPATH=/app 使其可 import
COPY modules/M3-rpdao/rpdao /app/rpdao

COPY modules/M6-api/app.py ./

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
