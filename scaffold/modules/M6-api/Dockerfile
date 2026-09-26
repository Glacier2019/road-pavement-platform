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
# 空表缺口归因（纯拼装：无 SQL、不 import M2）——独立成文件便于单测
COPY modules/M6-api/gaps.py ./

# 模块登记表（M9 的唯一真源）：归因里要报每张空表的归属模块与所处阶段。
# ⚠ 必须 COPY 进来：容器里读不到宿主机的 modules.yaml 时 _module_phases() 会静默
#   返回 {}，phase 就永远是 null —— 又一个"本地跑得通、容器里静默降级"的坑。
COPY modules/M9-console/config/modules.yaml /app/modules.yaml

# PyYAML：读上面那张登记表用，requirements.txt 里没有。
RUN pip install --no-cache-dir "PyYAML>=6,<7"

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
