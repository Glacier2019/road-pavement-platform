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
                 integer_only=False, limit=500):
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


class _FakeDao:
    """替换 M3 门面。真实 Dao 的接口面就是这些——这正是 M3 契约的可替代性证明。"""

    def __init__(self):
        self.lo = _FakeLo()
        self.ge = _FakeGe()

    def domain(self, code): return _FakeDomain(code)
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
        # 参数校验：非法筛选项应被 FastAPI 挡在门外（422），而不是进到 DAO
        ("GET",    "/v1/geometry/sections/6/stations?limit=0",      422),
        ("GET",    "/v1/geometry/sections/6/stations?from_km=-1",   422),
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
        """{路径: {方法: {参数名: (默认值, 文件名, schema, required)}}} + 覆盖记录。

        ★ 存四元组是因为后面有两条判据要用同一份解析结果：5i 只用前两项，
          5j 要用后两项。**一份解析、两处消费**，不各写一遍。
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
                        if prm.get("in") != "query":
                            continue
                        nm = prm["name"]
                        if nm in slot and slot[nm][1] != f.name:
                            over.setdefault(f"{method.upper()} {path}", []).append(
                                f"{nm}: {slot[nm][1]}={slot[nm][0]!r} → {f.name}")
                        slot[nm] = ((prm.get("schema") or {}).get("default"), f.name,
                                    prm.get("schema") or {},
                                    bool(prm.get("required", False)))
        return out, over

    def _compare(decl: dict, live: dict) -> tuple:  # noqa: D401
        """比「两边都有的端点」→ (比了几对, 名字不一致, 默认值不一致, 有默认值的对数)。

        ★ **只比两边都有的**：契约未收录的端点归第 2 组的路径门管，
          这里再报一次就是同一条性质有了两份定义。
        """
        pairs, def_pairs = 0, 0
        name_bad: list = []
        def_bad: list = []
        for path in sorted(set(live) & set(decl)):
            for method in sorted(set(live[path]) & set(decl[path])):
                impl = {q["name"]: (q.get("schema") or {}).get("default")
                        for q in (live[path][method].get("parameters") or [])
                        if q.get("in") == "query"}
                declm = decl[path][method]
                pairs += 1
                if sorted(impl) != sorted(declm):
                    name_bad.append(f"{method.upper()} {path}：契约 {sorted(declm)}"
                                    f" vs 实现 {sorted(impl)}")
                    continue
                for nm in sorted(declm):
                    if declm[nm][0] is None or impl[nm] is None:
                        continue
                    def_pairs += 1
                    if declm[nm][0] != impl[nm]:
                        def_bad.append(f"{method.upper()} {path} 的参数 {nm}："
                                       f"契约 {declm[nm][0]!r}（{declm[nm][1]}）"
                                       f" vs 实现 {impl[nm]!r}")
        return pairs, name_bad, def_bad, def_pairs

    _decl, _over = _declared_query()
    _pairs, _name_bad, _def_bad, _def_pairs = _compare(
        _decl, APP.app.openapi().get("paths", {}))
    print(f"  比了 {_pairs} 个端点、{_def_pairs} 个带默认值的参数")
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
    for x in _def_bad:
        print(f"  ✗ 默认值漂移 {x}")
    if _name_bad:
        fails.append(f"契约↔实现查询参数漂移 {len(_name_bad)} 处：{_name_bad}")
    if _def_bad:
        fails.append(f"契约↔实现默认值漂移 {len(_def_bad)} 处：{_def_bad}")
    if not (_name_bad or _def_bad) and _pairs >= 20 and _def_pairs >= 8:
        print("  ✓ 全部端点的查询参数名与默认值都与契约一致")

    # 元测试①：证明 _compare 的判据会响（三态：一致静默 / 名字漂移 / 默认值漂移）
    _ok_p = [{"name": "limit", "in": "query", "schema": {"default": 100}}]
    _mt_decl = {"P": {"get": {"limit": (100, "a.yaml")}}}
    _r0 = _compare(_mt_decl, {"P": {"get": {"parameters": _ok_p}}})
    _r1 = _compare(_mt_decl, {"P": {"get": {"parameters": [
        {"name": "offset", "in": "query", "schema": {"default": 0}}]}}})
    _r2 = _compare(_mt_decl, {"P": {"get": {"parameters": [
        {"name": "limit", "in": "query", "schema": {"default": 999}}]}}})
    _okm1 = (len(_r0[1]) == 0 and len(_r0[2]) == 0 and _r0[3] == 1
             and len(_r1[1]) == 1 and len(_r2[2]) == 1)
    print(f"  元测试：一致时静默、名字漂移会响、默认值漂移会响 → {'✓' if _okm1 else '✗'}")
    if not _okm1:
        fails.append(f"参数比对判据的元测试失败：{_r0[1:]} / {_r1[1]} / {_r2[2]}")

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
    # ★★ 两个坑，都是门自己照出来的：
    #   ① 名单最初是 5 条。把 anyOf 摊平逻辑收拢成一处之后变成 7 条 ——
    #      `from_km` / `to_km` 的 `ge=0` 原来**藏在 anyOf 里面**，看不见。
    #      ⇒ 「收拢一处」当场就还了本。
    #   ② 条目里嵌的是 Python `repr`，而 repr **对键序敏感**：实现侧 schema 的
    #      键是 FastAPI 按字母排的（maximum 在前），契约侧是手写的（minimum 在前），
    #      于是同一份约束被当成「新增」+「过期」各报一次。
    #      ⇒ 一律走 `_fmt()` 排序，键序不参与判定。
    _UNDECLARED_FROZEN = (
        "GET /v1/geometry/sections/{section_id}/geo/reverse 的参数 lat："
        "{'maximum': 90, 'minimum': -90}",
        "GET /v1/geometry/sections/{section_id}/geo/reverse 的参数 lon："
        "{'maximum': 180, 'minimum': -180}",
        "GET /v1/geometry/sections/{section_id}/geo/route 的参数 step_m："
        "{'exclusiveMinimum': 0, 'maximum': 1000}",
        "GET /v1/geometry/sections/{section_id}/stations 的参数 from_km："
        "{'minimum': 0}",
        "GET /v1/geometry/sections/{section_id}/stations 的参数 to_km："
        "{'minimum': 0}",
        "GET /v1/geometry/sections/{section_id}/stations 的参数 limit："
        "{'maximum': 5000, 'minimum': 1}",
        "GET /v1/objects/wim_axle 的参数 limit："
        "{'maximum': 2000, 'minimum': 1}",
    )

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
        return {q["name"]: q for q in (node.get("parameters") or [])
                if q.get("in") == "query"}

    _shape_bad: list = []
    _rng_bad: list = []
    _undecl: list = []
    _promised: list = []
    _s_pairs = 0
    for _p in sorted(set(_decl) & set(APP.app.openapi().get("paths", {}))):
        _lv = APP.app.openapi()["paths"][_p]
        for _mth in sorted(set(_decl[_p]) & set(_lv)):
            for _nm, _c in _decl[_p][_mth].items():
                _i = _params_of(_lv[_mth]).get(_nm)
                if _i is None:
                    continue
                _s_pairs += 1
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

    print(f"  比了 {_s_pairs} 个参数的形状 ｜ 类型/required 差 {len(_shape_bad)}"
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
    if len(_UNDECLARED_FROZEN) < 1:
        print("  ✗ 冻结名单是空的 —— 这条棘轮已成摆设")
        fails.append("契约未声明的约束冻结名单为空")
    if _s_pairs < 20 or len(_undecl) < 5:
        print(f"  ✗ 形状比对面太小或棘轮没量到东西（{_s_pairs} 个参数 / "
              f"{len(_undecl)} 条未声明，下界 20 / 5）")
        fails.append(f"参数形状比对失效：{_s_pairs} 个参数 / {len(_undecl)} 条未声明")
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
        print(f"  ✓ 三个方向都干净：类型与 required 全一致；{len(_undecl)} 条未声明的约束"
              f"与冻结名单逐条相符；契约没有许下实现给不出的约束")

    # ★★ 元测试：形状判据的每一条腿都要能被证明**会响**。
    #   宪法 V：一个找不到目标的判据永远不会失败，而「0 条发现」与「没在看」
    #   在输出上一模一样。第三个方向实测就是 0 条 —— 所以它**更需要**这个证明。
    _mt_ok = _shape({"type": "number", "exclusiveMinimum": 0})
    _mt_null = _shape({"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]})
    _mt_bare = _shape({"anyOf": [{"type": "string"}, {"type": "null"}]})
    # ① 摊平：anyOf 里的 format 必须被提出来，否则第三个方向全是假发现
    _mt_flat = (_mt_ok == ("number", {"exclusiveMinimum": 0})
                and _mt_null == ("string", {"format": "date"}))
    # ② 第三个方向：契约声明 format、实现只声明 string ⇒ 必须能发现
    _mt_seen = [k for k in sorted(_mt_null[1]) if k not in _mt_bare[1]]
    # ③ 反向：实现有、契约没有 ⇒ 走的是另一条腿，不该混进来
    _mt_rev = [k for k in sorted(_mt_bare[1]) if k not in _mt_null[1]]
    _okm = (_mt_flat and _mt_seen == ["format"] and _mt_rev == []
            and _shape({"type": "string"})[0] == "string"
            and _shape({"type": "integer"})[0] != _shape({"type": "string"})[0]
            # ④ 键序无关：名单条目由 _fmt 产出，两处键序不同必须印成同一个字符串
            and _fmt({"b": 2, "a": 1}) == _fmt({"a": 1, "b": 2}))
    print(f"  元测试：anyOf 摊平、类型可分辨、第三方向能发现、反向不串 → "
          f"{'✓' if _okm else '✗'}（摊平 {_mt_flat}／发现 {_mt_seen}／反向 {_mt_rev}）")
    if not _okm:
        fails.append("参数形状归一化的元测试失败")
    # 元测试②：钉死版本序。★ 上一条判据的覆盖规则建立在这个序上，
    #   而 sorted() 的字典序**不等于**版本序 —— 例如 v0.10 会排到 v0.2 前面。
    _ord = [f.name for f in sorted(contract_files(), key=_ver_key)]
    _want_ord = ["m6-gateway.geo.v0.1.yaml", "m6-gateway.geo.v0.2.yaml",
                 "m6-gateway.geo.v0.3.yaml", "m6-gateway.v0.3.yaml"]
    _okm2 = _ord == _want_ord
    print(f"  元测试：契约版本序 → {_ord} {'✓' if _okm2 else '✗'}")
    if not _okm2:
        fails.append(f"契约版本序不符：{_ord}，期望 {_want_ord}")

    print("\n结果：" + ("全部通过 ✓" if not fails else f"失败 {len(fails)} 项 → {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
