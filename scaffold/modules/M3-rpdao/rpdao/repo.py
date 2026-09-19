"""M3 各对象域仓储（Repository）——每个域的**唯一**数据出口。

分层约定：
  · ``DomainRepository``：通用能力（按表列/取单条/计数），只允许访问本域的表。
  · ``LoRepository`` / ``GeRepository`` 等：域特有查询（语义化命名，如 ``passages()``、
    ``stations()``，而不是让上层拼 SQL）。
  · 上层（M6）调用的是 ``dao.lo.passages(...)`` 这种**业务语义**方法，
    而不是 SQL 字符串——这样表结构变化时，改动被关在 DAO 内部。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, TYPE_CHECKING

from .catalog import DOMAINS, Domain
from .errors import ContractViolation, NotFound, UnknownTable
from .pool import quote_ident

if TYPE_CHECKING:  # 仅类型标注，避免运行期循环 import
    from .pool import Dao


class DomainRepository:
    """某个对象域的通用仓储。子类可加域特有的语义化查询。"""

    def __init__(self, dao: "Dao", domain: Domain) -> None:
        self._dao = dao
        self.domain = domain

    # -------------------------------------------------------------- 元信息
    @property
    def code(self) -> str:
        return self.domain.code

    @property
    def tables(self) -> tuple[str, ...]:
        """本域**已建**的物理表。"""
        return self.domain.tables

    def logical_entities(self) -> tuple[str, ...]:
        """本域在逻辑视图里有、但物理表还没建的实体（查询它们会报错）。"""
        return self.domain.logical

    def _check(self, table: str) -> str:
        """白名单校验：本域仓储不得越域取数。"""
        if table not in self.domain.tables:
            owner = self._owner_of(table)
            if owner != "?":
                raise ContractViolation(
                    f"{self.code} 域仓储不允许访问 {table}（该表属 {owner} 域）；"
                    f"请改用 dao.{owner.lower()}"
                )
            raise UnknownTable(f"{self.code} 域无此表：{table}；本域表 {self.tables}")
        return quote_ident(table)

    @staticmethod
    def _owner_of(table: str) -> str:
        for code, d in DOMAINS.items():
            if table in d.tables:
                return code
        return "?"

    # -------------------------------------------------------------- 通用查询
    def list_objects(self, table: str, *, limit: int = 100) -> list[dict[str, Any]]:
        t = self._check(table)
        return self._dao.query(f"SELECT * FROM {t} ORDER BY id LIMIT %(limit)s::int",
                               {"limit": limit})

    def get(self, table: str, obj_id: int) -> dict[str, Any]:
        t = self._check(table)
        row = self._dao.query_one(f"SELECT * FROM {t} WHERE id = %(id)s::bigint", {"id": obj_id})
        if row is None:
            raise NotFound(f"{table} 无 id={obj_id} 的对象")
        return row

    def count(self, table: str) -> int:
        t = self._check(table)
        return int(self._dao.scalar(f"SELECT count(*) AS n FROM {t}") or 0)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.code} {self.domain.name} 表{len(self.tables)}张>"


# ---------------------------------------------------------------------- LO 域
class LoRepository(DomainRepository):
    """LO 交通荷载域。骨架期唯一有真实数据的域，故这里的方法最完整。"""

    #: 过车记录查询。**可选筛选项一律显式 cast**：参数传 NULL 时 PostgreSQL
    #: 无法从 "($1 IS NULL OR ...)" 推断类型，会抛 AmbiguousParameter（骨架期实跑
    #: 复现过，导致该接口恒定 500）。改这里务必保留 ::类型。
    PASSAGES_SQL = """
    SELECT r.id, r.pass_time, r.lane_no, r.direction, r.axle_type_code, r.axle_num,
           r.speed_kmh, r.gross_weight_kg, r.overload_flag, r.overload_rate, r.esal,
           r.plate_no, r.quality_code, m.station_text
    FROM wim_axle_record r
    LEFT JOIN monitor_cross_section m ON m.id = r.cross_section_id
    WHERE (%(from_ts)s::timestamptz IS NULL OR r.pass_time >= %(from_ts)s::timestamptz)
      AND (%(to_ts)s::timestamptz   IS NULL OR r.pass_time <  %(to_ts)s::timestamptz)
      AND (%(station)s::text          IS NULL OR m.station_text = %(station)s::text)
      AND (%(overload_only)s::boolean = false OR r.overload_flag = true)
    ORDER BY r.pass_time DESC
    LIMIT %(limit)s::int
    """

    PASSAGE_ONE_SQL = """
    SELECT r.*, m.station_text FROM wim_axle_record r
    LEFT JOIN monitor_cross_section m ON m.id = r.cross_section_id
    WHERE r.id = %(rid)s::bigint
    """

    PASSAGE_AXLES_SQL = """
    SELECT axle_seq, group_seq, axle_weight_kg, group_weight_kg, axle_dist_mm
    FROM wim_axle_detail WHERE record_id = %(rid)s::bigint ORDER BY axle_seq
    """

    HOURLY_SQL = """
    SELECT date_trunc('hour', pass_time)          AS bucket,
           count(*)                               AS passages,
           count(*) FILTER (WHERE overload_flag)  AS overloaded,
           round(sum(esal)::numeric, 2)           AS esal_sum,
           round(avg(speed_kmh)::numeric, 1)      AS avg_speed_kmh,
           round(max(gross_weight_kg)::numeric, 0) AS max_gross_kg
    FROM wim_axle_record
    WHERE pass_time >= %(from_ts)s::timestamptz AND pass_time < %(to_ts)s::timestamptz
      AND (%(station)s::text IS NULL OR cross_section_id IN (
            SELECT id FROM monitor_cross_section WHERE station_text = %(station)s::text))
    GROUP BY 1 ORDER BY 1
    """

    def passages(
        self,
        *,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        station: str | None = None,
        overload_only: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """按时间/桩号/超载筛过车记录，时间倒序。"""
        return self._dao.query(self.PASSAGES_SQL, {
            "from_ts": from_ts, "to_ts": to_ts, "station": station,
            "overload_only": overload_only, "limit": limit,
        })

    def passage(self, record_id: int) -> dict[str, Any]:
        """单条过车记录 ＋ 轴明细（对象-链接展开）。"""
        row = self._dao.query_one(self.PASSAGE_ONE_SQL, {"rid": record_id})
        if row is None:
            raise NotFound(f"过车记录不存在：{record_id}")
        row["axles"] = self._dao.query(self.PASSAGE_AXLES_SQL, {"rid": record_id})
        return row

    def axles(self, record_id: int) -> list[dict[str, Any]]:
        return self._dao.query(self.PASSAGE_AXLES_SQL, {"rid": record_id})

    def hourly_buckets(self, day: date, *, station: str | None = None) -> list[dict[str, Any]]:
        """某天按小时聚合的过车量/超载数/ESAL/均速/最大总重。"""
        return self._dao.query(self.HOURLY_SQL, {
            "from_ts": datetime.combine(day, datetime.min.time()),
            "to_ts": datetime.combine(day, datetime.max.time()),
            "station": station,
        })

    def daily_summary(self, day: date, *, station: str | None = None) -> dict[str, Any]:
        """小时桶的汇总——M6 的 /v1/metrics/wim_hourly 直接返回它。

        把"求和"放在 DAO 而不是 M6：这是**指标语义**的一部分，
        换域/换口径时不应改出口服务。故 M6 拿到的是已经算好的结论。
        """
        rows = self.hourly_buckets(day, station=station)
        total = sum(r["passages"] for r in rows)
        overloaded = sum(r["overloaded"] for r in rows)
        return {
            "date": day.isoformat(),
            "station": station,
            "passages": total,
            "overloaded": overloaded,
            "overload_ratio": round(overloaded / total, 4) if total else None,
            "esal_sum": float(sum(r["esal_sum"] or 0 for r in rows)),
            "buckets": rows,
        }


# ---------------------------------------------------------------------- 工厂
class GeRepository(DomainRepository):
    """GE 道路几何域。

    为什么这个域不能照搬 `list_objects` 那套平铺查
    ---------------------------------------------------------------------------
    GE 的数据不是"一堆并列对象"，而是**以 `road_section` 为根的一棵树**：
    `station_sequence` / `alignment_pi` / `alignment_element` 全部 FK 锚在 section 上。
    平铺查「所有交点」在只有一个路段时看着没问题，路段一多就**静默串台**——
    把 A 路的交点混进 B 路的线形里，而两条路的桩号都从 0 开始，混了也看不出来。
    所以这里的方法一律**按路段取子树**，不带 section 的取法不提供。

    ⚠ 可选筛选项一律显式 cast（见 `LoRepository.PASSAGES_SQL` 的说明）：
    参数传 NULL 时 PostgreSQL 无法从 `(%(x)s IS NULL OR ...)` 推断类型，
    会抛 `AmbiguousParameter`，接口恒定 500。骨架期实跑复现过。
    """

    #: 路段列表。以 road_section 为根，挂上所属路线、设计项目、分段属性和三类几何计数。
    #: 等级不在这里算（见 completeness()），避免"列表页"被迫扫全表。
    SECTIONS_SQL = """
    SELECT s.id, s.section_name, s.start_station_text, s.end_station_text,
           s.start_station_km, s.end_station_km, s.length_m,
           s.pavement_type, s.climate_zone,
           l.id   AS line_id,   l.line_code, l.line_name, l.road_class,
           l.design_speed, l.lane_count,
           p.id   AS project_id, p.project_name, p.project_uid, p.designer,
           a.road_grade, a.design_speed_kmh, a.cross_section_form, a.roadway_width_m,
           (SELECT count(*) FROM station_sequence  t WHERE t.section_id = s.id) AS station_count,
           (SELECT count(*) FROM alignment_pi      t WHERE t.section_id = s.id) AS pi_count,
           (SELECT count(*) FROM alignment_element t WHERE t.section_id = s.id) AS element_count
    FROM road_section s
    JOIN road_line l        ON l.id = s.line_id
    LEFT JOIN design_project p      ON p.id = s.design_project_id
    LEFT JOIN section_design_attr a ON a.section_id = s.id
    ORDER BY l.line_code, s.id
    """

    ONE_SECTION_SQL = """
    SELECT s.id, s.section_name, s.start_station_text, s.end_station_text,
           s.start_station_km, s.end_station_km, s.length_m,
           s.pavement_type, s.climate_zone, s.direction,
           l.line_code, l.line_name, l.road_class, l.design_speed, l.lane_count,
           p.project_name, p.project_uid, p.source_file AS project_source_file,
           a.road_grade, a.cross_section_form, a.roadway_width_m,
           a.carriageway_crossfall_pct, a.shoulder_crossfall_pct,
           a.max_superelev_pct, a.superelev_rotate_mode, a.widening_mode,
           a.source_file AS attr_source_file
    FROM road_section s
    JOIN road_line l        ON l.id = s.line_id
    LEFT JOIN design_project p      ON p.id = s.design_project_id
    LEFT JOIN section_design_attr a ON a.section_id = s.id
    WHERE s.id = %(sid)s::bigint
    """

    #: 桩号序列。**桩号是 GE 域的一等实体**：其余逐桩数据全部锚在它上面，
    #: 所以这个查询是几何浏览页的主视图。
    STATIONS_SQL = """
    SELECT station_seq_no, station_local_km, station_absolute_km,
           station_text, station_type, is_integer_station
    FROM station_sequence
    WHERE section_id = %(sid)s::bigint
      AND (%(from_km)s::numeric IS NULL OR station_local_km >= %(from_km)s::numeric)
      AND (%(to_km)s::numeric   IS NULL OR station_local_km <= %(to_km)s::numeric)
      AND (%(integer_only)s::boolean = false OR is_integer_station = true)
    ORDER BY station_seq_no
    LIMIT %(limit)s::int
    """

    PIS_SQL = """
    SELECT pi_seq, pi_type, x_coord, y_coord, radius_m,
           spiral_ls1_m, spiral_ls2_m, spiral_a1, spiral_a2,
           prev_tangent_len_m, tangent_len_m, tangent_len2_m,
           arc_len_m, curve_len_m, deflection_deg, external_m
    FROM alignment_pi
    WHERE section_id = %(sid)s::bigint
    ORDER BY pi_seq
    """

    #: 线形单元。`pi_id` 为空的是**直线段**——直线不属于任何交点，这是正常的，
    #: 不是漏挂（实测 33 个单元里 9 条直线、`pi_id` 全空，双向都成立）。
    #:
    #: `azimuth_deg` 就是**起点**方位角（表里另有 `end_azimuth_deg`）。实测确认：
    #: 直线段两端相等；过渡段起止相接，且等于下一段的起点。表里"有 end_ 却没有
    #: start_"的写法会让调用方猜错 —— 几何浏览页第一版正是把它当成了并不存在的
    #: `start_azimuth_deg`，于是整列静默显示成空（值没错，是名字对不上）。
    #: 在 DAO 里显式起别名，属于这一层"语义化命名"的职责。
    ELEMENTS_SQL = """
    SELECT e.element_seq, e.element_type, e.pi_id,
           e.start_station_km, e.end_station_km, e.length_m,
           e.start_x, e.start_y, e.end_x, e.end_y,
           e.center_x, e.center_y,
           e.azimuth_deg     AS start_azimuth_deg,
           e.end_azimuth_deg AS end_azimuth_deg,
           e.radius_start_m, e.radius_end_m,
           p.pi_seq
    FROM alignment_element e
    LEFT JOIN alignment_pi p ON p.id = e.pi_id
    WHERE e.section_id = %(sid)s::bigint
    ORDER BY e.element_seq
    """

    #: 几何完整度等级规则。**必须与 M2 `adapters/base._LEVEL_RULES` 一致。**
    #:
    #: 为什么要写两遍：M3 不许 import M2（模块之间只认契约，见架构约定），
    #: 而"哪个段齐了算几级"这条规则两边都要用。既然只能各写一遍，
    #: 就用测试把两遍钉在一起（tests/contract/test_dao_contract.py 里有交叉核对）。
    #: 改这里必须同时改那边，否则测试会红。
    #: 每条是 (等级, 该级要求的段, 组合方式)。**组合方式也在数据里**，
    #: 所以上面那条 == 交叉核对会把组合方式一起钉住 —— 只对规则、不对组合方式的话，
    #: 两边可以一个 any 一个 all 而测试全绿，等级却在两处给出不同答案。
    LEVEL_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
        ("L4", ("cross_section",), "any"),
        ("L3", ("profile_grade_point", "profile_ground_point"), "all"),
        ("L2", ("alignment_pi", "alignment_element"), "any"),
        ("L1", ("station_sequence",), "any"),
    )

    #: 段 → (表, **定位列**)。
    #:
    #: 定位列**不统一**，这是实测出来的，不是推测：
    #:   · 多数表锚在 `section_id` 上（它们直接属于某个路段）；
    #:   · `profile_ground_point` / `geometry_point` 锚在 **`station_id`** 上
    #:     —— 它们是逐桩数据，桩号才是它们的父，故只能先按路段取出桩号再定位。
    #:
    #: 之所以把"锚定列"声明成**数据**而不是直接写四条 SQL：这样"锚定列是否真实存在"
    #: 这条检查就变成对**数据**比对，不必去解析 SQL。第一版写成四条手写 SQL 时，
    #: 检查器只能拿整条 SQL 里的 `WHERE x =` 去比外层表，结果被**子查询里的
    #: `WHERE section_id =`（那是 station_sequence 的列）假报了一次 ——
    #: 检查器自身的假报比漏报更消耗信任，所以改成现在这样。
    SEGMENT_ANCHOR: dict[str, tuple[str, str]] = {
        "station_sequence":    ("station_sequence",    "section_id"),
        "station_equation":    ("station_equation",    "section_id"),
        "alignment_pi":        ("alignment_pi",        "section_id"),
        "alignment_element":   ("alignment_element",   "section_id"),
        "profile_grade_point": ("profile_grade_point", "section_id"),
        "profile_ground_point": ("profile_ground_point", "station_id"),
        "geometry_point":      ("geometry_point",      "station_id"),
    }

    #: 逐桩定位列的名字。值为它时，计数要经 `station_sequence` 中转。
    STATION_ANCHOR: str = "station_id"

    #: ⚠ 这张表**不是**「所有可读段」的清单，而是**第一批（v0.3）**那一组 ——
    #: 它与 `adapters/base.SEGMENTS` 逐条对齐，测试会交叉核对「不多不少」。
    #: `.CTR`/`.WID`/`.SUP` 与 v0.5 的两张逐桩表**都不在这里**，它们照常可读
    #: （按 section_id 直接取），只是不走这套「逐桩经 station_sequence 中转」的定位。
    #: 我一度把 earthwork_section / roadbed_design_point 加了进来，测试当场报
    #: 「M3 独有 2 段」—— 那条交叉核对正是为了挡住这种「顺手往里加」。
    #:
    #: 属 DDL v0.4 第二批、**表还没建**的段。与"表已建但 0 行"含义不同：
    #: 前者是 schema 没到，后者是解析器没做。混为一谈会让"为什么只有 L2"
    #: 这个问题得到错误答案。
    SEGMENTS_NOT_BUILT: tuple[str, ...] = ("cross_section",)

    @classmethod
    def count_sql(cls, seg: str) -> str:
        """由 `SEGMENT_ANCHOR` 生成计数 SQL（**不要手写**，手写就会与声明脱钩）。"""
        table, col = cls.SEGMENT_ANCHOR[seg]
        if col == cls.STATION_ANCHOR:
            return (f"SELECT count(*) FROM {table} WHERE {col} IN "
                    f"(SELECT id FROM station_sequence WHERE section_id = %(sid)s::bigint)")
        return f"SELECT count(*) FROM {table} WHERE {col} = %(sid)s::bigint"

    def sections(self) -> list[dict[str, Any]]:
        """所有路段（含所属路线/项目/分段属性/三类几何计数）。"""
        return self._dao.query(self.SECTIONS_SQL)

    @staticmethod
    def level_hit(required: tuple[str, ...], present: set[str], mode: str) -> bool:
        """单个等级的判定。**必须与 M2 `adapters/base.level_hit` 逐字同义。**

        M3 不许 import M2，所以只能各写一遍 —— 那就用测试拿着同一批用例对拍两边，
        而不是靠"我记得两处写得一样"。归一化成"有数据的段集合"之后，
        这个函数就是纯函数，离线可测。
        """
        got = [s for s in required if s in present]
        return bool(got) if mode == "any" else len(got) == len(required)

    def section(self, section_id: int) -> dict[str, Any]:
        row = self._dao.query_one(self.ONE_SECTION_SQL, {"sid": section_id})
        if row is None:
            raise NotFound(f"路段不存在：section_id={section_id}")
        return row

    def stations(self, section_id: int, *, from_km: float | None = None,
                 to_km: float | None = None, integer_only: bool = False,
                 limit: int = 500) -> list[dict[str, Any]]:
        """某路段的桩号序列，可按区间/是否整桩筛。"""
        return self._dao.query(self.STATIONS_SQL, {
            "sid": section_id, "from_km": from_km, "to_km": to_km,
            "integer_only": integer_only, "limit": limit,
        })

    def alignment(self, section_id: int) -> dict[str, Any]:
        """某路段的平面线形：交点链 + 线形单元链（一次取回，前端不必拼两次）。"""
        return {
            "section_id": section_id,
            "pis": self._dao.query(self.PIS_SQL, {"sid": section_id}),
            "elements": self._dao.query(self.ELEMENTS_SQL, {"sid": section_id}),
        }

    def completeness(self, section_id: int) -> dict[str, Any]:
        """几何完整度报告：**为什么是 L2 而不是 L3**，要能一眼看出来。

        等级本身是派生量（由"哪些段有数据"决定），不是存下来的字段 ——
        所以这里现算，并把算它的依据（每张表的行数）一并返回，
        让"等级"这个结论**可被核对**，而不是一个只能相信的字符串。
        """
        counts: dict[str, int] = {}
        for seg in self.SEGMENT_ANCHOR:
            counts[seg] = int(self._dao.scalar(self.count_sql(seg), {"sid": section_id}) or 0)
        # 表还没建的段：记为 0 参与判级，但在 missing 里与"表已建但没数据"分开报
        for seg in self.SEGMENTS_NOT_BUILT:
            counts[seg] = 0

        # 归一化成"有数据的段集合"再判定：与 M2 用同一个纯函数，
        # 差别只在"有没有数据"怎么算（这里是行数 > 0）。
        present_set = {s for s, n in counts.items() if n}
        level = "L0"
        reason = "没有任何几何段"
        for lv, required, mode in self.LEVEL_RULES:
            if not self.level_hit(required, present_set, mode):
                continue
            hit = [s for s in required if s in present_set]
            if mode == "all" or len(required) == 1:
                reason = f"{'／'.join(hit)} 有数据"
            else:
                reason = f"{'／'.join(hit)} 有数据"
            break
        else:
            # 差一档时，把"还差什么"直接写进理由 —— 这个方法存在的意义就是
            # 回答"为什么是 L2 而不是 L3"，只报命中的段回答不了这个问题。
            for lv, required, mode in self.LEVEL_RULES:
                need = [s for s in required if s not in present_set]
                have = [s for s in required if s in present_set]
                if have and need:
                    reason = (f"{'／'.join(have)} 有数据，"
                              f"但 {lv} 要求 {'／'.join(required)} **全部**有数据，"
                              f"缺 {'／'.join(need)}")
                    break

        present = {s: n for s, n in counts.items() if n}
        missing = [s for s in counts if not counts.get(s)]
        return {
            "section_id": section_id,
            "geometry_level": level,
            "level_reason": reason,
            "present": present,
            "missing": missing,
            # 分开报：表还没建（schema 没到）≠ 表已建但没数据（解析器没做）
            "missing_not_built": [s for s in self.SEGMENTS_NOT_BUILT],
            "missing_no_data": [s for s in missing
                                if s not in self.SEGMENTS_NOT_BUILT],
            "counts": counts,
        }


_BUILDERS = {"LO": LoRepository, "GE": GeRepository}


def build_repository(code: str, dao: "Dao") -> DomainRepository:
    """域仓储工厂。没有专属子类的域先用通用仓储（接口一致，后续按需下沉）。"""
    return _BUILDERS.get(code, DomainRepository)(dao, DOMAINS[code])
