# M2 接入服务镜像（骨架版）
# 说明：模块自己带 Dockerfile 是"接入五件套"的第一件（见 MODULE-ONBOARDING.md）
#
# ⚠ 构建上下文是 scaffold 根（见 docker-compose.skeleton.yml 的 build.context），
#   不是 modules/M2-ingest/ —— 因为契约③写入收口后本服务依赖 M3（modules/M3-rpdao），
#   必须能 COPY 到它。直接 docker build 时的正确写法：
#     docker build -f modules/M2-ingest/Dockerfile -t rp-ingest .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PYTHONPATH=/app

WORKDIR /app

COPY modules/M2-ingest/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# M3 对象域数据访问层：装到 /app/rpdao，由 PYTHONPATH=/app 使其可 import。
# 收口后本服务**不再自带连接池、不再有裸 SQL**，读写一律经契约③。
COPY modules/M3-rpdao/rpdao /app/rpdao

# 契约⑤（设计数据导入）落地侧：解析适配器 + 落库器。
# 加了 /v1/design/import 之后，本服务要能 import 这些；在此之前它们只被测试用到，
# 没进镜像 —— 于是"测试里跑得通"和"服务里跑得通"是两件事。
# ⚠ adapters/ 要整目录 COPY：design_import 依赖 adapters.geom 与 adapters.weidi.*，
#   逐个文件列容易漏（漏了就是启动即 ImportError）。
COPY modules/M2-ingest/adapters /app/adapters

# ⚠ 顶层 .py 也一律通配 COPY，不再逐个列文件名。
#   2026-10-09 实测教训：`geometry_solver.py`（09-28 为 design_import 新增）漏在
#   逐个列名的清单外，而 `design_import.py` 第 40 行 `import geometry_solver` ——
#   镜像一重建就启动即 ModuleNotFoundError、容器 crash-loop。
#   之所以长期没暴露，是因为当时跑着的镜像停在 09-25、根本不含 design_import 的新代码：
#   **陈旧镜像会把"新代码没进镜像"这类错误整个盖住**。
#   顶层 .py 恰好就是全部运行时模块，通配可杜绝再次漏列；
#   子目录 tools/ 不受 `*.py` 影响，仍按下方说明刻意不进镜像。
COPY modules/M2-ingest/*.py ./

# 一次性转换工具 tools/tsf2txt.py **刻意不进镜像**：它是运维在导入前手工跑的
# （.tsf → .tsftxt），不是运行时的一部分；放进来会诱使别人以为服务能自己转换。
# 但 /v1/design/capabilities 要引用它的后缀规则 —— 那条规则已显式登记在
# adapters/weidi/__init__.py 的 CONVERSION_REQUIRED 里（adapters/ 已整目录 COPY）。

# 契约⑤ 的 schema：端点拿它校验自己产出的 IR 过不过契约。
# 放在 /app/contracts/design-import/，与 IR_SCHEMA_PATH 的默认值对应。
COPY contracts/design-import /app/contracts/design-import

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
