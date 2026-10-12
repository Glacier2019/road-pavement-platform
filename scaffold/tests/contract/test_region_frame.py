"""契约测试：M6 区域帧 —— 桩号 ↔ 经纬度（004-region-frame）。

运行（**不需要数据库、不需要容器、不需要网络**）：
  cd /data/cy/shujuku/scaffold
  ./run_contract_tests.sh 区域帧
  # 或直接：
  UV_CACHE_DIR="$PWD/../.uvcache" UV_PYTHON_INSTALL_DIR="$PWD/../.uvpython" \\
    uv run --quiet --with fastapi==0.115.6 --with httpx --with pyyaml --with pyyaml \\
    python tests/contract/test_region_frame.py

==================== 这条测试为什么存在 ====================
把桩号变成经纬度是「几何 → 投影 → 经纬度」三步。**两步都可能静默出错**：

  · 几何那步漏掉 `turn_flag` → 右转的曲线朝反方向弯。实测（本文件第 7 组）
    seq31 号圆曲线的终点差 **98.2664 m**，而 `geom` 自己的 docstring 写着
    「实测终点差 98 m，**而且不报错**」。98 m 是 1 m 判据的 98 倍。
  · 投影那步取错带号 → 位置差几百公里，而**路的形状完全正确**。

两件事都不会抛异常。所以本测试不满足于"跑通"，它做三件更难的事：
  1. **两侧判据**。只判「偏差 < 1 m」是不够的 —— 在直线段上线性插值本来就
     精确（实测 1e-6 m），这个判据在直线上恒过，属于空转。所以还判**下限**：
     曲线元素上的偏差必须落在一个由该元素半径算出来的区间内。
  2. **同向对照**。SC-008 把中央子午线从 117°E 换成 114°E，落点检查**必须失败**；
     换回来必须通过。一个只在正确参数下通过的检查，不是检查。
  3. **反向对照**。故意丢掉 `turn_flag`，**右转**元素的终点偏差必须跳到 ~98 m，
     而**左转**元素必须纹丝不动（因为 `geom` 的缺省 `sgn=+1` 恰好等于左转）。
     ★ 这一条最容易被写错：随手挑一个曲线元素做对照，有一半概率挑到左转的，
       于是对照恒过 —— 那不是对照，是装饰。

数据来源：`tests/contract/fixtures/route_geometry.json`（真实数据，自活库导出）。
用它喂一个假 DAO，于是既不碰数据库，又跑在**真几何**上。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import pathlib
import re
import sys

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_PATH = ROOT / "modules" / "M6-api" / "app.py"
GEO_PATH = ROOT / "modules" / "M6-api" / "geo.py"
M2GEOM = ROOT / "modules" / "M6-api" / "m2geom"
M2_ADAPTERS = ROOT / "modules" / "M2-ingest" / "adapters"
CRS_PATH = ROOT / "modules" / "M9-console" / "config" / "crs.yaml"
GEO_CONTRACT = ROOT / "contracts" / "openapi" / "m6-gateway.geo.v0.1.yaml"
FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "route_geometry.json"

# app.py 用 load_crs() 找配置；测试里把它指到仓库里的那一份。
# ★ 与容器一致：容器里 Dockerfile 把它 COPY 成 /app/crs.yaml，WORKDIR=/app。
import os  # noqa: E402
os.environ.setdefault("RP_CRS_CONFIG", str(CRS_PATH))

for p in (ROOT / "modules" / "M3-rpdao", ROOT / "modules" / "M6-api"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _ensure_pg_driver():
    """app.py 在 import 期就 import psycopg。本测试不触库，缺就用桩顶上。"""
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

        class ConnectionPool:
            def __init__(self, *a, **k): pass
            def open(self): pass
            def close(self): pass
            def connection(self, *a, **k):
                raise RuntimeError("本测试不应触达数据库")

        pool.ConnectionPool = ConnectionPool
        sys.modules["psycopg_pool"] = pool
    print("⚠ 未检测到 psycopg/psycopg_pool，已用桩替代（本测试不触库）\n")


_ensure_pg_driver()

from rpdao import CrsBindingError, GeRepository, NotFound, UnknownTable  # noqa: E402  （须在 sys.path 与 psycopg 桩就位之后）


def load_app():
    spec = importlib.util.spec_from_file_location("geo_api_app", APP_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["geo_api_app"] = mod
    spec.loader.exec_module(mod)
    return mod


def load_geo():
    spec = importlib.util.spec_from_file_location("geo_mod_under_test", GEO_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["geo_mod_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


FIX = json.loads(FIXTURE.read_text(encoding="utf-8"))
GEO = load_geo()
APP = load_app()


# ============================================================ DAO 形状
# ★★★★★ 本轮最贵的一条教训，就记在这里。
#
# 夹具记的是**原始表行**（`select * from alignment_element`）。但应用从来不
# 直接吃原始表行 —— 它吃的是 M3 `ELEMENTS_SQL` 的**投影结果**，而那条 SQL 把
# `azimuth_deg` 改名成了 `start_azimuth_deg`，并丢掉了几列。
#
# 假 DAO 若原样吐夹具行，它复现的就是一个**生产里不存在的形状**：
# 适配层按 `azimuth_deg` 去读，拿到 None，`geom` 抛「缺方位角」；
# 而契约测试全绿 —— 因为测试喂的正是那个不存在的形状。
# 实测：契约测试全部通过，容器里 `/geo` 返回 **500**。
#
# ⇒ **假 DAO 必须复现真 DAO 的形状，不只是它的数据。**
#   下面从 repo.py 里把那条 SQL 的投影抠出来，照着它重塑夹具。
ROOT_REPO = ROOT / "modules" / "M3-rpdao" / "rpdao" / "repo.py"


def _sql_projection(sql_name: str) -> dict[str, str]:
    """抠出一条 SELECT 的投影，返回 `{输出名: 源列名}`。

    ★ 不写死这张映射表。写死的话，SQL 哪天改了名，测试不会跟着变 ——
      那正是这次出错的形状：一份**手工维护的、以为和 SQL 一致**的副本。
    """
    src = ROOT_REPO.read_text(encoding="utf-8")
    m = re.search(sql_name + r'\s*=\s*"""(.*?)"""', src, re.S)
    assert m, f"repo.py 里找不到 {sql_name}"
    body = m.group(1)
    sel = body[body.index("SELECT") + len("SELECT"):body.index("FROM")]
    out: dict[str, str] = {}
    for raw in sel.split(","):
        item = " ".join(raw.split())
        if not item:
            continue
        mm = re.match(r"^(\w+)\.(\w+)(?:\s+AS\s+(\w+))?$", item, re.I)
        assert mm, f"{sql_name} 里这条投影解析不了：{item!r}"
        out[mm.group(3) or mm.group(2)] = mm.group(2)
    return out


ELEMENTS_PROJ = _sql_projection("ELEMENTS_SQL")


def dao_elements() -> list[dict]:
    """把夹具的**原始表行**投成 M3 `ELEMENTS_SQL` 的**输出形状**。"""
    return [{name: r.get(col) for name, col in ELEMENTS_PROJ.items()}
            for r in FIX["alignment_element"]]


# ============================================================ 假 DAO（夹具驱动）

class _FakeGe:
    """返回**真实**的 33 行线元与交点。数据来自夹具，不来自数据库。

    ★ 默认吐的是 `dao_elements()` —— **`ELEMENTS_SQL` 的输出形状**，
      不是夹具里的原始表行。理由见上面那段注释：形状错了，测试就会
      替一个生产里不存在的形状背书。

    ★★ 还带 `list_objects` —— 真 `dao.ge` 是 `DomainRepository`，
      通用取数口是它的**形状的一部分**。假的不带，新路由就直接
      `AttributeError`（这次会响）；更坏的情况是有人为了让它不响，
      把生产代码改成绕过仓储 —— 那就把域白名单也一起绕掉了。
      未知表名**抛错**，与真仓储的 `_check` 一致。
    """

    def __init__(self, elements=None, monitors=None):
        self._elements = dao_elements() if elements is None else elements
        self._monitors = (FIX["monitor_cross_section"]
                          if monitors is None else monitors)

    def alignment(self, section_id):
        if section_id != FIX["section_id"]:
            raise NotFound(f"路段 {section_id} 不存在")
        return {"section_id": section_id,
                "pis": [],
                "elements": [dict(r) for r in self._elements]}

    def sections(self):
        return FIX["road_section"]

    def section(self, section_id):
        if section_id != FIX["section_id"]:
            raise NotFound(f"路段 {section_id} 不存在")
        return FIX["road_section"][0]

    def list_objects(self, table, *, limit=100):
        if table != "monitor_cross_section":
            raise UnknownTable(f"GE 域无此表：{table}")
        return [dict(r) for r in self._monitors][:limit]


class _FakeRe:
    """RE 域仓储。`sensor_install` 是**设备溯源**的依据所在。"""

    def list_objects(self, table, *, limit=100):
        if table != "sensor_install":
            raise UnknownTable(f"RE 域无此表：{table}")
        return [dict(r) for r in FIX["sensor_install"]][:limit]


class _FakeDao:
    def __init__(self, elements=None, monitors=None):
        self.ge = _FakeGe(elements, monitors)
        self.re = _FakeRe()

    def ping(self): return True
    def pool_stats(self): return {"pool_size": 0, "pool_available": 0}
    def open(self): return self
    def close(self): pass


def sha256(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ============================================================ 工具

def elements():
    """生产形状的线元链（`ELEMENTS_SQL` 的输出形状，不是原始表行）。"""
    return GEO.as_adapter_elements(dao_elements())


def gf():
    return GEO.load_crs(CRS_PATH)


def grid_points():
    """`geometry_point` 缓存，键是桩号（米）。"""
    return {round(r[0] * 1000.0, 6): (r[1], r[2]) for r in FIX["geometry_point"]}


def non_grid_station(el, frac=0.37, grid=20.0, seams=None):
    """在线元 `el` 内部取一个桩号，**同时避开三样东西**：
       ① 20 m 缓存格点（否则对照退化成读缓存自身）；
       ② 线元接缝（接缝处 `locate` 归前一个元素，两种归属差 0.69 mm）；
       ③ 太靠近元素端点（那里 `point_at` 走 `d<=0` 分支，直接返回落库起点）。
    ★ 这三条都是实测出来的，不是防御性编程。
    """
    s0, s1 = el["start_station_m"], el["end_station_m"]
    s = s0 + (s1 - s0) * frac
    for _ in range(50):
        off_grid = abs(s / grid - round(s / grid)) * grid > 2.0
        far_ends = (s - s0 > 5.0) and (s1 - s > 5.0)
        far_seams = all(abs(s - x) > 2.0 for x in (seams or []))
        if off_grid and far_ends and far_seams:
            return s
        s += 1.3
    raise AssertionError(f"seq{el.get('seq')} 找不到合规的非格点桩号")


def lin_interp_at(pts, s):
    """相邻两个 20 m 缓存点之间的线性插值。"""
    keys = sorted(pts)
    for lo, hi in zip(keys, keys[1:]):
        if lo <= s <= hi:
            t = (s - lo) / (hi - lo)
            return (pts[lo][0] + t * (pts[hi][0] - pts[lo][0]),
                    pts[lo][1] + t * (pts[hi][1] - pts[lo][1]))
    raise AssertionError(f"桩号 {s} 不在缓存覆盖范围内")


def sagitta(radius_m, chord_m):
    """矢高：弦长 chord 的圆弧，**中点**到弦的距离。"""
    return radius_m - math.sqrt(radius_m ** 2 - (chord_m / 2.0) ** 2)


def arc_chord_deviation(radius_m, chord_m, t):
    """圆曲线上，**弦的 t 分点**到对应弧点的距离（精确闭式）。

    ★ 为什么不能一律用矢高：矢高是 t=0.5 处的值。实测里在弦的 0.685 处取样，
      真值 0.1660 m 而矢高 0.1923 m，相对差 13.7% —— 第一版判据就是这么红的。
      那不是代码错，是判据拿错了参考点。

    推导：把圆心放在原点，弦关于 x 轴对称，
      P0 = R(cos(θ/2), −sin(θ/2))，P1 = R(cos(θ/2), +sin(θ/2))，θ = chord/R
      弦上 t 分点   P(t) = (R cos(θ/2), R sin(θ/2)(2t−1))
      对应弧点      A(t) = R(cos α, sin α)，α = −θ/2 + tθ
      偏差          |P(t) − A(t)|
    """
    th = chord_m / radius_m
    half = th / 2.0
    alpha = -half + t * th
    dx = math.cos(half) - math.cos(alpha)
    dy = math.sin(half) * (2.0 * t - 1.0) - math.sin(alpha)
    return radius_m * math.hypot(dx, dy)


# ============================================================ 主流程

def main() -> int:
    fails: list[str] = []
    els = elements()
    g = gf()
    pts = grid_points()
    seams = [e["end_station_m"] for e in els[:-1]]

    def check(ok, msg, detail=""):
        print(f"  {'✓' if ok else '✗'} {msg}{('  ' + detail) if detail else ''}")
        if not ok:
            fails.append(msg)

    # ---------------------------------------------------------------- 1) 几何内核
    print("=== 1) 几何内核逐字节守卫（m2geom/ 是 M2 的副本，不许漂移）===")
    for name in ("errors.py", "geom.py"):
        a, b = sha256(M2_ADAPTERS / name), sha256(M2GEOM / name)
        check(a == b, f"{name} 与 M2 原件逐字节一致", f"sha256 {a[:16]}…")
    check((M2GEOM / "__init__.py").exists()
          and (M2GEOM / "__init__.py").stat().st_size == 0,
          "m2geom/__init__.py 存在且为空",
          "★ 空文件是刻意的：M2 的 __init__.py 会 from . import weidi 拖进 19 个解析器")
    # ★★ 上面那条"逐字节一致"是**硬约束**，它顺手禁止了一件事：
    #    不能在这两个 .py 里写注释（注释会破坏相等）。
    #    所以说明必须放在旁边 —— 这是那个 README 存在的理由。
    check((M2GEOM / "README.md").exists(),
          "m2geom/README.md 存在（说明为什么有副本、以及镜像不用它）",
          "★ 见下一条：守卫禁止在副本里写注释，说明只能另放")
    # ★★★ README 里最要紧的那句断言：「容器不用这一份」。
    #     若哪天有人在 Dockerfile 里改成 COPY modules/M6-api/m2geom，
    #     这句话就变成了假的 —— 那时这条必须红。
    _m6df = (ROOT / "modules" / "M6-api" / "Dockerfile").read_text(encoding="utf-8")
    check("M6-api/m2geom" not in _m6df,
          "★ M6 Dockerfile 不 COPY M6-api/m2geom（镜像从 M2 自己生成，不读这份副本）",
          "★ 若改成 COPY 这份副本，README 的结论就不成立了 —— 两处必须同时改")

    # ---------------------------------------------------------------- 2) 适配层
    print("\n=== 2) 适配层：与 geometry_solver 对同一批真数据输出相等 ===")
    sys.path.insert(0, str(ROOT / "modules" / "M2-ingest"))
    try:
        import geometry_solver as solver  # noqa: E402
        same = True
        for row in FIX["alignment_element"]:
            a = solver._as_adapter_element(row)
            b = GEO.as_adapter_element(row)
            if set(a) != set(b) or any(
                    (a[k] != b[k]) if not isinstance(a[k], float)
                    else abs(a[k] - b[k]) > 1e-12 for k in a):
                same = False
                print(f"    差异在 seq{row['element_seq']}：{a} vs {b}")
        check(same, f"{len(FIX['alignment_element'])} 行全部等价")
        # 反面：丢了 turn_flag，右转元素的符号必须真的丢
        bad = [dict(r, azimuth_deg=None) for r in FIX["alignment_element"]]
        n_sign = sum(1 for r in bad if GEO.turn_sign(r) is None)
        check(n_sign > 0, "反面对照：抹掉方位角后 turn_sign 返回 None（说明它真在读）",
              f"{n_sign} 行")
    except ImportError as exc:
        check(False, f"无法 import geometry_solver：{exc}")

    # ------------------------------------------------- 2b) DAO 形状
    # ★★★★★ 这一组是**事后补的**，因为不补就漏掉了一个真 bug：
    # `ELEMENTS_SQL` 把 `azimuth_deg` 改名成 `start_azimuth_deg`。
    # 假 DAO 从前吐原始表行，于是测试替一个生产里不存在的形状背书 ——
    # 契约测试全绿，容器里 `/geo` 返回 500。详见本文件顶部的说明。
    print("\n=== 2b) DAO 形状：假 DAO 必须复现真 DAO 的形状，不只是数据 ===")
    check(ELEMENTS_PROJ.get("start_azimuth_deg") == "azimuth_deg",
          "★ ELEMENTS_SQL 确实把 azimuth_deg 改名成了 start_azimuth_deg",
          f"投影共 {len(ELEMENTS_PROJ)} 列，"
          f"start_azimuth_deg ← {ELEMENTS_PROJ.get('start_azimuth_deg')}")
    _dao = dao_elements()
    _keys = set(_dao[0])
    check(_keys == set(ELEMENTS_PROJ),
          "DAO 形状的行键集合 = ELEMENTS_SQL 的输出列集合",
          f"{len(_keys)} 列")
    check("azimuth_deg" not in _keys and "start_azimuth_deg" in _keys,
          "★ DAO 形状里**没有** azimuth_deg（有的话这一组就是在测一个假形状）")

    # 关键回归：只有改过名的那个键，turn_flag 也必须还原出来。
    _curves_dao = [r for r in _dao if r["element_type"] != "line"]
    _signed_dao = [r for r in _curves_dao if GEO.turn_sign(r) in (-1, 1)]
    check(len(_signed_dao) == len(_curves_dao) and _signed_dao,
          "★ DAO 形状下，全部曲线线元都还原出转向符号（+1/−1）",
          f"{len(_signed_dao)}/{len(_curves_dao)}")
    _lines_dao = [r for r in _dao if r["element_type"] == "line"]
    check(all(GEO.turn_sign(r) is None for r in _lines_dao),
          "直线线元不还原符号（返回 None，不是随便给一个）",
          f"{len(_lines_dao)} 行")
    # 同一个元素，两种形状必须给出同一个符号 —— 否则"两个名字都认"
    # 只是碰巧在某一侧对了。
    _raw = {r["element_seq"]: r for r in FIX["alignment_element"]}
    _diff = [r["element_seq"] for r in _dao
             if GEO.turn_sign(r) != GEO.turn_sign(_raw[r["element_seq"]])]
    check(not _diff, "同一条线元在两种形状下转向符号一致", f"不一致 {_diff}")
    # 元测试（真能红的那种）：把两条路径各自**只留自己那个键**，
    # 符号必须一致。只认一个名字的实现会在这里立刻现形 ——
    # 实测：本轮修复前，右半边全是 None。
    _only_dao = [{"element_type": r["element_type"], "element_seq": r["element_seq"],
                  "start_azimuth_deg": r["start_azimuth_deg"],
                  "end_azimuth_deg": r["end_azimuth_deg"]} for r in _dao]
    _only_raw = [{"element_type": r["element_type"], "element_seq": r["element_seq"],
                  "azimuth_deg": r["azimuth_deg"],
                  "end_azimuth_deg": r["end_azimuth_deg"]}
                 for r in FIX["alignment_element"]]
    _mismatch = [a["element_seq"] for a, b in zip(_only_dao, _only_raw)
                 if GEO.turn_sign(a) != GEO.turn_sign(b)]
    check(not _mismatch,
          "★ 只给 start_azimuth_deg 与只给 azimuth_deg，还原出的符号完全相同",
          f"两条路径共 {len(_only_dao)} 行，不一致 {_mismatch}")

    # ---------------------------------------------------------------- 3) 投影往返
    print("\n=== 3) 投影往返闭合（正算→反算）===")
    worst = 0.0
    for lat, lon in [(25.185964, 117.359262), (25.16789, 117.34292),
                     (25.20878, 117.37376), (0.0, 117.0), (45.0, 117.0)]:
        n, e = GEO.forward(lat, lon, 117.0)
        lat2, lon2 = GEO.inverse(n, e, 117.0)
        dn = (lat2 - lat) * 111320.0
        de = (lon2 - lon) * 111320.0 * math.cos(math.radians(lat))
        worst = max(worst, math.hypot(dn, de))
    check(worst < 0.001, "5 个点往返偏差均小于 1 mm", f"最大 {worst * 1000:.6f} mm")

    # ---------------------------------------------------------------- 4) SC-002
    print("\n=== 4) SC-002 落点包围盒（39 带 / CM 117°E / EPSG:4548）===")
    route = GEO.route_lonlat(els, g, 20.0)
    b = route["bbox"]
    # 这组数字是 2026-10-11 用五带扫描独立量出来的（research.md R7）。
    check(25.16 < b["lat_min"] < 25.18 and 25.20 < b["lat_max"] < 25.22,
          "纬度落在闽中",
          f"{b['lat_min']:.5f}..{b['lat_max']:.5f} °N")
    check(117.33 < b["lon_min"] < 117.36 and 117.36 < b["lon_max"] < 117.39,
          "经度落在闽中",
          f"{b['lon_min']:.5f}..{b['lon_max']:.5f} °E")
    dy = (b["lat_max"] - b["lat_min"]) * 111.32
    dx = (b["lon_max"] - b["lon_min"]) * 111.32 * math.cos(math.radians(b["lat_min"]))
    check(math.hypot(dx, dy) > 4.0, "包围盒对角线与 5.805 km 的路线长度相称",
          f"{dx:.3f} × {dy:.3f} km")
    check(route["epsg"] == 4548, "回带 EPSG:4548",
          "★ 39 带有两个编码，4547 是 38 带（CM 114°E），取错差 300 km")

    # ---------------------------------------------------------------- 5) SC-001
    print("\n=== 5) SC-001 非格点对照：point_at vs 相邻缓存点线性插值 ===")
    print("     ★ 判据是**两侧**的：曲线上要有下限，全线上要有上限。")
    rows = []
    for el in els:
        if el["end_station_m"] - el["start_station_m"] < 40:
            continue
        s = non_grid_station(el, seams=seams)
        n, e = GEO.point_north_east(els, s)
        xl, yl = lin_interp_at(pts, s)
        rows.append((el, s, math.hypot(n - xl, e - yl)))

    cur = [r for r in rows if r[0]["type"] in ("circular", "transition")]
    lin = [r for r in rows if r[0]["type"] == "line"]
    worst_all = max(r[2] for r in rows)
    worst_lin = max((r[2] for r in lin), default=0.0)

    check(worst_all < 1.0, "全部非格点偏差小于 1 m（spec 的 SC-001）",
          f"最大 {worst_all:.6f} m，{len(rows)} 个采样")
    check(worst_lin < 1e-3,
          "直线元素上偏差≈0（正面说明：那里插值本来就精确）",
          f"最大 {worst_lin:.9f} m")
    check(min(r[2] for r in cur) > 0.005,
          "★ 曲线元素上偏差有下限（否则这条检查是空转）",
          f"最小 {min(r[2] for r in cur):.6f} m，{len(cur)} 个采样")

    # 与矢高闭式对齐。
    # ★ 只对「整个 20 m 缓存区间都落在**同一个圆曲线元素**内」的采样做这个比较。
    #   否则弦会横跨元素接缝，落在两个不同曲率上，量出来的不是 R 的矢高 ——
    #   第一版就是漏了这条，于是 8 个圆曲线里最短的那个（74.7 m）报了 81.9% 的
    #   相对差。那不是代码错，是判据错。
    checked = 0
    worst_rel = 0.0
    for el in els:
        if el["type"] != "circular":
            continue
        s0, s1 = el["start_station_m"], el["end_station_m"]
        keys = sorted(pts)
        for lo, hi in zip(keys, keys[1:]):
            if lo < s0 or hi > s1:
                continue                     # 区间没完全落在本元素内
            t = 0.685
            s = lo + (hi - lo) * t           # 非格点
            n, e = GEO.point_north_east(els, s)
            xl, yl = lin_interp_at(pts, s)
            d = math.hypot(n - xl, e - yl)
            pred = arc_chord_deviation(el["radius_start_m"], hi - lo, t)
            checked += 1
            worst_rel = max(worst_rel, abs(d - pred) / pred)
        if checked >= 8:
            break
    check(checked >= 8 and worst_rel < 0.03,
          "圆曲线上的偏差与弦−弧闭式相符（区间不跨接缝，且按采样位置取参考点）",
          f"{checked} 个区间，最大相对差 {worst_rel * 100:.2f}%"
          f"（余量来自缓存坐标 ~0.7 mm 的量化）")

    # ★ 反面：用**弦中点**的矢高当参考点，必须显式地不吻合 —— 证明这个参考点
    #   不是随便挑的，也证明上面那条判据真的在区分 t。
    t = 0.685
    el0 = next(e for e in els if e["type"] == "circular")
    s0, s1 = el0["start_station_m"], el0["end_station_m"]
    keys = sorted(pts)
    lo, hi = next((a, b) for a, b in zip(keys, keys[1:]) if a >= s0 and b <= s1)
    dev_t = arc_chord_deviation(el0["radius_start_m"], hi - lo, t)
    dev_mid = sagitta(el0["radius_start_m"], hi - lo)
    check(abs(dev_t - dev_mid) / dev_mid > 0.05,
          "★ 同一区间的 t=0.685 偏差与弦中点矢高**显著不同**"
          "（说明参考点必须按采样位置取）",
          f"{dev_t:.6f} m vs {dev_mid:.6f} m，差 "
          f"{(dev_mid - dev_t) / dev_mid * 100:.2f}%")

    # ---------------------------------------------------------------- 6) SC-008
    print("\n=== 6) SC-008 控制：把中央子午线换错，落点检查**必须失败** ===")
    wrong = GEO.route_lonlat(els, dict(g, central_meridian_deg=114.0), 20.0)
    wb = wrong["bbox"]
    moved = abs(wb["lon_max"] - b["lon_max"])
    same_lat = abs(wb["lat_max"] - b["lat_max"])
    check(moved > 2.0, "换到 38 带后经度整体平移约 3°",
          f"经度平移 {moved:.4f}°，纬度只动 {same_lat * 111.32:.3f} m")
    check(not (117.33 < wb["lon_min"] < 117.39),
          "★ 换错后第 4 组的落点判据**确实会红**（这不是空转的控制）",
          f"新经度 {wb['lon_min']:.5f}..{wb['lon_max']:.5f} °E")

    # ---------------------------------------------------------------- 7) turn_flag
    print("\n=== 7) 反向对照：丢掉 turn_flag，右转变大、左转不变 ===")
    bad_els = [{**e, "turn_flag": None} for e in els]
    worst_right, worst_left = 0.0, 0.0
    right_seq = left_seq = None
    for el in els:
        if el["type"] != "circular":
            continue
        s1 = el["end_station_m"] - 1e-6
        n0, e0 = GEO.point_north_east(els, s1)
        n1, e1 = GEO.point_north_east(bad_els, s1)
        d = math.hypot(n1 - n0, e1 - e0)
        daz = (el["end_azimuth_deg"] - el["azimuth_deg"] + 180) % 360 - 180
        if daz < 0:                       # 右转：默认 sgn=+1 是错的
            if d > worst_right:
                worst_right, right_seq = d, el["seq"]
        else:                             # 左转：默认 sgn=+1 恰好是对的
            worst_left = max(worst_left, d)
    check(worst_right > 90.0,
          "★ 右转圆曲线终点偏差跳到 ~98 m（geom 说的「终点差 98 m」）",
          f"seq{right_seq} → {worst_right:.4f} m")
    check(worst_left < 1e-6,
          "★ 左转圆曲线**纹丝不动**（同一方向的对照，说明上面的变化来自转向）",
          f"最大 {worst_left:.12f} m")

    # 解析校验：2R(1−cosΔ)
    zhi = next(e for e in els if e["seq"] == right_seq)
    pred = 2 * zhi["radius_start_m"] * (
        1 - math.cos(math.radians(abs(
            (zhi["end_azimuth_deg"] - zhi["azimuth_deg"] + 180) % 360 - 180))))
    check(abs(pred - worst_right) < 0.01,
          "偏差与闭式 2R(1−cosΔaz) 相符",
          f"闭式 {pred:.4f} m vs 实测 {worst_right:.4f} m")

    # ---------------------------------------------------------------- 8) 不变量
    print("\n=== 8) crs.yaml 的不变量（assumed ↔ epsg_after_confirm）===")
    check(g["assumed"] is False and g.get("epsg_after_confirm") == 4548,
          "当前状态：assumed=false 且 epsg_after_confirm=4548")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        for assumed, epsg, want_err in ((True, 4548, True), (False, None, True),
                                        (True, None, False)):
            doc = {"geographic_frame": {"zone_number": 39, "central_meridian_deg": 117,
                                        "assumed": assumed,
                                        "epsg_after_confirm": epsg}}
            p = pathlib.Path(td) / f"c{assumed}{epsg}.yaml"
            p.write_text(json.dumps(doc), encoding="utf-8")
            try:
                GEO.load_crs(p)
                got_err = False
            except GEO.CrsError:
                got_err = True
            check(got_err == want_err,
                  f"assumed={assumed}, epsg={epsg} → "
                  f"{'拒绝' if want_err else '接受'}")

    # ---------------------------------------------------------------- 9) 路由契约
    print("\n=== 9) 路由契约对齐（m6-gateway.geo.v0.1 ↔ 实现）===")
    import yaml  # noqa: E402
    doc = yaml.safe_load(GEO_CONTRACT.read_text(encoding="utf-8"))
    want = set(doc["paths"])
    impl = {r.path for r in APP.app.routes if r.path.startswith("/v1/geometry")}
    missing = sorted(p for p in want if p not in impl)
    check(not missing, f"契约声明的 {len(want)} 条路径都已实现",
          f"缺：{missing}" if missing else "")

    # ---------------------------------------------------------------- 10) HTTP
    print("\n=== 10) 真实请求（TestClient，假 DAO 驱动真实夹具）===")
    APP.dao = _FakeDao()
    c = TestClient(APP.app)
    sid = FIX["section_id"]

    r = c.get(f"/v1/geometry/sections/{sid}/geo", params={"station_m": 300.0})
    check(r.status_code == 200, "GET …/geo 200", f"HTTP {r.status_code}")
    j = r.json()
    check(j.get("epsg") == 4548 and j.get("source") == "computed_from_station",
          "回带 epsg 与 source",
          f"epsg={j.get('epsg')} source={j.get('source')}")
    check(25.16 < j["lat_deg"] < 25.22 and 117.33 < j["lon_deg"] < 117.39,
          "落点在闽中", f"{j['lat_deg']:.6f} °N / {j['lon_deg']:.6f} °E")

    r_out = c.get(f"/v1/geometry/sections/{sid}/geo", params={"station_m": -50.0})
    check(r_out.status_code == 400, "桩号越界 → 400（不是 404）",
          f"HTTP {r_out.status_code}")

    r_nf = c.get("/v1/geometry/sections/999999/geo", params={"station_m": 10.0})
    check(r_nf.status_code == 404, "路段不存在 → 404", f"HTTP {r_nf.status_code}")

    r_bad = c.get(f"/v1/geometry/sections/{sid}/geo", params={"station_m": "abc"})
    check(r_bad.status_code == 422, "桩号不是数 → 422", f"HTTP {r_bad.status_code}")

    r_rt = c.get(f"/v1/geometry/sections/{sid}/geo/route", params={"step_m": 20.0})
    check(r_rt.status_code == 200, "GET …/geo/route 200", f"HTTP {r_rt.status_code}")
    jr = r_rt.json()
    check(jr.get("point_count", 0) > 300 and len(jr.get("elements", [])) == 33,
          "折线覆盖 33 个线元",
          f"{jr.get('point_count')} 点 / {len(jr.get('elements', []))} 元素")

    lat0, lon0 = j["lat_deg"], j["lon_deg"]
    r_rev = c.get(f"/v1/geometry/sections/{sid}/geo/reverse",
                  params={"lat": lat0, "lon": lon0})
    check(r_rev.status_code == 200, "GET …/geo/reverse 200", f"HTTP {r_rev.status_code}")
    jv = r_rev.json()
    check(abs(jv["station_m"] - 300.0) < 5.0 and jv["offset_m"] < 5.0,
          "反算回到原桩号附近并回带 offset_m",
          f"桩号 {jv['station_m']:.3f} m（原 300.0），偏移 {jv['offset_m']:.6f} m")

    # ★ 反面：把 x/y 读反（这是库里真实存在的坑），落点必须跑到太平洋
    n_sw, e_sw = GEO.point_north_east(els, 300.0)
    lat_sw, lon_sw = GEO.inverse(e_sw, n_sw, 117.0)
    check(not (25.0 < lat_sw < 26.0),
          "★ 把北/东读反 → 落点跑到太平洋（说明这一层真的在区分）",
          f"读反后 {lat_sw:.4f} °N / {lon_sw:.4f} °E")

    # ============================================================ 11) US3 沿线设施
    print("\n=== 11) US3 沿线设施：位置怎么来的，必须说得出来 ===")

    sid6 = FIX["section_id"]          # 6 = 毕设，有 33 个线元
    els6 = elements()
    bb = GEO.route_lonlat(els6, gf(), 20.0)["bbox"]

    def _inside(la, lo):
        return (bb["lat_min"] - 1e-6 <= la <= bb["lat_max"] + 1e-6
                and bb["lon_min"] - 1e-6 <= lo <= bb["lon_max"] + 1e-6)

    r_ms = c.get(f"/v1/geometry/sections/{sid6}/monitor-sections")
    check(r_ms.status_code == 200, "GET …/monitor-sections 200",
          f"HTTP {r_ms.status_code}")
    jm = r_ms.json()
    rows = jm.get("cross_sections", [])

    # ★ 只应有路段 6 的那一个断面。路段 1 的两个断面**不属于**本路段 ——
    #   这正是 spec 的 Edge Cases 自己说的「两个路段桩号语义不同」：
    #   K4635+710 与 K2+900 不是同一个里程域，混在一起就是静默串台。
    check([r["id"] for r in rows] == [3],
          "只返回本路段的断面（K2+900），不含路段 1 的两个",
          f"ids={[r['id'] for r in rows]}")

    row3 = rows[0]
    check(row3["position_source"] == "computed_from_station",
          "K2+900 的位置由桩号换算（库里 lat/lon 为空）",
          f"source={row3['position_source']}")
    check("lat_deg" in row3 and "lon_deg" in row3,
          "有位置时 lat_deg/lon_deg 两个键都在", "键在")
    check(_inside(row3["lat_deg"], row3["lon_deg"]),
          "★ 换算出的位置落在 US1 给出的路线包围盒内",
          f"{row3['lat_deg']:.6f} °N / {row3['lon_deg']:.6f} °E ∈ "
          f"[{bb['lat_min']:.5f},{bb['lat_max']:.5f}]×"
          f"[{bb['lon_min']:.5f},{bb['lon_max']:.5f}]")
    check(jm["counts_by_source"] == {"stored": 0, "computed_from_station": 1,
                                     "unavailable": 0},
          "counts_by_source 与实际逐行来源一致",
          str(jm["counts_by_source"]))

    # ★★ 「路段不存在」与「路段存在但没断面」是**两件事**，不能都答 200 空列表。
    #    否则写错一个 section_id，得到的是"没有数据"而不是"没有这个路段" ——
    #    静默错，正是本 feature 一路在挡的东西。
    r_nf2 = c.get("/v1/geometry/sections/999999/monitor-sections")
    check(r_nf2.status_code == 404, "★ 路段不存在 → 404（不是 200 空列表）",
          f"HTTP {r_nf2.status_code}")

    # ★★ FR-010 的**结构性**保证：位置与来源一一对应，且
    #    「没有位置」不是 0，而是**没有这两个键**。
    for r in rows:
        has = ("lat_deg" in r) and ("lon_deg" in r)
        check(has == (r["position_source"] != "unavailable"),
              f"断面 {r['id']}：有位置 ⟺ 来源不是 unavailable",
              f"有键={has} source={r['position_source']}")
        if has:
            check(not (r["lat_deg"] == 0.0 and r["lon_deg"] == 0.0),
                  f"断面 {r['id']}：位置不是 (0,0)",
                  "非 (0,0)")
            check(_inside(r["lat_deg"], r["lon_deg"]),
                  f"★ 断面 {r['id']} 的位置落在本路段自己的区域帧内",
                  f"{r['lat_deg']:.6f}/{r['lon_deg']:.6f}")

    # ---- 仿真溯源：「判断」和「依据」两个都要有
    dv = row3.get("devices", [])
    check(len(dv) == 1, "K2+900 挂着 1 台设备", f"{len(dv)} 台")
    if dv:
        check(dv[0]["install_mode"] == "仿真" and dv[0]["simulated"] is True,
              "★ 仿真设备被标出来（依据是 install_mode，不是序列号）",
              f"install_mode={dv[0]['install_mode']} simulated={dv[0]['simulated']}")
        check(dv[0]["serial_no"] == "SIMWIM01",
              "仿真设备登记在自己的序列号下，不占用实体 WIM01",
              str(dv[0]["serial_no"]))

    # ★★ 元测试：**夹具里出现过的每一种安装方式都必须被分类**。
    #    将来数据里多出一种（比如"半实物仿真"），这条会红 ——
    #    否则它会被**静默当成实体资产**。
    modes = sorted({r["install_mode"] for r in FIX["sensor_install"]})
    unclassified = [m for m in modes if GEO.classify_install_mode(m) is None]
    check(not unclassified,
          "★ 夹具里每一种 install_mode 都被分类（不得有未分类）",
          f"出现过 {modes}；未分类 {unclassified}")
    check(GEO.classify_install_mode("仿真") is True
          and GEO.classify_install_mode("埋入式") is False
          and GEO.classify_install_mode("半实物仿真") is None
          and GEO.classify_install_mode(None) is None,
          "★ 未分类走 None，**不默认成实体**",
          "仿真→True 埋入式→False 半实物仿真→None 空→None")

    # ---- 「算不出来」是**正常的答案**，不是 404
    pos_sec1 = GEO.section_positions([], FIX["monitor_cross_section"],
                                     FIX["sensor_install"], gf())
    check(pos_sec1["counts_by_source"]["unavailable"] == 3
          and pos_sec1["counts_by_source"]["computed_from_station"] == 0,
          "★ 路段无线形元素时，断面全部 unavailable（不是被跳过）",
          str(pos_sec1["counts_by_source"]))
    xs0 = pos_sec1["cross_sections"][0]
    check("lat_deg" not in xs0 and "unavailable_reason" in xs0,
          "★ 算不出来时**没有 lat_deg 键**，但有理由",
          f"有 lat_deg={'lat_deg' in xs0}；理由={xs0.get('unavailable_reason')!r}")

    # ---- 半填（只给纬度）：NULL 的一种，不能补 0 顶上
    half = [dict(FIX["monitor_cross_section"][2], lat=25.2, lon=None)]
    h = GEO.section_positions(els6, half, FIX["sensor_install"], gf())
    check(h["cross_sections"][0]["position_source"] == "unavailable"
          and "lat_deg" not in h["cross_sections"][0],
          "★ 经纬度只填一半 → unavailable（不拿 0 补齐另一半）",
          h["cross_sections"][0].get("unavailable_reason", ""))

    # ---- 桩号越界：不能钳到端点当答案
    far = [dict(FIX["monitor_cross_section"][2], station_km=99.0)]
    f2 = GEO.section_positions(els6, far, FIX["sensor_install"], gf())
    check(f2["cross_sections"][0]["position_source"] == "unavailable",
          "★ 桩号越界 → unavailable（不钳到端点冒充）",
          f2["cross_sections"][0].get("unavailable_reason", ""))

    # ---- 库里有坐标时就用它，且**不声称**它来自换算
    st = [dict(FIX["monitor_cross_section"][2], lat=25.20, lon=117.35)]
    s2 = GEO.section_positions(els6, st, FIX["sensor_install"], gf())
    check(s2["cross_sections"][0]["position_source"] == "stored",
          "库里有经纬度 → stored（不再换算）",
          s2["cross_sections"][0]["position_source"])

    # ---- 元测试：**注入 NULL→0 的强制转换，上面那条区域帧判据必须红。**
    #    ★ 这是 FR-010 唯一能真正被证伪的地方：不看来源标签，
    #      只看**这个坐标在不在本路段自己的区域帧里**。
    #      (0°,0°) 在几内亚湾外，必然出帧。
    src = (ROOT / "modules" / "M6-api" / "geo.py").read_text(encoding="utf-8")
    bugged = src.replace(
        "        if lat_stored is not None and lon_stored is not None:",
        "        lat_stored = lat_stored or 0.0\n"
        "        lon_stored = lon_stored or 0.0\n"
        "        if True:", 1)
    check(bugged != src, "元测试：NULL→0 的注入点存在", "已注入")
    ns = {"__name__": "bugged_geo"}
    exec(compile(bugged, "bugged_geo.py", "exec"), ns)      # noqa: S102
    b = ns["section_positions"](els6, FIX["monitor_cross_section"],
                                FIX["sensor_install"], gf())
    b0 = b["cross_sections"][2]                             # id=3，K2+900
    check(b0["position_source"] == "stored"
          and b0["lat_deg"] == 0.0 and b0["lon_deg"] == 0.0,
          "元测试：注入后 (0,0) 被当成合法位置（错误确实发生了）",
          f"{b0.get('lat_deg')},{b0.get('lon_deg')} src={b0['position_source']}")
    check(not _inside(b0["lat_deg"], b0["lon_deg"]),
          "★ 元测试：那条区域帧判据**抓得住** NULL→0（这是它存在的理由）",
          "(0,0) 在区域帧之外 → 判据会红")

    # ============================================================ 12) 残差分带
    #
    # ★★★ 这一节补的是一道**被设计过、写进 docstring、从来没实现**的闸。
    #   `reverse_lonlat` 的前任 docstring 写着「超过 max_offset_m 一律抛错」，
    #   而签名里根本没有 max_offset_m —— 文档在，代码没有。
    #   spec 的 Edge Cases 与 data-model §5 也写着「带外 → 报错」。
    #   实测：任何经度都返回 200（一路扫到 130°E）。
    print("\n=== 12) 残差分带：带号管不住的那一类，offset 管得住 ===")

    # ---- 三值且穷尽
    _bands = {GEO.offset_band(d) for d in
              (0.0, 1.0, 5.0, 5.001, 12.0, 50.0, 50.001, 371.7, 264857.9)}
    check(_bands == {"on_roadbed", "in_corridor", "off_corridor"},
          "offset_band 取值恰好是三带（不多不少）",
          f"实际 {sorted(_bands)}")

    # ---- 边界来自**实测数据**，不是估计：
    #     5.0  = roadbed_design_point 的设计路基半宽 0.75+0.75+0+3.5
    #     50.0 = cross_section_ground_point 实测横向覆盖 0.019..50.000
    for d, want in ((5.0, "on_roadbed"), (5.001, "in_corridor"),
                    (50.0, "in_corridor"), (50.001, "off_corridor")):
        check(GEO.offset_band(d) == want, f"offset_m={d} → {want}",
              f"实际 {GEO.offset_band(d)}")
    check(GEO._ON_ROADBED_MAX_M == 5.0 and GEO._IN_CORRIDOR_MAX_M == 50.0,
          "★ 两条边界等于实测值（设计路基半宽 5.000 / 勘察走廊半宽 50.000）",
          f"{GEO._ON_ROADBED_MAX_M} / {GEO._IN_CORRIDOR_MAX_M}")

    # ---- ★★★ 本节的核心一条，也是选 B 不选 A 的**实测理由**：
    #       lon=117.36°E **完全在 39 带内**（离中央子午线只有 0.36°），
    #       实测残差却是 371.7 m —— 这个点根本不在路上。
    #       ⇒ 「带外就拒」会**放过它**。
    check(GEO.offset_band(371.7) == "off_corridor",
          "★ 带内的远点（371.7 m，实测值）落在 off_corridor —— 带号检查会放过它",
          "这正是「用 offset 当闸、不用带号当闸」的实测依据")

    # ---- 端到端：真元素 + 真经纬度
    _rev = GEO.reverse_lonlat(els6, 25.18, 120.0, gf())
    check(_rev.get("offset_band") == "off_corridor",
          "★ /geo/reverse 经度 120°E（带外 3°）→ offset_band=off_corridor",
          f"offset_m={_rev.get('offset_m')} band={_rev.get('offset_band')}")
    check(_rev.get("station_is_nearest") is True,
          "★★ 且 station_is_nearest=True（桩号是最近点的，不是这个点的）",
          f"station_m={_rev.get('station_m')}")
    check(abs(_rev.get("station_m") - max(e["end_station_m"] for e in els6)) < 1e-4,
          "★ 桩号确实钳到了链尾（这说明为什么必须给 station_is_nearest）",
          f"station_m={_rev.get('station_m')} 链尾={max(e['end_station_m'] for e in els6)}")
    check(_rev.get("offset_bands_m") == {"on_roadbed": 5.0, "in_corridor": 50.0},
          "响应把两条边界一并报出来（调用方不用猜阈值）",
          str(_rev.get("offset_bands_m")))
    check(_rev.get("source") == "computed_from_lonlat",
          "★★ source 是 computed_from_lonlat —— 输入是经纬度，桩号才是推出来的",
          f"实际 {_rev.get('source')}")

    # ---- ★★ 反向对照：拿**路线自己的点**回灌，必须回到同一个桩号、残差≈0。
    #      ★ 一开始我随手猜了一个「近点」(25.19,117.36)，实测残差 294 m ——
    #      猜的点根本不近。**对照样本不能猜，要从这条路自己身上取。**
    _fwd = GEO.station_to_lonlat(els6, 2000.0, gf())
    _back = GEO.reverse_lonlat(els6, _fwd["lat_deg"], _fwd["lon_deg"], gf())
    check(abs(_back["station_m"] - 2000.0) < 1.0,
          "★★ 往返闭环：K2+000 正算再反算，回到同一个桩号（±1 m）",
          f"2000.0 → {_back['station_m']}")
    check(_back["offset_m"] < 1e-3 and _back["offset_band"] == "on_roadbed",
          "★★ 且残差 < 1 mm、落在 on_roadbed —— 这就是「在路上」应有的样子",
          f"offset_m={_back['offset_m']} band={_back['offset_band']}")
    #
    # ★★ 容差为什么是 1 mm：`reverse_lonlat` 自己只细化到 1 mm
    #    （docstring：「在 best_s ± coarse_m 内二分到 1 mm」）。
    #    实测残差 **0.0398 mm**、往返桩号差 **0.031 mm** —— 远好于它承诺的。
    #    ★ 我第一版把容差写成 1e-6（1 微米），**比这个函数承诺的分辨率还严**，
    #    于是断言在一个完全正常的实现上变红。
    #    **容差不能比被测函数自己声明的精度更紧**，
    #    否则测的是浮点噪声，不是行为。
    check(_back["station_is_nearest"] is False,
          "★ 在路上时 station_is_nearest=False（桩号就是这个点的）",
          f"{_back['station_is_nearest']}")

    # ---- 元测试 1：把 50 m 那道边界放大到 1000 m，核心断言必须红
    _src = (ROOT / "modules" / "M6-api" / "geo.py").read_text(encoding="utf-8")
    _loose = _src.replace("_IN_CORRIDOR_MAX_M = 50.0", "_IN_CORRIDOR_MAX_M = 1000.0", 1)
    check(_loose != _src, "元测试：走廊边界的注入点存在", "已注入")
    _ns = {"__name__": "loose_geo"}
    exec(compile(_loose, "loose_geo.py", "exec"), _ns)       # noqa: S102
    check(_ns["offset_band"](371.7) != "off_corridor",
          "★ 元测试：把走廊放大到 1000 m，371.7 m 就**不再是** off_corridor —— "
          "上面那条断言确实在读这个阈值",
          f"实际 {_ns['offset_band'](371.7)}")

    # ---- 元测试 2：把 source 改回旧字符串，那条断言必须红
    _wrong = _src.replace('"source": "computed_from_lonlat"',
                          '"source": "computed_from_station"', 1)
    check(_wrong != _src, "元测试：source 的注入点存在", "已注入")
    _ns2 = {"__name__": "wrong_geo"}
    exec(compile(_wrong, "wrong_geo.py", "exec"), _ns2)      # noqa: S102
    check(_ns2["reverse_lonlat"](els6, 25.18, 120.0, gf())["source"]
          != "computed_from_lonlat",
          "★ 元测试：改回 computed_from_station，那条断言会红",
          "方向说反的标签确实能被抓住")

    # ================================================ 13) 契约字段名 ↔ 真实响应
    #
    # ★★★★★ 这一节为什么存在：
    #   §9 比的是**路径**（声明 == 实现），它**看不到字段名**。
    #   而本契约原先把字段名写在 prose `description:` 里 ——
    #   **没有任何机器能比对它**，于是它静默漂了很久：
    #     参数 `station_km`（实现收 station_m）
    #     字段 `x` / `y`（实现返 north_m / east_m）
    #     字段 `latlon`（实现返 points）
    #     字段 `distance_m` / `on_route` / `assumed`（**实现里都不存在**）
    #   ★★ 更糟的是下游文档正从这份契约抄：quickstart 的 `station_km`
    #      与 `latlon` 两个错，源头都在这里。**契约错了两次，被抄了两次。**
    #
    #   ⇒ 修法不是"以后仔细点"，是**把字段名变成机器可读的 `schema:`**，
    #     然后在这里逐一比对。**prose 写的契约不是契约，是注释。**
    print("\n=== 13) 契约字段名 ↔ 真实响应键（m6-gateway.geo.v0.1）===")

    def _declared(path: str) -> set:
        """契约声明的 200 响应字段名。"""
        return set(doc["paths"][path]["get"]["responses"]["200"]
                   ["content"]["application/json"]["schema"]["properties"])

    #    路径模板 → (实际请求 URL, 查参, **允许缺失的条件字段**)
    #    ★ 条件字段列出来是**声明**，不是放行：FR-010 要求
    #      lat_deg/lon_deg 在算不出来时**必须缺席**，
    #      所以它们不能进 required，比对时也就不能要求它们必然出现。
    _CASES = [
        ("/v1/geometry/sections/{section_id}/geo",
         f"/v1/geometry/sections/{sid}/geo", {"station_m": 300.0}, set()),
        ("/v1/geometry/sections/{section_id}/geo/reverse",
         f"/v1/geometry/sections/{sid}/geo/reverse",
         {"lat": 25.19, "lon": 117.36}, set()),
        ("/v1/geometry/sections/{section_id}/geo/route",
         f"/v1/geometry/sections/{sid}/geo/route", {"step_m": 20.0}, set()),
        ("/v1/geometry/sections/{section_id}/monitor-sections",
         f"/v1/geometry/sections/{sid}/monitor-sections", {},
         {"unavailable_reason"}),
    ]

    for tpl, url, params, _opt in _CASES:
        want_f = _declared(tpl)
        resp = c.get(url, params=params)
        check(resp.status_code == 200, f"13 · {tpl} 可请求", f"HTTP {resp.status_code}")
        got = set(resp.json().keys())
        # ---- ① 契约声明了、真实响应里没有 → 契约要么写错，要么漏标"条件出现"
        gone = sorted(want_f - got)
        check(not gone, f"13 · {tpl} 声明的字段都真的出现",
              f"★ 声明了但响应里没有：{gone}")
        # ---- ② 真实响应里有、契约没声明 → 契约漏了字段
        extra = sorted(got - want_f)
        check(not extra, f"13 · {tpl} 没有未声明的字段",
              f"★ 响应里有但契约没声明：{extra}")

    # ---- 元测试：把契约里 `station_m` 改名成 `x`，上面第①条必须红。
    #    ★ 这是本节存在的**唯一理由** —— 证明它读的真是契约里的名字，
    #      而不是把实际响应抄了一遍当期望值。
    _cfg = GEO_CONTRACT.read_text(encoding="utf-8")
    _broken = _cfg.replace("                  station_m: {type: number}\n"
                           "                  north_m: {type: number}\n"
                           "                  east_m: {type: number}\n",
                           "                  x: {type: number}\n"
                           "                  y: {type: number}\n", 1)
    check(_broken != _cfg, "元测试：契约字段名的注入点存在", "已注入")
    _bdoc = yaml.safe_load(_broken)
    _bwant = set(_bdoc["paths"]["/v1/geometry/sections/{section_id}/geo"]
                 ["get"]["responses"]["200"]["content"]["application/json"]
                 ["schema"]["properties"])
    _bgot = set(c.get(f"/v1/geometry/sections/{sid}/geo",
                      params={"station_m": 300.0}).json().keys())
    check(_bwant - _bgot,
          "★ 元测试：把契约里的 station_m 改成 x，比对立刻发现「声明了但没有」",
          f"注入后差集 = {sorted(_bwant - _bgot)}")

    # ================================================================ 14) 五态
    # ★ v0.6（契约变更工单 005）T012。
    #   逐字对拍 data-model.md E2 的五态表。**纯函数、不碰库**，所以这一段
    #   不违反本文件开头那句「不需要数据库、不需要容器、不需要网络」。
    print("\n=== 14) 生效基准的五态派生（E2 表：两级都有 / 不等报错 / 缺一级 / 全缺）===")

    def _row(s=None, p=None, s_assumed=True, p_assumed=True,
             s_zone=39, p_zone=39, s_cm=117.0, p_cm=117.0):
        """造一行「路段 + 所属项目」。只填五态表看得见的字段。"""
        return {
            "section_crs_epsg": s, "section_crs_zone_number": s_zone,
            "section_crs_central_meridian_deg": s_cm, "section_crs_zone_width_deg": 3.0,
            "section_crs_assumed": s_assumed, "section_crs_evidence": "路段侧依据",
            "project_crs_epsg": p, "project_crs_zone_number": p_zone,
            "project_crs_central_meridian_deg": p_cm, "project_crs_zone_width_deg": 3.0,
            "project_crs_assumed": p_assumed, "project_crs_evidence": "项目侧依据",
        }

    # ---- 态 1：两级都有且相等 → 取该值，source=project -------------------
    r = GeRepository.effective_crs(_row(s=4548, p=4548))
    check(r["epsg"] == 4548 and r["source"] == "project",
          "态1 · 两级都有且相等 → 取该值、source=project",
          f"epsg={r['epsg']} source={r['source']}")
    # ★ 态1 与态3 生效值相同、source 也相同 —— 这正是「来源级别是溯源，
    #   不是优先级」的直接体现。路段级填了相同的值，权威来源仍是项目级。
    r3 = GeRepository.effective_crs(_row(s=None, p=4548))
    check(r["epsg"] == r3["epsg"] and r["source"] == r3["source"],
          "态1 与态3 的来源级别相同（都是 project）—— 级别不是优先级",
          f"态1={r['source']} 态3={r3['source']}")

    # ---- 态 2：两级都有且不等 → 抛错，且**必须说出两级各是什么** ---------
    _err2 = None
    try:
        GeRepository.effective_crs(_row(s=4545, p=4548))
    except CrsBindingError as exc:
        _err2 = exc
    check(_err2 is not None, "态2 · 两级不等 → 抛 CrsBindingError",
          "已抛" if _err2 else "★ 没抛，静默回退了")
    if _err2 is not None:
        _txt = f"{_err2} {_err2.two_levels_note}"
        check("4548" in _txt and "4545" in _txt,
              "★ 态2 的错误信息说出了两级各是什么（SC-009）",
              f"原话：{_err2.two_levels_note}")
        # ★★ 元测试：把两个值都改成一样，态2 必须不再触发 ——
        #    证明上面那条不是因为「随便抛了个错」而通过。
        _quiet = None
        try:
            GeRepository.effective_crs(_row(s=4548, p=4548))
        except CrsBindingError as exc:
            _quiet = exc
        check(_quiet is None, "元测试：两级改成相等后，态2 不再抛错",
              "★ 仍抛错 = 判定没在看两级的取值" if _quiet else "已静默")

    # ---- 态 3：项目有、路段没填 → 取项目，source=project -----------------
    check(r3["epsg"] == 4548 and r3["source"] == "project",
          "态3 · 项目有、路段没填 → 取项目、source=project",
          f"epsg={r3['epsg']} source={r3['source']}")

    # ---- 态 4：项目没有（含路段无项目）、路段有 → 取路段，source=section -
    r4 = GeRepository.effective_crs(_row(s=4548, p=None))
    check(r4["epsg"] == 4548 and r4["source"] == "section",
          "态4 · 项目没填、路段有 → 取路段、source=section（FR-010）",
          f"epsg={r4['epsg']} source={r4['source']}")
    # ★★ 态4 是**实测必须通过**的一条：路段 1「滨海大通道试验段」
    #    design_project_id = NULL。判为报错，那个路段就永远无法定位。
    #    这里连同「整行都没有 project_ 键」也一并试 —— 那正是 LEFT JOIN 的形状。
    _no_keys = {k: v for k, v in _row(s=4548, p=None).items()
                if not k.startswith("project_")}
    r4b = GeRepository.effective_crs(_no_keys)
    check(r4b["epsg"] == 4548 and r4b["source"] == "section",
          "态4 · 整行连 project_ 键都没有（LEFT JOIN 的真实形状）也通过",
          f"epsg={r4b['epsg']} source={r4b['source']}")

    # ---- 态 5：两级都没有 → **有类型的「没有」**（FR-003 / E5）-----------
    _err5 = None
    try:
        GeRepository.effective_crs(_row(s=None, p=None))
    except CrsBindingError as exc:
        _err5 = exc
    check(_err5 is not None, "态5 · 两级都没有 → 抛 CrsBindingError（不是返回 None）",
          "已抛" if _err5 else "★ 返回了 None，上层会拿它当「随便挑个默认值」")
    if _err5 is not None:
        check("crs_epsg" in str(_err5) and "没有" in str(_err5),
              "★ 态5 的说法里说了「没有什么」和「去哪里配」",
              f"原话：{str(_err5)[:56]}…")

    # ---- 五态必须**穷尽**（SC-005 查的就是这个）--------------------------
    # ★ 组合只有四种（s 有/无 × p 有/无），第五态是「有有不等」这一支。
    #   这里枚举全部四种组合 × 相等/不等，断言「要么给值、要么抛错」，没有第三种结局。
    _outcomes = []
    for _s in (None, 4548, 4545):
        for _p in (None, 4548, 4545):
            try:
                _o = GeRepository.effective_crs(_row(s=_s, p=_p))
                _outcomes.append(("值", _s, _p, _o["epsg"], _o["source"]))
            except CrsBindingError:
                _outcomes.append(("错", _s, _p, None, None))
    _vals = [o for o in _outcomes if o[0] == "值"]
    _errs = [o for o in _outcomes if o[0] == "错"]
    check(len(_vals) + len(_errs) == 9,
          "★ 9 种组合全部落在「给值」或「抛错」两结局之一（没有第三种）",
          f"给值 {len(_vals)} / 抛错 {len(_errs)}")
    check(len(_vals) == 6,
          "★ 给值的是 6 种（态1 两种取值 + 态3 两种 + 态4 两种）",
          "；".join(f"({o[1]},{o[2]})→{o[3]}/{o[4]}" for o in _vals))
    # ★★ 这个数是 6 不是 5：E2 那张表是**规则**，不是**枚举** ——
    #    态1 和态4 各能容纳两种取值，所以 5 行规则 → 6 种给值组合。
    #    （第一版按「表有 5 行」写了 5，测试当场变红。它抓对了。
    #      把表的行数当成可能性的个数，是这个项目反复出现的那类错。）
    check(len(_errs) == 3,
          "★ 抛错的是 3 种（态2 两个方向 + 态5）",
          "；".join(f"({o[1]},{o[2]})" for o in _errs))

    print("\n" + "=" * 72)
    if fails:
        print(f"失败 {len(fails)} 项：")
        for f in fails:
            print(f"  · {f}")
        print("=" * 72)
        return 1
    print("全部通过")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
