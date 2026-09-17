# M9 平台管理台（集成面）
# 接入五件套第 1 件：模块自带容器定义（见 MODULE-ONBOARDING.md §2）
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
# 几何浏览页：自包含 HTML（内联 JS/CSS，无构建步骤），随镜像一起带
COPY geometry.html ./
# 配置外置：config/ 随镜像带默认值，生产由 volume/环境变量覆盖（五件套第 3 件）
COPY config/ ./config/

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
