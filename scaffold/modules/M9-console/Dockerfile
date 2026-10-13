# M9 平台管理台（集成面）
# 接入五件套第 1 件：模块自带容器定义（见 MODULE-ONBOARDING.md §2）
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app

COPY modules/M9-console/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY modules/M9-console/app.py ./
COPY modules/_shared/jsonnum.py ./
# 各页面：自包含 HTML（内联 JS/CSS，无构建步骤），随镜像一起带。
#
# ⚠⚠ 这里原先是一行一个文件（`COPY geo.html ./`、`COPY geometry.html ./` …）。
#    那是一份**写死的手工清单**，而它的失效方式特别阴：
#      · 路由在、文件不在 → GET 该页 500「页面文件缺失」，**其它页一切正常**，
#        于是很容易被当成路由写错了去查 app.py；
#      · 更阴的是 `.py`：本目录下的模块文件不在清单里时，import 才失败，
#        表现为**某一个接口** 500，而整个容器健康检查是绿的。
#    实测踩过**两次** —— 第一次是页面（下面这段注释就是那时候写的），
#    第二次是 005 新增的 `crs_drift.py`（`/v1/crs/drift` 直接 500）。
#    ★ 一份要人手维护的清单，就是一份迟早会漏的清单。所以这里改成**通配**：
#      新增 .py / .html **不需要动这个文件**，这一类故障从根上消失。
#      （对应宪法 VI 第①条：不许把平台自身的组成写成手工清单。）
COPY modules/M9-console/*.py ./
COPY modules/M9-console/*.html ./
# 配置外置：config/ 随镜像带默认值，生产由 volume/环境变量覆盖（五件套第 3 件）
COPY modules/M9-console/config/ ./config/

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]