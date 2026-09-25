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
# 各页面：自包含 HTML（内联 JS/CSS，无构建步骤），随镜像一起带。
# ⚠ 新增页面**必须在这里加一行**，否则路由在、页面文件不在 ——
#   表现是 GET 该页返回 500「页面文件缺失」，而其它页一切正常，
#   很容易被当成路由写错了去查 app.py。实测踩过一次。
COPY geometry.html ./
COPY index.html ./
COPY import.html ./
COPY tables.html ./
# 配置外置：config/ 随镜像带默认值，生产由 volume/环境变量覆盖（五件套第 3 件）
COPY config/ ./config/

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
