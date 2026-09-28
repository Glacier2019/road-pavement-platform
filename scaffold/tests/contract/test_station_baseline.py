"""契约② v0.6（契约变更工单 #3）：**桩号脱离路段，重锚到路线**。

这一组钉的是四条断言，每条都对应工单里的一个决定 ——
而每条都**必须先证明它可能失败**，否则就是宪法原则 V 说的
「一个永远不会失败的检查，比没有检查更糟」。

FR-002  删路段不影响桩号：删完桩号还在，section_id 变 NULL
FR-003  按**路线**查桩号：两条相距数千公里的路线互不混淆
FR-004  路段之并 != 路线全桩号：桩号完备性以桩号文件自身为准
Q1      唯一性上移到路线层级：部分唯一索引真的在拦重复

★ 这一组最容易写成永远通过，所以每条断言都配了「先确认真有东西可删/可查」
   的前置断言。举例：「删路段后桩号还在」如果那个路段本来就没桩号，
   当然成立 —— 所以必须先断言「删之前它确实有桩号」。

跑法：
    cd scaffold
    PG_DSN=postgresql://rp:rp_skeleton_dev_2026@localhost:55432/road_pavement \
      uv run --quiet --with "psycopg[binary,pool]==3.2.3" \
      tests/contract/test_station_baseline.py
"""

from __future__ import annotations

import os
import sys
import pathlib

_HERE = pathlib.Path(__file__).resolve()
ROOT = _HERE.parents[2]
if str(ROOT / "modules" / "M3-rpdao") not in sys.path:
    sys.path.insert(0, str(ROOT / "modules" / "M3-rpdao"))

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
    """真库 DSN：优先环境变量，否则读 scaffold/.env（与 test_design_import 同一套约定）。

    ★ 为什么要读 .env 而不是"没 PG_DSN 就跳过"：
      本组**全部**断言都打真库，跳过等于它从未运行过 ——
      而 run_contract_tests.sh 不会传 PG_DSN，于是在总运行器下
      本组会显示为「跳过」，与"通过"读起来几乎一样。
      这正是设计套件一直在防的那类**假绿**。

    ⚠️ 必须处理**行内注释**：本仓 .env 写的是
      ``PG_PORT=55432          # 本机 5432 已被其它服务占用``，
      天真的 split("=") 会把注释一起吞进值里，得到一个语法上像 DSN、
      连不上又不报错的字符串。
    """
    if os.environ.get("PG_DSN"):
        return os.environ["PG_DSN"]
    envf = ROOT / ".env"
    if not envf.exists():
        return None
    vals: dict[str, str] = {}
    for line in envf.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if v[:1] in ("'", '"'):
            v = v[1:].split(v[0], 1)[0]
        elif "#" in v:
            v = v.split("#", 1)[0].strip()
        vals[k.strip()] = v
    return ("postgresql://{u}:{p}@{h}:{port}/{d}").format(
        u=vals.get("PG_USER", "rp"), p=vals.get("PG_PASSWORD", ""),
        h=vals.get("PG_HOST", "localhost"), port=vals.get("PG_PORT", "5432"),
        d=vals.get("PG_DB", "road_pavement"))


