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
# ★★★ numeric 列必须以**数字**出去（T045）。独立成文件是为了让契约测试
#   能离线 import 它、并对「取消这个修法 ⇒ 判据变红」做元测试。
#   它只依赖 fastapi，不拖 rpdao / psycopg。
COPY modules/M6-api/jsonnum.py ./
# 空表缺口归因（纯拼装：无 SQL、不 import M2）——独立成文件便于单测
COPY modules/M6-api/gaps.py ./
# 区域帧：桩号 ↔ 经纬度（004）。投影是手写的，几何全部委托 m2geom。
COPY modules/M6-api/geo.py ./

# ---------------------------------------------------------------- 几何内核（004）
# ★ 为什么是**复制**而不是 import M2 的 adapters.geom：
#   `modules/M2-ingest/adapters/__init__.py` 会 `from . import base, errors, weidi`，
#   于是 `import adapters.geom` 会把 19 个纬地解析器一起拖进来 —— M6 不需要它们，
#   而它们又要读文件系统、带 GBK 解码假设。
#   所以把**那两个文件**逐字节搬进一个只含几何的包，M2 一个字节都不用改。
#
# ★★ 复制的代价是"可能漂移"。守卫在 tests/contract/test_region_frame.py：
#    它对 M2 原件做 sha256 比对，两边不一致就红。
#    __init__.py 用 printf 生成空文件 —— 不用 COPY，因为 M2 的 __init__.py
#    正是那个会拖进 weidi 的文件，复制它会把这个坑一起搬过来。
RUN mkdir -p /app/m2geom && printf '' > /app/m2geom/__init__.py
COPY modules/M2-ingest/adapters/errors.py /app/m2geom/errors.py
COPY modules/M2-ingest/adapters/geom.py   /app/m2geom/geom.py

# 坐标基准（带号 / 中央子午线 / EPSG）。与 modules.yaml 同一个道理：
# ⚠ 读不到时 geo.load_crs() 抛 CrsError → 路由返回 503，**不猜默认带号**。
#   这是刻意的：猜错的代价是位置差几百公里，且在局部视图里看不出来。
COPY modules/M9-console/config/crs.yaml /app/crs.yaml

# 模块登记表（M9 的唯一真源）：归因里要报每张空表的归属模块与所处阶段。
# ⚠ 必须 COPY 进来：容器里读不到宿主机的 modules.yaml 时 _module_phases() 会静默
#   返回 {}，phase 就永远是 null —— 又一个"本地跑得通、容器里静默降级"的坑。
COPY modules/M9-console/config/modules.yaml /app/modules.yaml

# PyYAML：读上面那张登记表用，requirements.txt 里没有。
RUN pip install --no-cache-dir "PyYAML>=6,<7"

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
