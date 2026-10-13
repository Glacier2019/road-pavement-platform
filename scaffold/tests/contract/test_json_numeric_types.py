"""T046：`numeric` 列必须以**数字**出去 —— 判据 + 元测试。

==================== 为什么有这个文件 ====================

`jsonnum.py` 记着根因：FastAPI 把**带返回注解**的路由交给 pydantic 序列化，
而 pydantic 的 `Decimal` JSON 序列化默认保精度，输出**字符串**。
2026-10-13 实测：M6 的 20 条 GET 路由**全部**带 `-> dict[str, Any]`，
其中 **10 条**把数字以字符串发出去。

★★★ 而这个缺陷此前**全绿通过** —— 因为 §13 只比**字段名**，不比**类型**。
   一条只比名字的判据，看不见类型错。这就是本节存在的理由。

==================== 判据分三层，一层比一层强 ====================

§1 **机制**（离线，真 FastAPI）：同一份 `Decimal`，挂 `NoResponseModel` → 数字；
   摘掉 → 字符串。**元测试**：摘掉之后，下面的检测器必须报红。
   ⇒ 证明「这个修法确实是那个原因」，以及「检测器确实看得见」。

§2 **接线**（离线，真 app）：遍历**真 M6 app 的每一条路由**，断言
   `route.response_model is None`。
   ★ 这是本文件的核心判据：它问的不是"打出来长什么样"，而是**"这条路由会不会
     被交给 pydantic"** —— 那正是缺陷的机制本身。
   ★★ 路由清单是**运行时从 `app.routes` 枚举**的，不是手写的。手写清单没人校，
     新路由会自动逃逸 —— 我们已经在别处栽过两次。
   **元测试**：搭一个 mini app，挂/不挂 `route_class`，同一谓词必须 True/False。

§3 **载荷**（离线，真 app + 假 DAO）：递归扫真实响应体，找「长得像纯数字的字符串」。
   ⚠ 这一层是**启发式**，已知有假阳性空间（一个本来就该是字符串的字段，
     比如某种编码 `"117.000000"`，会被它误报）。所以它是**旁证**，不是主判据；
     主判据是 §2。**这一条的限制写在这里，不要把它读成"全类型都校验了"。**

==================== 与 §13 的关系 ====================

`test_region_frame.py` §13 比对的是**契约声明的字段名 ↔ 真实响应键**，
它读的是 `contracts/openapi/m6-gateway.geo.v0.2.yaml` 的 `schema.properties`。
那里**本来就有 `type: number`**，只是没比。
本文件不管契约（契约只覆盖 geo 一族），管的是**机制层面**：全部路由。
两者互补，不重复。
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_PATH = ROOT / "modules" / "M6-api" / "app.py"
JSONNUM_PATH = ROOT / "modules" / "M6-api" / "jsonnum.py"
CRS_PATH = ROOT / "modules" / "M9-console" / "config" / "crs.yaml"

_FAILS: list[str] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    if ok:
        print(f"  [OK] {name}")
    else:
        print(f"  [!!] {name}")
        if detail:
            print(f"       {detail}")
        _FAILS.append(name)
    return ok


# ════════════════════════════════════════════════════════ 检测器（纯函数）
# ★ 抽成纯函数是**刻意的**：§1/§2 的元测试要能喂一个人造输入进去，
#   证明它真的会报红。夹在流程里的检查没法这样测。

_NUMERIC_STRING = re.compile(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$")


def numeric_shaped_strings(obj: object, path: str = "") -> list[str]:
    """递归找出「长得像纯数字」的字符串。返回 `路径=值` 列表。

    ⚠ 启发式：字符串里出现纯数字**不一定是缺陷**（可能是本来就该是字符串的编码）。
      所以这条只作旁证；机制层面的判据是 `response_model is None`。
    """
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += numeric_shaped_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out += numeric_shaped_strings(v, f"{path}[{i}]")
    elif isinstance(obj, str) and _NUMERIC_STRING.match(obj.strip()):
        out.append(f"{path}={obj!r}")
    return out


def routes_with_response_model(app: object) -> list[str]:
    """哪些路由会因为返回注解被交给 pydantic 序列化。

    ★ 这是缺陷的**机制**：`response_model` 非 None ⇒ `Decimal` 会被序列化成字符串。
    """
    from fastapi.routing import APIRoute

    bad: list[str] = []
    for r in getattr(app, "routes", []):
        if isinstance(r, APIRoute) and getattr(r, "response_model", None) is not None:
            methods = ",".join(sorted(m for m in r.methods if m != "HEAD"))
            bad.append(f"{methods} {r.path} → response_model={r.response_model!r}")
    return bad


# ════════════════════════════════════════════════════════ 真 app 的装配
# ★ 与 `test_region_frame.py` 同一手法：psycopg 用桩、不触库。
#   本测试**不连数据库、不起服务**，所以能在契约套件里稳定跑。


def _stub_pg() -> None:
    try:
        import psycopg  # noqa: F401
        import psycopg_pool  # noqa: F401
        return
    except ImportError:
        pass
    if "psycopg" not in sys.modules:
        psycopg = types.ModuleType("psycopg")
        rows = types.ModuleType("psycopg.rows")
        rows.dict_row = type("dict_row", (), {})
        psycopg.rows = rows
        sys.modules["psycopg"], sys.modules["psycopg.rows"] = psycopg, rows
    if "psycopg_pool" not in sys.modules:
        pool = types.ModuleType("psycopg_pool")

        class ConnectionPool:
            def __init__(self, *a: object, **k: object): pass
            def open(self): pass
            def close(self): pass
            def connection(self, *a: object, **k: object):
                raise RuntimeError("本测试不应触达数据库")

        pool.ConnectionPool = ConnectionPool
        sys.modules["psycopg_pool"] = pool


def load_app():
    _stub_pg()
    os.environ.setdefault("RP_CRS_CONFIG", str(CRS_PATH))
    for p in (ROOT / "modules" / "M3-rpdao", ROOT / "modules" / "M6-api"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    spec = importlib.util.spec_from_file_location("jsonnum_api_app", APP_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jsonnum_api_app"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def load_jsonnum():
    spec = importlib.util.spec_from_file_location("jsonnum_mod", JSONNUM_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ════════════════════════════════════════════════════════ §1 机制

def section1() -> None:
    print("\n=== 1) 机制：挂 NoResponseModel → 数字；摘掉 → 字符串 ===")
    from decimal import Decimal
    from typing import Any

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    NoResponseModel = load_jsonnum().NoResponseModel
    D = Decimal("117.000000")

    def build(guarded: bool):
        app = FastAPI()
        if guarded:
            app.router.route_class = NoResponseModel

        @app.get("/x")
        def h() -> dict[str, Any]:
            return {"v": D, "nested": {"w": D}, "list": [D]}
        return app

    raw_guarded = TestClient(build(True)).get("/x").text
    raw_plain = TestClient(build(False)).get("/x").text
    print(f"       挂了：{raw_guarded}")
    print(f"     没挂：{raw_plain}")

    g = json.loads(raw_guarded)
    p = json.loads(raw_plain)
    check(isinstance(g["v"], float) and isinstance(g["nested"]["w"], float)
          and isinstance(g["list"][0], float),
          "1 · 挂了 NoResponseModel，Decimal 是**数字**（含嵌套 dict / list）",
          f"实际 {raw_guarded}")
    check(isinstance(p["v"], str),
          "1 · 不挂的时候，Decimal 是**字符串** —— 这就是缺陷本身",
          f"实际 {raw_plain}")

    # ---- 元测试：检测器必须看得见「不挂」那一份，看不见「挂了」那一份
    bad_plain = numeric_shaped_strings(p)
    bad_guarded = numeric_shaped_strings(g)
    check(bool(bad_plain) and not bad_guarded,
          "★ 元测试：检测器对「不挂」的响应报红、对「挂了」的响应放行",
          f"不挂→{bad_plain}；挂了→{bad_guarded}")

    # ---- 元测试的元测试：人造一个字符串数字，检测器也要抓住
    synthetic = {"length_m": "19701.0", "nested": [{"east_m": "536050.82"}]}
    hits = numeric_shaped_strings(synthetic)
    check(len(hits) == 2,
          "★ 元测试：人造 payload 里的两个字符串数字都被抓出来",
          f"实际 {hits}")


# ════════════════════════════════════════════════════════ §2 接线（核心判据）

def section2() -> None:
    print("\n=== 2) 真 M6 app 的每条路由：response_model 必须都是 None ===")
    APP = load_app()
    routes = [r for r in APP.app.routes if hasattr(r, "methods")]
    bad = routes_with_response_model(APP.app)
    print(f"       运行时枚举到 {len(routes)} 条路由")
    check(len(routes) >= 20,
          "2 · 枚举到的路由数不少于 20（枚举本身没走空）",
          f"实际 {len(routes)}")
    check(not bad,
          f"2 · 全部 {len(routes)} 条路由的 response_model 都是 None",
          "★ 这些路由会被交给 pydantic，Decimal 会以字符串出去：\n         "
          + "\n         ".join(bad[:8]))

    # ---- 元测试：同一谓词，在「没挂 route_class」的 mini app 上必须报红
    from typing import Any

    from fastapi import FastAPI

    NoResponseModel = load_jsonnum().NoResponseModel

    def mini(guarded: bool):
        app = FastAPI()
        if guarded:
            app.router.route_class = NoResponseModel

        @app.get("/y")
        def h() -> dict[str, Any]:
            return {}
        return app

    check(not routes_with_response_model(mini(True)),
          "★ 元测试：挂了 route_class 的 mini app，谓词判「干净」")
    mini_bad = routes_with_response_model(mini(False))
    check(bool(mini_bad),
          "★ 元测试：不挂 route_class 的 mini app，谓词**必须报红**",
          f"实际 {mini_bad}")

    # ---- 元测试 2：换成 `FastAPI(route_class=...)` 这种写法，谓词必须报红
    #   ★ 我第一版就是这么写错的 —— 那个 kwarg 被静默吞掉，源码里看着像修好了。
    app_kwarg = FastAPI(route_class=NoResponseModel)  # type: ignore[call-arg]

    @app_kwarg.get("/z")
    def hz() -> dict[str, Any]:
        return {}
    check(bool(routes_with_response_model(app_kwarg)),
          "★ 元测试：`FastAPI(route_class=...)` **不算数**（kwarg 被静默吞掉），谓词必须报红",
          "如果这条红了，说明 FastAPI 改了行为 —— 请重新验证 jsonnum.py 的用法说明")


# ════════════════════════════════════════════════════════ §3 端到端载荷（打活服务）
#
# ★★★★★ 这一节为什么改成「打活服务」而不是「假 DAO + TestClient」：
#   第一版用假 DAO 驱动真 app，结果 **25 条路由只跑通 2 条**（假 DAO 什么都不给）。
#   那等于一条**几乎不会失败的检查** —— 按宪法 §V，比没有检查更糟。
#   所以改成对**真的在跑的 M6** 逐条打：覆盖真的够，而且测的是别人真正拿到的东西。
#   ⚠ 服务没起就**大声跳过**（记为 skipped、不打勾、不假装通过）。
#
# ★ 路由清单仍然是从**真 app 运行时枚举**的（`app.routes`），不是手写的 ——
#   这样新加的路由自动进网。
#
# ★ 这一节能看见 §2 看不见的一类：DAO/DB 层本身就把数字存成了文本。
#   §2 只保证「pydantic 不再插一脚」，保证不了「值本身是数字」。


def section3() -> bool:
    """返回 True 表示真的跑了；False 表示跳过（服务不可达）。"""
    print("\n=== 3) 对活的 M6 逐条打：响应体里没有「长得像数字的字符串」===")
    import urllib.error
    import urllib.request

    # ---- 端口来自 `.env`（复用甲建立的那一份解析，不另写一个）
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import _env  # noqa: E402

    port = _env.env().get("API_PORT", "8001")
    base = f"http://127.0.0.1:{port}"

    try:
        with urllib.request.urlopen(base + "/healthz", timeout=8):
            pass
    except urllib.error.HTTPError:
        pass  # 有 HTTP 响应就说明服务活着（健康检查也可能自报 503）
    except Exception as exc:  # noqa: BLE001
        print(f"  [--] SKIP：{base}/healthz 不可达（{type(exc).__name__}）")
        print("       ★ 这一节**没有跑**，别把它读成通过。起服务后重跑：")
        print("         docker compose -f docker-compose.skeleton.yml up -d api")
        return False

    APP = load_app()
    paths = sorted({r.path for r in APP.app.routes
                    if hasattr(r, "methods") and "GET" in r.methods})

    checked = 0
    skipped: list[str] = []
    leaks: list[str] = []
    for raw in paths:
        path = re.sub(r"\{[^}]+\}", "6", raw) if "{" in raw else raw
        if ":" in path:  # 自动文档那几条不归本节管
            continue
        try:
            with urllib.request.urlopen(base + path, timeout=20) as r:
                body = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            skipped.append(f"{raw}（HTTP {e.code}）")
            continue
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"{raw}（{type(exc).__name__}）")
            continue
        try:
            payload = json.loads(body)
        except Exception:  # noqa: BLE001
            skipped.append(f"{raw}（响应不是 JSON）")
            continue
        checked += 1
        hits = numeric_shaped_strings(payload)
        if hits:
            leaks.append(f"{raw} → {hits[:4]}")

    print(f"       路径 {len(paths)} 条：跑通 200 的 {checked} 条，未跑通 {len(skipped)} 条")
    for s in skipped[:6]:
        print(f"         · {s}")
    check(checked >= 10,
          f"3 · 至少 10 条路由被真实请求到（本次 {checked} 条）",
          "覆盖太少的话，这一节等于没跑 —— 见本节开头的说明")
    check(not leaks,
          "3 · 跑通的路由里，没有「字符串形状的数字」",
          "★ " + "；".join(leaks[:5]))
    return True



def main() -> int:
    print("=" * 78)
    print("T046 · numeric 列必须以数字出去：机制 / 接线 / 载荷")
    print("=" * 78)
    section1()
    section2()
    ran = section3()
    print("\n" + "=" * 78)
    if _FAILS:
        print(f"失败 {len(_FAILS)} 项：")
        for f in _FAILS:
            print(f"  - {f}")
        return 1
    if not ran:
        # ★ 跳到过的检查不许报「全部通过」 —— 那正是「假绿」。
        print("通过（§1 §2 全绿）；§3 **跳过**（活服务不可达，见上）")
        return 0
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