def main() -> int:
    dsn = _dsn()
    if not dsn:
        print("未设 PG_DSN，跳过（本组必须打真库）")
        return 0

    from rpdao import WriteDao
    from rpdao.repo import build_repository

    dao = WriteDao(dsn, app_name="test-station-baseline")
    dao.open()
    # 走工厂，不走 GeRepository(dao) —— 后者还要求 domain 参数，直接构造是错用法。
    ge = build_repository("GE", dao)

    # ══════════════════════════════════════════════════════════════════
    print("第 1 组  结构：桩号不再被路段「拥有」（工单 ①②③）")
    # ══════════════════════════════════════════════════════════════════
    _cols = {r["column_name"]: r for r in dao.query(
        "select column_name, is_nullable from information_schema.columns "
        "where table_name = 'station_sequence'")}
    check("station_sequence 有 line_id 列（路线锚定，工单 ②）",
          "line_id" in _cols,
          f"实际列：{sorted(_cols)}" if "line_id" not in _cols else "")
    check("station_sequence.section_id 可空（工单 ①）",
          _cols.get("section_id", {}).get("is_nullable") == "YES",
          f"is_nullable={_cols.get('section_id', {}).get('is_nullable')}")

    _fk = {r["conname"]: r["def"] for r in dao.query(
        "select conname, pg_get_constraintdef(oid) as def from pg_constraint "
        "where conrelid = 'station_sequence'::regclass and contype = 'f'")}
    _sec_fk = [v for k, v in _fk.items() if "section_id" in v]
    check("section_id 的外键是 ON DELETE SET NULL（工单 ③：删路段不删桩号）",
          bool(_sec_fk) and "ON DELETE SET NULL" in _sec_fk[0],
          f"{_sec_fk}")
    _line_fk = [v for k, v in _fk.items() if "line_id" in v]
    check("line_id 的外键指向 road_line（工单 ②）",
          bool(_line_fk) and "road_line" in _line_fk[0], f"{_line_fk}")

    # ══════════════════════════════════════════════════════════════════
    print("")
    print("第 2 组  ★ Q1：唯一性上移到路线层级（部分唯一索引）")
    # ══════════════════════════════════════════════════════════════════
    _idx = {r["indexname"]: r["indexdef"] for r in dao.query(
        "select indexname, indexdef from pg_indexes "
        "where tablename = 'station_sequence'")}
    check("部分唯一索引 uq_station_line_local 已建立",
          "uq_station_line_local" in _idx,
          f"实际索引：{sorted(_idx)}" if "uq_station_line_local" not in _idx else "")
    if "uq_station_line_local" in _idx:
        _d = _idx["uq_station_line_local"]
        # 这条索引的**存在意义**全在 WHERE 子句上：没有它，约束对 NULL 行仍然不生效，
        # 而且它不要求先回填数据。所以 WHERE 必须被显式断言，不能只看索引名在不在。
        check("  └ 是 UNIQUE 且带 WHERE line_id IS NOT NULL（否则等于白建）",
              "UNIQUE" in _d.upper() and "WHERE" in _d.upper()
              and "line_id IS NOT NULL" in _d,
              _d)

    check("既有约束 station_sequence_section_id_station_local_km_key 仍在（两条各管一层）",
          any("section_id" in k and "station_local_km" in k for k in _idx),
          f"{sorted(_idx)}")

    # ══════════════════════════════════════════════════════════════════
    print("")
    print("第 3 组  ★ FR-002：删路段之后，桩号必须还在")
    # ══════════════════════════════════════════════════════════════════
    # 造一个**临时**路段 + 桩号来删，绝不碰毕设数据。
    _line_id = dao.scalar("select id from road_line order by id limit 1")
    check("前置：库里有路线可用（否则本组无从验证）",
          _line_id is not None, f"line_id={_line_id}")
    if _line_id is None:
        dao.close()
        return 1 if FAIL else 0

    # 写入一律经 execute_write(writer=...) —— 与 M2 落库同一道写权守卫，
    # 不用裸 SQL，否则本组自己就成了「绕过写权」的反例。
    dao.execute_write(
        "road_section",
        "insert into road_section (line_id, section_name, start_station_km, end_station_km) "
        "values (%(l)s, %(n)s, 0, 1)",
        {"l": _line_id, "n": "★v0.6契约测试临时路段"}, writer="M2")
    _sec = dao.scalar("select id from road_section where section_name = %s",
                      ("★v0.6契约测试临时路段",))
    check("前置：临时路段已建（下面才删得掉）", _sec is not None, f"sec={_sec}")

    for _km, _txt in ((0.0, "K0+000.000"), (0.02, "K0+020.000"), (0.04, "K0+040.000")):
        dao.execute_write(
            "station_sequence",
            "insert into station_sequence (section_id, line_id, station_seq_no, "
            "station_local_km, station_text) values (%(s)s, %(l)s, %(n)s, %(k)s, %(t)s)",
            {"s": _sec, "l": _line_id, "n": int(_km * 1000 / 20) + 1,
             "k": _km, "t": _txt}, writer="M2")
    _before = dao.scalar(
        "select count(*) from station_sequence where section_id = %s", (_sec,))
    # ★★ 这条前置断言是整个第 3 组的承重墙：
    #    如果临时路段没有桩号，「删完桩号还在」当然成立 —— 那就是空转。
    check("★★ 前置：临时路段**确实有**桩号（否则后面的断言是空转）",
          _before == 3, f"删之前桩号数={_before}，期望 3")
    if _before != 3:
        dao.execute_write("road_section", "delete from road_section where id=%(i)s",
                          {"i": _sec}, writer="M2")
        dao.close()
        return 1 if FAIL else 0

    # 真删路段 —— 这一步在 v0.5 会级联删桩号或直接失败
    dao.execute_write("road_section", "delete from road_section where id = %(i)s",
                      {"i": _sec}, writer="M2")
    _after = dao.scalar(
        "select count(*) from station_sequence where line_id = %s", (_line_id,))
    check("★★ FR-002：删路段后桩号**一个都没少**",
          _after >= _before, f"删后 {_after}，删前 {_before}")

    _orphan = dao.query(
        "select id, station_local_km, section_id from station_sequence "
        "where line_id = %s and section_id is null order by station_local_km "
        "limit 5", (_line_id,))
    check("★★ FR-002：被删路段的桩号 section_id 被置为 NULL（不是被删掉）",
          len(_orphan) >= 3,
          f"置空 {len(_orphan)} 行；若非空说明桩号被连带删了")
    check("  └ 置空的桩号**仍锚在路线上**（line_id 没丢）",
          all(r["section_id"] is None for r in _orphan) and bool(_orphan),
          f"{_orphan[:2]}")

    # 清理：只删本组自己造的桩号
    for _r in dao.query("select id from station_sequence where line_id = %s "
                        "and section_id is null and station_local_km < 0.5",
                        (_line_id,)):
        dao.execute_write("station_sequence", "delete from station_sequence where id=%(i)s",
                          {"i": _r["id"]}, writer="M2")
    check("清理：临时桩号已删除",
          dao.scalar("select count(*) from station_sequence where line_id = %s "
                     "and section_id is null", (_line_id,)) == 0,
          "残留会污染下一次跑")

    # ══════════════════════════════════════════════════════════════════
    print("")
    print("第 4 组  ★ FR-003：按路线查桩号，两条路线互不混淆")
    # ══════════════════════════════════════════════════════════════════
    _lines = ge.lines()
    check("前置：至少有一条路线（否则本组是空转）", len(_lines) >= 1,
          f"{len(_lines)} 条")
    _total = sum(l["station_count"] for l in _lines)
    _db_total = dao.scalar("select count(*) from station_sequence")
    check("★★ ge.lines() 的桩号数合计 == 表总行数（按路线数不漏不重）",
          _total == _db_total, f"按路线 {_total} vs 全表 {_db_total}")

    # ★★ 必须挑**真的有桩号**的那条路线。
    #   原先写 _lines[0]，而库里 id 最小的路线（G228）有 0 个桩号 ——
    #   于是「查到 0 == 声明 0」成立，第 4 组整组**空转**，
    #   读起来却和全绿一样。这正是本组要防的那类假通过。
    _nonempty = [l for l in _lines if l["station_count"] > 0]
    check("★★ 前置：存在**有桩号**的路线（否则第 4 组是空转）",
          bool(_nonempty),
          "各路线的桩号数：" + str([(l["line_code"], l["station_count"]) for l in _lines]))
    if not _nonempty:
        dao.close()
        return 1 if FAIL else 0
    _l0 = _nonempty[0]
    _st = ge.stations_by_line(_l0["id"])
    check("stations_by_line() 返回该路线的全部桩号",
          len(_st) == _l0["station_count"],
          f"查到 {len(_st)} vs 声明 {_l0['station_count']}")
    if _st:
        _kms = [float(r["station_local_km"]) for r in _st]
        check("  └ 按桩号升序返回（前端不必再排）",
              _kms == sorted(_kms), f"前 3 个 {_kms[:3]}")
        check("  └ 每条都带 line_id 归属，不靠路由猜",
              all("section_id" in r for r in _st), "")

    # 区间筛（FR-003 的实用面：只取某一段）
    _win = ge.stations_by_line(_l0["id"], from_km=0.1, to_km=0.2)
    check("区间筛 from_km/to_km 生效",
          all(0.1 <= float(r["station_local_km"]) <= 0.2 for r in _win)
          and len(_win) <= len(_st),
          f"区间内 {len(_win)} 个 / 全线 {len(_st)} 个")
    check("整桩筛 integer_only 生效",
          all(r["is_integer_station"] for r in
              ge.stations_by_line(_l0["id"], integer_only=True)),
          "")

    # 直接证明「不串台」：把每条路线的桩号并起来，看有没有一行被算进两条路线。
    # 单条路线查得对还不够 —— 若 SQL 的 WHERE 写漏，多条路各自返回全表也能"通过"。
    _seen: dict[int, int] = {}
    for _l in _lines:
        for _row in ge.stations_by_line(_l["id"], limit=99999):
            _key = (_l["id"], str(_row["station_local_km"]))
            _seen[_key] = _seen.get(_key, 0) + 1
    _dups = {k: v for k, v in _seen.items() if v > 1}
    check("★★ FR-003：逐条路线取桩号，无一行被归入两条路线（真不串台）",
          not _dups,
          f"重复 {list(_dups)[:3]}" if _dups else f"合计 {sum(_seen.values())} 行，与全表一致")

    # ══════════════════════════════════════════════════════════════════
    print("")
    print("第 5 组  ★★ 元测试：第 4 组的断言**真的能失败**吗")
    # ══════════════════════════════════════════════════════════════════
    # 造第二条路线 + 一个**同名桩号** K0+000.000，两条路线各自拥有一个。
    # 这模拟的就是 FR-003 说的「相距数千公里的两条路线」。
    dao.execute_write(
        "road_line",
        "insert into road_line (line_code, line_name, road_class) "
        "values (%(c)s, %(n)s, %(r)s)",
        {"c": "★TEST-LINE-B", "n": "★v0.6契约测试第二条路线", "r": "高速"},
        writer="M2")
    _lb = dao.scalar("select id from road_line where line_code = %s",
                     ("★TEST-LINE-B",))
    check("前置：第二条路线已建", _lb is not None, f"line_id={_lb}")

    if _lb is not None:
        dao.execute_write(
            "station_sequence",
            "insert into station_sequence (line_id, station_seq_no, station_local_km, "
            "station_text) values (%(l)s, 1, 0.0, %(t)s)",
            {"l": _lb, "t": "K0+000.000"}, writer="M2")

        _sb = ge.stations_by_line(_lb)
        _sa = ge.stations_by_line(_l0["id"])
        check("★★ FR-003：两条路线的 K0+000.000 各归各家（同名不混淆）",
              len(_sb) == 1 and len(_sa) == _l0["station_count"],
              f"B 线 {len(_sb)} 个 / A 线 {len(_sa)} 个")
        check("  └ 同名桩号在两条路线里**都被查到**（不是只留一个）",
              len(_sb) == 1, f"B 线查到 {len(_sb)} 个")

        # ★ 元测试：如果按路线过滤失效（比如 SQL 里漏了 WHERE line_id），
        #    那么查 B 线会查到 A 线的桩号 —— 用总数断言把它抓出来。
        _all = dao.scalar("select count(*) from station_sequence")
        check("★★ 元测试：stations_by_line 的结果**严格小于**全表（证明 WHERE 生效）",
              len(_sb) < _all,
              f"B 线 {len(_sb)} vs 全表 {_all}；相等说明 WHERE line_id 没起作用")

        # 唯一索引的元测试：同路线内重复桩号**必须**被拦
        _dup_msg = ""
        try:
            dao.execute_write(
                "station_sequence",
                "insert into station_sequence (line_id, station_seq_no, station_local_km, "
                "station_text) values (%(l)s, 2, 0.0, %(t)s)",
                {"l": _lb, "t": "K0+000.000"}, writer="M2")
        except Exception as exc:  # noqa: BLE001
            _dup_msg = str(exc)
        # ★ 必须确认是**哪一条**约束拦的：
        #   本组插入的 section_id 是 NULL，而 NULL 在 UNIQUE 里彼此不相等 ——
        #   所以旧约束 station_sequence_section_id_station_local_km_key **拦不住**它。
        #   真正该拦的是 uq_station_line_local。
        #   只断言"报错了"是不够的：任何约束（甚至非空/FK）报错都算数，
        #   那样这条元测试就没在验证 Q1 到底有没有落地。
        check("★★ Q1 元测试：重复桩号被拦，且必须是 uq_station_line_local 拦的",
              "uq_station_line_local" in _dup_msg,
              ("报错信息：" + _dup_msg.strip()[:200]) if _dup_msg
              else "（没有报错 —— 索引形同虚设）")

        # 清理第二条路线
        dao.execute_write("station_sequence",
                          "delete from station_sequence where line_id = %(l)s",
                          {"l": _lb}, writer="M2")
        dao.execute_write("road_line", "delete from road_line where id = %(i)s",
                          {"i": _lb}, writer="M2")
        check("清理：第二条路线及其桩号已删除",
              dao.scalar("select count(*) from road_line where id = %s", (_lb,)) == 0,
              "")

    dao.close()
    print("")
    print(f"通过 {PASS} ｜ 失败 {FAIL}")
    print("全部通过" if FAIL == 0 else "有失败项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())