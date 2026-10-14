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
# ★ 2026-10-12（005 / T017）：**现行契约是 v0.2**（新增 5 个 `crs_*` 字段）。
#   v0.1 **原样冻结，一个字都不许改**（宪法 §I：契约禁止就地改）。
#   它仍然被下面那条"向后兼容"判据引用着 —— 不是留着好看的。
GEO_CONTRACT = ROOT / "contracts" / "openapi" / "m6-gateway.geo.v0.2.yaml"
GEO_CONTRACT_V01 = ROOT / "contracts" / "openapi" / "m6-gateway.geo.v0.1.yaml"
FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "route_geometry.json"

# ★ 2026-10-12（005 / Q3）：**M6 已不再读这个文件**（`load_crs` 收 dict）。
#   下面这行现在是**给测试自己用**的 —— `crs_doc()` 经 `GEO.crs_path()` 找它。
#   ⚠ 别把它读成"生产还在靠这个环境变量"：生产靠库，不靠文件。
#   文件名仍叫 CRS_PATH，因为对**测试**来说它就是那个文件的路径。
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

# ------------------------------------------------------------ 夹具行的 crs 补齐
# ★★ 夹具是**迁移之前**导出的：`road_section` 那一行只有 6 个键，一个 crs_* 都没有。
#    而 `SECTIONS_SQL` / `ONE_SECTION_SQL` 现在会带出 12 个
#    `section_crs_*` / `project_crs_*`。⇒ 假 DAO 必须把这段差距补上。
#
#    **不补会怎样**：假 DAO 的形状 ≠ 真 DAO 的形状。生产代码在真库上跑得通、
#    在假 DAO 上全部 503 —— 于是测试要么替一个不存在的形状背书，
#    要么被人把生产代码改成绕过仓储（那会把域白名单一起绕掉）。
#
# ★ 补进去的值就是 `sql/99_seed_crs_39band.sql` 写进活库的那一套，
#   逐字对应：项目级 39 带 / CM 117 / EPSG 4548 / assumed=false；
#   路段级**留空** ⇒ 走五态表的**态 3**（项目有、路段没填 → 取项目）。
#   路段侧留空不是省事，是**故意让它落在态3**：态1 与态3 生效值相同
#   但来源级别相同（都是 project），正好覆盖「source 是溯源不是优先级」。
# ★ 两个 `numeric` 列给 **`Decimal`**，不是 `float` —— 这一点必须忠实：
#   psycopg 从 `numeric(9,6)` 取出来就是 `Decimal`，而 FastAPI 把 `Decimal`
#   序列化成**字符串**。给假 DAO 填 `117.0`（float）的话，
#   这个缺陷在离线套件里**永远复现不了**，只有真库能暴露它 ——
#   而"只有真库能暴露"就等于"没有判据"。
from decimal import Decimal  # noqa: E402

_CRS_FILL = {
    "section_crs_epsg": None,
    "section_crs_zone_number": None,
    "section_crs_central_meridian_deg": None,
    "section_crs_zone_width_deg": None,
    "section_crs_assumed": None,
    "section_crs_evidence": None,
    "project_crs_epsg": 4548,
    "project_crs_zone_number": 39,
    "project_crs_central_meridian_deg": Decimal("117.000000"),
    "project_crs_zone_width_deg": Decimal("3.00"),
    "project_crs_assumed": False,
    "project_crs_evidence": "39 带已确认（见 sql/99_seed_crs_39band.sql）",
}


def section_row(crs=None):
    """夹具那行路段 + 12 个 crs 列 = **真 ONE_SECTION_SQL 的形状**。

    ★ `crs` 用来换一套基准（T013 要造「两个不同基准的项目共存」）。
      不传就是夹具那行的 39 带。参数是**覆盖**，不是替换 ——
      只想改项目级时，只给 `project_crs_*` 几个键就够。
    """
    row = {**FIX["road_section"][0], **_CRS_FILL}
    if crs is not None:
        row.update(crs)
    return row


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

    def __init__(self, elements=None, monitors=None, crs=None, row=None):
        self._elements = dao_elements() if elements is None else elements
        self._monitors = (FIX["monitor_cross_section"]
                          if monitors is None else monitors)
        # ★ 换一套基准（T013）。默认 None = 夹具那行的 39 带。
        self._crs = crs
        # ★★ 整行覆盖（T018 要造「两级都没配」的路段）。
        #    给了它，就**同时决定这个假仓储服务哪一个 section_id** ——
        #    否则 `section(1)` 会先撞上「路段 1 不存在」那条守卫，
        #    那时测到的其实是守卫，而不是「没有基准」这件事。
        self._row = row

    @property
    def _served_id(self):
        if self._row is not None and "id" in self._row:
            return self._row["id"]
        return FIX["section_id"]

    def _one(self):
        return dict(self._row) if self._row is not None else section_row(self._crs)

    def alignment(self, section_id):
        if section_id != self._served_id:
            raise NotFound(f"路段 {section_id} 不存在")
        return {"section_id": section_id,
                "pis": [],
                "elements": [dict(r) for r in self._elements]}

    def sections(self):
        return [self._one()]

    def section(self, section_id):
        if section_id != self._served_id:
            raise NotFound(f"路段 {section_id} 不存在")
        return self._one()

    # ★★ 委派给**真的**实现，而不是在这里再抄一遍五态表。
    #    抄一遍 = 让测试替另一份规则背书，于是五态表有了两份会各自漂的实现，
    #    而两份都会绿。这里补上的只是"这个方法存在"这一件事。
    #    ★ `staticmethod(...)` 不能省：不加的话它变成实例方法，
    #      调用时 `self` 会被塞进 `row` 的位置，然后报一个与真因无关的 TypeError。
    effective_crs = staticmethod(GeRepository.effective_crs)

    #: ★★ 005 / T043：SRID → 投影族。**假的也要有这个口** ——
    #:    和 `list_objects` 同理：真 `dao.ge` 现在有 `crs_family`，
    #:    假的不带，新代码就直接 `AttributeError`；更坏的是有人为了让它不响，
    #:    把生产代码改成「不查族」—— 那就把 T043 整个绕掉了。
    #:    · 默认空表 ⇒ 一切 SRID 都当 `tmerc`（夹具本来就是 39 带高斯-克吕格）。
    #:    · T044 的元测试往这里塞 `{2154: "lcc"}`，断言接口返回 503。
    #:    ★★ **改它必须还原** —— 它是类属性，会污染同进程里后面所有用例，
    #:       而那些用例会红在一个跟本次断言毫无关系的地方。
    FAMILY_BY_SRID: dict[int, str] = {}

    def crs_family(self, srid):
        return self.FAMILY_BY_SRID.get(int(srid), "tmerc")

    def list_objects(self, table, *, limit=100, offset=0):
        if table != "monitor_cross_section":
            raise UnknownTable(f"GE 域无此表：{table}")
        # ★★ `offset` 要**真的跳行**（2026-10-14）：一个收下 offset 却不跳的
        #   假仓储，会让翻页用例**永远绿** —— 而那正是要测的东西。
        return [dict(r) for r in self._monitors][offset:offset + limit]


class _FakeRe:
    """RE 域仓储。`sensor_install` 是**设备溯源**的依据所在。"""

    def list_objects(self, table, *, limit=100, offset=0):
        if table != "sensor_install":
            raise UnknownTable(f"RE 域无此表：{table}")
        # ★★ `offset` 要**真的跳行**（2026-10-14）：一个收下 offset 却不跳的
        #   假仓储，会让翻页用例**永远绿** —— 而那正是要测的东西。
        return [dict(r) for r in FIX["sensor_install"]][offset:offset + limit]


class _FakeDao:
    def __init__(self, elements=None, monitors=None, crs=None, row=None):
        self.ge = _FakeGe(elements, monitors, crs, row)
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


def crs_doc():
    """读那份**派生产物**文件，返回原始文档。

    ★ 2026-10-12（005 / Q3）：**"读文件"这件事从 M6 搬到了测试里。**
      库成为权威之后 M6 不再读它（`load_crs` 已改为收 dict）。
      测试仍然要读 —— 因为测试要问的问题正是
      「**文件里写的这些，和库里的是不是一样**」，那个问题必须有人读文件。
      如果让 M6 代读，问的就不是这个问题了。
    """
    import yaml  # noqa: E402
    return yaml.safe_load(GEO.crs_path().read_text(encoding="utf-8")) or {}


