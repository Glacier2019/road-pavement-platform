"""契约② FR-018：**按桩号区间查询**（T033 落 M3、T034 落 M6）。

两条硬性点：

  ① **相交 ≠ 包含**。一个 200 m 的线形单元跨过 50 m 的查询窗时必须被返回。
     写成 `BETWEEN`（完全包含）会静默丢掉长单元 —— 返回的行数看着还挺正常。
  ② `span_m`（落在区间内的长度）与 `length_m`（单元全长）**并存**。
     被截断时两者不等；调用方拿 `length_m` 做积分会把里程轴算错。

★ 本组必须打真库：这两个性质都在**数据**里，不在代码里 ——
  mock 一个 DAO 只能证明「SQL 字符串拼对了」，证明不了「相交真的多返回了行」。

跑法：见 scaffold/run_contract_tests.sh（API 依赖档）。
"""

from __future__ import annotations

import os
import re
import sys
import pathlib

_HERE = pathlib.Path(__file__).resolve()
ROOT = _HERE.parents[2]
for _up in _HERE.parents:
    if (_up / "modules" / "M3-rpdao").is_dir():
        ROOT = _up
        break
for _m in ("M3-rpdao", "M6-api"):
    _p = str(ROOT / "modules" / _m)
    if _p not in sys.path:
        sys.path.insert(0, _p)

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK] {name}" + (f" —— {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [!!] {name}" + (f" —— {detail}" if detail else ""))
    return ok


def _dsn() -> str | None:
    """先看环境变量，再回落到 scaffold/.env（与 test_design_import.py 同一约定）。

    ⚠ `.env` 的值带行内注释（`PG_PORT=55432   # ...`），naive 的 split("=")
      会把注释吞进值里，得到一个**看着合法、连不上、且静默失败**的 DSN。
      所以必须剥掉引号与 `#` 之后的部分。
    """
    env = os.environ.get("PG_DSN")
    if env:
        return env.strip()
    f = ROOT / ".env"
    if not f.exists():
        return None
    kv: dict[str, str] = {}
    for line in f.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        k, v = line.split("=", 1)
        v = v.split("#", 1)[0].strip().strip(chr(39)).strip(chr(34))
        kv[k.strip()] = v
    need = ("PG_PORT", "PG_USER", "PG_PASSWORD", "PG_DB")
    if not all(k in kv for k in need):
        return None
    return (f"postgresql://{kv[chr(39)+chr(39)]}" if False else
            f"postgresql://{kv['PG_USER']}:{kv['PG_PASSWORD']}"
            f"@127.0.0.1:{kv['PG_PORT']}/{kv['PG_DB']}")


def main() -> int:
    dsn = _dsn()
    if not dsn:
        print("  未设 PG_DSN 且 scaffold/.env 无 PG_* —— 跳过")
        return 0
    os.environ["PG_DSN"] = dsn

    from rpdao import Dao
    from rpdao.repo import build_repository

    d = Dao(dsn, app_name="test-station-query")
    d.open()
    try:
        ge = build_repository("GE", d)
        secs = d.query("SELECT id FROM road_section ORDER BY id")
        # ★ 没有 `ge.elements(sid)` 这个方法：线形单元是跟交点链一起由 `alignment()` 返回的。
        # ★ 基准一律用 elements_by_station 取，不用旧的 alignment()：
        #   旧 ELEMENTS_SQL 不选 e.id（只有 element_seq），拿它做基准会 KeyError。
        cand = [(s["id"], len(ge.elements_by_station(s["id"], 0.0, 100.0))) for s in secs]
        print(f"  路段线形单元数：{cand}")
        nz = [sid for sid, n in cand if n > 0]
        if not check("★★ 前置：至少有一个路段**确实有**线形单元", bool(nz), str(cand)):
            return 1
        sid = nz[0]
        all_el = ge.elements_by_station(sid, 0.0, 100.0)

        print("")
        print("第 1 组  相交 ≠ 包含")
        full = ge.elements_by_station(sid, 0.0, 100.0)
        check("全区间取回全部单元（不与别的筛选串味）",
              len(full) == len(all_el), f"{len(full)} vs {len(all_el)}")
        cut = next((e for e in all_el if float(e["end_station_km"]) > 0.5), None)
        if not check("★★ 前置：存在一个**跨过 0.5 km** 的单元（否则下一条是空转）",
                     cut is not None):
            return 1
        win = ge.elements_by_station(sid, 0.0, 0.5)
        ids = {e["id"] for e in win}
        check("★★ 跨窗单元**被返回**（相交语义 —— `BETWEEN` 会漏掉它）",
              cut["id"] in ids,
              f"单元 {cut['element_seq']} {cut['start_station_km']}~{cut['end_station_km']}")
        got = next((e for e in win if e["id"] == cut["id"]), None)
        if got:
            span = float(got["span_m"])
            length = float(got["length_m"])
            check("★★ `span_m` < `length_m`（被区间截断了）", span < length,
                  f"span={span:.3f} length={length:.3f}")
            check("★★ `span_m` = hand-computed 截断长度（不是随手填的）",
                  abs(span - (0.5 - float(got["start_station_km"])) * 1000.0) < 1e-6,
                  f"span={span:.6f}")
            _f2 = next(e for e in full if e["id"] == cut["id"])
            check("★ 全区间里它的 span == length（没被截断时两者相等）",
                  abs(float(_f2["span_m"]) - length) < 1e-6)

        print("")
        print("第 2 组  区间边界与异常入参")
        check("空区间（from == to）不报错",
              isinstance(ge.elements_by_station(sid, 1.0, 1.0), list))
        check("完全在数据之外的区间返回空列表（查询不是校验）",
              ge.elements_by_station(sid, 900.0, 901.0) == [])
        try:
            ge.elements_by_station(sid, 5.0, 1.0)
            check("★ 区间反了**报错**（不是静默返回空）", False, "没报")
        except ValueError as exc:
            check("★ 区间反了**报错**（不是静默返回空）", "反了" in str(exc), str(exc)[:50])

        print("")
        print("第 3 组  逐桩段采样：段名白名单")
        sp = ge.samples_by_station(sid, "station_sequence", 0.0, 100.0)
        check("★★ `station_sequence` 全区间取回全部桩号",
              len(sp) == len(ge.stations(sid)), f"{len(sp)} vs {len(ge.stations(sid))}")
        try:
            ge.samples_by_station(sid, "no_such_segment", 0.0, 1.0)
            check("★★ 未知段名**报错**（不静默返回空 —— 那是「无数据」的意思）", False, "没报")
        except ValueError as exc:
            check("★★ 未知段名**报错**（不静默返回空 —— 那是「无数据」的意思）",
                  "未知段" in str(exc), str(exc)[:60])
        narrow = ge.samples_by_station(sid, "station_sequence", 0.0, 0.6)
        check("★ 窄区间确实**少于**全区间（区间条件真的生效了）",
              len(narrow) < len(sp), f"{len(narrow)} vs {len(sp)}")

        # ---------------------------------------------------------------- 第 4 组
        # ★★★★★ 2026-10-13 新增。起因：`SAMPLE_ANCHORS` 那张「段名 → 桩号列」
        #   的对照表里，**6 条错了 2 条**，两条一调就 500 —— 而这里当时只调过
        #   `station_sequence` **一个**段，所以一直是绿的。
        #
        #   错的两条：
        #     · `superelev_transition` 写 `start_station_km`，该表根本没这列
        #       （它是逐桩行，列名就是 `station_km`）。契约里也写着同一个错名字
        #       —— 契约和代码一起错，对着看是看不出来的。
        #     · `profile_ground_point` 写 `station_km`，该表没有桩号列，
        #       桩号得从 `station_sequence` JOIN 出来。
        #
        #   ⇒ 这一组**逐个段真调一遍**。段名清单**从代码推**（不在测试里另抄
        #     一份），所以以后加段自动进网；而「新加的段是错的」会当场红。
        print("")
        print("第 4 组  逐桩段：每个段名都要真的能用")
        _anchors = dict(ge.SAMPLE_ANCHORS)
        _segs = sorted(_anchors)
        check("★★ 前置：段名清单非空且取自代码（不在这里另抄一份）",
              len(_segs) >= 6, f"{len(_segs)} 个：{_segs}")

        _broken: dict[str, str] = {}
        _nonempty: dict[str, int] = {}
        for _seg in _segs:
            try:
                _rows = ge.samples_by_station(sid, _seg, 0.0, 100.0)
            except Exception as exc:  # noqa: BLE001 —— 要的就是把任何异常都记下来
                _broken[_seg] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:80]}"
                continue
            _nonempty[_seg] = len(_rows)
        check("★★★ **每一个**段名都能真的查出来（不是只测第一个）",
              not _broken, f"坏的：{_broken}" if _broken else f"行数 {_nonempty}")
        # ★ 前置守卫：至少要有一个段真的取到了行，否则上面那条是空转
        #   （全都返回空列表也能「不报错」）。
        check("★★ 前置：至少有一个段**确实取到了行**（否则上一条是空转）",
              any(v > 0 for v in _nonempty.values()), str(_nonempty))

        # ⚠ JOIN 的那一段**不许**把 JOIN 进来的列漏进响应。
        #   不加这个守卫的话，写法退化成 `SELECT *` 也没人发现 ——
        #   而多出来的列会让下游以为这张表自带桩号。
        _gp = ge.samples_by_station(sid, "profile_ground_point", 0.0, 100.0)
        if _gp:
            _leaked = sorted(set(_gp[0]) - {"id", "station_id", "ground_elev_m", "remark"})
            check("★★ JOIN 取的那一段**不泄漏**被 JOIN 表的列（`g.*` 而不是 `*`）",
                  not _leaked, f"多出来的列：{_leaked}")
        else:
            check("★ 前置：`profile_ground_point` 有行才谈得上检泄漏", False,
                  "该段 0 行 —— 换一条有数据的路段再跑")

        # ---- 元测试：把对照表改回**当时那个错的**写法，上面那条必须变红 ----
        #   （宪法 V：「一个永远不会失败的检查，比没有检查更糟糕」。）
        _saved = _anchors["superelev_transition"]
        try:
            # 复刻 2026-10-13 之前的写法
            ge.SAMPLE_ANCHORS["superelev_transition"] = (
                "start_station_km", "superelev_transition", "*")
            try:
                ge.samples_by_station(sid, "superelev_transition", 0.0, 100.0)
                _meta_red = False          # 居然没报 —— 说明新判据抓不住旧错误
            except Exception:              # noqa: BLE001
                _meta_red = True
        finally:
            ge.SAMPLE_ANCHORS["superelev_transition"] = _saved
        check("★★ 元测试：把 `superelev_transition` 改回 `start_station_km`，"
              "上面那种查法**必须**报错 —— 否则这套新检查抓不住今天修的那个错",
              _meta_red, "已确认能抓住" if _meta_red else "改回错写法之后居然还能查通")

        # ---- 契约 ↔ 代码：两边列的段名必须是**同一个集合** ----
        #   ★ 这不是重复检查。今天的教训正是「契约和代码一起错」，
        #     所以必须有一条**把两边摆到一起比**的判据。
        _contract = ROOT / "contracts" / "openapi" / "m6-gateway.v0.3.yaml"
        _advertised: set[str] = set()
        if _contract.exists():
            _txt = _contract.read_text(encoding="utf-8")
            _m = re.search(r"段名（([^）]+)）", _txt)
            if _m:
                _advertised = {s.strip() for s in _m.group(1).split("/") if s.strip()}
        check("★★ 前置：契约里能读到它公布的段名清单（否则下一条是空转）",
              len(_advertised) >= 6, f"{sorted(_advertised)}")
        check("★★★ 契约公布的段名 == 代码认的段名（**相等**，不是子集 —— "
              "契约多写一个会 422，少写一个没人知道）",
              _advertised == set(_segs),
              f"契约独有 {sorted(_advertised - set(_segs))}；"
              f"代码独有 {sorted(set(_segs) - _advertised)}")
    finally:
        d.close()

    print("")
    print(f"通过 {PASS} ｜ 失败 {FAIL}")
    print("全部通过" if FAIL == 0 else "有失败项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())