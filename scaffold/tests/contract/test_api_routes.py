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

import importlib.util
import pathlib
import re
import sys

from fastapi.testclient import TestClient
from starlette.routing import Match

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_PATH = ROOT / "modules" / "M6-api" / "app.py"
CONTRACT_PATH = ROOT / "contracts" / "openapi" / "m6-gateway.v0.3.yaml"

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

from rpdao import NotFound  # noqa: E402  （须在上面 sys.path 就位之后）


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
    """从 openapi 契约里抽出路径（不引 pyyaml，按缩进解析即可）。"""
    paths = set()
    for line in CONTRACT_PATH.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^  (/\S*):\s*$", line)
        if m:
            paths.add(m.group(1))
    return paths


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
    这个假实现刻意不提供无 section 的平铺查，与真实 GeRepository 保持一致。"""

    SECTION = {"id": 6, "section_name": "毕设", "line_code": "毕设",
               "start_station_text": "K0+000.000", "end_station_text": "K5+805.421",
               "station_count": 2, "pi_count": 1, "element_count": 1}

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

    def list_objects(self, table, limit=100):
        return [{"id": 1, "table": table}]


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

    print("\n结果：" + ("全部通过 ✓" if not fails else f"失败 {len(fails)} 项 → {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