def gf():
    """生效基准的**文件版**（`geographic_frame` 段）。

    ⚠ 路线几何仍拿文件里的值来算 —— 因为库里的值就是照它装的
      （`sql/99_seed_crs_39band.sql`），两者此刻相等。
      Phase 3（T013）会造**第二个**项目，那时两套基准才真正分开走。
    """
    _doc = crs_doc()
    # ★★ 005 / T043：`projection_family` **故意不写进 `crs.yaml`**。
    #    · 它是 `spatial_ref_sys.proj4text` 里 `+proj=` 的直接派生物，
    #      而文件里已经有 `epsg_after_confirm: 4548` —— 族由 SRID 唯一决定。
    #    · 往文件里再抄一份，就是**同一件事的第二个真相来源**；
    #      两者会漂：改了 SRID 而忘了改族，文件就自相矛盾。
    #    · M6 已经**不读这个文件**（Q3 裁决），所以它不构成生产依赖。
    #    ⇒ 所以由**使用方**声明，而不是让文件携带。
    _doc["geographic_frame"]["projection_family"] = "tmerc"
    return GEO.load_crs(_doc, origin="crs.yaml（派生产物）")


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
    _devs = []
    for lat, lon in [(25.185964, 117.359262), (25.16789, 117.34292),
                     (25.20878, 117.37376), (0.0, 117.0), (45.0, 117.0)]:
        n, e = GEO.forward(lat, lon, 117.0)
        lat2, lon2 = GEO.inverse(n, e, 117.0)
        dn = (lat2 - lat) * 111320.0
        de = (lon2 - lon) * 111320.0 * math.cos(math.radians(lat))
        worst = max(worst, math.hypot(dn, de))
        _devs.append(math.hypot(dn, de) * 1000)
    check(worst < 0.001, "5 个点往返偏差均小于 1 mm", f"最大 {worst * 1000:.6f} mm")
    # ★★ T023（quickstart 第 7 步）：「004 的精度一位不变」。
    #    原先这一条只**印出** `最大 0.146233 mm`，靠人眼跟 quickstart 上的
    #    那个数比 —— 而人眼比对不是判据：换个日子跑、数字变成 0.146234，
    #    没人会注意到，可 FR-007 要的恰恰是「**逐位**相同」。
    #    ⇒ 把 004 记录的两个实测值**冻结成断言**。它们变了就必须有人解释。
    #    ★ 这不是「容差」，是**记录值**：不做区间、不给余量，就是那两个数。
    #      所以别把它当成可以调松的阈值 —— 它只有「一样」和「不一样」。
    check(f"{worst * 1000:.6f}" == "0.146233",
          "★★ 3) T023 · 004 记录的往返**最大**偏差逐位未变"
          "（FR-007 要的是浮点逐位相同，不是「在容差内」）",
          f"记录 0.146233 mm，现在 {worst * 1000:.6f} mm")
    check(f"{_devs[0]:.6f}" == "0.050835",
          "★★ 3) T023 · 004 记录的**首点**偏差逐位未变（quickstart 第 7 步"
          "把这两个数并列写着，只冻一个等于只守了一半）",
          f"记录 0.050835 mm，现在 {_devs[0]:.6f} mm")

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
    print("\n=== 8) 基准文档的不变量（assumed ↔ epsg_after_confirm）===")
    check(g["assumed"] is False and g.get("epsg_after_confirm") == 4548,
          "当前状态：assumed=false 且 epsg_after_confirm=4548")
    # ★ 2026-10-12（005 / Q3）：`load_crs` 改为收**已解析的 dict**。
    #   校验逻辑一个字没动，只是不再有临时文件与路径 ——
    #   旧写法要落 3 个临时 yaml 才能问出同样这三个问题。
    # ★ 注意这个"简化"本身不是目的：它顺带说明**这条判据从来测的都不是
    #   "能不能读到文件"**。能读文件那条性质另有其测（§10 的容器可达性）。
    for assumed, epsg, want_err in ((True, 4548, True), (False, None, True),
                                    (True, None, False)):
        doc = {"geographic_frame": {"zone_number": 39, "central_meridian_deg": 117,
                                    "assumed": assumed,
                                    "epsg_after_confirm": epsg,
                                    # ★★ 005 / T043：`load_crs` 现在还查
                                    #    「本实现算不算得了这个基准」。
                                    #    ★ 这三条测的是 **assumed ↔ epsg 的不变量**，
                                    #      不是族检查 —— 所以这里把族填成合法的，
                                    #      让它们继续只测自己那一件事。
                                    #    ★★ 族检查**另有其测**（§20 / T044）：
                                    #      那里专门喂一个 `lcc` 进去断言被拒。
                                    #      ★ 两件事不塞进同一个用例，是因为那样
                                    #        失败时分不清是谁错了。
                                    "projection_family": "tmerc"}}
        try:
            GEO.load_crs(doc)
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
    print("\n=== 13) 契约字段名 ↔ 真实响应键（m6-gateway.geo.v0.2）===")

    def _declared(path: str, doc_: dict | None = None) -> set:
        """契约声明的 200 响应字段名。

        ★ `doc_` 是留给**元测试注入**的：不给就用真契约。
        """
        _d = doc if doc_ is None else doc_
        return set(_d["paths"][path]["get"]["responses"]["200"]
                   ["content"]["application/json"]["schema"]["properties"])

    def _declared_props(path: str, doc_: dict | None = None) -> dict:
        """契约声明的 200 响应字段 → 它的 schema（含 `type`）。

        ★ 与 `_declared` 的区别：后者只要**名字**，这里要**名字 + 类型**。
        """
        _d = doc if doc_ is None else doc_
        return (_d["paths"][path]["get"]["responses"]["200"]
                ["content"]["application/json"]["schema"]["properties"])

    def _type_mismatches(props: dict, payload: dict) -> list:
        """契约说 `number`/`integer` 的字段，响应里必须是数字。

        ★★★ 抽成**纯函数**是为了元测试：喂一个人造的 payload 进来，
           证明它真的会报红。夹在流程里的检查没法这样测。

        ⚠ 只查**声明了类型**的字段。没写 `type` 的、以及 `boolean`/`string`/
          `array`/`object` 的，本节不管 —— 不假装全类型都校验了。
        """
        out = []
        for k, spec in props.items():
            if not isinstance(spec, dict):
                continue
            want = spec.get("type")
            if want not in ("number", "integer") or k not in payload:
                continue
            v = payload[k]
            # ★ bool 是 int 的子类，必须排掉 —— 否则 `True` 会被当成数字放行
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                out.append(f"{k}={v!r}（声明 {want}，实为 {type(v).__name__}）")
            elif want == "integer" and not float(v).is_integer():
                out.append(f"{k}={v!r}（声明 integer，实为小数）")
        return out

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
        # ---- ③ ★ 类型也要比（T046①）。契约里 `type: number` / `integer`
        #   的字段，响应里就必须是**数字**，不能是字符串。
        #
        #   ★★★★★ 这一条为什么必须补：契约里**本来就写着** `type: number`，
        #     但 005 之前这里只比**字段名**，于是 `central_meridian_deg` 从
        #     `117` 变成 `"117.000000"` 的时候，**全绿通过**。
        #     一份声明了类型却没人读的契约，和没声明是一样的。
        prop = _declared_props(tpl)
        bad_t = _type_mismatches(prop, resp.json())
        check(not bad_t, f"13 · {tpl} 声明了类型的字段，类型都对得上",
              "★ " + "；".join(bad_t))

    # ---- 元测试：把契约里 `station_m` 改名成 `x`，上面第①条必须红。
    #    ★ 这是本节存在的**唯一理由** —— 证明它读的真是契约里的名字，
    #      而不是把实际响应抄了一遍当期望值。
    # ---- 元测试：把契约里 `/geo` 的 `station_m` 删掉，上面第①条必须红。
    #    ★ 这是本节存在的**唯一理由** —— 证明它读的真是契约里的名字，
    #      而不是把实际响应抄了一遍当期望值。
    #    ★★ 注入用**结构化**手法（深拷贝契约文档、删一个键），不用字面量替换：
    #      字面量得连缩进一起写对，而契约一换版缩进就变，注入会**静默失效** ——
    #      失效的样子是"元测试失败"，读起来却像契约有问题。
    #      从 v0.1 换到 v0.2 时我正是这么踩了一次。
    import copy  # noqa: E402

    _bwant = _declared("/v1/geometry/sections/{section_id}/geo")
    check("station_m" in _bwant, "元测试前置：契约的 /geo 确实声明了 station_m",
          f"实际字段 {sorted(_bwant)}")
    _bdoc = copy.deepcopy(doc)
    _bp = (_bdoc["paths"]["/v1/geometry/sections/{section_id}/geo"]["get"]["responses"]
           ["200"]["content"]["application/json"]["schema"]["properties"])
    # ⚠ 必须是**改名**（弹掉 + 塞个新的），不能只 `pop`：
    #   第①条判的是 `声明了 - 响应里有`。只删键 ⇒ 缺的那个键不在"声明了"里了，
    #   差集反而**变小**，元测试永远红 —— 而它红得看起来像契约有问题。
    _bp["x"] = _bp.pop("station_m")
    _bwant2 = _declared("/v1/geometry/sections/{section_id}/geo", _bdoc)
    _bgot = set(c.get(f"/v1/geometry/sections/{sid}/geo",
                      params={"station_m": 300.0}).json().keys())
    check("x" in (_bwant2 - _bgot),
          "★ 元测试：把契约里的 station_m 改名成 x 之后，比对立刻发现「声明了但没有」",
          f"改名后差集 = {sorted(_bwant2 - _bgot)}")

    # ---- 元测试（T046①）：类型比对必须抓得住「声明 number、实为字符串」。
    #
    # ★★★★ 这个元测试的形状很关键：**注入的是响应侧，不是契约侧**。
    #   因为缺陷的形态就是「契约没写错（`type: number` 一直是对的），
    #   是**实现**把数字发成了字符串」。若只注入契约，测的就不是这条判据了。
    #   ⇒ 所以这里直接拿 `_type_mismatches` 这个纯函数，
    #     喂一份**模拟 005 之前那个真实响应**的 payload（117 是字符串）。
    _mp = _declared_props("/v1/geometry/sections/{section_id}/geo")
    check(_mp.get("central_meridian_deg", {}).get("type") == "number",
          "元测试前置：契约的 central_meridian_deg 确实声明成 number",
          f"实际 {_mp.get('central_meridian_deg')!r}")
    # ★ 这一份就是**当年真实发出去的样子** —— 值对、类型错，所以只比名字的判据全绿。
    _old_payload = {"section_id": 6, "station_m": 300.0, "zone_number": 39,
                    "central_meridian_deg": "117.000000",
                    "epsg": 4548, "crs_assumed": False}
    _mt = _type_mismatches(_mp, _old_payload)
    check(any("central_meridian_deg" in s for s in _mt),
          "★ 元测试：把 central_meridian_deg 发成字符串（005 之前的真实形态），"
          "类型比对立刻报红",
          f"实际 {_mt}")
    # ---- 反向：值对、类型也对 ⇒ 不许误报
    _ok_payload = dict(_old_payload, central_meridian_deg=117.0)
    check(not _type_mismatches(_mp, _ok_payload),
          "★ 元测试反向：值对类型也对时，类型比对**不许误报**",
          f"实际 {_type_mismatches(_mp, _ok_payload)}")
    # ---- 反向：bool 不是数字（`True` 会被 isinstance(x, int) 放行，必须排掉）
    check(bool(_type_mismatches({"epsg": {"type": "integer"}}, {"epsg": True})),
          "★ 元测试：bool 不许被当成数字（`isinstance(True, int)` 是 True，得专门排）")

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

    # ================================================================ 15) I1
    # ★ v0.6（契约变更工单 005）T011。
    #   `check_i1` 的 lookup 参数是**注入**的 —— 所以这一段同样不连库。
    print("\n=== 15) I1 不变量：SRID 必须查得到，三个注释缺了不许报错 ===")

    # 造一个「只有这几个 SRID 存在」的假登记表。
    _KNOWN = {4326, 4545, 4548, 2154}
    _lookup = lambda s: int(s) in _KNOWN  # noqa: E731

    _r = _row(s=4548, p=4548)
    _eff = GeRepository.effective_crs(_r)

    # ---- ① 查得到的 SRID：必须无话可说 -----------------------------------
    _p = GeRepository.check_i1(_r, _eff, _lookup)
    check(_p == [], "查得到的 SRID（4548）→ 无问题", f"problems={_p}")

    # ---- ② 查不到的 SRID：必须报，且说清是「编码不存在」不是「不支持」----
    _r_bad = _row(s=999999, p=999999)
    _p_bad = GeRepository.check_i1(_r_bad, GeRepository.effective_crs(_r_bad), _lookup)
    check(len(_p_bad) == 1 and "999999" in _p_bad[0],
          "★ 查不到的 SRID（999999）→ 报出问题、并原样说出那个数",
          f"{_p_bad[:1]}")
    check(_p_bad and "根本不存在" in _p_bad[0],
          "★ 说法区分了「编码不存在」与「投影不支持」",
          "★ 这两件事的处置完全不同：一个是录错了，一个是还没实现")

    # ---- ③ ★★ 三个注释缺了 MUST NOT 报错（I1 原话）---------------------
    #   为什么这不是宽容：UTM 与 Lambert **没有「带号」这个记法**
    #   （实测：8500 个 SRID 只有 5818 个有 +lon_0，UTM 全部没有）。
    #   拿一个不存在的记法去要求它，会把正确的数据判成错的。
    _r_noann = {"section_crs_epsg": 4548, "project_crs_epsg": 4548}
    _eff_noann = GeRepository.effective_crs(_r_noann)
    _p_noann = GeRepository.check_i1(_r_noann, _eff_noann, _lookup)
    check(_p_noann == [],
          "★★ 三个注释全缺 → **不报错**（UTM/Lambert 没有带号记法）",
          f"problems={_p_noann}")

    # ---- ④ 只给一个注释也不许报错（「任一个缺值」）----------------------
    _r_part = {"section_crs_epsg": 4548, "project_crs_epsg": 4548,
               "project_crs_zone_number": 39}
    _p_part = GeRepository.check_i1(_r_part,
                                    GeRepository.effective_crs(_r_part), _lookup)
    check(_p_part == [],
          "★ 只给「带号」一个注释 → 同样**不报错**（任一个缺值都不算错）",
          f"problems={_p_part}")

    # ---- ⑤ 元测试：lookup 反过来，②必须不再报 ------------------------
    _p_flip = GeRepository.check_i1(_r_bad, GeRepository.effective_crs(_r_bad),
                                    lambda s: True)
    check(_p_flip == [],
          "元测试：把 lookup 改成「什么都查得到」，②立刻不报 —— 证明它真在查表",
          "★ 仍报错 = 判定没在看 lookup" if _p_flip else "已静默")

    # ---- ⑥ ★★★ 元测试：两条被否证的写法 MUST NOT 出现在**代码**里 ----------
    #   这是一条**源码级**守卫，因为前面几条只能证明「结果对」，
    #   证明不了「没走那条错路」—— 而那条错路在本题里恰好会算出对的答案
    #   （4545 的 36×3 与真实 lon_0 都是 108）。★ 偶然对上，所以必须查源码。
    #
    #   ★★ 用 AST 取函数体，**不用字符串切分**：
    #      第一版写成 `src.split("def check_i1")[1].split("def stations")[0]`，
    #      而 "def stations" 在 check_i1 之后**只出现一次**、位置在文件更后面，
    #      切出来是个空串 —— 于是「在空集里找不到」被算成了失败。
    #      一个永远为真的断言和一个永远为假的断言一样没用。
    #   ★★ 并且**排除注释**：注释里提到这两条错路是**必要的**
    #      （不写下来，下一个人还会走一遍）。查的是代码，不是文字。
    import ast as _ast  # noqa: E402

    _repo_src = (ROOT / "modules" / "M3-rpdao" / "rpdao" / "repo.py").read_text(
        encoding="utf-8")
    _tree = _ast.parse(_repo_src)
    _fn = None
    for _node in _ast.walk(_tree):
        if isinstance(_node, _ast.FunctionDef) and _node.name == "check_i1":
            _fn = _node
    check(_fn is not None, "元测试前置：在 repo.py 的 AST 里找到了 check_i1",
          "★ 找不到 = 下面两条在对着空气断言" if _fn is None else "已定位")

    _code = ""
    if _fn is not None:
        # 只留**代码**：丢掉 docstring 与一切字符串字面量。
        # ★ ast.unparse 会把注释也丢掉 —— 正是我们要的。
        _body_nodes = [n for n in _fn.body
                       if not (isinstance(n, _ast.Expr)
                               and isinstance(n.value, _ast.Constant)
                               and isinstance(n.value.value, str))]
        _code = "\n".join(_ast.unparse(n) for n in _body_nodes)
        check(len(_code) > 200,
              "元测试前置：取到的函数体不是空的",
              f"取到 {len(_code)} 字符（空的话下面两条等于没测）")

    check("proj4text" not in _code,
          "★★★ check_i1 的**代码**里没有「从 proj4text 抓 +lon_0」这条已否证的写法",
          "（UTM 全部没有 +lon_0，抓不到不等于错）")
    check(not re.search(r"crs_zone_number\s*\*\s*3", _code),
          "★★★ check_i1 的**代码**里没有 `带号 × 3` 这条已否证的写法",
          "★ 那个 3 是硬编码常量，test_sim_track.py 有守卫说带号是配置项")

    # ================================================================ 16) v0.2
    # 005 / T017。这一段的每一条判据，都对应一个**实测抓到的**缺陷。
    print("\n=== 16) 契约 v0.2：投影、类型、向后兼容 ===")

    # --- 16a) 向后兼容：v0.1 声明过的字段，v0.2 里必须一个不少 ----------------
    # ★ 宪法 §I「契约禁止就地改」⇒ v0.2 是新文件。但"新文件"不等于
    #   "可以悄悄丢字段"：老客户端照着 v0.1 写的解析代码必须继续能跑。
    _d01 = yaml.safe_load(GEO_CONTRACT_V01.read_text(encoding="utf-8"))
    _d02 = yaml.safe_load(GEO_CONTRACT.read_text(encoding="utf-8"))

    def _props(doc, path):
        return set(doc["paths"][path]["get"]["responses"]["200"]
                   ["content"]["application/json"]["schema"]["properties"])

    _lost = {}
    for _p in _d01["paths"]:
        _gone = _props(_d01, _p) - _props(_d02, _p)
        if _gone:
            _lost[_p] = sorted(_gone)
    check(not _lost, f"v0.1 声明的 {len(_d01['paths'])} 条路径，字段一个都没丢",
          f"★ 丢了：{_lost}" if _lost else "只增不减")

    # --- 16b) `/sections`：12 个 DAO 内部列一个都不许漏出去 -------------------
    r = c.get("/v1/geometry/sections")
    check(r.status_code == 200, "16 · /sections 可请求", f"HTTP {r.status_code}")
    _items = r.json()["items"]
    _leaked = sorted({k for it in _items for k in it if "_crs_" in k})
    check(not _leaked,
          "★★ /sections 里没有 DAO 的内部列名（`section_crs_*` / `project_crs_*`）",
          f"★ 漏了：{_leaked}" if _leaked else "已按契约投影")

    _need = {"crs_source", "crs_assumed", "crs_zone_number",
             "crs_central_meridian_deg", "crs_epsg"}
    _miss = sorted({k for it in _items for k in _need - set(it)})
    check(not _miss, "★ /sections 每项都带契约声明的 5 个 crs_* 字段",
          f"★ 缺：{_miss}" if _miss else f"共 {len(_items)} 项")
    if _items:
        _it0 = _items[0]
        check(_it0["crs_source"] == "project" and _it0["crs_assumed"] is False,
              "★ 生效基准来自项目级：crs_source=project, crs_assumed=false",
              f"实际 {_it0['crs_source']!r} / {_it0['crs_assumed']!r}")

    # --- 16c) 【核心】`numeric` 不许以字符串出去 -----------------------------
    # ★★★ 这一条守的是一个**实测出来的、平台级的**行为：
    #   同一个 `Decimal`，路由**不写**返回注解时经 `jsonable_encoder` → float；
    #   路由**写了** `-> dict[str, Any]` 时经 pydantic → **字符串**
    #   （pydantic 的 `Decimal` JSON 序列化默认保精度，输出 str）。
    #   005 之前没人看见它 —— 因为 004 时代中央子午线来自 YAML（一个 int）。
    #   改为读库之后，`numeric(9,6)` 的 `Decimal` 第一次走上这条路。
    # ⇒ 断言**响应 JSON 里的类型**，而不是 Python 里的类型：字符串是在
    #   序列化那一步才出现的，在 Python 层看永远是 Decimal，测不出来。
    check(isinstance(_it0["crs_central_meridian_deg"], (int, float))
          and not isinstance(_it0["crs_central_meridian_deg"], bool),
          "★★★ /sections 的 crs_central_meridian_deg 是 JSON 数字，不是字符串",
          f"实际 {_it0['crs_central_meridian_deg']!r} "
          f"（{type(_it0['crs_central_meridian_deg']).__name__}）")

    for _tpl, _url, _q in (("geo", f"/v1/geometry/sections/{sid}/geo", "station_m=2900"),
                           ("reverse", f"/v1/geometry/sections/{sid}/geo/reverse",
                            "lat=25.19&lon=117.36"),
                           ("route", f"/v1/geometry/sections/{sid}/geo/route", "step_m=20"),
                           ("monitor", f"/v1/geometry/sections/{sid}/monitor-sections", "")):
        _rr = c.get(_url + ("?" + _q if _q else ""))
        _j = _rr.json()
        check(_rr.status_code == 200 and isinstance(_j.get("crs_source"), str)
              and "crs_assumed" in _j,
              f"16 · {_tpl} 带 crs_source / crs_assumed",
              f"HTTP {_rr.status_code} source={_j.get('crs_source')!r}")
        check(isinstance(_j.get("central_meridian_deg"), (int, float))
              and not isinstance(_j.get("central_meridian_deg"), bool),
              f"★★ {_tpl} 的 central_meridian_deg 是 JSON 数字",
              f"实际 {_j.get('central_meridian_deg')!r}")

    # --- 16d) 两层守卫：`_num` 是**层内**的，`NoResponseModel` 是**全局**的 -----
    #
    # ★★★★★ 2026-10-13 T045 改写了这一节，原因值得记下来：
    #   原先这里摘掉 `_num`、断言「值**确实**变成字符串」。那是当时的事实 ——
    #   让 `Decimal` 变字符串的**唯一**原因是 `_num` 在转换。
    #   T045 把 `app.router.route_class = NoResponseModel` 挂上之后，
    #   **`_num` 不再是那个原因了**：就算摘掉它，全局那一层照样把值发成数字。
    #   ⇒ 于是这条元测试**红了，而且是真阳性** —— 它守的那个前提已经不存在。
    #
    #   ★ 修法不是"把它改松"，是**把它改成现在的事实**：
    #     ① 摘掉 `_num` ⇒ 仍是数字 —— 这是 T045 的**收益**，值得写成判据；
    #     ② 再单独证明「数字 vs 字符串」确实由 route_class 决定（元测试）。
    #   ①② 合起来才说得清「现在到底靠哪一层」。只留①会变成一句没人验的观察。
    _keep = APP._num
    try:
        APP._num = lambda v: v          # 还原成"不转换"
        _naked = c.get("/v1/geometry/sections").json()["items"][0]["crs_central_meridian_deg"]
    finally:
        APP._num = _keep
    check(isinstance(_naked, (int, float)) and not isinstance(_naked, bool),
          "★★★ 摘掉 `_num` 之后，值**仍然是数字** —— 全局的 route_class 接住了",
          f"实际 {_naked!r}（{type(_naked).__name__}）—— 若变回字符串，说明全局那层失效了")

    # ② 元测试：现场搭两个 mini app，同一条 `-> dict[str, Any]` 路由返回同一个
    #    `Decimal`，只差挂不挂 `NoResponseModel`。
    #    ★ 这才证明「数字 vs 字符串」是由 route_class 决定的 —— 而不是碰巧。
    from decimal import Decimal  # noqa: E402
    from typing import Any  # noqa: E402

    from fastapi import FastAPI  # noqa: E402
    from jsonnum import NoResponseModel  # noqa: E402

    def _mini(guarded: bool):
        _a = FastAPI()
        if guarded:
            _a.router.route_class = NoResponseModel

        @_a.get("/m")
        def _h() -> dict[str, Any]:
            return {"central_meridian_deg": Decimal("117.000000")}
        return _a

    import json as _json  # noqa: E402
    _g = _json.loads(TestClient(_mini(True)).get("/m").text)["central_meridian_deg"]
    _p = _json.loads(TestClient(_mini(False)).get("/m").text)["central_meridian_deg"]
    check(isinstance(_g, (int, float)) and isinstance(_p, str),
          "★★★ 元测试：同一条路由，挂 route_class → 数字；不挂 → 字符串",
          f"挂了={_g!r}（{type(_g).__name__}） 不挂={_p!r}（{type(_p).__name__}）")

    # ================================================================ 17) T013/T014/T018
    # ★ 上一段（§16）测的是「形状对不对」，这一段测的是「行为对不对」——
    #   三个判据分别对应 SC-002 / SC-009 / FR-003，全部**带对照**。
    print("\n=== 17) 两个基准共存（SC-002）/ 系统层报错（SC-009）/ 有类型的「没有」（FR-003）===")

    # ---- 17a) SC-002：经度之差 MUST 等于中央子午线之差 ---------------------
    # ★★ SC-002 是一条**对照判据**，不是「两条都算出来了」。
    #    它要的是：两个**设计坐标逐位相同**的点，分属两条中央子午线，
    #    经度必须不同，且差值**恰好等于**两条中央子午线之差（容差 1e-9 度）。
    #    ★ 这个等式真的能成立，因为高斯-克吕格的经差只由 (N, E) 决定，
    #      与 λ0 无关：λ = λ0 + f(N, E)。所以它是一条**闭合**判据 ——
    #      不需要外部真值，也不需要数据库。
    _N_, _E_ = 2786941.4940090827, 536050.8231566962   # 实测路段 6 上的一个点
    _lon117 = GEO.inverse(_N_, _E_, 117.0)[1]
    _lon108 = GEO.inverse(_N_, _E_, 108.0)[1]
    check(abs((_lon108 - _lon117) - (108.0 - 117.0)) < 1e-9,
          "★★ SC-002 · 同一设计坐标在两个基准下，经度之差 = 中央子午线之差（1e-9°）",
          f"实测 {_lon108 - _lon117:.12f}°，应为 -9.0°")
    # ★★ 对照组：把基准改回「全平台一份」（两条都用 117），两个经度**必须变回相同**。
    #    没有这一步，SC-001/002 可以被「两边其实用了同一个基准」通过 ——
    #    而那正是 004 的老样子。
    _lon117b = GEO.inverse(_N_, _E_, 117.0)[1]
    check(_lon117b == _lon117 and abs(_lon108 - _lon117) > 8.9,
          "★★ SC-002 对照组 · 基准退回全平台一份时，两边经度变回相同",
          f"同基准 {_lon117b!r} == {_lon117!r}；差 {abs(_lon108 - _lon117):.4f}°")

    # ---- 17b) T013：第二个基准真的走得通整条路（load_crs → forward）--------
    # ★ 上面那一小段只用了 `inverse`。这里把**另一条路**也走一遍：
    #   造第二个项目（36 带 / 中央子午线 108° / EPSG 4545），用同一份线元，
    #   断言桩号换算出来的带号与中央子午线就是那个项目的。
    _DOC_36 = {"geographic_frame": {
        "ready": True, "ellipsoid": "CGCS2000", "projection": "高斯-克吕格",
        "zone_width_deg": 3, "zone_number": 36, "central_meridian_deg": 108,
        "assumed": False, "epsg_after_confirm": 4545,
          "projection_family": "tmerc",
        "evidence": ["T013 造的第二个项目：36 带，中央子午线 108°"],
    }}
    _gf36 = GEO.load_crs(_DOC_36, origin="T013 造的第二个项目")
    check(_gf36["zone_number"] == 36 and _gf36["central_meridian_deg"] == 108.0,
          "17b · 第二个项目的基准文档读得进来（zone 36 / CM 108）",
          f"zone={_gf36['zone_number']} cm={_gf36['central_meridian_deg']}")
    _els = elements()
    _p39 = GEO.station_to_lonlat(_els, 2900.0, gf())
    _p36 = GEO.station_to_lonlat(_els, 2900.0, _gf36)
    check(_p39["zone_number"] == 39 and _p36["zone_number"] == 36,
          "★ 17b · 同一份线元，两个基准各自报出自己的带号",
          f"39 带给 {_p39['zone_number']}；36 带给 {_p36['zone_number']}")
    check(abs((_p36["lon_deg"] - _p39["lon_deg"]) - (108.0 - 117.0)) < 1e-9,
          "★★ 17b · 两个项目共存时，同一桩号的经度差仍 = 中央子午线之差",
          f"实测 {_p36['lon_deg'] - _p39['lon_deg']:.12f}°")
    check(_p39["lat_deg"] == _p36["lat_deg"] and _p39["north_m"] == _p36["north_m"],
          "★ 17b · 北坐标不受中央子午线影响（差值只出在经度上）",
          f"lat {_p39['lat_deg']!r} / north {_p39['north_m']!r}")

    # ---- 17c) T014 + SC-009：两级不等 MUST 报错，且说出两级各是什么 --------
    # ★★ §14 已经在**纯函数**层测过这一条。这里测的是**系统层** ——
    #    SC-009 说的是「系统 MUST 报错」，一个只在单元测试里抛的异常
    #    可能被某个 except 吞掉，而用户看到的是一个静默回退的坐标。
    _orig_dao = APP.dao
    try:
        APP.dao = _FakeDao(crs={"project_crs_epsg": 4548,
                                "project_crs_zone_number": 39,
                                "project_crs_central_meridian_deg": Decimal("117.000000"),
                                "section_crs_epsg": 4545,
                                "section_crs_zone_number": 36,
                                "section_crs_central_meridian_deg": Decimal("108.000000")})
        _r = c.get(f"/v1/geometry/sections/{sid}/geo", params={"station_m": 2900.0})
        check(_r.status_code == 503,
              "★★ 17c · 两级基准不一致 → HTTP 503（不是 200、不是 400）",
              f"HTTP {_r.status_code}：{str(_r.text)[:90]}")
        _d = str(_r.json().get("detail", ""))
        check("4548" in _d and "4545" in _d,
              "★★ 17c · 错误信息说出了两级**各自的取值**（SC-009）",
              f"原话：{_d[:130]}")

        # ★ 应通过的一侧（宪法 §I：「契约测试必须覆盖应通过／应拒绝两侧」）：
        #   把两级改成相等，同一个请求必须回到 200。
        APP.dao = _FakeDao(crs={"project_crs_epsg": 4548,
                                "section_crs_epsg": 4548,
                                "section_crs_zone_number": 39,
                                "section_crs_central_meridian_deg": Decimal("117.000000")})
        _r2 = c.get(f"/v1/geometry/sections/{sid}/geo", params={"station_m": 2900.0})
        # ⚠ 这里是 `epsg` / `zone_number`，**不是** `crs_epsg` ——
        #   四个 `crs_*` 前缀字段是 `/sections` 的（那是把 DAO 的内部列名
        #   投影成契约名）；单主体路径上的基准字段仍叫 `epsg` / `zone_number`。
        check(_r2.status_code == 200 and _r2.json().get("epsg") == 4548
              and _r2.json().get("crs_source") == "project",
              "★ 17c 应通过侧 · 两级相等 → 200，并入取到 epsg=4548",
              f"HTTP {_r2.status_code} epsg={_r2.json().get('epsg')!r}")

        # ---- 17d) T018 + FR-003 / E5：有类型的「没有」 -------------------
        # ★ 真实用例：路段 1「滨海大通道试验段」，design_project_id 为 NULL，
        #   自身也没配。这是**平台里真实存在的数据**，不是造的。
        _sec1 = {**FIX["road_section"][0], "id": 1, "design_project_id": None,
                 "section_name": "滨海大通道试验段",
                 **{k: None for k in _CRS_FILL}}
        APP.dao = _FakeDao(row=_sec1)
        _r3 = c.get("/v1/geometry/sections/1/geo", params={"station_m": 5000.0})
        check(_r3.status_code == 503, "★★ 17d · 路段 1 两级都没配 → 503（E5 的「有类型的没有」）",
              f"HTTP {_r3.status_code}：{str(_r3.text)[:90]}")
        _body3 = _r3.json()
        _banned = {"lat_deg", "lon_deg", "north_m", "east_m",
                   "zone_number", "central_meridian_deg", "epsg", "crs_epsg"}
        check(not (_banned & set(_body3)),
              "★★ 17d · 响应里 MUST NOT 出现任何经纬度/平面坐标字段",
              f"★ 出现了：{sorted(_banned & set(_body3))}")
        _d3 = str(_body3.get("detail", ""))
        check("路段 1" in _d3 and "没有所属项目" in _d3,
              "★★ 17d · 那句话**点名了是哪条路段**、并说清是「没有所属项目」",
              f"原话：{_d3[:140]}")
        # ★★ MUST NOT 回落到 39 带（FR-003 / E5、SC-007）。
        #    这里断的是**信息层面**：那个响应里不该出现任何带号或 EPSG 取值。
        #    它是一条弱判据（字符串比对），所以下面 17e 还配了行为判据。
        _leak = [v for v in ("4548", "4545", "117", "36", "39") if v in _d3]
        check(not _leak,
              "★★ 17d · 那句解释里没有夹带任何带号 / EPSG 取值（不回落）",
              f"★ 夹带了：{_leak}" if _leak else "干净")

        # ---- 17e) 元测试：给它配上一个基准，同一个请求必须变得可用 --------
        # ★★ 这是 17d 的**对照**。没有它，17d 可以通过，而真实原因是
        #    「路段 1 恰好没有线元」或「这个假仓储根本不服务路段 1」——
        #    那样测到的就不是「没有基准」这件事。
        _sec1_ok = {**_sec1,
                    "project_crs_epsg": 4548, "project_crs_zone_number": 39,
                    "project_crs_central_meridian_deg": Decimal("117.000000"),
                    "project_crs_zone_width_deg": Decimal("3.00"),
                    "project_crs_assumed": False,
                    "project_crs_evidence": "T018 元测试补挂的"}
        APP.dao = _FakeDao(row=_sec1_ok)
        _r4 = c.get("/v1/geometry/sections/1/geo", params={"station_m": 5000.0})
        check(_r4.status_code == 200 and _r4.json().get("crs_source") == "project",
              "★★★ 17e 元测试 · 给路段 1 挂上项目级基准后，同一个请求变成 200",
              f"HTTP {_r4.status_code}：{str(_r4.text)[:90]}")
        check(_r4.json().get("lat_deg") is not None,
              "★ 17e · 而且它真的算出了经纬度（不是「返回 200 但什么也没有」）",
              f"lat_deg={_r4.json().get('lat_deg')!r}")
        if _r4.status_code == 200:
            check(_r4.json().get("zone_number") == 39,
                  "★ 17e · 挂上的是 39 带，它就报 39 —— 没有偷偷用别的默认值",
                  f"zone_number={_r4.json().get('zone_number')!r}")

        # ---- 17f) 「不存在」与「没有基准」MUST 是两个不同的答复 ----------
        # ★★ 404 = 这个主体不存在；503 = 主体在，但它没有基准。
        #    两者合并的话，运维会照着提示去**改一个不存在的路段**。
        #    实测线上：路段 9 → 404，路段 1 → 503。
        APP.dao = _FakeDao()
        _r5 = c.get("/v1/geometry/sections/9/geo", params={"station_m": 100.0})
        check(_r5.status_code == 404,
              "★★ 17f · 不存在的路段 → 404，与「没有基准」的 503 区分开",
              f"HTTP {_r5.status_code}：{str(_r5.text)[:70]}")
        check(_r5.status_code != _r3.status_code,
              "★★ 17f · 这两个码**确实不同**（同一段代码里两个 if 挨着写，很容易塌成一个）",
              f"不存在={_r5.status_code} vs 没基准={_r3.status_code}")
    finally:
        APP.dao = _orig_dao

    # ═══════════════════════════════════════════════════════════ 18) US2 / US3
    #   T020 / T021 / T024 / T025。三条判据一次做完：通用化了，老路没动。
    print("\n=== 18) US2/US3：老路没动 · 两级一致 · 五个消费者 · 文件跟着库走 ===")

    import importlib.util as _ilu  # noqa: E402
    import subprocess as _sp  # noqa: E402

    # ---- 18a) T020 / SC-007：004 精度不退化，判据是**浮点逐位相同** --------
    # ★ FR-007 的原文就是「判据 = 浮点逐位相同」，不是「误差在容差内」。
    #   所以这里用 `==` 和 `repr()` 比，**一次 `abs() < tol` 都不许出现** ——
    #   容差会把「动了」和「没动」变成同一个答案。
    #
    # ★ 怎么才算证明了「没动」？两条独立造出来的基准文档：
    #     · 文件版：crs.yaml 解析出来（004 一直用的那条路）
    #     · 库版  ：按 psycopg 从 numeric(9,6) 读出来的**类型和精度**手搓
    #   同一份线元链、同一批桩号，两边的每一个数**逐位相同** ——
    #   这才说明「把权威从文件换成库」这件事本身没有挪动任何一个比特。
    _lib18 = _ilu.spec_from_file_location("_evm18", ROOT / "simulator" / "crg" / "export_viewer_mesh.py")
    _evm18 = _ilu.module_from_spec(_lib18)
    _lib18.loader.exec_module(_evm18)

    _gf_file = gf()
    _gf_db = GEO.load_crs({
        "geographic_frame": {
            "ready": True, "ellipsoid": "CGCS2000", "projection": "高斯-克吕格",
            "zone_width_deg": Decimal("3.00"), "zone_number": 39,
            "central_meridian_deg": Decimal("117.000000"),
            "assumed": False, "epsg_after_confirm": 4548,
            "projection_family": "tmerc",
        }}, origin="18a 造出来的库版基准（psycopg 会给出的类型）")

    # ★★ 先说清**哪一半老路根本不经过基准** —— 这是 SC-007 最强的一条论据，
    #    比任何数值比对都硬：`point_north_east` 的签名里**没有 gf**。
    #    北/东就是设计坐标本身，004 那条几何路径压根不做投影，所以
    #    「把权威从文件换成库」在结构上**碰不到它**。
    #    ★ 一条只靠比数值的判据，会在某天有人「顺手」给这个函数加上 gf 时
    #      继续是绿的（因为此刻两者相等）—— 断言签名才挡得住那次改动。
    import inspect as _insp  # noqa: E402
    _sig_pne = _insp.signature(GEO.point_north_east)
    check(len(_sig_pne.parameters) == 2 and "gf" not in _sig_pne.parameters,
          "★★ 18a · 设计帧的函数签名里**没有基准参数** —— "
          "004 的几何路径结构上不经过投影，基准换源碰不到它",
          f"参数 {list(_sig_pne.parameters)}")

    _els18 = elements()
    _st18 = [0.0, 137.5, 900.0, 2900.0, 4200.0, 5805.421]
    _ll_diff = 0
    for _s in _st18:
        _la = GEO.station_to_lonlat(_els18, _s, _gf_file)
        _lb = GEO.station_to_lonlat(_els18, _s, _gf_db)
        _ll_diff += (repr(_la["lat_deg"]) != repr(_lb["lat_deg"])) + \
                    (repr(_la["lon_deg"]) != repr(_lb["lon_deg"]))
    check(_ll_diff == 0,
          "★★ 18a · T020/SC-007：库版与文件版基准下，6 个桩号的经纬度**逐位相同**",
          f"差 {_ll_diff} 处")

    # ★ 整条路线 + bbox 一起比 —— 判据是 `repr()` 相等，不是「四个数接近」。
    #   T023 要核的正是这三个量（往返两位小数、桩号差、bbox 四个数）。
    _ra18 = GEO.route_lonlat(_els18, _gf_file)
    _rb18 = GEO.route_lonlat(_els18, _gf_db)
    check(repr(_ra18) == repr(_rb18) and (_ra18.get("point_count") or 0) > 300,
          "★★ 18a · T020/T023：整条路线折线 + bbox **整份 repr 相同**"
          "（bbox 四个数也随之逐位相同）",
          # ★ 这个 detail 我第一版写成 `len(_ra18.get('points'))` —— `route_lonlat`
          #   返回的键是 `point_count`，没有 `points`，于是它就印了个「点数 0」。
          #   判据本身没错（比的是整份 dict），错的是这句话**看着像判据空了**。
          #   ⇒ 顺带把「点数必须够多」也并进判据：空折线的 repr 当然相等。
          f"点数 {_ra18.get('point_count')}")

    # 与 §17a 的实测冻结值对一遍 —— 守的是「今天算出来的还是那天那个数」
    _n18, _e18 = 2786941.4940090827, 536050.8231566962
    _p18 = GEO.point_north_east(_els18, 2900.0)
    check(_p18[0] == _n18 and _p18[1] == _e18,
          "★★ 18a · T020：桩号 2900 的北/东与**实测冻结值**逐位相同（`==`，非容差）",
          f"实得 {_p18[0]!r} / {_p18[1]!r}")

    # ★ 往返闭合：**不是 0**，实测 0.051 mm。
    #   我第一版把判据写成「要的是 0」—— 那是我自己想象的精度，不是 004 的。
    #   004 记的是「往返闭合 ≤ 0.457 mm」，而 `inverse` 走的是子午线弧长级数 +
    #   迭代反算，所以它本来就不可能逐位回到原值。
    #   ⇒ 判据锚在 **004 那行已记录的界**上，并把实测值一起印出来 ——
    #     将来有人把它改坏了，红的那一行会同时给出「实测多少」和「界是多少」。
    _rt18 = GEO.inverse(_n18, _e18, 117.0)
    _fw18 = GEO.forward(_rt18[0], _rt18[1], 117.0)
    _dn18, _de18 = abs(_fw18[0] - _n18), abs(_fw18[1] - _e18)
    check(_dn18 < 0.457e-3 and _de18 < 0.457e-3,
          "★★ 18a · 往返闭合在 **004 记录的界（0.457 mm）**之内"
          "—— ★ 不是「等于 0」，那条级数反算做不到",
          f"实测 Δ北={_dn18 * 1000:.4f} mm、Δ东={_de18 * 1000:.4f} mm")
    check(_dn18 > 0 or _de18 > 0,
          "★★ 18a · 并如实记下「它**不**是逐位闭合的」—— "
          "若哪天变成 0，说明有人把反算换成了查表，那条要重新审",
          f"Δ北={_dn18!r}")

    # ---- 18b) T021 / data-model E2 第 1 态：两级都填、值相等 ---------------
    # ★ 这一条要证明的是「路段级填了相同的值，也**不改变**权威来源是项目级」——
    #   即路段级是**冗余校验**，不是覆盖。所以判据不只是「200」，
    #   而是 `crs_source` 必须仍是 `"project"`。
    _orig_dao18 = APP.dao
    try:
        _both18 = {**section_row(), "id": 6, "design_project_id": 2,
                   "project_crs_epsg": 4548, "project_crs_zone_number": 39,
                   "project_crs_central_meridian_deg": Decimal("117.000000"),
                   "project_crs_zone_width_deg": Decimal("3.00"),
                   "project_crs_assumed": False,
                   "project_crs_evidence": "项目级依据",
                   "section_crs_epsg": 4548, "section_crs_zone_number": 39,
                   "section_crs_central_meridian_deg": Decimal("117.000000"),
                   "section_crs_zone_width_deg": Decimal("3.00"),
                   "section_crs_assumed": False,
                   "section_crs_evidence": "路段级依据（与项目同值）"}
        APP.dao = _FakeDao(row=_both18)
        _r18 = c.get("/v1/geometry/sections/6/geo", params={"station_m": 2900.0})
        _j18 = _r18.json() if _r18.status_code == 200 else {}
        check(_r18.status_code == 200,
              "★★ 18b · T021/E2态1：两级都填且相等 → 通过（不是「不一致」那条路）",
              f"HTTP {_r18.status_code}：{str(_r18.text)[:70]}")
        check(_j18.get("crs_source") == "project",
              "★★ 18b · 但 `crs_source` **仍是 `project`** —— "
              "路段级填同值不夺权威，它是冗余校验",
              f"实得 {_j18.get('crs_source')!r}")
        check(_j18.get("epsg") == 4548 and _j18.get("zone_number") == 39,
              "★★ 18b · 取到的值就是那一个（两级同值，取谁都是它）",
              f"epsg={_j18.get('epsg')} zone={_j18.get('zone_number')}")

        # 反面对照：**只有路段级有值**，此时权威必须变成 section。
        # ★ 没有这个对照，「project」可能只是一个被写死的默认值 ——
        #   而那会让上面那条判据在**任何**实现下都成立。
        #
        # ⚠⚠ `_CRS_FILL` 的默认值里 **`project_crs_epsg` 是 4548**（不是 None）：
        #    那是 §17「应通过侧」要的形态。所以这里必须**显式**把项目级清空，
        #    不能指望「不写就是没配」。
        #    ★ 第一版就是栽在这上面：我漏写了 project_* 六个键，于是这一格
        #      悄悄变成「项目 4548 / 路段 4545」—— **两级不一致**，
        #      守卫正确地抛了 503。判据红了，而它红得有道理：
        #      真要测的是「项目没配」，我却造了个「两级打架」。
        #    ⇒ 夹具的默认值是**有含义的**，写反面用例时必须逐个键压实。
        _only18 = {**section_row(), "id": 6, "design_project_id": 2,
                   "project_crs_epsg": None, "project_crs_zone_number": None,
                   "project_crs_central_meridian_deg": None,
                   "project_crs_zone_width_deg": None,
                   "project_crs_assumed": None, "project_crs_evidence": None,
                   "section_crs_epsg": 4545, "section_crs_zone_number": 36,
                   "section_crs_central_meridian_deg": Decimal("108.000000"),
                   "section_crs_zone_width_deg": Decimal("3.00"),
                   "section_crs_assumed": False,
                   "section_crs_evidence": "仅路段级"}
        APP.dao = _FakeDao(row=_only18)
        _r18b = c.get("/v1/geometry/sections/6/geo", params={"station_m": 2900.0})
        _j18b = _r18b.json() if _r18b.status_code == 200 else {}
        check(_r18b.status_code == 200 and _j18b.get("crs_source") == "section",
              "★★ 18b · 对照组：项目级没填、只有路段级 → 权威变成 `section`"
              "（证明 `project` 不是写死的默认）",
              f"HTTP {_r18b.status_code} source={_j18b.get('crs_source')!r}")
        check(_j18b.get("epsg") == 4545,
              "★★ 18b · 对照组取到的确实是路段级那个 EPSG（4545，不是项目的 4548）",
              f"实得 {_j18b.get('epsg')!r}")
    finally:
        APP.dao = _orig_dao18

    # ---- 18c) T024 / SC-011：五个消费者一个都没坏，且两个离线消费者**仍不连库**
    _evm_file = ROOT / "simulator" / "crg" / "export_viewer_mesh.py"
    _sim_mjs = ROOT / "tests" / "integration" / "sim_inline_script.mjs"
    _m9_app = ROOT / "modules" / "M9-console" / "app.py"
    _probe = ROOT / "tests" / "e2e" / "quickstart_probe.py"

    _flags18 = _evm18._read_crs_flags(CRS_PATH)
    check(_flags18 == (True, False),
          "★★ 18c · T024/SC-011：`export_viewer_mesh._read_crs_flags` **真函数**"
          "读真文件 → (ready, assumed)",
          f"实得 {_flags18!r}")

    # node 侧：把 `.mjs` 里那个真实的 `parseCrsYaml` 抠出来，用真 node 跑真文件。
    # ★ 抄一份逻辑进测试是不行的 —— 抄的那份会与页面用的那份各自演进而无人察觉。
    _mjs_txt = _sim_mjs.read_text(encoding="utf-8")
    _h = _mjs_txt.find("function parseCrsYaml(")
    _fn = ""
    if _h >= 0:
        _depth, _i = 0, _mjs_txt.find("{", _h)
        for _j in range(_i, len(_mjs_txt)):
            if _mjs_txt[_j] == "{":
                _depth += 1
            elif _mjs_txt[_j] == "}":
                _depth -= 1
                if _depth == 0:
                    _fn = _mjs_txt[_h:_j + 1]
                    break
    check(bool(_fn) and len(_fn) > 200,
          "★★ 18c · 从 .mjs 里抠出了 `parseCrsYaml` 的源码（抠不到就没法测真函数）",
          f"取到 {len(_fn)} 字节")

    _node18 = ("const fs=require('fs');"
               + _fn
               + "const d=parseCrsYaml(fs.readFileSync(process.argv[1],'utf8'));"
               + "const g=d.geographic_frame||{};"
               + "console.log(JSON.stringify({ready:g.ready,assumed:g.assumed,"
                 "zone:g.zone_number,cm:g.central_meridian_deg}));")
    _p18 = _sp.run(["node", "-e", _node18, str(CRS_PATH)],
                   capture_output=True, text=True, timeout=60)
    _ok18 = False
    _det18 = _p18.stderr.strip()[:120]
    if _p18.returncode == 0:
        import json as _json18  # noqa: E402
        _nd = _json18.loads(_p18.stdout.strip())
        _ok18 = _nd.get("ready") is True and _nd.get("assumed") is False \
            and _nd.get("zone") == 39
        _det18 = str(_nd)
    check(_ok18,
          "★★ 18c · node 侧真 `parseCrsYaml` 读真文件也拿得到 ready/assumed/zone",
          _det18)

    # ★★ 扫「有没有连库」不能用 `in` 扫全文 —— 我在 §15 已经栽过一次同类的跟头：
    #    散文里写着「M9 没有任何 PG_DSN」，`"PG_DSN" in 源码` 就命中了**那句话本身**，
    #    于是一条**守护得正好的**硬线被判成了违规。
    #    ⇒ 用 AST 只看**代码**：`Name` 节点、`import`、以及**不是 docstring 的字符串字面量**
    #      （`os.environ["PG_DSN"]` 的键是字符串字面量，所以字符串不能一律跳过）。
    #      注释与 docstring 由 AST 天然排除。
    def _db_usage_py(path, names):
        _t = path.read_text(encoding="utf-8")
        _tree = _ast18.parse(_t)
        _docs = set()
        for _n in _ast18.walk(_tree):
            if isinstance(_n, (_ast18.Module, _ast18.FunctionDef,
                               _ast18.AsyncFunctionDef, _ast18.ClassDef)):
                _b = getattr(_n, "body", [])
                if _b and isinstance(_b[0], _ast18.Expr) and \
                        isinstance(_b[0].value, _ast18.Constant) and \
                        isinstance(_b[0].value.value, str):
                    _docs.add(id(_b[0].value))
        _hits = set()
        for _n in _ast18.walk(_tree):
            if isinstance(_n, _ast18.Name) and _n.id in names:
                _hits.add(_n.id)
            elif isinstance(_n, (_ast18.Import, _ast18.ImportFrom)):
                _mod = getattr(_n, "module", "") or ""
                for _a in _n.names:
                    if "psycopg" in (_a.name + _mod):
                        _hits.add("psycopg")
            elif isinstance(_n, _ast18.Constant) and isinstance(_n.value, str) \
                    and id(_n) not in _docs:
                for _k in names:
                    if _k in _n.value:
                        _hits.add(_k + "（字符串字面量里）")
        return sorted(_hits)

    import ast as _ast18  # noqa: E402

    for _p, _n in ((_evm_file, "export_viewer_mesh.py"),
                   (_m9_app, "M9 app.py")):
        _leak = _db_usage_py(_p, ("PG_DSN", "psycopg", "rpdao"))
        check(not _leak,
              f"★★ 18c · {_n} **不连库**（AST 判定，散文里的「没有 PG_DSN」不算命中）",
              f"出现 {_leak}" if _leak else "零命中")
        check("crs.yaml" in _p.read_text(encoding="utf-8"),
              f"★★ 18c · {_n} 仍指向**文件**（T029：一个消费者都不许改）")

    _mjs_txt_probe = _sim_mjs.read_text(encoding="utf-8")
    _mjs_leak = [k for k in ("psycopg", "PG_DSN", "rpdao") if k in _mjs_txt_probe]
    check(not _mjs_leak,
          "★★ 18c · sim_inline_script.mjs **不连库**（JS 无 AST 通道，用子串扫）",
          f"出现 {_mjs_leak}" if _mjs_leak else "零命中")
    check("crs.yaml" in _mjs_txt_probe,
          "★★ 18c · sim_inline_script.mjs 仍指向**文件**")

    check(not _db_usage_py(_m9_app, ("PG_DSN", "psycopg")),
          "★★ 18c · M9 `app.py` 里没有 psycopg / PG_DSN —— "
          "所以 T027「让 M9 比文件与库」按字面写不成，只能经 /gw 拿值")

    # ---- 18d) T025 / SC-012：文件与库一致 + 元测试 -----------------------
    sys.path.insert(0, str(ROOT / "modules" / "M9-console"))
    import crs_drift as _drift18  # noqa: E402

    _doc18 = crs_doc()
    _db18 = {"epsg": 4548, "zone_number": 39,
             "central_meridian_deg": Decimal("117.000000"),
             "assumed": False,
             # ★ evidence 用**真文件的 8 条**，并按种子的落盘形态加上 `N. ` 编号。
             #   我第一版在这里手搓了 3 条假依据 —— 于是「一致」那条判据当场红了
             #   （库里 3 条 vs 文件 8 条）。★ 那不是被测对象有问题，是**夹具在说谎**：
             #   它声称代表库，而库里从来不是那 3 条。
             #   ⇒ 夹具要么是实测来的，要么是把真实形态**显式地**重建出来。
             #     这里选后者：种子写的就是「文件那 8 条 + 行首编号」，
             #     重建它顺便验了 `parse_evidence` 两侧剥编号是对称的。
             #   ⚠ 真·文件 vs **真库** 的那条绿色判据在 T026 的
             #     `tools/export_crs_yaml.py --check`（连库跑，实测已绿）。
             #     本判据在离线状态下能守住的是「比对逻辑本身」。
             "evidence": "\n".join(
                 f"{_i}. {_t}" for _i, _t in
                 enumerate(_doc18["geographic_frame"]["evidence"], 1))}
    _doc18 = crs_doc()
    _mis18 = _drift18.compare(_doc18, _db18)
    check(not _mis18,
          "★★ 18d · T025/SC-012：真 crs.yaml 与库取值**一致**",
          f"{len(_mis18)} 处不一致：{_mis18[:2]}" if _mis18 else "零处")

    # 元测试①：把**文件**里的 assumed 翻掉 → 检查必须变红，且说得出两边各是什么
    import copy as _copy18  # noqa: E402
    _bad18 = _copy18.deepcopy(_doc18)
    _bad18["geographic_frame"]["assumed"] = True
    _m1_18 = _drift18.compare(_bad18, _db18)
    check(bool(_m1_18),
          "★★ 18d · ★元测试①：改**文件**一处（assumed）→ 检查真的变红",
          f"{_m1_18[:1]}" if _m1_18 else "★ 仍然是绿的 —— 这条检查不会失败")
    _m1_txt = " ".join(_m1_18)
    check("assumed" in _m1_txt and "True" in _m1_txt and "False" in _m1_txt,
          "★★ 18d · 而且它**说得出哪个字段、库里是什么、文件里是什么**"
          "（只说「不一致」，接手的人还得自己翻两份）",
          _m1_txt[:110])

    # 元测试②：只改**条数**（多条少条都试）—— 判据比的是集合，不是前缀
    for _n_ev, _label in ((7, "少一条"), (9, "多一条")):
        _bad18b = _copy18.deepcopy(_doc18)
        _ev = list(_bad18b["geographic_frame"]["evidence"])
        _bad18b["geographic_frame"]["evidence"] = (_ev[:_n_ev] if _n_ev < len(_ev)
                                                  else _ev + ["凭空多出来的一条"])
        _m2_18 = _drift18.compare(_bad18b, _db18)
        check(bool(_m2_18) and "evidence" in " ".join(_m2_18),
              f"★★ 18d · ★元测试②：依据{_label} → 变红且点名 evidence"
              "（种子里写死了「一条不多一条不少」，这里就是它的执行者）",
              (" ".join(_m2_18))[:110])

    # 元测试③：**库**那边动 → 同样必须红。只盯一侧的检查会漏掉一半的漂移方向。
    _db18b = {**_db18, "epsg": 4545, "zone_number": 36,
              "central_meridian_deg": Decimal("108.000000")}
    _m3_18 = _drift18.compare(_doc18, _db18b)
    check(bool(_m3_18) and "epsg" in " ".join(_m3_18),
          "★★ 18d · ★元测试③：改**库**一侧 → 同样变红并点名 epsg"
          "（只测「文件错了」会漏掉「库变了文件没跟上」，而那才是最常见的方向）",
          (" ".join(_m3_18))[:110])

    _missing18 = _drift18.compare({}, _db18)
    check(bool(_missing18),
          "★★ 18d · 文件整份读不到（空文档）→ 也必须红，不许静默通过",
          f"{len(_missing18)} 处")

    # ---- 18e) 三态：「没得比」必须与「一致」和「不一致」都分开 -----------
    # ★★ 实测发现：M6 的两条 HTTP 通路**都不带 `crs_evidence`**
    #    （它在 M6 那边被 `_CRS_ROW_KEYS` 剥掉了）。而 M9 只能经契约通道取值。
    #    于是「evidence」这一项在 M9 那条路上**永远没得比**。
    #    ⇒ 把「通道不提供」当成不一致 → 那条巡检永远红；
    #      当成一致 → 等于宣称「依据也核对过了」，而**根本没看**。
    #    两条都不行，所以必须有第三态，而且第三态必须**显式报出来**。
    _no_ev18 = {"epsg": 4548, "zone_number": 39,
                "central_meridian_deg": Decimal("117.000000"),
                "assumed": False}          # ← 故意不放 evidence 键
    _mis_e, _skip_e = _drift18.compare_detailed(_doc18, _no_ev18)
    check(not _mis_e and _skip_e == ["evidence"],
          "★★ 18e · 源侧**没有** evidence 这个键 → 不报不一致，但记进「未比对」",
          f"不一致 {_mis_e}；未比对 {_skip_e}")

    # ★★★ 这一条是**防滥用**：没有它，「跳过」会变成一个能吞掉一切的口子 ——
    #     将来 M6 少给任何一个字段，巡检都会由红转绿而没人察觉。
    #     所以边界必须钉死：**键在、值为空** ≠ 「没得比」，那是一条真不一致。
    _null_ev18 = {**_no_ev18, "evidence": None}
    _mis_n, _skip_n = _drift18.compare_detailed(_doc18, _null_ev18)
    check(bool(_mis_n) and not _skip_n,
          "★★★ 18e · ★元测试（防滥用）：键**在**而值是 None → 是**真不一致**，"
          "不是「没得比」（空值 ≠ 没去看）",
          f"不一致 {len(_mis_n)} 条；未比对 {_skip_n}")

    check((not _mis_e) and bool(_skip_e),
          "★★ 18e · 这组输入**就是**「只判 `not mismatches` 会误报成一致」的那种："
          "不一致为空，但有未比对项 —— 两者必须都空才算看过",
          f"不一致 {len(_mis_e)} 条、未比对 {_skip_e}")
    _rep18 = _drift18.describe(_mis_e, source="M6 契约值", skipped=_skip_e)
    check("未比对" in _rep18 and "evidence" in _rep18,
          "★★ 18e · 报告里**写得出**「未比对 evidence」，而且说清是通道的局限",
          _rep18.splitlines()[-2][:100] if len(_rep18.splitlines()) > 1 else _rep18[:100])
    check("不是「一致」" in _rep18 or "没得比" in _rep18,
          "★★ 18e · 报告**不许**把「未比对」说成「一致」",
          "")

    # 而真源（导出器走 DAO，拿得到 evidence）→ skipped 必须是空的。
    # ★ 没有这一条，上面那些「跳过」的判据可能在任何输入下都成立。
    _mis_f, _skip_f = _drift18.compare_detailed(_doc18, _db18)
    check(not _skip_f,
          "★★ 18e · 源侧**有** evidence（导出器走 DAO 的那条路）→ 未比对为空，"
          "于是五项**全部**真的看过了",
          f"未比对 {_skip_f}")

    # ---- 18f) M9 镜像里必须有目录里的每个 .py/.html ----------------------
    # ★ 这条判据**不在本文件里**，归属地是 `tests/contract/test_console.py`
    #   （M9 镜像清单那一段）。同一条性质不许有两份定义 —— 两份会各自漂。
    #   005 期间就是它抓到了 `crs_drift.py` 没进镜像（`/v1/crs/drift` 500）。
    #   实测取证留在 quickstart_probe 判据 2b：真容器里拉一次那条路由。

    # ---- 18g) 写入侧守卫：**文件在** 且 **有人会跑它** ---------------------
    # ★★★ 为什么这条判据要存在 —— 005 期间实测查出来的漏：
    #     quickstart 第 2 步主张「给路段配一个不同的基准 → 必须失败」。
    #     实测 `UPDATE 1`：写入**成功了**。全仓扫过 —— 没有触发器、
    #     没有 crs 相关 check 约束、M2 写路径完全不碰 crs。
    #     ⇒ 那个不变量**根本不存在**，只有读取侧的 503 在兜底。
    #     ★ 而 L352 的注释**早就写着**「由写入侧拒绝」——
    #       所以这不是「还没做」，是「建了一半，另一半没人认领」。
    #
    # ★ 这条判据只管**静态的两件事**，行为本身由 probe 判据 2c 实测：
    #   ① 守卫文件在；
    #   ② ★★ 它**真的会被跑到** —— 全新装那条路（initdb.d 挂载）。
    #      ②才是这一条的重点：**迁移从不自动执行**（compose 只挂 10/20/90），
    #      所以「只写进迁移」对全新装等于没有守卫，而文件存在、
    #      代码也「做了」，谁都发现不了。
    #      ★★ 这与 004 踩的是同一个坑，只是换了一个对象。
    _g_dir = pathlib.Path(__file__).resolve().parents[2] / "sql"
    _g_sql = _g_dir / "25_crs_two_level_guard.sql"
    _g_txt = _g_sql.read_text(encoding="utf-8") if _g_sql.is_file() else ""
    check(_g_sql.is_file() and "crs_pair_must_agree" in _g_txt,
          "18g · 写入侧守卫的**定义文件**在（sql/25_crs_two_level_guard.sql）",
          f"{_g_sql} 存在={_g_sql.is_file()}")

    # ★ 两张表都要挂 —— 只挂 road_section 拦不住 `UPDATE design_project`
    _tc = _g_txt.count("BEFORE INSERT OR UPDATE")
    check(_tc == 2 and "ON design_project" in _g_txt and "ON road_section" in _g_txt,
          "★★ 18g · 守卫**两张表都挂**（对称）——只挂路段的话，"
          "改项目同样能造出一对矛盾的值，而它改的是全项目所有路段的依据",
          f"BEFORE INSERT OR UPDATE 出现 {_tc} 次")

    # ★★ 最关键的一条：被 initdb 挂载了吗
    _cmp = (pathlib.Path(__file__).resolve().parents[2]
            / "docker-compose.skeleton.yml").read_text(encoding="utf-8")
    _init = [ln for ln in _cmp.splitlines()
             if "docker-entrypoint-initdb.d" in ln and ".sql:" in ln]
    _mounted = [ln for ln in _init if "25_crs_two_level_guard.sql" in ln]

    def _mount_guard(text: str) -> bool:
        return any("docker-entrypoint-initdb.d" in ln
                   and "25_crs_two_level_guard.sql" in ln
                   for ln in text.splitlines())

    check(_mount_guard(_cmp),
          "★★★ 18g · 而它**真的会被跑到**：已挂进 initdb.d，全新装自动执行。"
          "★ 迁移从不自动执行（只挂 10/20/90）——「文件写了但没人跑」"
          "等于没有守卫，而且谁都看不出来",
          f"initdb.d 挂着 {len(_init)} 个 sql："
          f"{[ln.split('/')[-1].split(':')[0] for ln in _init]}")

    # ★ 元测试：把挂载那一行拿掉 → 必须变红。否则「有人会跑它」这半句是空话。
    _cmp_nomount = "\n".join(ln for ln in _cmp.splitlines()
                             if "25_crs_two_level_guard.sql" not in ln)
    check(not _mount_guard(_cmp_nomount),
          "★★ 18g · ★元测试：把 initdb.d 那条挂载拿掉 → 上面那条会红。"
          "（「文件存在」和「有人会跑它」是两件事，这条判据守的是后一件）",
          f"去掉后仍报已挂载={_mount_guard(_cmp_nomount)}")

    # ---- 18h) initdb.d 挂的宿主文件**必须对容器可读** ----------------------
    # ★★★ 这一条也是实测撞出来的，而且比「少了守卫」严重得多。
    #     005 期间用一次性库真跑全新装来验证 18g 那句主张，结果：
    #       `psql: error: .../25_crs_two_level_guard.sql: Permission denied`
    #       ⇒ postgres 容器以 `postgres` 用户跑，读不了 **600** 的文件；
    #       ⇒ **整个 initdb 中止、容器直接退出** —— 不是「少了守卫」，
    #         是**全新装根本起不来**。
    #     根因：那个文件由 `write` 工具创建，mode 是 `-rw-------`。
    #     ★ git 只记录**可执行位**，不记录读写位 ——
    #       `git ls-files -s` 里所有 sql 全是 `100644`，
    #       所以**仓库是好的、clone 出来也是 644**，坑只在本地工作区，
    #       恰恰又是跑全新装的那一份。
    #     ⇒ 这条判据守的是「宿主权限」这个 git 表达不了的维度。
    _sql_dir = pathlib.Path(__file__).resolve().parents[2] / "sql"
    _host_files = [ln.split(":")[0].strip().lstrip("- ").replace("./", "")
                   for ln in _cmp.splitlines()
                   if "docker-entrypoint-initdb.d" in ln and ".sql:" in ln]
    _unreadable = []
    for _h in _host_files:
        _p = pathlib.Path(__file__).resolve().parents[2] / _h
        if _p.is_file() and not (_p.stat().st_mode & 0o044):
            _unreadable.append(f"{_h} ({oct(_p.stat().st_mode & 0o777)})")
    check(not _unreadable,
          "★★★ 18h · initdb.d 挂的每个宿主 sql 都**对容器可读**。"
          "★ 不可读不是「少个守卫」，是 **initdb 直接中止、全新装起不来** —— "
          "而 git 只记可执行位，`ls-files -s` 里全是 100644，本地 600 看不出来",
          f"不可读：{_unreadable}" if _unreadable else f"查了 {len(_host_files)} 个文件")

    # ★ 元测试：把真实权限位喂进去，判据必须变红
    _fake600 = [0o100600]  # 一个 600 的普通文件
    _red = [m for m in _fake600 if not (m & 0o044)]
    check(bool(_red),
          "★★ 18h · ★元测试：喂一个 600 的权限位 → 判据会红"
          "（否则这条永远绿：`0o044` 的判定写错了也看不出来）",
          f"0o600 & 0o044 = {0o600 & 0o044}")

    # ═══════════════════════════════════════════════════════════ 19) T038–T041
    #   独立对拍：手写投影 vs PostGIS（PROJ）—— **本仓唯一一份外部证据**。
    print("\n=== 19) 独立对拍：手写高斯-克吕格 vs PostGIS 的 ST_Transform（SC-003）===")

    # ---- 19 之前的自问：为什么非要一个「外部」参考？ ------------------------
    # ★★ 因为在这条判据之前，「投影是准的」这件事的全部证据是
    #    **我们自己跟自己比**：正算 → 反算 → 回到原点（§18a 那条 0.05 mm）。
    #    往返闭合只证明 forward 和 inverse **互为逆运算**，
    #    不证明它们**都对着同一个正确的投影** ——
    #    两个函数一起偏，往返照样闭合，而且闭合得一样漂亮。
    #    这与「跑一遍自己的测试全绿」是同一种自证（宪法 §VI 第③条）。
    #    ⇒ 必须找一个**别人写的、没参与过这段代码**的实现来比。
    #      PostGIS 里的 PROJ 就是它：它不认识我们，也不认识我们的常数。
    #
    # ★ T038 的分工说明：为什么用 `docker exec psql` 而不是 psycopg？
    #   本套件挂在 `CONSOLE` 桶（fastapi ＋ httpx ＋ pyyaml，**没有 psycopg**）。
    #   走 psql 就够拿到 PostGIS 的结果，不必为此换桶、加依赖 ——
    #   ★ 换桶会让这条判据多一个失败理由（依赖装不上），而失败理由越多，
    #     它真正想说的那件事就越容易被淹没。
    _pts19 = [
        ("起点", 25.167892132910886, 117.342920),
        ("中点", 25.188336000000000, 117.358340),
        ("终点", 25.208781529761435, 117.373760),
    ]
    _CM19, _SRID19 = 117.0, 4548

    def _oracle(_want_srid: int) -> list[dict]:
        """问 PostGIS 要三点的投影结果。取不到就抛，绝不返回空表。"""
        _vals = ",".join(f"({la},{lo})" for _, la, lo in _pts19)
        _q = (
            "select json_agg(json_build_object("
            "'n', round(st_y(st_transform(st_setsrid(st_makepoint(lo,la),4326),"
            f"{_want_srid}))::numeric,9), "
            "'e', round(st_x(st_transform(st_setsrid(st_makepoint(lo,la),4326),"
            f"{_want_srid}))::numeric,9))) "
            f"from (values {_vals}) as t(la,lo)"
        )
        _r = _sp.run(
            ["docker", "exec", "rp-pg", "sh", "-c",
             f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "{_q}"'],
            capture_output=True, text=True, timeout=120,
        )
        _out = _r.stdout.strip()
        if not _out:
            raise RuntimeError(
                f"取不到 PostGIS 结果（容器没起？）stderr={_r.stderr.strip()[:200]}")
        return json.loads(_out)

    # ★★ 「取不到」必须**失败**，不许算通过、也不许算跳过。
    #    ★ 跳过会被「失败 0 ｜ 跳过 0」这条判据挡住没错，
    #      但更大的问题是：一个取不到证据的检查如果安静地过去了，
    #      它下次取不到时也没人会知道。
    try:
        _pg19 = _oracle(_SRID19)
    except Exception as _e19:  # noqa: BLE001
        check(False, "★★ 19a · T038 能从 PostGIS 取到对拍基准（需要 rp-pg 容器在）",
              f"取不到：{_e19}")
        _pg19 = []
    else:
        check(True, "★★ 19a · T038 能从 PostGIS 取到对拍基准（需要 rp-pg 容器在）",
              f"取到 {len(_pg19)} 个点的 ST_Transform 结果")

    # ---- 19b) T038/T039：逐点比，容差 1e-4 m（0.1 mm）---------------------
    # ★ T039 容差依据（写死在这里，别让它成为传说）：
    #     · 实测基线：north 最大 **9.246 µm**、east 约 **0.001 µm**（2026-10-13）。
    #     · 取 **1e-4 m = 100 µm**，比实测宽约一个数量级。
    #     · **不许取 1e-9** —— 那是拿浮点噪声当判据：
    #       north 的 9 µm 是**系统性**的（级数截断阶数不同），不是噪声，
    #       取 1e-9 会让这条判据在正常机器上随机红，然后被人把容差改大，
    #       连「真的偏了」也一起放过。
    #     · 也不许取 1e-2 —— 那比 9 µm 宽三个数量级，
    #       等于说「偏 1 cm 也没关系」，而 1 cm 在路面放样里是有关系的。
    _TOL19 = 1e-4
    _worst19_n = _worst19_e = 0.0
    if _pg19:
        _rows19 = []
        for (_nm, _la, _lo), _g in zip(_pts19, _pg19):
            _n, _e = GEO.forward(_la, _lo, _CM19)
            _dn, _de = _n - float(_g["n"]), _e - float(_g["e"])
            _worst19_n = max(_worst19_n, abs(_dn))
            _worst19_e = max(_worst19_e, abs(_de))
            _rows19.append((_nm, _n, float(_g["n"]), _dn, _e, float(_g["e"]), _de))
            print(f"    {_nm}  north 手写 {_n:.6f} / PostGIS {float(_g['n']):.6f} "
                  f"差 {_dn:+.3e} m")
            print(f"    {_nm}  east  手写 {_e:.6f} / PostGIS {float(_g['e']):.6f} "
                  f"差 {_de:+.3e} m")
        check(_worst19_n <= _TOL19,
              f"★★ 19b · T038 三点 north 与 PostGIS 一致（容差 {_TOL19:g} m）",
              f"实测最大差 {_worst19_n:.6f} m（{_worst19_n*1e6:.3f} µm）")
        check(_worst19_e <= _TOL19,
              f"★★ 19b · T038 三点 east 与 PostGIS 一致（容差 {_TOL19:g} m）",
              f"实测最大差 {_worst19_e:.6f} m（{_worst19_e*1e6:.3f} µm）")
        # ★ 把「有多准」变成一个**每次都被打印出来**的数（T042 的同一件事）
        print(f"    ★ 实测基线：north 最大 {_worst19_n*1e6:.3f} µm ｜ "
              f"east 最大 {_worst19_e*1e6:.3f} µm ｜ 容差 {_TOL19*1e6:.0f} µm")

    # ---- 19c) ★ T040 元测试：把假东偏挪 1 m → 对拍**必须变红** --------------
    # ★★ 不做这一步，就无从知道 19b 是「真的在比」还是「两边都调了同一个函数」。
    #    004 的往返判据就吃过这个形态的亏：两边共享常数时，它永远是绿的。
    #    ★ 做法是**临时改掉 `geo._FALSE_EASTING`**（500000 → 500001），
    #      重算，再改回来。改的是**我们这一侧**的常数 ——
    #      PostGIS 那边一动不动，所以这正是「两边不是同一个实现」的证明。
    if _pg19:
        _old_fe = GEO._FALSE_EASTING
        try:
            GEO._FALSE_EASTING = 500001.0
            _mut_worst = 0.0
            for (_nm, _la, _lo), _g in zip(_pts19, _pg19):
                _n2, _e2 = GEO.forward(_la, _lo, _CM19)
                _mut_worst = max(_mut_worst, abs(_e2 - float(_g["e"])))
        finally:
            GEO._FALSE_EASTING = _old_fe
        check(_mut_worst > _TOL19,
              "★★ 19c · ★元测试：`_FALSE_EASTING` 挪 1 m → 对拍必须变红"
              "（否则 19b 可能是在跟自己比）",
              f"挪后 east 最大差 {_mut_worst:.6f} m，容差 {_TOL19:g} m")
        # ★ 元测试之后必须确认**改回来了** —— 否则后面所有几何判据都被污染，
        #   而它们会红在一个跟本次改动毫无关系的地方。
        # ★ 同时用**行为**验证还原：重算第一点，east 必须回到 PostGIS 那一侧。
        _n3, _e3 = GEO.forward(_pts19[0][1], _pts19[0][2], _CM19)
        check(GEO._FALSE_EASTING == _old_fe
              and abs(_e3 - float(_pg19[0]["e"])) <= _TOL19,
              "★★ 19c · 元测试后 `_FALSE_EASTING` 已还原（常数 + 行为两侧都对）",
              f"常数={GEO._FALSE_EASTING}（原 {_old_fe}），"
              f"重算 east 差 {abs(_e3 - float(_pg19[0]['e'])):.3e} m")

    # ---- 19d) T041：★ 这条判据的**适用边界** ------------------------------
    # ★★★ 不写这一句，将来一定会有人拿它去判一个它管不了的案子。
    #    这条对拍只在**同基准**变换上成立：两边都把 4326 的经纬度
    #    当作 CGCS2000 的经纬度来解释（`ST_SetSRID(...,4326)` 然后直接转 4548），
    #    而手写的 `forward()` 用的是 GRS80 椭球 + 3 度带高斯。
    #    ★ 跨基准时（如 WGS84 → CGCS2000），两个实现**可能合法地不一致** ——
    #      因为差异来自**基准转换参数**（七参数/四参数），
    #      那不是投影公式的事，两边用的参数集不同、甚至都没配。
    #      那时**不能简单判红**，得先判「谁该对」。
    # ★ 换句话说：这条判据守的是「投影公式实现对不对」，
    #   **不是**「坐标基准换得对不对」。两件事，别混。
    check(_SRID19 in (4548, 4545, 4524) and _CM19 == 117.0,
          "★ 19d · T041 适用边界：只在**同基准**（CGCS2000 3 度带）上成立，"
          "跨基准时两个实现可能合法地不一致 —— 那时先判「谁该对」，不要直接判红",
          f"本次 SRID={_SRID19} CM={_CM19}，属同基准")

    # ═══════════════════════════════════════════════════════════ 20) T043 / T044
    #   「算不了的基准，必须当场拒绝，不许算出一个看着正常的错值」。
    print("\n=== 20) 投影族守卫：填一个本实现算不了的 SRID，接口必须 503（FR-013 / SC-009）===")

    # ---- 20 之前的自问：这个洞到底长什么样？ ------------------------------
    # ★★★ 数据模型按宪法 §VI **接受任意 SRID** —— 这是对的，
    #     通用产品不该被自己的实现限制住入库。
    #     但手写换算**只实现了高斯-克吕格**。
    #     两者合起来的旧行为是：`crs_epsg` 填成 `2154`（Lambert，`+proj=lcc`），
    #     代码**不报错**，把 Lambert 的参数当高斯-克吕格算，
    #     输出一个**量级正常、位置错掉几百米**的坐标。
    # ★★ 这正是 004 那条教训的形状 ——「**看着正常的错值比缺失更危险**」。
    #     缺失会被发现（503 / 空值 / 报错），错值不会，它会一路流到放样桩上。
    # ★ 所以下面 20b 断的是**行为**（503），20c 断的是**对照**（换个能算的 SRID 就 200），
    #   20d 断的是**那个 SRID 真的就是 lcc**（去问真库，不靠假仓储自证）。

    # ---- 20a) `geo.py` 层：族不在已实现集合里 → 抛 CrsError ---------------
    _raised = None
    try:
        GEO.require_family({"projection_family": "lcc"}, origin="T044 元测试")
    except GEO.CrsError as _e:
        _raised = str(_e)
    check(_raised is not None and "lcc" in _raised,
          "★★ 20a · T043 `geo.py` 层：族 `lcc` 不在 `IMPLEMENTED_FAMILIES` 里 → 抛 CrsError",
          f"消息：{(_raised or '（没抛！）')[:110]}")
    check(GEO.IMPLEMENTED_FAMILIES == frozenset({"tmerc"}),
          "★ 20a · 本实现自述只实现了 `tmerc`（这份自述就是那条四十行公式的写照）",
          f"IMPLEMENTED_FAMILIES={sorted(GEO.IMPLEMENTED_FAMILIES)}")

    # ★★ 「不声明」也**必须**报错 —— 「不知道」不是「支持」。
    #    ★ 这条单独测，因为它最容易被人为了方便而改掉（「没写就当 tmerc 吧」）。
    _raised2 = None
    try:
        GEO.require_family({}, origin="T044 元测试")
    except GEO.CrsError as _e2:
        _raised2 = str(_e2)
    check(_raised2 is not None and "不知道" in _raised2,
          "★★ 20a · 没声明族 → 也报错（**「不知道」不是「支持」**）",
          f"消息：{(_raised2 or '（放行了！）')[:110]}")

    # ---- 20b) 接口层：`crs_epsg=2154` → 503，且消息里说出那个 SRID --------
    _row2154 = {**FIX["road_section"][0], "id": 6, **_CRS_FILL,
                "project_crs_epsg": 2154,
                "project_crs_zone_number": 39,
                "project_crs_central_meridian_deg": Decimal("117.000000"),
                "project_crs_zone_width_deg": Decimal("3.00"),
                "project_crs_assumed": False,
                "project_crs_evidence": "T044 元测试：故意填一个 Lambert 的 SRID"}
    _old_fam = dict(_FakeGe.FAMILY_BY_SRID)
    try:
        _FakeGe.FAMILY_BY_SRID = {2154: "lcc"}
        APP.dao = _FakeDao(row=_row2154)
        _c20 = TestClient(APP.app)
        _r2154 = _c20.get("/v1/geometry/sections/6/geo", params={"station_m": 2900.0})
        check(_r2154.status_code == 503,
              "★★ 20b · T044 元测试：`crs_epsg=2154`（Lambert）→ **503**",
              f"HTTP {_r2154.status_code}")
        _d20 = str(_r2154.json().get("detail", ""))
        check("2154" in _d20,
              "★★ 20b · 那句话里**说出了那个 SRID**（`2154`）—— 否则运维不知道去改哪一行",
              f"原话：{_d20[:150]}")
        check("lcc" in _d20,
              "★★ 20b · 那句话里也说了**族**（`lcc`）—— 只说 SRID 他不知道为什么不行",
              f"原话：{_d20[:150]}")
        # ★★ 最重要的一条：**不许返回坐标**。
        #    503 的响应体里出现经纬度，就是「拒绝了但还是算了一个」。
        _banned20 = {"lat_deg", "lon_deg", "north_m", "east_m",
                     "zone_number", "central_meridian_deg", "epsg"}
        check(not (_banned20 & set(_r2154.json())),
              "★★ 20b · 响应里 MUST NOT 出现任何经纬度 / 平面坐标字段",
              f"★ 出现了：{sorted(_banned20 & set(_r2154.json()))}")

        # ---- 20c) 对照：同一行形状，只把 SRID 换成能算的 → 必须 200 --------
        # ★★ 没有这一条，20b 可以通过而真实原因是「这个假仓储根本不服务路段 6」
        #    或「这行的线元不对」—— 测到的就不是「族算不了」这件事。
        _row4548 = {**_row2154, "project_crs_epsg": 4548,
                    "project_crs_evidence": "T044 对照：同一个形状，换一个能算的 SRID"}
        APP.dao = _FakeDao(row=_row4548)
        _r4548 = TestClient(APP.app).get(
            "/v1/geometry/sections/6/geo", params={"station_m": 2900.0})
        check(_r4548.status_code == 200,
              "★★ 20c · ★对照：**同一个行形状**只把 SRID 换成 4548（tmerc）→ 200",
              f"HTTP {_r4548.status_code}：{str(_r4548.text)[:90]}")
        check("lat_deg" in _r4548.json(),
              "★ 20c · 对照那一侧**真的返回了坐标**（不是又一次 503）",
              f"键：{sorted(_r4548.json())[:8]}")
    finally:
        # ★★ 类属性，必须还原 —— 否则后面所有用例都把 2154 当 lcc，
        #    而它们会红在一个跟 T044 毫无关系的地方。
        _FakeGe.FAMILY_BY_SRID = _old_fam
        APP.dao = _FakeDao()
    check(_FakeGe.FAMILY_BY_SRID == {} or _FakeGe.FAMILY_BY_SRID is _old_fam,
          "★★ 20c · 元测试后 `FAMILY_BY_SRID` 已还原（不污染后面的用例）",
          f"当前={_FakeGe.FAMILY_BY_SRID}")

    # ---- 20d) ★ 去真库核对：`2154` 到底是不是 `lcc` -----------------------
    # ★★ 为什么非要问真库：上面的假仓储是**我自己写的** `{2154: "lcc"}`。
    #    如果我在假仓储里写 `{2154: "tmerc"}`，20b 会照样红、20c 会照样绿，
    #    而**生产里根本没有这个判断** —— 这就是「自证不算证据」（宪法 §VI ③）。
    #    ⇒ 至少要有**一处**是真的从 `spatial_ref_sys.proj4text` 里取出来的。
    try:
        _fams20 = json.loads(_sp.run(
            ["docker", "exec", "rp-pg", "sh", "-c",
             'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "'
             "select json_object_agg(srid, "
             "coalesce(substring(proj4text from '\\\\+proj=([a-z0-9_]+)'), '?')) "
             'from spatial_ref_sys where srid in (2154, 4548)"'],
            capture_output=True, text=True, timeout=120).stdout.strip())
    except Exception:  # noqa: BLE001
        _fams20 = {}
    check(_fams20.get("2154") == "lcc" and _fams20.get("4548") == "tmerc",
          "★★ 20d · 去**真库**核对：2154 真的是 `lcc`、4548 真的是 `tmerc`"
          "（假仓储里的 `{2154: 'lcc'}` 不是我自己编的）",
          f"实测 {_fams20}")

    # ============================================================ 21
    print("\n=== 21) 任意桩号处的几何：坐标 + 方位角 + 曲率（一次定位，四样一起出）===")
    # ★ 这一组守的是「在路段**任意一点**上求坐标与曲率」这件事的**接口契约**：
    #   给没给、以及给的**是什么语义**。
    #   数值本身另有更硬的一道门 —— test_geometry_solver.py 第 6 组拿真库的
    #   逐桩表逐列对质（azimuth 容差 5e-06 度、curvature 容差 5e-11、且第 8 组
    #   证明那道门真的能红）。**那一组管「算得对不对」，这一组管「给的是什么」。**
    #   两边都要有：算得对但语义混，调用方照样用错。
    _els21 = elements()
    _g21 = gf()
    _o21 = GEO.station_to_lonlat(_els21, 2000.0, _g21)

    check("azimuth_deg" in _o21 and "curvature_1pm" in _o21,
          "★ 21a · 桩号处的几何里给了 `azimuth_deg` 与 `curvature_1pm`"
          "（「任意点求坐标与曲率」缺的就是这两样）",
          f"键 {sorted(_o21)}")

    # ★★ 这道闸是**故意**的：`geometry_point.h_radius_m` 在库里指的是
    #    「该线元所属平曲线的半径」（元素级标签 —— 缓和曲线上是常数，等于它
    #    两端那个圆曲线的 R），**不是**「该点的曲率半径」（= 1/|κ|，沿线变化）。
    #    同一个名字两个含义比少一个字段更糟：调用方会把 450 当成脚下这段路的
    #    半径，而该点真实半径可能是 1911 m。谁顺手把它加回来，这里会红。
    check("h_radius_m" not in _o21,
          "★★ 21b · 返回里**没有** `h_radius_m`（那名字在库里是元素级标签，"
          "与「该点半径」同名不同义，宁缺勿混）",
          f"键 {sorted(_o21)}")

    _bad21, _ncirc21 = [], 0
    for _e21 in _els21:
        _mid21 = (_e21["start_station_m"] + _e21["end_station_m"]) / 2.0
        _k21 = GEO.station_to_lonlat(_els21, _mid21, _g21)["curvature_1pm"]
        if _e21["type"] == "circular":
            _ncirc21 += 1
            _r21 = _e21["radius_start_m"]
            # ★ κ 是**带符号**的：右转为负、左转为正。所以要比的是 |1/κ| ——
            #   第一版写成 `1/κ` 直接比 R（正数），四个右转曲线当场报红，
            #   而**代码是对的、断言是错的**。（本路段 8 个圆曲线里 4 个右转。）
            if not _k21 or abs(abs(1.0 / _k21) - _r21) > 1e-9:
                _bad21.append(f"#{_e21['seq']} |1/κ|="
                              f"{abs(1.0 / _k21) if _k21 else 0} vs R={_r21}")
        elif _e21["type"] == "line" and _k21 != 0.0:
            _bad21.append(f"#{_e21['seq']} 直线 κ={_k21!r} 不是 0.0")
    check(_ncirc21 > 0 and not _bad21,
          f"★ 21c · 圆曲线中点 1/κ 复现线元半径（{_ncirc21} 个，差 ≤ 1e-9 m）；"
          "直线上的 κ **恰为 0.0**（不是 1e-18）",
          f"不符 {_bad21}" if _bad21 else f"圆曲线 {_ncirc21} 个全符")

    _line21 = [e for e in _els21 if e["type"] == "line"]
    _dmax21 = 0.0
    for _e21 in _line21:
        _a21 = (_e21["start_station_m"] + _e21["end_station_m"]) / 2.0
        _p21a = GEO.point_north_east(_els21, _a21)
        _p21b = GEO.point_north_east(_els21, _a21 + 1.0)
        # `point_north_east` 回的是 (北, 东)，方位角自北起顺时针 ⇒ atan2(Δ东, Δ北)
        _fd21 = math.degrees(math.atan2(_p21b[1] - _p21a[1], _p21b[0] - _p21a[0])) % 360.0
        _az21 = GEO.station_to_lonlat(_els21, _a21, _g21)["azimuth_deg"]
        _dmax21 = max(_dmax21, abs((_fd21 - _az21 + 180.0) % 360.0 - 180.0))
    # ★ 容差 1e-6 度是有依据的，不是随手放的：坐标量级 2.79e6 m，float64 的
    #   末位约 6.2e-10 m，除以 1 m 的差分步长 ≈ 6.2e-10 rad ≈ 3.6e-08 度。
    #   第一版写 1e-9 度 —— 比浮点噪声还紧，实测 6.6e-09 度被判红。
    check(bool(_line21) and _dmax21 < 1e-6,
          f"★ 21d · 直线上「坐标差分算出的方向」== 报出来的方位角"
          f"（{len(_line21)} 个线元，差 ≤ 1e-6 度）—— 方位角与坐标同源，不是两张皮",
          f"最大差 {_dmax21:.3e} 度")

    _tr21 = [e for e in _els21 if e["type"] == "transition"]
    _rel21, _det21 = 1e9, "无缓和曲线"
    if _tr21:
        _e21 = _tr21[0]
        _a21 = (_e21["start_station_m"] + _e21["end_station_m"]) / 2.0
        _h21 = 1.0
        # ★★ 用**中心**差分，不用前向差分。θ(d) 是 d 的二次式（κ 线性 ⇒
        #    ∫κ ds 二次），而二次式在 [d-h/2, d+h/2] 上的中心差商**恰好等于**
        #    它在 d 处的导数 —— 这是个恒等式，不是近似。
        #    ⇒ 残差只剩浮点噪声，容差可以收到 1e-9 相对。
        #    第一版用前向差分、容差 1%，实测 1.67% 报红；那个 1.67% 是**真误差**
        #    （O(h) 项 = (κ1-κ0)h/(2L)，本线元 L≈60 m），不是代码错。
        _t0 = math.radians(GEO.station_to_lonlat(
            _els21, _a21 - _h21 / 2.0, _g21)["azimuth_deg"])
        _t1 = math.radians(GEO.station_to_lonlat(
            _els21, _a21 + _h21 / 2.0, _g21)["azimuth_deg"])
        _kfd = ((_t1 - _t0 + math.pi) % (2 * math.pi) - math.pi) / _h21
        _kcf = GEO.station_to_lonlat(_els21, _a21, _g21)["curvature_1pm"]
        _rel21 = abs(_kfd - _kcf) / abs(_kcf) if _kcf else abs(_kfd)
        _det21 = (f"#{_e21['seq']} 中心差分 {_kfd:.9e} vs 闭式 {_kcf:.9e} 1/m"
                  f"（h={_h21} m，二次式上中心差商是恒等式）")
    check(bool(_tr21) and _rel21 < 1e-9,
          "★ 21e · 缓和曲线上「方位角中心差商」== 报出来的曲率（相对差 ≤ 1e-9）"
          "—— 曲率是解出来的，不是搬来的常数",
          _det21)

    _bad21b = []
    _lo21 = min(e["start_station_m"] for e in _els21)
    _hi21 = max(e["end_station_m"] for e in _els21)
    for _s21 in (_lo21 - 1.0, _hi21 + 1.0):
        try:
            GEO.station_to_lonlat(_els21, _s21, _g21)
            _bad21b.append(f"{_s21:.1f} 没抛")
        except GEO.StationOutsideAlignment:
            pass
    check(not _bad21b,
          "★ 21f · 桩号出链仍抛 `StationOutsideAlignment`"
          "（多给了两样，不等于开始猜）",
          f"{_bad21b}" if _bad21b else f"链覆盖 {_lo21:.3f} .. {_hi21:.3f} m")

    _seam21 = _els21[len(_els21) // 2]
    _seq21 = GEO.station_to_lonlat(_els21, _seam21["end_station_m"], _g21)["element_seq"]
    check(_seq21 == _seam21["seq"],
          "★ 21g · 线元接缝处归**前**一个元素 —— 与 `locate` 同一条规则，"
          "不另立一套（两种归属差 0.69 mm）",
          f"接缝 {_seam21['end_station_m']:.3f} m 归 #{_seq21}，"
          f"期望 #{_seam21['seq']}")

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
