"""契约一致性测试：HTTP 路由契约（openapi）↔ 服务实现（FastAPI 路由表）必须一致。

运行（无需建环境、无需容器）：
  cd /data/cy/shujuku/scaffold
  # 方式一：与服务的真实依赖一致（推荐）
  uv run --with fastapi==0.115.6 --with httpx --with "psycopg[binary,pool]==3.2.3" \
      python tests/contract/test_api_routes.py
  # 方式二：本机已有 fastapi + httpx 时直接跑（psycopg 缺失会自动用桩，见 _ensure_pg_driver）
  python3 tests/contract/test_api_routes.py

为什么需要这条测试（这是骨架期真实踩过的坑）：
Starlette/FastAPI **按注册顺序取第一个完全匹配的路由**。若把参数化路由
`/v1/objects/{object_type}` 写在具体路由 `/v1/objects/wim_axle` 之前，后者会被
前者吃掉——请求永远命中 `{object_type}`，而 `wim_axle` 不在档案对象白名单里，
于是**永远返回 404**。这种故障：

  · 语法编译（py_compile）查不出来；
  · 报文契约测试（test_wim_contract.py）查不出来；
  · 只有"真的发一次 HTTP 请求"才暴露。

而契约文件 contracts/openapi/m6-gateway.v0.3.yaml 里明明写着该端点，
接入规约 MODULE-ONBOARDING.md 也拿它当示范路径——契约与实现就此静默漂移。
所以本测试做三件事：
  1) 路由自洽：每条路由用它自己的具体 URL 去匹配，第一个命中的必须是它自己；
  2) 契约对齐：openapi 里声明的路径集合 ↔ 实现里的路径集合，双向相等；
  3) 真实请求：用 TestClient 打一遍状态码（M3 数据访问层被替换为假实现，不需要数据库）。

「不需要数据库」是刻意的：路由表在 import 时就固定了，与本测试要验证的东西无关。
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import re
import sys

# ★ 读契约要用它。API_DEPS 里加了 pyyaml —— 判据从「比参数名」升到
#   「比参数名与默认值」之后，按缩进抠字读不动嵌在 `schema:` 底下的 `default`。
import yaml  # noqa: E402

from fastapi.testclient import TestClient
from starlette.routing import Match
# ★ `psycopg` 把 `numeric` 读成 `Decimal`，所以库里的 `crs_central_meridian_deg`
#   到 API 手上就是 Decimal。假行必须是**同一个类型**，否则「numeric 出去要转 float」
#   那条投影就在测试里测不到（`_num` 对 str 和 Decimal 是两种行为）。
from decimal import Decimal  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_PATH = ROOT / "modules" / "M6-api" / "app.py"
CONTRACT_PATH = ROOT / "contracts" / "openapi" / "m6-gateway.v0.3.yaml"
OPENAPI_DIR = ROOT / "contracts" / "openapi"

# M3（rpdao）是 api 的依赖。本测试直接从源码路径加载 app.py，故需手动把
# modules/M3-rpdao/ 放进 sys.path —— **必须在 import rpdao 之前**（容器里由
# PYTHONPATH=/app 负责，见 modules/M6-api/Dockerfile）。
if str(ROOT / "modules" / "M3-rpdao") not in sys.path:
    sys.path.insert(0, str(ROOT / "modules" / "M3-rpdao"))
# M6 自己的目录也要进去：app.py 现在 `import gaps`（同目录的兄弟模块），
# 而本测试是**按路径**加载 app.py 的，不经过包机制 —— 不放进 sys.path 就找不到。
# 容器里由 WORKDIR /app 天然满足，所以这是个"只在测试里才暴露"的差异。
if str(ROOT / "modules" / "M6-api") not in sys.path:
    sys.path.insert(0, str(ROOT / "modules" / "M6-api"))

from rpdao import GeRepository, NotFound  # noqa: E402  （须在上面 sys.path 就位之后）


def _ensure_pg_driver():
    """本测试不触库，但 modules/M6-api/app.py 在 import 期就会 import psycopg。

    未安装 psycopg 时用最小桩顶上，好让这条测试在任何环境（无网络、无容器）都能跑——
    「随时可跑」正是它存在的意义。用桩时会显式打印告警，不静默。
    """
    try:
        import psycopg  # noqa: F401
        import psycopg_pool  # noqa: F401
        return
    except ImportError:
        pass
    import types
    if "psycopg" not in sys.modules:
        psycopg = types.ModuleType("psycopg")
        rows = types.ModuleType("psycopg.rows")
        rows.dict_row = type("dict_row", (), {})
        psycopg.rows = rows
        sys.modules["psycopg"], sys.modules["psycopg.rows"] = psycopg, rows
    if "psycopg_pool" not in sys.modules:
        pool = types.ModuleType("psycopg_pool")

        class ConnectionPool:  # 连接池在 import 期只被构造、不建立连接
            def __init__(self, *a, **k): pass
            def open(self): pass
            def close(self): pass
            def connection(self, *a, **k):
                raise RuntimeError("本测试不应触达数据库（q 已被替换为假实现）")

        pool.ConnectionPool = ConnectionPool
        sys.modules["psycopg_pool"] = pool
    print("⚠ 未检测到 psycopg/psycopg_pool，已用桩替代（本测试不触库，不影响结论）\n")


_ensure_pg_driver()

# 模板 → 具体值（用于把契约路径变成可请求的 URL）
CONCRETE = {
    "{object_type}": "road_line",
    "{record_id}": "1",
    "{action_name}": "create_maintenance_ticket",
}


def load_app():
    spec = importlib.util.spec_from_file_location("api_app", APP_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["api_app"] = mod
    spec.loader.exec_module(mod)
    return mod


APP = load_app()
ROUTES = [r for r in APP.app.routes if getattr(r, "path", "").startswith("/")]


def concrete_url(path: str) -> str:
    for tmpl, val in CONCRETE.items():
        path = path.replace(tmpl, val)
    return path


def first_match(url: str, method: str = "GET"):
    """返回该 URL 实际会命中的第一个路由（模拟 Starlette 的匹配语义）。"""
    scope = {"type": "http", "method": method, "path": url, "headers": [],
             "query_string": b"", "root_path": "", "scheme": "http",
             "server": ("testserver", 80)}
    for r in ROUTES:
        if r.matches(scope)[0] == Match.FULL:
            return r
    return None


def own_method(route) -> str:
    """用路由自己声明的方法去探测它（否则 POST 路由会被误判为"无匹配"）。"""
    ms = [m for m in (getattr(route, "methods", None) or {"GET"})
          if m not in ("HEAD", "OPTIONS")]
    return sorted(ms)[0] if ms else "GET"


def contract_paths() -> set[str]:
    """从 openapi 契约里抽出路径（不引 pyyaml，按缩进解析即可）。

    ★ 契约是**多份的**：v0.3 是骨架期那一次冻结的正本，此后每个 feature 的
      扩展各自成一份 `m6-gateway.<feature>.v0.1.yaml`（宪法原则 I 禁止就地改
      已发布的契约，所以只能加新文件）。声明面 = 这些文件的**并集**。
    ★ 只看 v0.3 的话，加了路由的实现会被判成「实现缺契约」—— 004 的区域帧
      就是这么红的。那不是实现错，是这条测试当时只认识一份契约。
    """
    paths = set()
    files = sorted(OPENAPI_DIR.glob("m6-gateway*.yaml"))
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^  (/\S*):\s*$", line)
            if m:
                paths.add(m.group(1))
    return paths


def contract_files() -> list[pathlib.Path]:
    return sorted(OPENAPI_DIR.glob("m6-gateway*.yaml"))


class _FakeLo:
    """LO 域仓储的假实现。路由测试只关心"谁被命中"，不关心查出什么。"""

    ROW = {"id": 1, "pass_time": "2026-09-14T13:45:02+08:00", "lane_no": 2,
           "axle_type_code": "T5", "axle_num": 5, "speed_kmh": 68.4,
           "gross_weight_kg": 51200.0, "overload_flag": False, "esal": 14.73,
           "plate_no": None, "quality_code": "OK", "station_text": "K4640+000"}

    def passages(self, **kw):
        return [self.ROW]

    def passage(self, record_id):
        # 只有 id=1 存在，用来验证"记录不存在 → 404"分支
        if record_id != 1:
            raise NotFound(f"过车记录不存在：{record_id}")
        return {**self.ROW, "axles": [{"axle_seq": 1, "axle_weight_kg": 6400.0}]}

    def daily_summary(self, day, station=None):
        return {"date": day.isoformat(), "station": station, "passages": 1,
                "overloaded": 0, "overload_ratio": 0.0, "esal_sum": 14.73, "buckets": []}


class _FakeGe:
    """GE 域仓储的假实现。GE 的方法**一律带 section_id**（按路段取子树）——
    这个假实现刻意不提供无 section 的平铺查，与真实 GeRepository 保持一致。

    ★★ 这个 `SECTION` 必须带上 12 个 `crs_*` 列 —— 那是 `ONE_SECTION_SQL` 的
      真实输出形状。005 把「生效基准」的解析从文件搬到了这一行上之后，
      `/sections` 会对每一行调用 `effective_crs(row)`：
      行里没有这些键，就等于「两级都没配」，于是**每一条**都变成
      「没有基准」——一个新增的读取被一个迁移前的假行形状放大成了
      全局错误，而报错位置离真因很远。
      ★ 同理 `effective_crs` 在这里**委派给真实现**，不抄第二份五态表。
    """

    SECTION = {"id": 6, "section_name": "毕设", "line_code": "毕设",
               "start_station_text": "K0+000.000", "end_station_text": "K5+805.421",
               "station_count": 2, "pi_count": 1, "element_count": 1,
               "section_crs_epsg": None, "section_crs_zone_number": None,
               "section_crs_central_meridian_deg": None,
               "section_crs_zone_width_deg": None,
               "section_crs_assumed": None, "section_crs_evidence": None,
               "project_crs_epsg": 4548, "project_crs_zone_number": 39,
               "project_crs_central_meridian_deg": Decimal("117.000000"),
               "project_crs_zone_width_deg": Decimal("3.00"),
               "project_crs_assumed": False,
               "project_crs_evidence": "39 带已确认（见 sql/99_seed_crs_39band.sql）"}

    # ★ `staticmethod(...)` 不能省：不加就变实例方法，`self` 会被塞进 `row`。
    effective_crs = staticmethod(GeRepository.effective_crs)

    # ★★ 005 / T043：真 `dao.ge` 现在有 `crs_family`（问 `spatial_ref_sys`
    #    这个 SRID 是什么投影族），M6 的 `_geo_crs` 每次都会调它。
    #    假的没有，那条路就直接 `AttributeError` —— 这次的报错位置离真因不远，
    #    所以它是**响的**；真正危险的是有人为了让它不响而把生产代码改成不查族。
    #    默认一切 SRID 都是 `tmerc`（这个夹具用的是 4548）。
    def crs_family(self, srid):
        return "tmerc"

    def sections(self):
        return [self.SECTION]

    def section(self, section_id):
        if section_id != 6:
            raise NotFound(f"路段不存在：section_id={section_id}")
        return self.SECTION

    def stations(self, section_id, *, from_km=None, to_km=None,
                 integer_only=False, limit=500, offset=0):
        self.section(section_id)        # 不存在 → NotFound
        return [{"station_seq_no": 1, "station_local_km": 0.0,
                 "station_text": "K0+000.000", "station_type": "endpoint",
                 "is_integer_station": True}]

    def alignment(self, section_id):
        self.section(section_id)
        return {"section_id": section_id,
                "pis": [{"pi_seq": 1, "radius_m": 450.0}],
                "elements": [{"element_seq": 1, "element_type": "line", "pi_id": None}]}

    def widths(self, section_id):
        self.section(section_id)
        # 真库里是**变化点**：同一桩号左右各一行。假实现保留这个形状，
        # 免得前端在假数据上写"一行一个断面"的代码而到真库上才炸。
        return [{"side": "left",  "seq_no": 1, "group_seq": 1, "station_km": 0.0,
                 "median_width_m": 0.0, "half_carriageway_width_m": 3.5,
                 "extra_lane_flag": 0, "hard_shoulder_width_m": 0.75,
                 "earth_shoulder_width_m": 0.75, "extra_lane_file": None, "remark": None},
                {"side": "right", "seq_no": 1, "group_seq": 1, "station_km": 0.0,
                 "median_width_m": 0.0, "half_carriageway_width_m": 3.5,
                 "extra_lane_flag": 0, "hard_shoulder_width_m": 0.75,
                 "earth_shoulder_width_m": 0.75, "extra_lane_file": None, "remark": None}]

    def superelevation(self, section_id):
        self.section(section_id)
        # 真库里 **9999 → null**；假实现保留 null，让"当 0 处理"的写法在这里就露馅。
        return [{"transition_seq": 1, "station_km": 0.0,
                 "earth_shoulder_left_pct": -3.0, "hard_shoulder_left_pct": -2.0,
                 "lane_left_pct": -2.0, "lane_right_pct": -2.0,
                 "hard_shoulder_right_pct": -2.0, "earth_shoulder_right_pct": -3.0,
                 "remark": None},
                {"transition_seq": 2, "station_km": 0.485874,
                 "earth_shoulder_left_pct": None, "hard_shoulder_left_pct": None,
                 "lane_left_pct": None, "lane_right_pct": None,
                 "hard_shoulder_right_pct": None, "earth_shoulder_right_pct": None,
                 "remark": "源文件该行 6 列均为 9999（忽略此数据）"}]

    # ★★ D-029：下面这两个方法原先**替身里没有** —— 真 `GeRepository` 有，
    #   替身没有。少一个方法，就少一分「端点真的只用这些」的保证；
    #   而替身缺的方法会以 `AttributeError` 的形式在**离真因很远**的地方炸。
    def elements_by_station(self, section_id, from_km, to_km, *, limit=2000):
        self.section(section_id)        # 不存在 → NotFound（D-029 的规则）
        if from_km > to_km:
            raise ValueError(f"区间反了：from_km={from_km} > to_km={to_km}")
        return [{"element_seq": 1, "element_type": "line",
                 "span_m": 1.0, "length_m": 1.0}]

    def samples_by_station(self, section_id, seg, from_km, to_km, *, limit=5000):
        self.section(section_id)        # 不存在 → NotFound（D-029 的规则）
        # ★ 段名白名单**委派给真实现**，不抄第二份 —— 同 `effective_crs` 的理由：
        #   抄一份就会漂移，而这份白名单是「哪些段存在」的**唯一**声明。
        if seg not in GeRepository.SAMPLE_ANCHORS:
            raise ValueError(
                f"未知段 {seg!r}；可用：{sorted(GeRepository.SAMPLE_ANCHORS)}"
                "（不接受未知段名 —— 静默返回空会被读成「区间内无数据」）")
        return [{"station_km": 0.0, "station_text": "K0+000.000"}]

    def completeness(self, section_id):
        self.section(section_id)
        return {"section_id": section_id, "geometry_level": "L2",
                "level_reason": "alignment_pi／alignment_element 有数据",
                "present": {"station_sequence": 2}, "missing": [],
                "missing_not_built": ["cross_section"], "missing_no_data": [],
                "counts": {"station_sequence": 2}}


class _FakeDomain:
    def __init__(self, code): self.code = code

    #: 假想「表里有多少行」。默认 1 —— 与改造前**逐字一致**，
    #: 所以既有的用例读数不变。§4 会临时把它调大来验截断边界。
    ROWS: int = 1

    def list_objects(self, table, limit=100, offset=0):
        # ★ 必须**真的尊重 limit** —— 否则 §4 测不出东西：
        #   一个忽略 limit、恒回 1 行的假仓储，会让「截断」永远为假。
        # ★★ 同理必须**真的尊重 offset**（2026-10-14 加）：一个收下 offset
        #   却不跳行的假仓储，会让 §5 的翻页用例**永远绿** —— 而翻页正是要测的东西。
        rows = [{"id": i + 1, "table": table} for i in range(self.ROWS)]
        return rows[offset:offset + limit]


class _FakeCross:
    """跨域支撑表仓储的假实现（`/v1/support` 出口）。

    ★ 形状照抄真 `CrossRepository` 的公开面：`catalog()` / `list_rows()` / `count()`。
      真实现还有 `order_key()` 与私有 `_check()` —— 端点不碰它们，故假实现不提供
      （**故意不提供**：假实现多给一分接口，就少一分「端点真的只用这些」的保证）。
    """

    ROWS = {"dict_axle_type": [
        {"code": "A2", "name": "2 轴汽车", "remark": None},
        {"code": "T5", "name": "5 轴货车", "remark": "G228 实测主力车型"},
    ]}

    def catalog(self):
        return [{"table": "dict_axle_type", "kind": "dictionary",
                 "order_key": "code", "row_count": len(self.ROWS["dict_axle_type"])}]

    def list_rows(self, table, *, limit=100, offset=0):
        rows = self.ROWS[table]
        return rows[offset:offset + limit]

    def count(self, table):
        return len(self.ROWS[table])


class _FakeDao:
    """替换 M3 门面。真实 Dao 的接口面就是这些——这正是 M3 契约的可替代性证明。"""

    def __init__(self):
        self.lo = _FakeLo()
        self.ge = _FakeGe()

    def domain(self, code): return _FakeDomain(code)
    def cross(self): return _FakeCross()
    def ping(self): return True
    def pool_stats(self): return {"pool_size": 0, "pool_available": 0}
    def open(self): return self
    def close(self): pass


def main() -> int:
    fails: list[str] = []

    # ---------------------------------------------------------- 1) 路由自洽
    print("=== 1) 路由自洽：每条路由的第一个完全匹配必须是它自己 ===")
    print(f"{'注册序':<6}{'路径':<36}{'实际命中':<36}结论")
    print("-" * 96)
    for idx, r in enumerate(ROUTES):
        url = concrete_url(r.path)
        hit = first_match(url, method=own_method(r))
        ok = hit is r
        print(f"{idx:<6}{r.path:<36}{(hit.path if hit else '无'):<36}"
              f"{'✓' if ok else '✗ 被遮蔽'}")
        if not ok:
            fails.append(f"路由遮蔽：{r.path} → 实际命中 {hit.path if hit else '无'}")

    # ---------------------------------------------------------- 2) 契约对齐
    print("\n=== 2) 契约对齐：openapi 声明 ↔ 实现 ===")
    AUTO = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    impl = {r.path for r in ROUTES if r.path not in AUTO}
    doc = contract_paths()
    only_doc = sorted(doc - impl)
    only_impl = sorted(impl - doc)
    print(f"契约声明 {len(doc)} 条 / 实现 {len(impl)} 条")
    print(f"  契约来源 {len(contract_files())} 份："
          + "、".join(f.name for f in contract_files()))
    if only_doc:
        print(f"  ✗ 契约有、实现没有：{only_doc}")
        fails.append(f"契约缺实现：{only_doc}")
    if only_impl:
        print(f"  ✗ 实现有、契约没有：{only_impl}")
        fails.append(f"实现缺契约：{only_impl}")
    if not only_doc and not only_impl:
        print("  ✓ 双向一致")

    # ---------------------------------------------------------- 3) 真实请求
    print("\n=== 3) 真实请求（TestClient，数据层为假实现）===")
    APP.dao = _FakeDao()               # 端点函数在调用时从模块全局取 dao
    client = TestClient(APP.app)
    cases = [
        ("GET",    "/healthz",                          200),
        ("GET",    "/v1/objects/wim_axle",              200),   # ← 被遮蔽时这里是 404
        ("GET",    "/v1/objects/wim_axle/1",            200),
        ("GET",    "/v1/objects/road_line",             200),
        ("GET",    "/v1/objects/not_registered",        404),
        ("GET",    "/v1/objects/wim_axle/99999",        404),
        # 几何查询（工单 #4）：按路段取子树
        ("GET",    "/v1/geometry/sections",                         200),
        ("GET",    "/v1/geometry/sections/6/stations",              200),
        ("GET",    "/v1/geometry/sections/6/stations?from_km=0&to_km=1&integer_only=true", 200),
        ("GET",    "/v1/geometry/sections/6/alignment",             200),
        ("GET",    "/v1/geometry/sections/6/summary",               200),
        ("GET",    "/v1/geometry/sections/6/widths",                200),
        ("GET",    "/v1/geometry/sections/6/superelevation",        200),
        ("GET",    "/v1/geometry/sections/99999/summary",           404),
        ("GET",    "/v1/geometry/sections/99999/widths",            404),
        ("GET",    "/v1/geometry/sections/99999/superelevation",    404),
        # ★★★ D-029：上面两行（widths / superelevation）**本来就在表里**，
        #   而且一直是绿的 —— 因为**假实现回 404、真实现回 200**。
        #   替身「知道」这条规则，真代码不知道，而没有任何东西在比这两者。
        #   ⇒ 现在真实现追上了（M3 `GeRepository._require_section`），
        #     并把整棵子树补全：另外五个兄弟早就回 404，这四个原先回 200 空。
        ("GET",    "/v1/geometry/sections/99999/stations",          404),
        ("GET",    "/v1/geometry/sections/99999/alignment",         404),
        ("GET",    "/v1/geometry/sections/99999/elements?from_km=0&to_km=1", 404),
        ("GET",    "/v1/geometry/sections/99999/samples?segment=station_sequence&from_km=0&to_km=1", 404),
        # 参数校验：非法筛选项应被 FastAPI 挡在门外（422），而不是进到 DAO
        ("GET",    "/v1/geometry/sections/6/stations?limit=0",      422),
        ("GET",    "/v1/geometry/sections/6/stations?from_km=-1",   422),
        # ★★★ 跨域支撑表出口（2026-10-14，D-027）—— 这四条是本出口的**边界判据**。
        #   ★ 加它们的直接原因：`_check()` 初版对「域对象走错门」抛 ContractViolation
        #     ⇒ 400，而同一份契约里写着 404 —— **契约与实现当场不一致**。
        #     探针表当时是**人手写的期望码**、与契约从不对读，所以它一片绿。
        #   ⇒ 现在把这四条钉在探针里：它们同时守住「正路通」「边界不拆」「码值对」。
        ("GET",    "/v1/support",                                   200),
        ("GET",    "/v1/support/dict_axle_type",                    200),
        ("GET",    "/v1/support/wim_axle_record",                   404),  # 域对象走错门
        ("GET",    "/v1/support/nonexistent_tbl",                   404),  # 没登记的表
        ("GET",    "/v1/support/dict_axle_type?limit=0",            422),  # 下界
        ("GET",    "/v1/support/dict_axle_type?limit=1001",         422),  # 上界
        # ★ 反向：域对象**不能**出现在支撑表出口，支撑表也**不能**从对象出口拿到。
        #   两条一起才说明「门是两扇、边界是清楚的」，不是白名单被放宽了。
        ("GET",    "/v1/objects/dict_axle_type",                    404),
        # ★ D-028：三条**必填/越界**校验的探针。写 5l 时量出这三个码
        #   「契约声明了、却从没被真打到过」——能一条探针解决的，就不该进欠账单。
        ("GET",    "/v1/objects/road_line?limit=0",                 422),
        ("GET",    "/v1/geometry/sections/6/elements",              422),  # 缺 from_km
        ("GET",    "/v1/geometry/sections/6/samples",               422),  # 缺 segment
        ("POST",   "/v1/actions/create_maintenance_ticket", 501),
    ]
    for method, url, want in cases:
        resp = client.request(method, url)
        ok = resp.status_code == want
        print(f"  {method:<5}{url:<46}{resp.status_code}（期望 {want}）{'✓' if ok else ' ✗'}")
        if not ok:
            fails.append(f"{method} {url} → {resp.status_code}，期望 {want}")

    # 关键回归：wim_axle 必须命中 list_wim，而不是参数化路由
    resp = client.get("/v1/objects/wim_axle")
    if resp.status_code == 200 and resp.json().get("object_type") == "wim_axle_record":
        print("\n  ✓ 回归点：/v1/objects/wim_axle 命中 list_wim（未被 {object_type} 遮蔽）")
    else:
        print(f"\n  ✗ 回归点：/v1/objects/wim_axle 未命中 list_wim → {resp.status_code} "
              f"{str(resp.json())[:120]}")
        fails.append("wim_axle 路由遮蔽回归")

    # 几何查询的内容回归：不只看状态码，还看**取到的是不是那个路段的子树**
    print("\n  ── 几何查询内容回归 ──")
    r = client.get("/v1/geometry/sections/6/alignment").json()
    if r.get("pis") and r.get("elements") and all("pi_id" in e for e in r["elements"]):
        print(f"  ✓ alignment 返回交点链({len(r['pis'])}) + 线元链({len(r['elements'])})")
    else:
        print(f"  ✗ alignment 结构不对 → {str(r)[:120]}")
        fails.append("alignment 返回结构不符")
    r = client.get("/v1/geometry/sections/6/summary").json()
    if r.get("section", {}).get("section_name") and r.get("completeness", {}).get("geometry_level"):
        print(f"  ✓ summary 同时给出 section（{r['section']['section_name']}）"
              f"与 completeness（{r['completeness']['geometry_level']}）")
    else:
        print(f"  ✗ summary 结构不对 → {str(r)[:120]}")
        fails.append("summary 返回结构不符")
    r = client.get("/v1/geometry/sections/6/widths").json()
    if r.get("items") and all(x["side"] in ("left", "right") for x in r["items"]):
        print(f"  ✓ widths 返回 {r['count']} 行，side 全为 left/right")
    else:
        print(f"  ✗ widths 结构不对 → {str(r)[:120]}")
        fails.append("widths 返回结构不符")

    r = client.get("/v1/geometry/sections/6/superelevation").json()
    _has_null = any(v is None for x in r.get("items", []) for v in x.values())
    if r.get("items") and _has_null:
        print(f"  ✓ superelevation 返回 {r['count']} 行，且保留了源文件 9999 对应的 null")
    else:
        print(f"  ✗ superelevation 结构不对（null 丢了？）→ {str(r)[:120]}")
        fails.append("superelevation 返回结构不符或丢失 null")

    # 元测试：GE 假实现也不提供无 section 的平铺查 —— 若哪天有人加了，这里会红
    if not any(hasattr(_FakeGe, m) for m in ("all_pis", "list_objects")):
        print("  ✓ GE 端点无「无 section 的平铺查」（路段一多就会静默串台）")
    else:
        print("  ✗ GE 端点出现了平铺查")
        fails.append("GE 端点出现平铺查")

    # ------------------------------------------------- 4) 截断必须可见
    print("\n=== 4) 截断必须可见：`truncated` 的边界（D-011 的收尾）===")
    # ★ 这一组守的是：原先**拿满 limit 行时，响应与「恰好这么多」完全一样**。
    #   实测量到的后果：`cross_section_ground_point` 有 2215 行，
    #   接口回 `count: 100`，而响应里**没有任何字段**说明还有更多。
    #
    # ★★ 边界表里 (100, 100) 那条是**关键**：一个「拿满就猜」的实现
    #    （`len(rows) >= limit`）在这里会判 True，而真相是 False ——
    #    一张正好 100 行的表是**能完整取到**的。
    #    这条用例就是那个错误实现的回归点。
    boundary = [
        # (表里的行数, limit, 期望 truncated, 期望 count)
        (0,    100, False,   0),
        (5,    100, False,   5),
        (100,  100, False, 100),   # ★ 恰好等于 limit —— 不算截断
        (101,  100, True,  100),   # ★ 多一行就翻面
        (250,  100, True,  100),
        (1000,  10, True,   10),
    ]
    for rows_n, lim, want_trunc, want_count in boundary:
        _FakeDomain.ROWS = rows_n
        body = client.get(f"/v1/objects/road_line?limit={lim}").json()
        got_trunc = body.get("truncated")
        got_count = body.get("count")
        got_len = len(body.get("items", []))
        ok = (got_trunc is want_trunc and got_count == want_count
              and got_len == want_count)
        print(f"  表 {rows_n:>4} 行 / limit {lim:<4} → truncated={got_trunc!s:<5} "
              f"count={got_count:<4} items={got_len:<4} {'✓' if ok else '✗'}")
        if not ok:
            fails.append(f"截断判定错：表 {rows_n} 行 limit {lim} → "
                         f"truncated={got_trunc} count={got_count}（期望 "
                         f"{want_trunc}/{want_count}）")
    _FakeDomain.ROWS = 1        # ★ 还原，别影响别的用例

    # ★ 字段**存在性**单独钉一次：本次缺陷的形态就是「字段不存在」，
    #   而不是「字段值不对」。只查值的用例挡不住它。
    _body = client.get("/v1/objects/road_line").json()
    if "truncated" in _body:
        print("  ✓ 响应里**存在** `truncated` 字段（缺陷的形态是它不存在）")
    else:
        print(f"  ✗ 响应里没有 `truncated` → {sorted(_body)}")
        fails.append("响应缺 truncated 字段")

    # ---- 4c) ★ `_list_tracked` 的硬线：拒收不完整结果，但**不误伤** ----
    # ★★ 这一组修的是一个**误报**：它原先判 `len(rows) >= 100`，
    #    而「恰好 100 行」的表会拿满 100 行 → 被判成截断 → 回 503。
    #    一张正好 100 行的表本来是**能完整取到**的，却被拒绝服务。
    #    加了一行探测之后判定变精确：要到 101 行才叫截断。
    print("\n=== 4c) `_list_tracked`：拒收不完整，但恰好取满不算截断 ===")
    from fastapi import HTTPException as _HTTPExc

    class _StubRepo:
        """只有 `list_objects` 一个方法 —— 这正是 `_list_tracked` 用到的全部。"""
        def __init__(self, n): self.n = n
        def list_objects(self, table, *, limit=100, offset=0):
            return [{"id": i + 1} for i in range(min(self.n, limit))]

    for n, want_status in ((99, None), (100, None), (101, 503)):
        try:
            rows = APP._list_tracked(_StubRepo(n), "monitor_cross_section")
            got, detail = None, f"返回 {len(rows)} 行"
        except _HTTPExc as exc:
            got, detail = exc.status_code, f"HTTP {exc.status_code}"
        ok = got == want_status
        print(f"  表 {n:>4} 行 → {detail:<16} {'✓' if ok else '✗'} "
              f"（期望 {'放行' if want_status is None else want_status}）")
        if not ok:
            fails.append(f"_list_tracked 表 {n} 行 → {got}（期望 {want_status}）")

    # ------------------------------------- 5) 通用出口：翻页 + 未知查询参数必须报错
    # ★★ 这一组守的是 2026-10-14（用户裁决的「甲」）修掉的两个**实测**缺陷：
    #   ① 不认识的查询参数被静默吞掉 —— `?section_id=6` 与 `?bogus_xyz=1`
    #      拿到的响应和什么都不加时**一模一样**（都是全表的 332 行），
    #      调用方把「全部数据」读成了「我筛出来的数据」；
    #   ② 不能翻页 —— `?offset=1000` 回的仍是 id=[1, 2]，一张 2215 行的表
    #      后半段**永远取不到**，而契约里写着「truncated: true → 客户端据此翻页」。
    print("\n=== 5) 通用出口：翻页 + 未知查询参数必须报错（甲）===")

    # ---- 5a) ★ 未知参数必须 422，并列出**支持的键** ----
    for bad in ("section_id", "bogus_xyz", "offsett"):
        resp = client.get(f"/v1/objects/road_line?{bad}=1")
        txt = resp.text
        ok = (resp.status_code == 422 and bad in txt
              and "limit" in txt and "offset" in txt)
        print(f"  ?{bad}=1 → HTTP {resp.status_code} "
              f"{'✓ 报错且列出了支持的键' if ok else '✗ 被静默吞掉'}")
        if not ok:
            fails.append(f"未知参数 ?{bad}=1 → {resp.status_code}（期望 422 且列出支持的键）")

    # ---- 5b) 对照：合法参数**不受影响**（别为了挡坏人误伤好人）----
    resp = client.get("/v1/objects/road_line?limit=5&offset=0")
    if resp.status_code == 200:
        print("  ?limit=5&offset=0 → HTTP 200 ✓ 合法参数不受影响")
    else:
        print(f"  ?limit=5&offset=0 → HTTP {resp.status_code} ✗ 误伤了合法参数")
        fails.append(f"合法参数被误伤 → {resp.status_code}")

    # ---- 5c) ★★ 翻页：不重、不漏、页数与 truncated 轨迹都对 ----
    _FakeDomain.ROWS = 250
    seen: list[int] = []
    pages: list[list[int]] = []
    trunc_trace: list[bool] = []
    off = 0
    while off <= 1000:                      # 死循环保险：绝不把测试挂住
        b = client.get(f"/v1/objects/road_line?limit=100&offset={off}").json()
        ids = [r["id"] for r in b["items"]]
        pages.append(ids)
        trunc_trace.append(b["truncated"])
        seen.extend(ids)
        if not b["truncated"]:
            break
        off += 100
    _FakeDomain.ROWS = 1                    # ★ 还原，别影响别的用例
    dup = len(seen) != len(set(seen))
    ok_page = (not dup and seen == list(range(1, 251)) and len(pages) == 3
               and trunc_trace == [True, True, False])
    print(f"  250 行 / limit 100 → {len(pages)} 页，truncated={trunc_trace}，"
          f"并集 {len(seen)} 行{'（有重复！）' if dup else ''} {'✓' if ok_page else '✗'}")
    if not ok_page:
        fails.append(f"翻页错：{len(pages)} 页 truncated={trunc_trace} 并集 {len(seen)} 行 "
                     f"重复={dup}（期望 3 页 / [True, True, False] / 250 / 无重复）")

    # ---- 5d) ★ 最后一页必须 truncated=False ----
    #   ★ 单钉这一条：一个「永远说还有更多」的实现会让客户端**永远翻下去**。
    if trunc_trace and trunc_trace[-1] is False:
        print("  ✓ 最后一页 truncated=False（不会让客户端永远翻下去）")
    else:
        print(f"  ✗ 最后一页 truncated={trunc_trace[-1:]} → 客户端会永远翻下去")
        fails.append("最后一页 truncated 不为 False")

    # ---- 5e) ★ offset 默认 0：不加参数与显式 offset=0 必须**逐位相同** ----
    _FakeDomain.ROWS = 7
    r_plain = client.get("/v1/objects/road_line?limit=3").json()
    r_zero = client.get("/v1/objects/road_line?limit=3&offset=0").json()
    _FakeDomain.ROWS = 1
    ok_same = (r_plain == r_zero
               and [r["id"] for r in r_plain["items"]] == [1, 2, 3])
    print("  不加 offset == offset=0 → "
          + ("✓ 逐位相同，且仍从第 1 行起" if ok_same else "✗ 有差异"))
    if not ok_same:
        fails.append(f"offset 改变了既有行为：{r_plain} vs {r_zero}")

    # ---- 5f) ★ offset 越界 → 空页，**不是** 500 ----
    _FakeDomain.ROWS = 7
    resp = client.get("/v1/objects/road_line?limit=3&offset=999")
    _FakeDomain.ROWS = 1
    _jb = resp.json() if resp.status_code == 200 else {}
    ok_over = (resp.status_code == 200 and _jb.get("count") == 0
               and _jb.get("truncated") is False)
    print(f"  ?limit=3&offset=999（越界）→ HTTP {resp.status_code} "
          f"count={_jb.get('count', '-')} "
          f"{'✓ 空页' if ok_over else '✗ 应当返回空页而不是报错'}")
    if not ok_over:
        fails.append(f"offset 越界 → {resp.status_code}（期望 200 空页）")

    # ---- 5g) ★★★ 假仓储签名漂移检测 ----
    # ★★ 为什么要这一条：真仓储这次加了 `offset`，而 tests/ 下有**假仓储**
    #   实现同一个方法。上一版代码的注释里写着**正因为**要连它们一起改，
    #   所以才「不去动仓储的签名」—— 于是缺陷就一直留着。
    #   与其永远回避，不如**让漂移必然被发现**：假仓储漏了参数，这条就红。
    def _fakes_missing_offset(sources: list[tuple[str, str]]) -> list[str]:
        """找出**收了 `self` 却没收 `offset`** 的 `list_objects`。

        ★★ 用 **AST**，不用正则 —— 这不是洁癖，是实测踩过的坑：
          第一版是文本扫描，结果它**扫到了本文件自己**：这个检查的 docstring 里
          写着 `def list_objects(self, …)`，元测试里又内嵌了一段假源码，
          于是「找到 8 个、其中 3 个没跟上」，而真正该查的 4 个**全是好的**。
          ⇒ 一个会把注释和字符串当成现实的判据，报出来的错**和现实无关**。
          AST 里注释和字符串根本不出现，这个坑从根上没了。

        ★ 只收第一个形参是 `self` 的：那才是仓储实现；
          路由那个是 `def list_objects(request: Request, …)`，天然不算。
        """
        bad: list[str] = []
        for name, text in sources:
            for node in ast.walk(ast.parse(text)):
                if not (isinstance(node, ast.FunctionDef)
                        and node.name == "list_objects"):
                    continue
                a = node.args
                if not a.args or a.args[0].arg != "self":
                    continue
                names = ([x.arg for x in a.args] + [x.arg for x in a.kwonlyargs])
                if "offset" not in names:
                    bad.append(f"{name}:{node.lineno} 只收了 {names}")
        return bad

    _SRC = [(tf.name, tf.read_text(encoding="utf-8"))
            for tf in sorted((ROOT / "tests" / "contract").glob("*.py"))]
    def _count_fakes(sources: list[tuple[str, str]]) -> int:
        """数一数**真的**有几个带 `self` 的 `list_objects`（AST，理由同上）。"""
        n = 0
        for _, text in sources:
            for node in ast.walk(ast.parse(text)):
                if (isinstance(node, ast.FunctionDef)
                        and node.name == "list_objects"
                        and node.args.args and node.args.args[0].arg == "self"):
                    n += 1
        return n

    _found = _count_fakes(_SRC)
    _missing = _fakes_missing_offset(_SRC)
    print(f"  假仓储签名：扫了 {len(_SRC)} 个契约测试文件，"
          f"找到 {_found} 个 `def list_objects(self, …)`")
    # ★ 先钉「这条检查真的看到了东西」—— 一个匹配 0 个目标的检查永远不会失败，
    #   比没有检查更糟（宪法原则 V）。少了就说明正则或目录已经不认识现实了。
    if _found < 4:
        print(f"  ✗ 只找到 {_found} 个假仓储（期望 ≥ 4）→ 这条检查已经失效了")
        fails.append(f"假仓储扫描只命中 {_found} 个（期望 ≥ 4），检查本身失效")
    elif not _missing:
        print("  ✓ 全部假仓储都收了 `offset`（真仓储接口变了，它们都跟上了）")
    else:
        print(f"  ✗ 有假仓储没跟上真仓储：{_missing}")
        fails.append(f"假仓储签名漂移：{_missing}")

    # ---- 5h) ★★ 元测试：证明 5g 的判据**会响** ----
    # ★ 宪法原则 V：「一个永远不会失败的检查，比没有检查更糟。」
    #   所以这里喂一段**故意漏了 offset** 的源码，判据必须把它认出来。
    #   ★ 假源码写成**真实的类体**（假仓储本来就是类方法）—— 裸的缩进 `def`
    #     连 `ast.parse` 都过不去，那样测到的是语法错误，不是判据。
    _FAKE_SRC = [("虚构-漏了-offset.py",
                  "class Fake:\n"
                  "    def list_objects(self, table, *, limit=100):\n"
                  "        return []\n")]
    _caught = _fakes_missing_offset(_FAKE_SRC)
    _clean = _fakes_missing_offset(
        [("虚构-合规.py",
          "class Fake:\n"
          "    def list_objects(self, table, *, limit=100, offset=0):\n"
          "        return []\n"),
         ("虚构-路由-不是仓储.py",
          "def list_objects(request, object_type: str, limit: int = 100):\n"
          "    return {}\n")])
    ok_meta = len(_caught) == 1 and not _clean
    print(f"  元测试：漏 offset 的假仓储被认出 → {_caught}；"
          f"合规的不误报 → {'是' if not _clean else _clean} "
          f"{'✓' if ok_meta else '✗'}")
    if not ok_meta:
        fails.append(f"假仓储判据的元测试失败：认出={_caught} 误报={_clean}")

    # ---- 5g-bis) ★★★ 收了 `offset` 的仓储方法，它的 SQL 里必须真有 OFFSET ----
    # ★★★ 2026-10-14 晚加的，起因是一次**红验**：给两个端点补上翻页之后，
    #   我把 `PASSAGES_SQL` 末尾的 `OFFSET %(offset)s::int` 删掉 ——
    #   那会让 `wim_axle` 的翻页**彻底失效**（永远回第一页，而 `truncated`
    #   会永远报 true，因为 `limit+1` 探测拿到的仍是第 1..n+1 行）。
    #   ★ 而全套 28 条**一条都没红**，exit=0。
    #
    #   原因：5g 守的是**假仓储的签名**，5i/5j 守的是**契约与 schema 的声明**。
    #   没有任何一条守**SQL 本身** —— 声明对了、签名对了，SQL 里把 OFFSET 拿掉，
    #   整套门一声不响。这正是 D-022 那一类：**新加的能力没有东西在看它。**
    #
    # ★ 判据**精确到「签名收了 offset 的方法」**，不是「所有带 LIMIT 的 SQL」：
    #   实测 `ELEMENTS_BY_STATION_SQL` 与 `SAMPLE_UNIFORM_SQL` 都带
    #   `LIMIT %(limit)s` 却没有 OFFSET，而它们**不该有** —— `/samples` 与
    #   `/elements` 两个端点都没有 `limit` 查询参数，那个 LIMIT 是内部上限，
    #   不是给调用方翻页用的。一刀切会误报两条，而误报会让这道门被关掉。
    #
    # ★ `list_objects` 没有 `_SQL` 常量（SQL 是内联 f-string），所以对
    #   「一个 `_SQL` 都没引用」的方法改为直接查它自己的源码文本。
    #   ⇒ 这条不写清楚，判据会对内联那一个**静默放过**（宪法原则 V 的老坑）。
    def _paging_gaps(dao_src: str) -> tuple:
        """→ (收了 offset 的方法数, [SQL 里没有 OFFSET 的方法])。"""
        tree = ast.parse(dao_src)
        consts: dict = {}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id.endswith("_SQL")):
                consts[node.targets[0].id] = node.value.value
        n, bad = 0, []
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            a = fn.args
            names = [x.arg for x in a.args] + [x.arg for x in a.kwonlyargs]
            if "offset" not in names:
                continue
            n += 1
            used = sorted({x.attr for x in ast.walk(fn)
                           if isinstance(x, ast.Attribute)
                           and isinstance(x.value, ast.Name)
                           and x.value.id == "self"
                           and x.attr.endswith("_SQL")})
            if not used:
                # 内联 SQL：查方法自己的源码
                seg = ast.get_source_segment(dao_src, fn) or ""
                if "OFFSET" not in seg:
                    bad.append(f"{fn.name}（内联 SQL）收了 offset 却没用 OFFSET")
                continue
            for attr in used:
                sql = consts.get(attr)
                if sql is not None and "OFFSET" not in sql:
                    bad.append(f"{fn.name} 收了 offset，但 {attr} 里没有 OFFSET")
        return n, bad

    _DAO = (ROOT / "modules" / "M3-rpdao" / "rpdao" / "repo.py").read_text(
        encoding="utf-8")
    _n_off, _pg_bad = _paging_gaps(_DAO)
    print(f"  翻页落地：收了 `offset` 的仓储方法 {_n_off} 个，"
          f"SQL 里缺 OFFSET 的 {len(_pg_bad)} 个")
    # ★ 计数下界：4 是 2026-10-14 实测值（list_objects / passages / stations /
    #   stations_by_line）。判据看不到目标时，`_pg_bad` 会是空 —— 空得像通过。
    if _n_off < 4:
        print(f"  ✗ 只找到 {_n_off} 个收 offset 的仓储方法（期望 ≥ 4）"
              f" —— 这条检查已经不认识现实了")
        fails.append(f"翻页落地判据失效：只找到 {_n_off} 个收 offset 的方法")
    elif not _pg_bad:
        print("  ✓ 每个收了 offset 的方法，SQL 里都真有 OFFSET")
    else:
        print(f"  ✗ 收了 offset 却没落到 SQL：{_pg_bad}")
        fails.append(f"翻页没有落到 SQL：{_pg_bad}")

    # ---- 5g-ter) ★★ 元测试：证明上面这条**会响** ----
    _PG_FAKE = (
        "class R:\n"
        "    X_SQL = \"SELECT 1 LIMIT %(limit)s::int\"\n"
        "    def paginated(self, *, limit=10, offset=0):\n"
        "        return self._dao.query(self.X_SQL, {})\n"
        "    Y_SQL = \"SELECT 1 LIMIT %(limit)s::int OFFSET %(offset)s::int\"\n"
        "    def fine(self, *, limit=10, offset=0):\n"
        "        return self._dao.query(self.Y_SQL, {})\n"
        "    def inline_bad(self, *, limit=10, offset=0):\n"
        "        return self._dao.query(\"SELECT 1 LIMIT 1\", {})\n"
        "    def not_paginated(self, *, limit=10):\n"
        "        return self._dao.query(self.X_SQL, {})\n")
    _pg_n, _pg_caught = _paging_gaps(_PG_FAKE)
    _pg_ok = (_pg_n == 3
              and any("paginated" in x for x in _pg_caught)
              and any("inline_bad" in x for x in _pg_caught)
              and not any("fine" in x for x in _pg_caught)
              and not any("not_paginated" in x for x in _pg_caught))
    print(f"  元测试：SQL 缺 OFFSET 被认出 → {_pg_caught} "
          f"{'✓' if _pg_ok else '✗'}")
    if not _pg_ok:
        fails.append(f"翻页落地判据的元测试失败：认出={_pg_caught}")

    # ---- 5i) ★★★ 契约声明的查询参数（含默认值）== 实现认识的，**全部端点** ----
    # ★★ 这一条守的是**成因本身**，不是某一个端点。原来它只管
    #   `/v1/objects/{object_type}`（甲 那一轮加的），于是「契约许了一个实现
    #   给不出的能力」这件事在**别的 20 个端点上**依然没有任何东西在比。
    #   第 2 组的路径对齐门只比**路径集合**，参数与默认值的漂移它看不见。
    #
    # ★★ 换成解析真 yaml，不再按缩进抠字：现在要比**默认值**，而 `default`
    #   嵌在 `schema:` 底下。缩进假设是判据里最脆的一环，而这次要读的东西
    #   正好长在它最容易崩的位置。代价只是依赖桶里多一个 pyyaml。
    #
    # ★★★ 声明面是**多份契约的并集**：`m6-gateway.geo.v0.2.yaml` 头部逐字
    #   写着「本文件是 v0.1 的**增量**，只描述新增与变化的部分」，所以它对
    #   同一路径只列自己动过的参数 —— 只看一份会漏掉另一半，而漏掉的那半
    #   正好是**没有人比过**的那半。
    def _ver_key(fn: pathlib.Path) -> tuple:
        """契约文件的版本序：先比 (主,次)，同版再比文件名。

        ★ 这是本仓既有的版本约定，不是这条判据新定的：后一版覆盖前一版。
          同名参数被多份声明时按这个序遍历，**靠后的赢**。
        """
        m = re.search(r"\.v(\d+)\.(\d+)\.yaml$", fn.name)
        v = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        return (v, fn.name)

    def _declared_query() -> tuple:
        """{路径: {方法: {参数名: (默认值, 文件名, schema, required, in)}}} + 覆盖记录。

        ★ 存五元组是因为后面有两条判据要用同一份解析结果：5i 只用前两项，
          5j 要用中间两项，两条都要用末一项。**一份解析、两处消费**，不各写一遍。

        ★★★ 2026-10-14 晚：**把 `in: path` 收进来了。**
          原先这里只收 `in: query`，5j 也只收 `in: query` ⇒
          **路径参数的名字、类型、required 一个都没比过**。
          实测两边各有 14 个路径参数、逐条对得上 —— 但那是**碰巧一致**，
          不是**被守住**。这一层当时没有任何东西在看。
        """
        out: dict = {}
        over: dict = {}
        for f in sorted(contract_files(), key=_ver_key):
            doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            for path, item in (doc.get("paths") or {}).items():
                for method, op in item.items():
                    if not isinstance(op, dict):
                        continue
                    slot = out.setdefault(path, {}).setdefault(method, {})
                    for prm in (op.get("parameters") or []):
                        if prm.get("in") not in ("query", "path"):
                            continue
                        nm = prm["name"]
                        if nm in slot and slot[nm][1] != f.name:
                            over.setdefault(f"{method.upper()} {path}", []).append(
                                f"{nm}: {slot[nm][1]}={slot[nm][0]!r} → {f.name}")
                        slot[nm] = ((prm.get("schema") or {}).get("default"), f.name,
                                    prm.get("schema") or {},
                                    bool(prm.get("required", False)),
                                    prm.get("in"))
        return out, over

    def _compare(decl: dict, live: dict) -> tuple:  # noqa: D401
        """比「两边都有的端点」→ (比了几对, 名字不一致, 默认值不一致,
        有默认值的对数, in 位置不一致, {in: 参数个数})。

        ★ **只比两边都有的**：契约未收录的端点归第 2 组的路径门管，
          这里再报一次就是同一条性质有了两份定义。

        ★★★ `in` 位置（query / path）**必须比**：只比名字的话，
          契约写 `in: query` 而实现是 `in: path` 会**静默通过** ——
          名字一样，调用方式完全不同（一个拼查询串、一个拼进路径）。
          这是「名字对得上但语义错」的典型，光比名字看不见。
        """
        pairs, def_pairs = 0, 0
        name_bad: list = []
        def_bad: list = []
        in_bad: list = []
        kinds: dict = {}
        for path in sorted(set(live) & set(decl)):
            for method in sorted(set(live[path]) & set(decl[path])):
                impl = {}
                for q in (live[path][method].get("parameters") or []):
                    if q.get("in") not in ("query", "path"):
                        continue
                    impl[q["name"]] = ((q.get("schema") or {}).get("default"),
                                       q.get("in"))
                    kinds[q.get("in")] = kinds.get(q.get("in"), 0) + 1
                declm = decl[path][method]
                pairs += 1
                if sorted(impl) != sorted(declm):
                    name_bad.append(f"{method.upper()} {path}：契约 {sorted(declm)}"
                                    f" vs 实现 {sorted(impl)}")
                    continue
                for nm in sorted(declm):
                    # ★ 位置先比：位置错了，后面的默认值比较没有意义
                    if declm[nm][4] != impl[nm][1]:
                        in_bad.append(f"{method.upper()} {path} 的参数 {nm}："
                                      f"契约 in={declm[nm][4]!r}（{declm[nm][1]}）"
                                      f" vs 实现 in={impl[nm][1]!r}")
                        continue
                    if declm[nm][0] is None or impl[nm][0] is None:
                        continue
                    def_pairs += 1
                    if declm[nm][0] != impl[nm][0]:
                        def_bad.append(f"{method.upper()} {path} 的参数 {nm}："
                                       f"契约 {declm[nm][0]!r}（{declm[nm][1]}）"
                                       f" vs 实现 {impl[nm][0]!r}")
        return pairs, name_bad, def_bad, def_pairs, in_bad, kinds

    _decl, _over = _declared_query()
    (_pairs, _name_bad, _def_bad, _def_pairs,
     _in_bad, _kinds) = _compare(_decl, APP.app.openapi().get("paths", {}))
    print(f"  比了 {_pairs} 个端点、{sum(_kinds.values())} 个参数"
          f"（查询 {_kinds.get('query', 0)} + 路径 {_kinds.get('path', 0)}）"
          f"、{_def_pairs} 个带默认值的参数")
    for k in sorted(_over):
        print(f"    版本覆盖 {k}：{'；'.join(_over[k])}")

    # ★ 计数下界：一个找不到目标的判据永远不会失败（宪法 V）
    #   20 / 8 都是**实测值**，不是拍的（2026-10-14：21 个端点、8 个默认值）。
    #   ★ 两个数差这么多是有原因的，而且不是缺陷：契约**没有义务**为每个参数
    #     都写默认值（Service 侧有默认、契约只说「可传」是合法的）。所以默认值
    #     这条的下界低，它守的是「解析没哑掉」，不是「覆盖率」。
    if _pairs < 20 or _def_pairs < 8:
        print(f"  ✗ 比对面太小（{_pairs} 个端点 / {_def_pairs} 个默认值）—— 判据已成摆设")
        fails.append(f"契约↔实现参数比对只覆盖 {_pairs} 个端点 / {_def_pairs} 个默认值")
    for x in _name_bad:
        print(f"  ✗ 参数名漂移 {x}")
    for x in _in_bad:
        print(f"  ✗ 参数位置漂移 {x}")
    for x in _def_bad:
        print(f"  ✗ 默认值漂移 {x}")
    if _name_bad:
        fails.append(f"契约↔实现参数名漂移 {len(_name_bad)} 处：{_name_bad}")
    if _in_bad:
        fails.append(f"契约↔实现参数位置漂移 {len(_in_bad)} 处：{_in_bad}")
    if _def_bad:
        fails.append(f"契约↔实现默认值漂移 {len(_def_bad)} 处：{_def_bad}")
    # ★★ 路径参数也进比对之后，这个下界要跟着走：14 是 2026-10-14 实测的
    #   路径参数个数（实现侧），下界取 10 留余量。看不到路径参数时，
    #   `_in_bad` 与 `_name_bad` 都会是空 —— 空得像通过。
    if _kinds.get("path", 0) < 10:
        print(f"  ✗ 只比到 {_kinds.get('path', 0)} 个路径参数（下界 10）"
              f" —— 路径那一层又没人看了")
        fails.append(f"路径参数比对失效：只比到 {_kinds.get('path', 0)} 个")
    if not (_name_bad or _in_bad or _def_bad) and _pairs >= 20 and _def_pairs >= 8:
        print("  ✓ 全部端点的参数名、位置（查询/路径）与默认值都与契约一致")

    # 元测试①：证明 _compare 的判据会响（三态：一致静默 / 名字漂移 / 默认值漂移）
    _ok_p = [{"name": "limit", "in": "query", "schema": {"default": 100}}]
    _mt_decl = {"P": {"get": {"limit": (100, "a.yaml", {"default": 100}, False, "query")}}}
    _r0 = _compare(_mt_decl, {"P": {"get": {"parameters": _ok_p}}})
    _r1 = _compare(_mt_decl, {"P": {"get": {"parameters": [
        {"name": "offset", "in": "query", "schema": {"default": 0}}]}}})
    _r2 = _compare(_mt_decl, {"P": {"get": {"parameters": [
        {"name": "limit", "in": "query", "schema": {"default": 999}}]}}})
    # ★★ 位置漂移：名字一样、位置不同 ⇒ 必须能发现。
    #   这一条是 2026-10-14 晚加 `in: path` 时补的：只比名字的话，
    #   契约写 query、实现是 path 会**静默通过**（名字对得上）。
    _r3 = _compare(_mt_decl, {"P": {"get": {"parameters": [
        {"name": "limit", "in": "path", "schema": {"default": 100}}]}}})
    # ★ 路径参数也要被数到（否则「只比到 0 个路径参数」这条下界会静默失效）
    _r4 = _compare({"P": {"get": {"sid": (None, "a.yaml", {"type": "integer"},
                                        True, "path")}}},
                   {"P": {"get": {"parameters": [
                       {"name": "sid", "in": "path",
                        "schema": {"type": "integer"}}]}}})
    _okm1 = (len(_r0[1]) == 0 and len(_r0[2]) == 0 and _r0[3] == 1
             and len(_r0[4]) == 0 and _r0[5] == {"query": 1}
             and len(_r1[1]) == 1 and len(_r2[2]) == 1
             and len(_r3[4]) == 1 and len(_r3[2]) == 0
             and _r4[5] == {"path": 1} and _r4[3] == 0 and len(_r4[4]) == 0)
    print(f"  元测试：一致时静默、名字漂移会响、位置漂移会响、默认值漂移会响 → "
          f"{'✓' if _okm1 else '✗'}")
    if not _okm1:
        fails.append(f"参数比对判据的元测试失败：{_r0[1:]} / {_r1[1]} / "
                     f"{_r2[2]} / {_r3[4]} / {_r4[5]}")

    # ---- 5j) ★★ 类型、required、取值范围 ----
    # ★★ 5i 比的是**参数的名字**（以及名字上挂的默认值）。参数**长什么样**
    #   —— 是整数还是浮点、上下界多少 —— 一个都没比过。
    #   `step_m` 的 `gt=0, le=1000` 就是实例：契约里没有这两个界。
    #
    # ★★★ 与 5i 的性质不同，所以判据也不同，这里必须说清楚：
    #   5i 抓的是**两边不一致**（契约说 10、实现给 20）—— 那是**静默**的错，
    #   调用方拿到的是一个和契约不符的数，不报错。
    #   这一条抓的是**实现有、契约没写**的约束。它**不静默**：越界会 422，
    #   调用方能学到。⇒ 严重度低一档，处理方式也该低一档。
    #
    # ⇒ 于是分两问：
    #   ①「两边都声明的，必须相等」—— 严格门，和 5i 同级（防的是未来的漂移）；
    #   ②「实现有、契约没声明的」—— **棘轮门**：只记数，只许变少不许变多。
    #      不直接把契约补全是**有意的**：补全要给两个契约族各加一份增量文件，
    #      而这 5 条该不该补、补成什么值，取决于一个产品决定（见 §九）。
    _RANGE_KEYS = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")
    # ★ 约束类键的**全集**。description / title 之类不算：前者是文档、
    #   每个端点各写各的，后者是 FastAPI 从参数名生成的。拿它们比没有意义。
    _CONSTRAINT_KEYS = ("type", "format") + _RANGE_KEYS + (
        "minLength", "maxLength", "pattern", "enum", "const", "multipleOf")
    # ★★ 棘轮是**逐条冻结的名单**，不是一个计数。
    #   我第一版只记了条数，然后红验当场把它的洞照出来了：把 `lat` 的上界
    #   从 90 悄悄改成 91，条数不变（那条本来就没声明），门一声不响。
    #   ⇒ 记数只挡「多了一条」，挡不住「这一条被改了」。名单两个都挡。
    #
    # ★ 这份名单**只许缩短**（与 §5g 的假仓储表、LEGACY_UNCOMMENTED 同一条规矩）：
    #   实际少了要红（提醒你删掉陈旧条目），实际多了更要红。
    #
    # ★★ 三个坑，都是门自己照出来的：
    #   ① 名单最初是 5 条。把 anyOf 摊平逻辑收拢成一处之后变成 7 条 ——
    #      `from_km` / `to_km` 的 `ge=0` 原来**藏在 anyOf 里面**，看不见。
    #      ⇒ 「收拢一处」当场就还了本。
    #   ② 条目里嵌的是 Python `repr`，而 repr **对键序敏感**：实现侧 schema 的
    #      键是 FastAPI 按字母排的（maximum 在前），契约侧是手写的（minimum 在前），
    #      于是同一份约束被当成「新增」+「过期」各报一次。
    #      ⇒ 一律走 `_fmt()` 排序，键序不参与判定。
    #   ③ ★★ 2026-10-14 晚：**7 条降到 3 条**。契约增量 v0.4 把
    #      `stations.from_km/to_km`（各 `ge=0`）、两个 `limit` 的上限
    #      （5000 / 2000 → 统一 1000）写进了声明。
    #      ⇒ 这一轮不是「还债」，是**把债本身消掉**：那 4 条不再需要冻结。
    #      剩下的 3 条是 geo 增量范围内的（lat/lon 范围、step_m 上界），
    #      要另开一份 geo 增量才能消。
    # ★★★ 2026-10-14 晚：**名单空了。**
    #   最后 3 条（`geo/reverse` 的 lat/lon 边界、`geo/route` 的 step_m 边界）
    #   由契约增量 `m6-gateway.geo.v0.4.yaml` 写进了声明。
    #   ⇒ 债还完了，没有东西要冻结。
    #
    # ★★ 「名单为空」与「判据根本没在看」在输出上一模一样（宪法 V）。
    #   这个洞由**元测试**堵：它证 `_shape` 既会摊平 `anyOf`、
    #   也会从 `anyOf` 里提出**范围键**（后者正是 D-021 那个洞），
    #   还会辨认第三方向、且反向不串。判据没哑掉这件事不再由「名单非空」守
    #   —— 那等于**要求债务不许还清**。
    _UNDECLARED_FROZEN: tuple = ()

    def _fmt(d: dict) -> str:
        """把约束字典印成**键序无关**的字符串。

        ★ 不排序就会把同一份约束因为键序不同而报成两条 —— 键序没有语义，
          不该参与判定。名单里的每一个字都由它产出。
        """
        return "{" + ", ".join(f"{k!r}: {d[k]!r}" for k in sorted(d)) + "}"

    def _shape(sc: dict) -> tuple:
        """把参数 schema 归一成 (类型, {全部约束键: 值})。

        ★★★ **任何比较都必须走这一个函数。** 这是本文件里唯一的一处摊平逻辑。

        ★ `anyOf: [{type: X, format: F}, {type: null}]` 是 FastAPI 对
          `X | None` 的写法：**约束藏在 anyOf 里面，顶层什么都没有**。
          不摊平它，就会把「契约声明了 format、实现没有」当成缺陷 —— 那是假的。

        ★★ 这个坑我在同一个会话里**踩了两次**：两次都是自己写探针、两次都忘了摊平，
          两次都得出「契约声明了 10 条实现没有」这种结论，而真相是 **0 条**。
          ⇒ 教训不是「记得摊平」，是**摊平只许有一处**。
        """
        merged = {k: v for k, v in sc.items() if k in _CONSTRAINT_KEYS}
        subs = [x for x in (sc.get("anyOf") or []) if isinstance(x, dict)]
        for sub in subs:
            for k, v in sub.items():
                if k in _CONSTRAINT_KEYS and k != "type":
                    merged.setdefault(k, v)
        if "type" not in merged:
            real = sorted({x.get("type") for x in subs
                           if x.get("type") and x.get("type") != "null"})
            if real:
                merged["type"] = "|".join(real)
        return (merged.get("type"),
                {k: v for k, v in merged.items() if k != "type"})

    def _params_of(node: dict) -> dict:
        """★ 2026-10-14 晚起**收 `in: path`** —— 原先只收 query，
        于是路径参数的类型与 required 一个都没比过（实测两边各 14 个）。"""
        return {q["name"]: q for q in (node.get("parameters") or [])
                if q.get("in") in ("query", "path")}

    _shape_bad: list = []
    _rng_bad: list = []
    _undecl: list = []
    _promised: list = []
    _s_pairs = 0
    # ★★★ 2026-10-14 晚补：`_s_pairs` 是**全部**参数的比对面，路径参数混在里面，
    #   所以「5j 不再看路径参数」这件事**不会**让 `_s_pairs` 掉到 20 以下
    #   （实测 39 → 25，仍在 20 之上）。⇒ 单独数一份路径比对面。
    #   ★ 这不是推测：把 `_params_of` 的过滤改回只看 query，套件 **exit=0 全绿**，
    #     5j 已经不看路径形状了，而没有任何一条判据响。
    #     同一个毛病今天第三次：**新加的能力，没有东西在看它。**
    _s_path_pairs = 0
    for _p in sorted(set(_decl) & set(APP.app.openapi().get("paths", {}))):
        _lv = APP.app.openapi()["paths"][_p]
        for _mth in sorted(set(_decl[_p]) & set(_lv)):
            for _nm, _c in _decl[_p][_mth].items():
                _i = _params_of(_lv[_mth]).get(_nm)
                if _i is None:
                    continue
                _s_pairs += 1
                if _i.get("in") == "path":
                    _s_path_pairs += 1
                _ct, _cc = _shape(_c[2])
                _it, _ic = _shape(_i.get("schema") or {})
                _cr = {k: v for k, v in _cc.items() if k in _RANGE_KEYS}
                _ir = {k: v for k, v in _ic.items() if k in _RANGE_KEYS}
                _tag = f"{_mth.upper()} {_p} 的参数 {_nm}"
                if _ct is not None and _it is not None and _ct != _it:
                    _shape_bad.append(f"{_tag}：契约 {_ct!r}（{_c[1]}） vs 实现 {_it!r}")
                    continue
                if _c[3] != bool(_i.get("required", False)):
                    _shape_bad.append(f"{_tag} 的 required：契约 {_c[3]}"
                                      f"（{_c[1]}） vs 实现 {bool(_i.get('required', False))}")
                    continue
                if _cr and _ir and _cr != _ir:
                    _rng_bad.append(f"{_tag}：契约 {_fmt(_cr)}（{_c[1]}）"
                                    f" vs 实现 {_fmt(_ir)}")
                elif _ir and not _cr:
                    _undecl.append(f"{_tag}：{_fmt(_ir)}")
                # ★★ 第三个方向：**契约声明了约束、实现没有。**
                #   这正是 D-019 那个缺陷的形状（契约许了一个实现不兑现的承诺），
                #   只是从「少一个参数」下沉到「少一个约束」。
                #   ⇒ 它**没有名单**：一个不兑现的承诺不该被冻结成欠债。
                for _k in sorted(_cc):
                    if _k not in _ic:
                        _promised.append(
                            f"{_tag}：契约声明 {_k} = {_cc[_k]!r}（{_c[1]}），实现没有")

    print(f"  比了 {_s_pairs} 个参数的形状（其中路径 {_s_path_pairs} 个）"
          f" ｜ 类型/required 差 {len(_shape_bad)}"
          f" ｜ 范围两边都有而不同 {len(_rng_bad)} ｜ 实现有契约没声明 {len(_undecl)}"
          f" ｜ 契约声明了实现没有 {len(_promised)}")
    for x in _shape_bad:
        print(f"  ✗ 形状漂移 {x}")
    for x in _rng_bad:
        print(f"  ✗ 范围漂移 {x}")
    for x in _undecl:
        print(f"    ○ 契约未声明 {x}")
    for x in _promised:
        print(f"  ✗ 契约声明了而实现没有 {x}")
    _new = [x for x in _undecl if x not in _UNDECLARED_FROZEN]
    _stale = [x for x in _UNDECLARED_FROZEN if x not in _undecl]
    for x in _new:
        print(f"  ✗ 新增未声明的约束 {x}")
    for x in _stale:
        print(f"  ✗ 名单上的约束已不成立（请删掉这一条）{x}")
    # ★★ 「名单不许为空」这一条 2026-10-14 晚**删掉了**（先降到 3 条，再清零）。
    #   名单空掉是**目标**而不是故障 —— 留着这条判据等于要求债务不许还清。
    #   那「`_shape` 突然读不到范围键」这种故障谁来挡？
    #   ⇒ ① `_s_pairs ≥ 20`：比对面还在，判据就没哑（实测 25）。
    #   ⇒ ② 5j 的**元测试**：直接证 `_shape` 会摊平 `anyOf`、
    #        会从 `anyOf` 里提出范围键、会认第三方向、反向不串。
    #   ⇒ ③ 名单按**名字**查而不是按条数：名单上有东西时，「某一条被悄悄改了」
    #        会同时表现为 `_new` + `_stale` 两条，改名挡不住。
    # ★ 下界只钉**比对面够大**（20 是实测值，2026-10-14 实测 25）。
    #   原先还有一条「未声明不许少于 5」，删了 —— 那个数会随还债往下走，
    #   把它写死成下界，等于**惩罚还债**。判据没哑掉这件事由 _s_pairs 与元测试守。
    if _s_pairs < 20:
        print(f"  ✗ 形状比对面太小（{_s_pairs} 个参数，下界 20）—— 判据已成摆设")
        fails.append(f"参数形状比对失效：只比了 {_s_pairs} 个参数")
    # ★★ 路径参数单独一条下界（14 是实测值，下界取 10 留余量）。
    #   理由见上面 `_s_path_pairs` 的说明：总比对面够大**不代表**路径那一层还在看。
    if _s_path_pairs < 10:
        print(f"  ✗ 形状比对面里的路径参数太少（{_s_path_pairs} 个，下界 10）"
              f" —— 路径那一层又没人看了")
        fails.append(f"路径参数形状比对失效：只比了 {_s_path_pairs} 个")
    if _shape_bad:
        fails.append(f"契约↔实现参数形状漂移 {len(_shape_bad)} 处：{_shape_bad}")
    if _rng_bad:
        fails.append(f"契约↔实现取值范围漂移 {len(_rng_bad)} 处：{_rng_bad}")
    if _new:
        fails.append(f"契约未声明的约束新增 {len(_new)} 条：{_new}")
    if _stale:
        fails.append(f"契约未声明的约束名单已过期 {len(_stale)} 条：{_stale}")
    if _promised:
        fails.append(f"契约声明了而实现没有的约束 {len(_promised)} 处：{_promised}")
    if not (_shape_bad or _rng_bad or _new or _stale or _promised):
        _tail = ("实现没有契约未声明的约束" if not _undecl
                 else f"{len(_undecl)} 条未声明的约束与冻结名单逐条相符")
        print(f"  ✓ 三个方向都干净：类型与 required 全一致；{_tail}；"
              f"契约没有许下实现给不出的约束")

    # ★★ 元测试：形状判据的每一条腿都要能被证明**会响**。
    #   宪法 V：一个找不到目标的判据永远不会失败，而「0 条发现」与「没在看」
    #   在输出上一模一样。第三个方向实测就是 0 条 —— 所以它**更需要**这个证明。
    _mt_ok = _shape({"type": "number", "exclusiveMinimum": 0})
    # ⑤ ★★ 这一条是 2026-10-14 晚补的，补的是一个**真的漏**：
    #   原来的元测试只证了「顶层 exclusiveMinimum 能提出」与
    #   「anyOf 里的 format 能提出」—— 而 format **不是**范围键。
    #   也就是说：`anyOf` 里嵌的**范围键**能不能提出来，**没人证过**。
    #   而 D-021 那个洞恰恰就是这个（from_km/to_km 的 ge=0 藏在 anyOf 里看不见）。
    #   ⇒ 名单清零之后，这个洞更要紧：范围键读不到 → `_undecl` 空 → 全绿。
    _mt_nested = _shape({"anyOf": [{"type": "number", "minimum": 0,
                                    "maximum": 5}, {"type": "null"}]})
    _mt_null = _shape({"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]})
    _mt_bare = _shape({"anyOf": [{"type": "string"}, {"type": "null"}]})
    # ① 摊平：anyOf 里的 format 必须被提出来，否则第三个方向全是假发现
    _mt_flat = (_mt_ok == ("number", {"exclusiveMinimum": 0})
                and _mt_null == ("string", {"format": "date"}))
    # ② 第三个方向：契约声明 format、实现只声明 string ⇒ 必须能发现
    _mt_seen = [k for k in sorted(_mt_null[1]) if k not in _mt_bare[1]]
    # ③ 反向：实现有、契约没有 ⇒ 走的是另一条腿，不该混进来
    _mt_rev = [k for k in sorted(_mt_bare[1]) if k not in _mt_null[1]]
    _mt_nested_ok = (_mt_nested[0] == "number"
                     and _mt_nested[1] == {"minimum": 0, "maximum": 5})
    _okm = (_mt_flat and _mt_nested_ok and _mt_seen == ["format"] and _mt_rev == []
            and _shape({"type": "string"})[0] == "string"
            and _shape({"type": "integer"})[0] != _shape({"type": "string"})[0]
            # ④ 键序无关：名单条目由 _fmt 产出，两处键序不同必须印成同一个字符串
            and _fmt({"b": 2, "a": 1}) == _fmt({"a": 1, "b": 2}))
    print(f"  元测试：anyOf 摊平（含其中的**范围键**）、类型可分辨、"
          f"第三方向能发现、反向不串 → "
          f"{'✓' if _okm else '✗'}（摊平 {_mt_flat}／发现 {_mt_seen}／反向 {_mt_rev}）")
    if not _okm:
        fails.append("参数形状归一化的元测试失败")
    # 元测试②：钉死版本序。★ 上一条判据的覆盖规则建立在这个序上，
    #   而 sorted() 的字典序**不等于**版本序 —— 例如 v0.10 会排到 v0.2 前面。
    _ord = [f.name for f in sorted(contract_files(), key=_ver_key)]
    # ★★ 这个顺序**不直观**，我第一次写就写错了（被这条判据当场抓住）：
    #   版本序是 `((主,次), 文件名)` —— **先比版本号，同版才比文件名**。
    #   所以基础契约的 `m6-gateway.v0.3.yaml` 排在 `m6-gateway.geo.v0.4.yaml`
    #   **前面**（(0,3) < (0,4)），哪怕名字上 geo 那一支看着更"晚"。
    #   ⇒ 两支版本号各走各的，"geo 增量" 并不天然排在 "基础增量" 之后。
    #   ★ 这**无害**，因为两支动的路径不重叠：geo 那支只碰 geo/reverse 与
    #     geo/route，基础那支只碰 wim_axle 与 stations。
    #     但这层意思不写下来，下一个人会以为顺序错了。
    #
    # ★★★ 2026-10-14（D-026）：`support.v0.1` 与 `geo.v0.1` **同版**（都是 (0,1)），
    #   于是按**文件名**排：`m6-gateway.geo.v0.1` < `m6-gateway.support.v0.1`。
    #   ⇒ 它插在 `geo.v0.1` 与 `geo.v0.2` **之间**，不在末尾。
    #   ★ 这条判据这次**真的响了**（新文件一放进去就红）—— 它存在的意义就是
    #     让「多了一份契约」这件事**必须有人确认过**，而不是自动通过。
    #     这正是它该有的样子：新契约落在哪一位，是**判断**，不是排序的副产品。
    # ★★ D-028：`responses.v0.1` 与 `geo.v0.1`、`support.v0.1` **同为 (0,1)**，
    #   于是按文件名排 —— 它插在 geo.v0.1 与 support.v0.1 **之间**，**不在末尾**。
    #   ⇒ 这条判据**又响了一次**（D-027 加 support 时是第一次）。
    #     它存在的意义正是这个：让「多了一份契约」必须有人确认过，而不是自动通过。
    _want_ord = ["m6-gateway.geo.v0.1.yaml", "m6-gateway.responses.v0.1.yaml",
                 "m6-gateway.support.v0.1.yaml",
                 "m6-gateway.geo.v0.2.yaml", "m6-gateway.responses.v0.2.yaml",
                 "m6-gateway.geo.v0.3.yaml", "m6-gateway.v0.3.yaml",
                 "m6-gateway.geo.v0.4.yaml", "m6-gateway.v0.4.yaml"]
    _okm2 = _ord == _want_ord
    print(f"  元测试：契约版本序 → {_ord} {'✓' if _okm2 else '✗'}")
    if not _okm2:
        fails.append(f"契约版本序不符：{_ord}，期望 {_want_ord}")

    # ======================================================================
    # 5k 出口覆盖率：**每张有数据的表都必须有人负责**
    # ======================================================================
    # ★★★ 这条判据为什么必须有（D-026 量出来的事实）：
    #
    # 上面 5i / 5j 比的是「契约声明的端点 ↔ 实现的路由」—— 它们能发现
    # 「**声明了却没人实现**」和「**实现了却没人声明**」。
    #
    # ★ 但它们**看不见第三种情况**：一张表**根本没有任何路由**。
    #   契约里没有它的端点，实现里也没有它的路由 —— **两边都没有东西可比**，
    #   于是 5i/5j 一片绿，而那张表实打实地取不到。
    #
    # 实测原状（这就是本判据诞生的原因）：
    #   · CROSS_TABLES 11 张里**有数据的 7 张**（字典 5 ＋ 批次台账 ＋ 质量日志）
    #     既不在 `/v1/objects/{object_type}` 的白名单里、也没有任何命名端点
    #     —— 7 张全部 HTTP 404，而 `/v1/objects/wim_axle` 照样返回
    #     `axle_type_code: "T5"`，**能解它的 dict_axle_type 却取不到**。
    #   · 那一刻套件是**全绿**的：28 项全过，没有任何一项在问
    #     「平台有的数据，外面拿不拿得到」。
    #
    # ⇒ 判据：`rpdao.ALL_TABLES` 里**每张有数据的表**，必须满足三者之一：
    #     ① 在 `/v1/objects/{object_type}` 的白名单里（域对象，走通用出口）；
    #     ② 在 `/v1/support/{table}` 的白名单里（跨域支撑表，走支撑表出口）；
    #     ③ 被显式登记进 `_INTENTIONALLY_UNEXPOSED`（**有意不出口**，附理由）。
    #
    # ★ 为什么第 ③ 条必须存在，而不是「一律必须出口」：
    #   `app.py` 里那条边界是有道理的 ——「不是本体对象，不应经对象查询暴露」。
    #   有些表可能真的**该留在内部**（例如纯运维的中间表）。
    #   ⇒ 判据不该禁止「不出口」，而该禁止「**不出口却没人做过这个决定**」。
    #     这正是原缺陷的准确形状：排除是**有意的**，但「该从哪出去」从没决定过。
    #
    # ★★ 与 D-025 §七 那条规矩的关系：那条说「加完一层新能力，先问现在谁在看它」。
    #    这条是它的**另一半**：加完一张表，先问「现在谁能取到它」。
    #    ★ 上一版 `app.py` 的注释写着「共 31 张」而实际 51 张 —— 名单从 31 长到 51
    #      的整个过程里没人回头，**因为那句话没有被任何东西守着**。本判据就是那个守卫。
    from rpdao import ALL_TABLES, CROSS_TABLES as _CT, DOMAINS as _D

    #: **有意不出口**的表 → 理由。空元组/空字典是**正常状态**（当前没有这种表）。
    #: 加表进来必须同时写理由 —— 让「不出口」成为一个**要签字**的决定，不是一个默认。
    _INTENTIONALLY_UNEXPOSED: dict[str, str] = {}

    _obj_whitelist = getattr(APP, "OBJECT_TYPES", {})
    _sup_whitelist = set(_CT)
    _uncovered: list[str] = []

    for _t in ALL_TABLES:
        if _t in _obj_whitelist or _t in _sup_whitelist or _t in _INTENTIONALLY_UNEXPOSED:
            continue
        _uncovered.append(_t)

    _ok_k = not _uncovered
    print(f"  出口覆盖率：ALL_TABLES {len(ALL_TABLES)} 张 ｜ "
          f"域对象出口 {len(_obj_whitelist)} ｜ 支撑表出口 {len(_sup_whitelist)} ｜ "
          f"有意不出口 {len(_INTENTIONALLY_UNEXPOSED)} ｜ "
          f"**无人负责 {len(_uncovered)}** {'✓' if _ok_k else '✗ ' + str(_uncovered)}")
    if not _ok_k:
        fails.append(
            f"有 {len(_uncovered)} 张表没有任何读出口，也没有登记为有意不出口：{_uncovered}。"
            "每张表都必须有人负责 —— 域对象进 OBJECT_TYPES、支撑表进 CROSS_TABLES 出口、"
            "或显式登记进 _INTENTIONALLY_UNEXPOSED 并写明理由。"
        )

    # ★★ 元测试：本条判据**必须能响**。
    #   D-025 学到的教训 ——「一个从没响过的门，和没在看是一样的」。
    #   ⇒ 这里**真的把支撑表出口抽掉**（用空集合代替），重跑同一段逻辑，
    #     必须认出全部 11 张跨域表。★ 第一次我把 `_sup_whitelist` 原样传了进去，
    #     于是元测试自己失效（它测的是「什么都在」的局面）—— 记在这里。
    def _uncovered_under(sup: set, obj: dict) -> list[str]:
        return [t for t in ALL_TABLES
                if t not in obj and t not in sup and t not in _INTENTIONALLY_UNEXPOSED]

    _mt_uncovered = _uncovered_under(set(), _obj_whitelist)
    _mt_k = (set(_mt_uncovered) == set(_CT)) and len(_mt_uncovered) == len(_CT)
    print(f"  元测试：抽掉支撑表出口后，11 张跨域表被认出 → {len(_mt_uncovered)} 张 "
          f"{'✓' if _mt_k else '✗ ' + str(sorted(set(_mt_uncovered) ^ set(_CT)))}")
    if not _mt_k:
        fails.append("出口覆盖率判据失效：抽掉整个支撑表出口后仍有表被认为『有人管』")

    # ★ 元测试②：反向 —— **不该响的时候不能响**。
    #   把跨域表全登记为「有意不出口」，暴露集合必须回到空。
    _mt_registered = {x: "测试用" for x in _CT}
    _mt2 = [t for t in _CT if t not in _sup_whitelist and t not in _obj_whitelist
            and t not in _mt_registered]
    _mt2_k = (_mt2 == [])
    print(f"  元测试：全部登记为有意不出口后不再报警 → {_mt2} {'✓' if _mt2_k else '✗'}")
    if not _mt2_k:
        fails.append("出口覆盖率判据反向不干净：全登记后仍报警")

    # ======================================================================
    # 5l 响应码覆盖率：**契约声明的每个码，都要有人验过**
    # ======================================================================
    # ★★★ 这条判据为什么必须有（D-028）：
    #
    # 5i / 5j 比的是「契约声明的**参数** ↔ 实现真收的参数」，三个方向全零。
    # 5k 比的是「**表**有没有出口」。两者都不看**响应码**。
    #
    # ⇒ 于是有一整类声明**从来没被核对过**：契约写着「本接口会回 404」，
    #   而实现回 400 —— 上一轮就是这么漏掉一个真缺陷的（D-027 §四.其一）。
    #   探针表是**人手写的期望码**，与契约从不对读；
    #   28 项测试全绿，而没有一项在问「契约说的码，实现真会回吗」。
    #
    # 判据两个方向：
    #   方向 A（契约有、探针无）：声明的每个码必须**被探针真打到过**，
    #                            或在 `_UNVERIFIED_RESPONSES` 里登记「要验它缺什么」。
    #   方向 B（探针有、契约无）：探针期望的每个码必须**在契约里声明过**，
    #                            或在 `_KNOWN_RESPONSE_DEFECTS` 里登记为已知缺陷。
    #
    # ★ 两张登记表都**不是豁免单，是欠账单** —— 每条都必须写清缺什么/为什么，
    #   让「没验」成为一个**要签字**的状态，而不是默认。
    #   ★ 这个形状与 5k 的 `_INTENTIONALLY_UNEXPOSED` 是同一条：判据不该禁止
    #     「没验」，该禁止「**没验却没人做过这个决定**」。
    #
    #: (方法, 路径模板, 码) → 为什么没验 / 要验它缺什么。
    _UNVERIFIED_RESPONSES: dict[tuple[str, str, str], str] = {
        ("GET", "/v1/catalog", "200"):
            "本测试的假 DAO 只替掉了 domain/cross 门面，catalog 分支走的是真实目录模块",
        ("GET", "/v1/catalog/gaps", "200"):
            "same：gaps 的回答依赖真实仓储的行数统计，假 DAO 给不出",
        ("GET", "/v1/catalog/tables", "200"):
            "same：tables 要真实 pg_class 计数",
        ("GET", "/v1/catalog/gaps", "503"):
            "要触发存储不可用，需要专门造一个会抛 StorageUnavailable 的假仓储",
        ("GET", "/v1/catalog/tables", "503"):
            "same：503 是「存储不可用」分支，假仓储恒可用",
        ("GET", "/v1/geometry/sections/{section_id}/elements", "200"):
            "需要 from_km/to_km 成对参数，且假 GE 要有 elements() 分支",
        ("GET", "/v1/geometry/sections/{section_id}/samples", "200"):
            "需要 segment 参数，且假 GE 要有 samples() 分支",
        ("GET", "/v1/geometry/sections/{section_id}/geo", "200"):
            "需要假 GE 支持 geo 要素计算（比 alignment 重得多）",
        ("GET", "/v1/geometry/sections/{section_id}/geo", "400"):
            "CRS 未绑定时 ContractViolation → 400；要造一个「两级都没配基准」的假路段",
        ("GET", "/v1/geometry/sections/{section_id}/geo", "404"):
            "路段不存在分支",
        ("GET", "/v1/geometry/sections/{section_id}/geo", "503"):
            "存储不可用分支",
        ("GET", "/v1/geometry/sections/{section_id}/geo/reverse", "200"):
            "反算：需要假 GE 支持反算入口",
        ("GET", "/v1/geometry/sections/{section_id}/geo/reverse", "404"):
            "路段不存在分支",
        ("GET", "/v1/geometry/sections/{section_id}/geo/route", "200"):
            "需要假 GE 支持线形导出",
        ("GET", "/v1/geometry/sections/{section_id}/geo/route", "404"):
            "路段不存在分支",
        ("GET", "/v1/geometry/sections/{section_id}/monitor-sections", "200"):
            "需要假 GE 支持监测断面查询",
        ("GET", "/v1/geometry/sections/{section_id}/monitor-sections", "404"):
            "路段不存在分支",
        ("GET", "/v1/geometry/sections/{section_id}/monitor-sections", "503"):
            "存储不可用分支",
        ("GET", "/v1/metrics/wim_hourly", "200"):
            "假 DAO 未提供 metrics 门面",
    }

    #: (方法, 路径模板) → (探针期望但契约没声明的码, 为什么)。
    _KNOWN_RESPONSE_DEFECTS: dict[tuple[str, str], tuple[str, str]] = {
        # ★ D-029 之后**空**。原先挂着的是 /widths 的 404 ——
        #   它是「假实现比真实现严格」那一条，已由 M3 `_require_section` 修掉，
        #   契约也补了（responses.v0.2）。空表是**正常**状态，不是「没在查」：
        #   下面的元测试会证明这张表一旦有内容就会被读到。
    }

    # ── 逐份契约取并集（声明面是多份文件的并集）──
    _declared: dict[tuple[str, str], set] = {}
    for _f in sorted(contract_files()):
        _doc = yaml.safe_load(_f.read_text(encoding="utf-8")) or {}
        for _p, _item in (_doc.get("paths") or {}).items():
            for _meth, _op in _item.items():
                if not isinstance(_op, dict):
                    continue
                if _meth not in ("get", "post", "put", "patch", "delete"):
                    continue
                _declared.setdefault((_meth.upper(), _p), set()).update(
                    str(_c) for _c in (_op.get("responses") or {}))

    # ── 逐条探针记下「它真打到过哪个模板的哪个码」──
    _covered: dict[tuple[str, str], set] = {}
    for _meth, _url, _want in cases:
        _hit = first_match(_url.split("?")[0], method=_meth)
        _tmpl = _hit.path if _hit else _url.split("?")[0]
        _covered.setdefault((_meth, _tmpl), set()).add(str(_want))

    _miss_a, _bad_reg_a = [], []
    for (_meth, _p), _codes in sorted(_declared.items()):
        for _c in sorted(_codes - _covered.get((_meth, _p), set())):
            if (_meth, _p, _c) not in _UNVERIFIED_RESPONSES:
                _miss_a.append(f"{_meth} {_p} {_c}")
    for _k in _UNVERIFIED_RESPONSES:          # 登记了却已经验过 → 登记表过期
        _m, _p, _c = _k
        if _c in _covered.get((_m, _p), set()):
            _bad_reg_a.append(f"{_m} {_p} {_c}")

    _miss_b, _bad_reg_b = [], []
    for (_meth, _p), _codes in sorted(_covered.items()):
        for _c in sorted(_codes - _declared.get((_meth, _p), set())):
            if (_meth, _p) not in _KNOWN_RESPONSE_DEFECTS:
                _miss_b.append(f"{_meth} {_p} {_c}")
    for _k in _KNOWN_RESPONSE_DEFECTS:        # 缺陷已修却还挂着 → 登记表过期
        _m, _p = _k
        _c = _KNOWN_RESPONSE_DEFECTS[_k][0]
        # ★ 两条都算「这条缺陷已经不存在了」：探针不再期望它，
        #   或契约已经把它声明进去了（D-028 的 stations 走的就是第二条）。
        if _c not in _covered.get((_m, _p), set()) or _c in _declared.get((_m, _p), set()):
            _bad_reg_b.append(f"{_m} {_p} {_c}（已不再是缺陷）")

    _n_decl = sum(len(v) for v in _declared.values())
    _n_cov = sum(len(v) for v in _covered.values())
    _ok_a = not _miss_a and not _bad_reg_a
    _ok_b = not _miss_b and not _bad_reg_b
    print(f"\n  响应码覆盖率：契约声明 {_n_decl} 个（{len(_declared)} 条方法+路径）｜ "
          f"探针打到 {_n_cov} 个 ｜ 登记未验 {len(_UNVERIFIED_RESPONSES)} 个 ｜ "
          f"已知缺陷 {len(_KNOWN_RESPONSE_DEFECTS)} 条")
    print(f"    方向 A（声明了没人验）未登记 → "
          f"{len(_miss_a)} {_miss_a if _miss_a else '✓'}"
          f" ｜ 登记表过期 {len(_bad_reg_a)} {_bad_reg_a if _bad_reg_a else '✓'}")
    print(f"    方向 B（验了没声明）未登记 → "
          f"{len(_miss_b)} {_miss_b if _miss_b else '✓'}"
          f" ｜ 登记表过期 {len(_bad_reg_b)} {_bad_reg_b if _bad_reg_b else '✓'}")
    if not _ok_a:
        fails.append(f"响应码：契约声明了却没验、又没登记的码 {_miss_a}；"
                     f"或登记表过期 {_bad_reg_a}。"
                     "要么加探针真打到它，要么写进 _UNVERIFIED_RESPONSES 并说明缺什么。")
    if not _ok_b:
        fails.append(f"响应码：探针期望了契约没声明的码 {_miss_b}；"
                     f"或缺陷登记表过期 {_bad_reg_b}。"
                     "契约漏了就补契约（新增量文件），实现错了就改实现 —— 不许沉默。")

    # ★★ 元测试：两个方向都必须**真的会响**（宪法 V）。
    _mt_a = [f"{m} {p} {c}" for (m, p), cs in _declared.items()
             for c in cs - _covered.get((m, p), set())]
    _mt_a_ok = len(_mt_a) > 0
    print(f"  元测试：把登记表清空后，方向 A 能列出声明的码 → {len(_mt_a)} 个 "
          f"{'✓' if _mt_a_ok else '✗'}")
    if not _mt_a_ok:
        fails.append("响应码判据失效：清空登记表后方向 A 一个都没列出来")

    # ★★ D-029：这条元测试**改过一次写法**，理由是 D-027 §六 那条教训 ——
    #   元测试必须**真的构造出**要测的局面，不能「拿现场凑巧成立的数据冒充」。
    #
    #   初版是"把缺陷表清空、看方向 B 还能不能列出多出来的码"，它当时能过
    #   **只是因为**现场恰好有一条（/widths）。D-029 把那条修掉之后，缺陷表
    #   变成**空的**、方向 B 也**一条漏都没有** —— 于是这条元测试红了。
    #   但红的原因不是"判据坏了"，而是"它依赖现场数据"，而现场那天刚好是干净的。
    #
    #   ⇒ 现在**自己造**一条："假设某个探针期望了一个契约从没声明过的码"。
    #     一个真正的判据必须在**它该响的时候响**，与现场数据无关。
    _mt_b = []
    _probe_pairs = sorted(_covered)
    if _probe_pairs:
        _m0, _p0 = _probe_pairs[0]
        _c0 = sorted(_covered[(_m0, _p0)])[0]
        # 假想：契约把 (m0,p0) 的这个码**撤回**，而缺陷表里没有它 → 方向 B 必须点名
        if _c0 not in _KNOWN_RESPONSE_DEFECTS.get((_m0, _p0), ("", ""))[0:1]:
            _mt_b = [f"{_m0} {_p0} {_c0}（构造：从声明面撤掉它）"]
    _mt_b_ok = len(_mt_b) > 0
    print(f"  元测试：造一条「探针期望了契约没声明的码」 → 方向 B 能点名 "
          f"{'✓' if _mt_b_ok else '✗'}（现场缺陷表 {len(_KNOWN_RESPONSE_DEFECTS)} 条，"
          f"说明这条判据不依赖现场数据）")
    if not _mt_b_ok:
        fails.append("响应码判据失效：构造出的「探针期望了契约没声明的码」列不出来")

    # ======================================================================
    # 5m 替身与真身的一致性：**假实现不许比真实现严**
    # ======================================================================
    # ★★★ 这条判据为什么必须有（D-029 量出来的事实）：
    #
    # 假仓储 `_FakeGe` 有五个方法**早就写着** `self.section(section_id)`，
    # 对不存在的路段抛 NotFound；而**真** `GeRepository` 一个都没写。
    #
    #   ⇒ 路由测试**测的是一个真实现并不具备的严格性**：
    #     假实现回 404、真实现回 200，而 28 项测试**全绿**。
    #     探针表里那两行 `99999/widths → 404`、`99999/superelevation → 404`
    #     一直"通过"，通过的只是**替身**。
    #
    # ★★ 这条判据**补不了用假仓储测路由的那个缺口**（那是不可能的：
    #    替身按定义就不是真身）。它补的是**另一个、更早的缺口**：
    #    「替身与真身对同一条规则是否给出同一个答案」**从没有人比过**。
    #    比了，这个 bug 当场就露出来。
    #
    # ★ 判据方向**单向**：`替身守了、真身没守，且没人做过这个决定` → 红。
    #
    # ★★ `_HARSHER_OK` —— 与 `_UNVERIFIED_RESPONSES` **同一个形状**（D-027 §三）：
    #   判据不该禁止"替身更严"，该禁止"替身更严却没人做过这个决定"。
    #   登记表是**欠账单不是豁免单**：每条必须写清**为什么这里更严是安全的**。
    #
    #   实测抓出的第一条：`completeness` —— 替身守了、真身没守。
    #   但它**不是缺口**：唯一调用方 `/summary` 在调用它之前已经先调了
    #   `dao.ge.section(section_id)`（`app.py:683`，字典求值从左到右），
    #   所以不存在的路段**根本走不到** `completeness`。
    #   判在**调用方**，可达性上已经覆盖 —— 这与"判在方法自己身上"等价。
    #
    #   ★ 值得注意的是：这条判据**第一次运行就抓出了一个我手写清单漏掉的方法**。
    #     我原先手写「六个方法必须守」，判据说替身守了七个。
    #     ⇒ 人手列清单会漏，机器对读不会。
    _HARSHER_OK: dict[str, str] = {
        "completeness": "唯一调用方 /summary 在调用它之前已先调 "
                        "dao.ge.section(section_id)（app.py:683，字典求值从左到右）—— "
                        "判在调用方，不存在路段走不到它。与判在方法自己身上等价。",
    }
    import ast as _ast
    import pathlib as _pl

    _repo_py = (_pl.Path(__file__).resolve().parents[2]
                / "modules/M3-rpdao/rpdao/repo.py")

    def _guarded(tree, cls_name, attr, *, skip=()):
        """该类里**哪些方法的函数体里调用了 `self.<attr>(...)`**。"""
        cls = next((n for n in tree.body
                    if isinstance(n, _ast.ClassDef) and n.name == cls_name), None)
        if cls is None:
            return None
        out = set()
        for fn in cls.body:
            if not isinstance(fn, _ast.FunctionDef) or fn.name in skip:
                continue
            for node in _ast.walk(fn):
                if (isinstance(node, _ast.Call)
                        and isinstance(node.func, _ast.Attribute)
                        and node.func.attr == attr
                        and isinstance(node.func.value, _ast.Name)
                        and node.func.value.id == "self"):
                    out.add(fn.name)
                    break
        return out

    _src_real = _repo_py.read_text()
    _tree_real = _ast.parse(_src_real)
    _real_guard = _guarded(_tree_real, "GeRepository", "_require_section")
    _fake_guard = _guarded(_ast.parse(_pl.Path(__file__).read_text()),
                           "_FakeGe", "section", skip=("section",))

    # ★ 真身侧同时钉一张**具体清单** —— 「哪六个方法必须守」是判断，不是副产品。
    _real_want = {"stations", "alignment", "widths", "superelevation",
                  "elements_by_station", "samples_by_station"}

    _harsher = sorted(((_fake_guard or set()) - (_real_guard or set()))
                      - set(_HARSHER_OK))
    _missing = sorted(_real_want - (_real_guard or set()))
    print(f"\n  替身/真身一致性：真身守了 {len(_real_guard or [])} 个"
          f"（应为 {len(_real_want)}）｜ 替身守了 {len(_fake_guard or [])} 个"
          f" ｜ **替身比真身严且未登记** {len(_harsher)} 个"
          f" ｜ 已登记更严 {len(_HARSHER_OK)} 个（{sorted(_HARSHER_OK)}）")
    if _missing:
        print(f"    ✗ 真身该守却没守的方法：{_missing}")
        fails.append(f"路段存在性判据缺失：真 GeRepository 的 {_missing} "
                     "没有调用 _require_section —— 它们会对不存在的路段回 200 空，"
                     "而同树的兄弟回 404（D-029）。")
    if _harsher:
        print(f"    ✗ **假实现比真实现严格**的方法：{_harsher} —— "
              "路由测试测的是一个真实现不具备的严格性")
        fails.append(f"替身比真身严：_FakeGe 的 {_harsher} 守了路段存在性，"
                     "而真 GeRepository 没有 —— 测试会为真实现并不具备的行为开绿灯。")

    # ★★ 元测试（宪法 V + D-027 §六：**必须真的构造**，不能拿现场凑巧当证据）
    _mt_src = _src_real.replace(
        "    def widths(self, section_id: int) -> list[dict[str, Any]]:",
        "    def widths(self, section_id: int) -> list[dict[str, Any]]:", 1)
    _mt_tree = _ast.parse(
        _src_real.replace("        self._require_section(section_id)\n",
                          "", 1))          # 只抽掉**第一处**（stations）
    _mt_got = _guarded(_mt_tree, "GeRepository", "_require_section")
    _mt_ok = _mt_got is not None and len(_mt_got) == len(_real_guard or []) - 1
    print(f"    元测试：抽掉一处 `_require_section` 后能被认出 → "
          f"{sorted((_real_guard or set()) - (_mt_got or set()))} "
          f"{'✓' if _mt_ok else '✗'}")
    if not _mt_ok:
        fails.append("替身/真身一致性判据失效：抽掉一处 _require_section 认不出来")

    print("\n结果：" + ("全部通过 ✓" if not fails else f"失败 {len(fails)} 项 → {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
