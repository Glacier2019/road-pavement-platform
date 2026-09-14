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

而契约文件 contracts/openapi/m6-gateway.v0.1.yaml 里明明写着该端点，
接入规约 MODULE-ONBOARDING.md 也拿它当示范路径——契约与实现就此静默漂移。
所以本测试做三件事：
  1) 路由自洽：每条路由用它自己的具体 URL 去匹配，第一个命中的必须是它自己；
  2) 契约对齐：openapi 里声明的路径集合 ↔ 实现里的路径集合，双向相等；
  3) 真实请求：用 TestClient 打一遍状态码（数据访问层被替换为假实现，不需要数据库）。

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
APP_PATH = ROOT / "services" / "api" / "app.py"
CONTRACT_PATH = ROOT / "contracts" / "openapi" / "m6-gateway.v0.1.yaml"


def _ensure_pg_driver():
    """本测试不触库，但 services/api/app.py 在 import 期就会 import psycopg。

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


def fake_q(sql: str, params: dict, one: bool = False):
    """替换数据访问层：路由测试关心的是"谁被命中"，不是"查出了什么"。"""
    if "wim_axle_detail" in sql:
        return [{"axle_seq": 1, "group_seq": None, "axle_weight_kg": 6400.0,
                 "group_weight_kg": None, "axle_dist_mm": 0.0}]
    if "wim_axle_record" in sql:
        row = {"id": 1, "pass_time": "2026-09-14T13:45:02+08:00", "lane_no": 2,
               "axle_type_code": "T5", "axle_num": 5, "speed_kmh": 68.4,
               "gross_weight_kg": 51200.0, "overload_flag": False, "esal": 14.73,
               "plate_no": None, "quality_code": "OK", "stake_text": "K4640+000"}
        if one:
            # 只有 id=1 这条记录存在，用来验证"记录不存在 → 404"分支
            return row if params.get("rid") in (None, 1) else None
        return [row]
    if one:
        return {"id": 1}
    return [{"id": 1}]


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
    APP.q = fake_q                     # 端点函数在调用时从模块全局取 q
    client = TestClient(APP.app)
    cases = [
        ("GET",    "/healthz",                          200),
        ("GET",    "/v1/objects/wim_axle",              200),   # ← 被遮蔽时这里是 404
        ("GET",    "/v1/objects/wim_axle/1",            200),
        ("GET",    "/v1/objects/road_line",             200),
        ("GET",    "/v1/objects/not_registered",        404),
        ("GET",    "/v1/objects/wim_axle/99999",        404),
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

    print("\n结果：" + ("全部通过 ✓" if not fails else f"失败 {len(fails)} 项 → {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
