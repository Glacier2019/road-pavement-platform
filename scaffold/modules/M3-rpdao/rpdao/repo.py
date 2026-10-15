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

from .catalog import ALL_TABLES, CROSS_KIND, CROSS_ORDER_KEY, CROSS_TABLES, DOMAINS, Domain, domain_of
from .errors import ContractViolation, CrsBindingError, NotFound, UnknownTable
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
    #: ★★ 2026-10-14 加 `offset`（用户裁决的「甲」）。加之前这条路**不能翻页**：
    #  实测 `cross_section_ground_point` 有 2215 行，而 `?limit=1000&offset=1000`
    #  回的仍是 id=[1, 2] —— 第 1001..2215 行**永远取不到**。而契约里还明写着
    #  「truncated: true → 客户端据此翻页」，那句在当时是**空话**。
    #
    #  ★ `offset` 默认 0 ⇒ 既有的 `list_objects(t, limit=n)` 调用**行为逐位不变**。
    #  ★★ `ORDER BY id` 是翻页的**前提**：没有全序，LIMIT/OFFSET 会重复或漏行。
    #     这一列本来就在，但它的作用从「让输出好看」升格成「让分页正确」。
    def list_objects(self, table: str, *, limit: int = 100,
                     offset: int = 0) -> list[dict[str, Any]]:
        t = self._check(table)
        return self._dao.query(
            f"SELECT * FROM {t} ORDER BY id LIMIT %(limit)s::int OFFSET %(offset)s::int",
            {"limit": limit, "offset": offset})

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
    LIMIT %(limit)s::int OFFSET %(offset)s::int
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
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """按时间/桩号/超载筛过车记录，时间倒序。

        ★★ 2026-10-14 加 `offset`。加之前这条路**不能翻页**：
          `ORDER BY pass_time DESC LIMIT n` 只能取最早那一段，
          而调用方**拿不到任何信号**说明后面还有。
          `offset` 默认 0 ⇒ 既有的 `passages(limit=n)` 调用**行为逐位不变**。
        """
        return self._dao.query(self.PASSAGES_SQL, {
            "from_ts": from_ts, "to_ts": to_ts, "station": station,
            "overload_only": overload_only, "limit": limit, "offset": offset,
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
    #:
    #: ★ v0.6（契约变更工单 005）新增两套 crs_ 列：`s.crs_*` 与 `p.crs_*`。
    #:  **不新增连接** —— `LEFT JOIN design_project p` 本来就在（同一张表的既有连接，
    #:  只是从前没查这几列）。`LEFT` 而非 `INNER` 意味着**项目级基准天然可空**，
    #:  这正是一级路段的正常状态：路段有、项目没有 ⇒ 应当取路段的。
    #:
    #:  别名带层级前缀（section_ / project_），因为**同一个名字要出现两次**。
    #:  取哪一级、两级冲突怎么办，是 `effective_crs()` 的判断，不是 SQL 的判断 ——
    #:  SQL 只如实把两级都取出来（宪法 II：读只经 M3；判断留给能同时看到两级的地方）。
    SECTIONS_SQL = """
    SELECT s.id, s.section_name, s.start_station_text, s.end_station_text,
           s.start_station_km, s.end_station_km, s.length_m,
           s.pavement_type, s.climate_zone,
           l.id   AS line_id,   l.line_code, l.line_name, l.road_class,
           l.design_speed, l.lane_count,
           p.id   AS project_id, p.project_name, p.project_uid, p.designer,
           a.road_grade, a.design_speed_kmh, a.cross_section_form, a.roadway_width_m,
           s.crs_epsg                 AS section_crs_epsg,
           s.crs_zone_number          AS section_crs_zone_number,
           s.crs_central_meridian_deg AS section_crs_central_meridian_deg,
           s.crs_zone_width_deg       AS section_crs_zone_width_deg,
           s.crs_assumed              AS section_crs_assumed,
           s.crs_evidence             AS section_crs_evidence,
           p.crs_epsg                 AS project_crs_epsg,
           p.crs_zone_number          AS project_crs_zone_number,
           p.crs_central_meridian_deg AS project_crs_central_meridian_deg,
           p.crs_zone_width_deg       AS project_crs_zone_width_deg,
           p.crs_assumed              AS project_crs_assumed,
           p.crs_evidence             AS project_crs_evidence,
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
    SELECT s.id, s.design_project_id,
           s.section_name, s.start_station_text, s.end_station_text,
           s.start_station_km, s.end_station_km, s.length_m,
           s.pavement_type, s.climate_zone, s.direction,
           l.line_code, l.line_name, l.road_class, l.design_speed, l.lane_count,
           p.project_name, p.project_uid, p.source_file AS project_source_file,
           a.road_grade, a.cross_section_form, a.roadway_width_m,
           a.carriageway_crossfall_pct, a.shoulder_crossfall_pct,
           a.max_superelev_pct, a.superelev_rotate_mode, a.widening_mode,
           a.source_file AS attr_source_file,
           s.crs_epsg                 AS section_crs_epsg,
           s.crs_zone_number          AS section_crs_zone_number,
           s.crs_central_meridian_deg AS section_crs_central_meridian_deg,
           s.crs_zone_width_deg       AS section_crs_zone_width_deg,
           s.crs_assumed              AS section_crs_assumed,
           s.crs_evidence             AS section_crs_evidence,
           p.crs_epsg                 AS project_crs_epsg,
           p.crs_zone_number          AS project_crs_zone_number,
           p.crs_central_meridian_deg AS project_crs_central_meridian_deg,
           p.crs_zone_width_deg       AS project_crs_zone_width_deg,
           p.crs_assumed              AS project_crs_assumed,
           p.crs_evidence             AS project_crs_evidence
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
    LIMIT %(limit)s::int OFFSET %(offset)s::int
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

    WIDTHS_SQL = """
    SELECT side, seq_no, group_seq, station_km, median_width_m,
           half_carriageway_width_m, extra_lane_flag, hard_shoulder_width_m,
           earth_shoulder_width_m, extra_lane_file, remark
    FROM roadbed_width
    WHERE section_id = %(sid)s::bigint
    ORDER BY station_km, side, group_seq, seq_no
    """

    SUPERELEV_SQL = """
    SELECT transition_seq, station_km, earth_shoulder_left_pct, hard_shoulder_left_pct,
           lane_left_pct, lane_right_pct, hard_shoulder_right_pct,
           earth_shoulder_right_pct, remark
    FROM superelev_transition
    WHERE section_id = %(sid)s::bigint
    ORDER BY transition_seq
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
        # ↓ v0.5 K 节新增。★锚定列是 **section_id 而不是 station_id**：
        #   .HDM 实测 333 个断面，station_sequence 只有 332 个（多一个 5701.461），
        #   **不是子集**，所以 cross_section_ground_point 直接带 station_km，
        #   不走「逐桩经 station_sequence 中转」那套。计数因此与 .CTR/.SUP/.WID 同类。
        "cross_section":       ("cross_section_ground_point", "section_id"),
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
    #: 属 DDL 某批次、**表还没建**的段。与"表已建但 0 行"含义不同：
    #: 前者是 schema 没到，后者是解析器没做。混为一谈会让"为什么只有 L2"
    #: 这个问题得到错误答案。
    #:
    #: ★ v0.5 K 节起**清空** —— 最后一条 cross_section 已建表
    #:   （cross_section_ground_point，见 89_migrate_v05_hdm.sql），
    #:   故它已挪进上面的 SEGMENT_ANCHOR。
    #:   ⚠ 但 **L4 仍然到不了** —— 这次变的是**原因**，不是结论：
    #:     改之前：schema 没到（表不存在）
    #:     改之后：解析器没做（表在，但 .HDM 适配器还没写，0 行）
    #:   这个区别正是本类返回里 missing_not_built / missing_no_data 分开报的意义。
    SEGMENTS_NOT_BUILT: tuple[str, ...] = ()

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

    @classmethod
    def derive_level(cls, present: set[str]) -> tuple[str, str]:
        """由"有数据的段集合"推出 (等级, 理由)。**纯函数，不碰库，可离线对拍。**

        与 M2 `adapters/base.derive_level` 同义。M3 不许 import M2，所以各写一遍 ——
        那就用测试拿同一批输入对拍两边。

        ⚠ 这个函数是**因为一个真 bug 才被抽出来的**：
          原来这段逻辑内联在 `completeness()` 里，循环里只算了 `reason`，
          **忘了写 `level = lv`** —— 于是 `geometry_level` 恒为 `"L0"`，
          而 `level_reason` 是对的（"cross_section 有数据"）。
          结果是「结论错、依据对」这种最刺眼的组合：毕设路段明明有 2215 个
          横断面测点（L4 的条件），徽章却显示 L0。

          为什么原来的测试没抓住：交叉核对只比了 `LEVEL_RULES`（规则表）和
          `level_hit`（谓词）—— **两个输入都对，用它们的那段循环错了**。
          所以现在把判级本身也变成纯函数，让测试能直接喂输入、断言输出。
        """
        for lv, required, mode in cls.LEVEL_RULES:
            if not cls.level_hit(required, present, mode):
                continue
            hit = [s for s in required if s in present]
            # ★ 这一行就是原来漏掉的
            return lv, f"{'／'.join(hit)} 有数据"

        # 一档都没命中：把"最接近的那一档还差什么"写进理由 ——
        # 这个方法存在的意义就是回答"为什么是 L2 而不是 L3"，
        # 只报命中的段回答不了这个问题。
        for lv, required, mode in cls.LEVEL_RULES:
            need = [s for s in required if s not in present]
            have = [s for s in required if s in present]
            if have and need:
                return "L0", (f"{'／'.join(have)} 有数据，"
                              f"但 {lv} 要求 {'／'.join(required)} **全部**有数据，"
                              f"缺 {'／'.join(need)}")
        return "L0", "没有任何几何段"

    @staticmethod
    def effective_crs(row: dict[str, Any]) -> dict[str, Any]:
        """由一行（路段 + 所属项目）算出**生效的**坐标基准。**纯函数，不碰库，可离线对拍。**

        ★★★ 为什么这是 005 的核心，也是最容易被写错的地方
        ---------------------------------------------------
        两级都存在，所以「用哪个」**不是选择题，是一致性检查**（Q1b = B）。
        写成「路段有就用路段、没有就用项目」——那是**级联**，会静默放过
        「两级都填了、但填的不一样」这个真正的错误：它看起来像一次正常回退。

        逐字实现 `specs/005-project-crs-binding/data-model.md` E2 的五态表：

          | # | 项目级 | 路段级       | 生效值 | source    | 行为                    |
          |---|--------|--------------|--------|-----------|-------------------------|
          | 1 | 有     | 有，且相等   | 该值   | project   | 通过                    |
          | 2 | 有     | 有，不相等   | —      | —         | ★ 抛错，说出两级各是什么 |
          | 3 | 有     | 没填         | 项目的 | project   | 通过                    |
          | 4 | 没填   | 有           | 路段的 | section   | 通过（FR-010）          |
          | 5 | 没填   | 没填         | —      | —         | ★ 抛错（有类型的「没有」）|

        ★★ 第 4 条为什么**必须**通过而不是报错：实测路段 1「滨海大通道试验段」
        `design_project_id = NULL`。判为报错，那个路段就**永远无法定位** ——
        那是把能用的数据锁死，不是在防错。而且它在数据层已经是被处理过的形状
        （`LEFT JOIN design_project` 本来就会给出 `project_id = NULL`）。

        ★★ **`source` 是溯源，不是优先级顺序。** 第 1 条与第 3 条生效值相同、
        `source` 都是 `project` —— 路段级填了相同的值，也不改变
        「权威来源仍是项目级」这个事实。人要知道**这个值是在哪一级被写下的**，
        这样他要改它时，知道该去改哪一行。

        ★ 第 5 条抛错而不是返回 None：见 `CrsBindingError` 的说明。

        参数
        ----
        row：能同时看到两级的字典。`SECTIONS_SQL` / `ONE_SECTION_SQL` 的输出
        直接可用（别名 `section_crs_*` / `project_crs_*`）。缺键按「没填」处理 ——
        因为「没填」与「没有这个键」在五态表里是同一档，不该让调用方先补齐键。
        """
        def cell(prefix: str, name: str) -> Any:
            return row.get(f"{prefix}_crs_{name}")

        s_epsg = cell("section", "epsg")
        p_epsg = cell("project", "epsg")

        # ---- 第 5 条：两级都没有 --------------------------------------------
        # 放在最前面，因为它不是「比较失败」，是「没有什么可比」。
        if s_epsg is None and p_epsg is None:
            # ★ E5 要求这不只是「有类型的没有」，还要**说清为什么**。
            #   于是这里必须指出**是谁**没配 —— 面对上千条路段时，
            #   「两级都没有」不告诉运维该去改哪一行。
            #   ★★ 而「根本没有所属项目」与「项目在、但项目自己没配」
            #     是两件要改不同地方的事，所以 `design_project_id`
            #     为此在 `ONE_SECTION_SQL` 里补了一列。
            _sid = row.get("id")
            _pid = row.get("design_project_id")
            _who = f"路段 {_sid}" if _sid is not None else "这条路段"
            if _pid is None:
                _why = "它没有所属项目（design_project_id 为空），路段自身也没配 crs_epsg"
            else:
                _why = f"它和所属项目（id={_pid}）都没有配 crs_epsg"
            raise CrsBindingError(
                f"{_who} 没有配坐标基准：{_why}。"
                "区域帧不猜默认带号 —— 猜错的代价是位置差几百公里，"
                "而且在局部视图里看不出来。请给这条路段或它所属的项目配一个 EPSG。",
                two_levels_note=(
                    f"section_id={_sid}；"
                    f"design_project_id={_pid if _pid is not None else 'NULL'}；"
                    "section_crs_epsg=NULL；project_crs_epsg=NULL"),
            )

        # ---- 第 2 条：两级都有且不等 ----------------------------------------
        # ★ 这一条是 005 存在的理由（Q1b = B）。错误信息 MUST 说出两级各是什么（SC-009）——
        #   只报「不一致」，用户得自己去两处查，那就把一次报错变成了两次查询。
        if s_epsg is not None and p_epsg is not None and int(s_epsg) != int(p_epsg):
            raise CrsBindingError(
                f"两级坐标基准不一致：项目级是 EPSG {p_epsg}，"
                f"路段级是 EPSG {s_epsg}。同一个路段只能有一个基准 —— "
                "长线状工程跨越两个投影带时，规范上按整条线一个中央子午线处理，"
                "所以「两级不同」几乎总是录入错误。"
                "要改就改一处，让它与另一处相同。",
                two_levels_note=(f"project_crs_epsg={p_epsg}；"
                                 f"section_crs_epsg={s_epsg}"),
            )

        # ---- 第 1 / 3 条：项目级有 ⇒ 项目级说了算 --------------------------
        # 第 1 条（路段也填了、且相等）也走这里，且 source 仍是 project。
        if p_epsg is not None:
            return {
                "epsg": int(p_epsg),
                "zone_number": cell("project", "zone_number"),
                "central_meridian_deg": cell("project", "central_meridian_deg"),
                "zone_width_deg": cell("project", "zone_width_deg"),
                "assumed": cell("project", "assumed"),
                "evidence": cell("project", "evidence"),
                "source": "project",
            }

        # ---- 第 4 条：项目没有（含路段无项目）、路段有 ----------------------
        return {
            "epsg": int(s_epsg),
            "zone_number": cell("section", "zone_number"),
            "central_meridian_deg": cell("section", "central_meridian_deg"),
            "zone_width_deg": cell("section", "zone_width_deg"),
            "assumed": cell("section", "assumed"),
            "evidence": cell("section", "evidence"),
            "source": "section",
        }

    def section(self, section_id: int) -> dict[str, Any]:
        row = self._dao.query_one(self.ONE_SECTION_SQL, {"sid": section_id})
        if row is None:
            raise NotFound(f"路段不存在：section_id={section_id}")
        return row

    def _require_section(self, section_id: int) -> None:
        """先证明**路段存在**，再回答"它有没有数据"。

        ★★★ 为什么每个按路段取数的方法都必须先过这一道（D-029）
        ------------------------------------------------------------
        本域有六个"按路段取一组行"的方法：`stations` / `alignment` /
        `widths` / `superelevation` / `elements_by_station` /
        `samples_by_station`。它们原先**都不判断路段存不存在**，
        于是 `section_id=99999` 一路走到底，返回 **200 + 空列表**。

        而 `section()`（本类）和 `/summary`、`/geo`、`/geo/reverse`、
        `/geo/route`、`/monitor-sections` 早就判了，回 **404**。

        ⇒ 同一个 `section_id`、同一棵 `/v1/geometry/sections/{id}/` 子树，
          **五个兄弟回 404、六个兄弟回 200 空**。调用方看到 200 空，
          无法区分"这个路段没有宽度数据"和"根本没有这个路段"。

        ★★ 这条判据在本文件里**早就写着**，只是漏了兄弟：
          `samples_by_station` 对 `seg` 写着 ——
            「空列表会被读成『这段在区间内没有数据』，而实际是『段名写错了』」
          对 `seg` 用了，对 `section_id` **没有用**。同一个判断、同一段话、漏了一半。

        ★★ `M6-api/app.py` 的 `/monitor-sections` 也写着同一条，并已照做：
            「不先证明，写错一个 section_id 得到的是『没有数据』，
              而不是『没有这个路段』—— 这正是本 feature 一路在挡的那类静默错。」
          现在把这句从**一个端点**提升为**整棵子树的规则**。

        ★ 为什么放在 M3 而不是 M6：M6 那层自己写着「本层只做两件事：
          转发筛选参数、把 M3 的语义化异常翻成 HTTP。SQL 一律在 M3」。
          判在 M3，**所有调用方**（网关、控制台、以后的 SDK）都拿到同一条保证；
          判在 M6，每个新入口都要各自记得再判一次。

        ★ 代价：每次取数多一次主键查询。**故意的** —— 它换来的是
          "这个 section_id 到底存不存在"这个问题**只有一个答案**。
        """
        self.section(section_id)

    # ------------------------------------------------------- 域外只读参照表
    #: 本域**只读**引用的域外表（v0.6，契约变更工单 005）。
    #: 它们不在 `domain.tables` 里，所以走不了 `list_objects`/`count` —— 那是**故意**的。
    #: 见 `catalog.REFERENCE_TABLES` 的说明：为查坐标系而放宽域白名单，
    #: 等于把「M3 不许越域乱读」这条守卫拆了。
    REFERENCE_TABLES = ("spatial_ref_sys",)

    #: 只读、单列、单值。**不接受任意列名** —— 那会把这条通道变回通用查询。
    _SRID_EXISTS_SQL = "SELECT 1 AS ok FROM spatial_ref_sys WHERE srid = %(srid)s::int"

    #: 从 `proj4text` 里取出**投影族**（`+proj=tmerc` → `tmerc`）。
    #:
    #: ★★ 2026-10-13（005 / T043）：这一列是**手写换算能不能算**的唯一判据来源。
    #:    `T003` 实测发现的那个洞：数据模型按宪法 §VI 接受了**任意 SRID**，
    #:    而 `geo.py` 的手写换算**只实现了高斯-克吕格**。
    #:    `crs_epsg` 被填成 `2154`（Lambert，`+proj=lcc`）时，旧代码会
    #:    把它喂进高斯-克吕格公式，**产出一个看起来完全正常的错值**。
    #:
    #: ★ 为什么不写一张「支持哪些 SRID」的白名单：
    #:    白名单要手维护，加一个新带号就得改代码；而 **`proj4text` 是 PROJ 自己维护的**，
    #:    它说什么族就是什么族。★ 而且白名单会漏 —— 漏掉的那个 SRID 不会报错，
    #:    它只是**不做检查**（同 §4「文件名单」的失效形状）。
    #:
    #: ★★ 为什么不按投影的**人类名字**去查（如「高斯-克吕格」）：
    #:    `spatial_ref_sys` 里同样的拼写有好几种，`srid=4524` 与 `4548` 的
    #:    `srtext` 措辞并不一致。**只有 `proj4text` 的 `+proj=` 是机器可判的。**
    _SRID_FAMILY_SQL = (
        "SELECT substring(proj4text from '\\+proj=([a-z0-9_]+)') AS fam "
        "FROM spatial_ref_sys WHERE srid = %(srid)s::int"
    )

    def srid_exists(self, srid: int) -> bool:
        """这个 SRID 在 PostGIS 的坐标系登记表里查得到吗？（FR-005 / I1）

        ★ 这是本仓库里**唯一**读 `spatial_ref_sys` 的地方。

        ★★ 它和「写不进去」不是一回事，两者都要有：
          · 数据库层：`crs_epsg` 挂 `REFERENCES spatial_ref_sys(srid)` ⇒ 查不到的
            SRID **结构上存不进去**（实测过：`Key (srid)=(999999) is not present`）。
          · 这一层：读出来的值仍然要**当场核一遍** —— 因为数据可能来自
            绕过 FK 的路径（手工 SQL、`COPY`、恢复的备份），也可能是在
            FK 建立之前就写进去的。**结构上的保证只覆盖它建立之后的写入。**

        ★ 查不到**不是**返回 False 就完事：调用方（见 `check_i1`）必须把它变成
          一次显式失败。一个「查不到就当 4326」的兜底，会让 99% 的调用方正常，
          只在换基准时静默算错。
        """
        return self._dao.query_one(self._SRID_EXISTS_SQL, {"srid": int(srid)}) is not None

    def crs_family(self, srid: int) -> str | None:
        """这个 SRID 用的是**哪个投影族**？（005 / T043）

        返回 `'tmerc'` / `'lcc'` / `'utm'` / `'longlat'` …；
        SRID 查不到、或它的 `proj4text` 里没有 `+proj=`，返回 `None`。

        ★★★ 为什么这个方法是「看着正常的错值」那类缺陷的解药
        ---------------------------------------------------
        数据模型按宪法 §VI **接受任意 SRID**（这是对的 —— 通用产品不能被
        自己的实现限制住入库）。但手写换算**只实现了高斯-克吕格**。
        两者合起来的旧行为是：填一个 `2154`（Lambert），代码**不报错**，
        把 Lambert 的参数当高斯-克吕格算，**输出一个量级正常、位置错掉几百米的坐标**。

        ★★ 这正是 004 立下的那条教训的形状 ——
          「**看着正常的错值比缺失更危险**」：
          缺失会被发现（503、空值、报错），错值不会，它会一路流到放样桩上。

        ★ 所以职责划分是：**「能入库」和「能算」是两件事，中间必须有人说话。**
          · 数据库层（FK + 守卫）：只保证 SRID **存在**
          · 这一层：把「存在」翻译成「它是什么族」
          · `geo.py`：声明**自己实现了哪些族**，不在列就抛错
          · `app.py`：把那个错翻成 503，并在消息里**说出 SRID**

        ★ 为什么不在这里判「支持不支持」：
          那样 M3 就得知道 M6 实现了什么，两个模块会**互相锁死** ——
          改 `geo.py` 支持的族，还得回来改 M3。族是**客观事实**（PROJ 说的），
          「支持」是**某个实现的属性**，所以它们属于不同的层。
        """
        row = self._dao.query_one(self._SRID_FAMILY_SQL, {"srid": int(srid)})
        if row is None:
            return None
        fam = row.get("fam")
        return str(fam) if fam else None

    @staticmethod
    def check_i1(row: dict[str, Any], effective: dict[str, Any],
                 lookup) -> list[str]:
        """I1：SRID 为权威，三个习惯记法是**可空注释**。返回问题清单（空 = 通过）。

        逐字实现 `data-model.md` E3 的 I1（2026-10-12 按宪法原则 VI 改写后的版本）：

            crs_epsg 是唯一权威字段：有基准时 MUST 非空，且 MUST 在
            spatial_ref_sys.srid 里查得到（FR-005），查不到当场拒绝。

            crs_zone_number / crs_central_meridian_deg / crs_zone_width_deg
            是可空注释：三个都有值时 ⇒ 与 crs_epsg 所指的基准 MUST 自洽；
                        任一个缺值时 ⇒ ★ MUST NOT 因此报错。

        ★★ 「缺值不报错」不是宽容，是**正确**：UTM 与 Lambert 没有「带号」记法
          （实测：8500 个 SRID 里 5818 个有 `+lon_0`，**UTM 全部没有**）。
          拿一个不存在的记法去要求它，会把正确的数据判成错的。

        ★★★ 自洽**不用算式判，用行为判** —— 本函数**刻意不实现**那两条已被实测
          否证的写法：
            ① `crs_central_meridian_deg == crs_zone_number * 3` —— 那个 3 是硬编码
               常量，而 `test_sim_track.py` 有一条既有守卫逐字写着
               「带号是配置项，不是代码里的常量」（research.md R10）。
               实测否证：`4524` 按 6 度带规律算出 48，实际是 108。
            ② 从 `proj4text` 抓 `+lon_0` —— UTM 全部没有，抓不到不等于错。
          行为比对在 Phase 7 的 PostGIS 对照里做（T038–T042，容差 1e-4 m，
          不许取 1e-9 —— 那是拿浮点噪声当判据）。

        参数
        ----
        row        一行原始数据（含 `section_crs_*` / `project_crs_*`）。
        effective  `effective_crs(row)` 的结果。
        lookup     形如 `srid_exists` 的可调用对象。**注入进来**是为了让这个
                   函数**离线可测** —— 测试传一个假的 lookup 即可，
                   不必连库（保住 `test_region_frame.py` 开头那句
                   「不需要数据库、不需要容器、不需要网络」）。
        """
        problems: list[str] = []

        # ---- ① 权威字段：MUST 在登记表里查得到 ------------------------------
        srid = effective.get("epsg")
        if srid is None:
            problems.append("生效基准里没有 crs_epsg —— 五态派生不该产出这种结果")
        elif not lookup(int(srid)):
            problems.append(
                f"crs_epsg={srid} 在 spatial_ref_sys 里查不到。"
                "这不是「不支持」，是「这个编码根本不存在」——"
                "请核对 EPSG 编码，或先在坐标系登记表里补充它。"
            )

        # ---- ② 三个注释：要么都给、要么不查；给全了才比对 -------------------
        # ★ 注意这里**不比对行为**（那要调投影内核，属于 Phase 7）。
        #   本函数只做两件不依赖投影数学的事：权威字段查表 + 注释三缺一不算错。
        #   行为比对留一个显式的「待办」，而不是假装这里已经做完了。
        prefix = "section" if effective.get("source") == "section" else "project"
        ann = {k: row.get(f"{prefix}_crs_{k}")
               for k in ("zone_number", "central_meridian_deg", "zone_width_deg")}
        present = {k: v for k, v in ann.items() if v is not None}
        if present and len(present) != 3:
            # ★★ 这一条**不报错** —— 只在返回值里提一句，供展示层提示。
            #   I1 的原话是「任一个缺值时 MUST NOT 因此报错」。
            #   把它写进 problems 就是违反 I1，所以这里刻意什么都不做，
            #   只留注释说明为什么这一段是空的。
            pass
        return problems

    def stations(self, section_id: int, *, from_km: float | None = None,
                 to_km: float | None = None, integer_only: bool = False,
                 limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        """某路段的桩号序列，可按区间/是否整桩筛。

        ★★ 2026-10-14 加 `offset`，理由同 `passages`：原先只能取前 n 条，
          而 5000 的上限**不是**「能取多少」的旋钮 —— 第 5001 条永远取不到。
        """
        self._require_section(section_id)
        return self._dao.query(self.STATIONS_SQL, {
            "sid": section_id, "from_km": from_km, "to_km": to_km,
            "integer_only": integer_only, "limit": limit, "offset": offset,
        })
    # ── v0.6（契约变更工单 #3）：桩号脱离路段，按**路线**取 ──────────────
    #
    # ★ 为什么必须新增这两个方法，而不是继续用 stations(section_id)：
    #   改前桩号挂在路段下，WHERE section_id = ? 就够了。改后桩号锚定**路线**，
    #   路段只是可空的「引用」—— 于是出现两类桩号**按路段永远查不到**：
    #     ① 还没归属任何路段的（section_id IS NULL）；
    #     ② 路段被删过的（ON DELETE SET NULL 之后同样 IS NULL）。
    #   而这两类恰恰是本工单要支持的场景。
    #
    #   更要紧的是**串台**：两条路线的桩号都从 K0+000 起算，
    #   按路段查时若路段归属错了，两条路的桩号会混在一起而看不出来。
    #   按路线查是唯一能区分它们的口径（FR-003）。
    LINE_STATIONS_SQL = """
    SELECT t.station_seq_no, t.station_local_km, t.station_absolute_km,
           t.station_text, t.station_type, t.is_integer_station,
           t.section_id,
           s.section_name
    FROM station_sequence t
    LEFT JOIN road_section s ON s.id = t.section_id
    WHERE t.line_id = %(lid)s::bigint
      AND (%(from_km)s::numeric IS NULL OR t.station_local_km >= %(from_km)s::numeric)
      AND (%(to_km)s::numeric   IS NULL OR t.station_local_km <= %(to_km)s::numeric)
      AND (NOT %(integer_only)s::boolean OR t.is_integer_station)
    ORDER BY t.station_local_km
    LIMIT %(limit)s::integer OFFSET %(offset)s::integer
    """

    #: 路线列表（含桩号数）。桩号数是按 line_id 数的 —— 与
    #: SECTIONS_SQL 里按 section_id 数的那个**不是同一个口径**，
    #: 两者不相等是**正常的**（路段覆盖不到全线，见 FR-004）。
    LINES_SQL = """
    SELECT l.id, l.line_code, l.line_name, l.admin_region, l.road_class,
           l.design_speed, l.lane_count,
           (SELECT count(*) FROM station_sequence t WHERE t.line_id = l.id)
               AS station_count,
           (SELECT count(*) FROM station_sequence t WHERE t.line_id = l.id
                                              AND t.section_id IS NULL)
               AS unassigned_station_count,
           (SELECT count(*) FROM road_section r WHERE r.line_id = l.id)
               AS section_count
    FROM road_line l
    ORDER BY l.line_code, l.id
    """

    def lines(self) -> list[dict[str, Any]]:
        """所有路线（含桩号数 / 未归属路段的桩号数 / 路段数）。

        ★ unassigned_station_count 是 v0.6 才可能非 0 的：
          改前 section_id 是 NOT NULL，「无路段桩号」这个概念不存在。
          把它显式查出来，是为了让「桩号 > 路段覆盖」这件事**看得见**（FR-004），
          而不是让人以为桩号数对不上就是数据坏了。
        """
        return self._dao.query(self.LINES_SQL)

    def stations_by_line(self, line_id: int, *, from_km: float | None = None,
                         to_km: float | None = None, integer_only: bool = False,
                         limit: int = 5000, offset: int = 0) -> list[dict[str, Any]]:
        """某**路线**的桩号序列（FR-003）。可按区间/是否整桩筛。

        与 stations(section_id) 的区别不只是换个 WHERE 列：
        本方法能取到 section_id IS NULL 的桩号，按路段取则永远取不到。
        limit 默认给到 5000（一条路线全线可能几千个桩号，不设小默认值）。
        """
        return self._dao.query(self.LINE_STATIONS_SQL, {
            "lid": line_id, "from_km": from_km, "to_km": to_km,
            "integer_only": integer_only, "limit": limit, "offset": offset,
        })

    def alignment(self, section_id: int) -> dict[str, Any]:
        """某路段的平面线形：交点链 + 线形单元链（一次取回，前端不必拼两次）。"""
        self._require_section(section_id)
        return {
            "section_id": section_id,
            "pis": self._dao.query(self.PIS_SQL, {"sid": section_id}),
            "elements": self._dao.query(self.ELEMENTS_SQL, {"sid": section_id}),
        }

    def widths(self, section_id: int) -> list[dict[str, Any]]:
        """某路段的**路幅宽度表**（`.WID`）。

        ⚠ 三件事得先知道，否则会把这表用错：

        ① **它是"变化点"表，不是"逐桩"表**。一行 = 一个宽度分组的起点，
           不是每个桩号一行。实测毕设路段**只有 4 行**，而 `.WID` 覆盖
           0.000–5701.461 m。想拿"某个桩号的宽度"必须**按桩号取最后一行**
           （`<= 该桩号` 的 `station_km` 最大者），这里不替调用方做这个决定 ——
           那是**业务语义**，不是取数。

        ② 终点 5701.461 m **早于**路线终点 5805.421 m：后 103.960 m **没有**
           路幅宽度数据。这是**源文件缺口**（实测该工程正是如此），不是解析漏了。

        ③ `side` 是 **`left` / `right`**（全库 8 张 side 表统一这个词表），
           同一桩号左右各一行（`group_seq` 区分同侧多组）。别假设"一行就是一个断面"。
           源文件里写 `[LEFT]`/`[RIGHT]`，解析器负责归一化成小写、去括号。

        ⚠ 与 GE 域其它变化点表一样，本表带 `station_km` 而**不带 `station_id`**：
           变化点桩号（`.WID`/`.CTR`/`.SUP`/`.HDM`）不保证是 `station_sequence`
           的子集，挂 FK 会拒掉合法数据。
        """
        self._require_section(section_id)
        return self._dao.query(self.WIDTHS_SQL, {"sid": section_id})

    def superelevation(self, section_id: int) -> list[dict[str, Any]]:
        """某路段的**超高过渡**（`.SUP`）：逐桩的六个横坡值（百分数）。

        ⚠ ① 这是**控制点**表，不是逐桩表。实测毕设路段 76 行。**控制点之间
              横坡是线性渐变的** —— 想拿某个桩号的横坡必须插值，直接取
              "最后一行"是错的。（这一条不是推测：`.lj` 的 11 个高差列能由本表
              复现，而**只有插值才对得上** —— 按"沿用上值"算有 55/332 行不符。
              见 tests/contract/test_design_import.py 第 12b 组。）

        ⚠ ② 源文件的 **9999 表示"忽略此数据"**，落库为 **NULL**。
              语义是「横坡渐变**穿过**这个控制点继续走」，所以插值时要
              **跳过 NULL 去找它两侧最近的非 NULL**，而不是把它当 0、
              也不是把前一个值沿用它。

        ⚠ ③ **左右两侧用的是同一套符号约定**。正常路拱两侧都写 −2.00，
              超高段写 +4.00 / −4.00。所以"左比右高"这类判断不能直接比大小 ——
              要按 `−σ·宽度·横坡` 折算成高差（σ = +1 左 / −1 右）。

        列序按教程 §13.5：土路肩 / 硬路肩 / 行车道 / 桩号 / 行车道 / 硬路肩 / 土路肩。
        """
        self._require_section(section_id)
        return self._dao.query(self.SUPERELEV_SQL, {"sid": section_id})

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
        level, reason = self.derive_level(present_set)

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


    # ---------------------------------------------------- FR-018 按桩号区间查询
    #: 要素查询共用 SELECT。**用 `start_station_km` / `end_station_km` 判相交**：
    #:
    #: ★ 「与区间相交」而不是「完全落在区间内」（`BETWEEN` 那种写法）：
    #:   一个 200 m 长的线形单元，只要它**跨过**查询区间，就必须被返回 ——
    #:   否则按 50 m 一段采样时会**整段丢失**长单元，而返回的行数看着还挺正常。
    #:   这是把「相交」写成「包含」时最典型的静默丢数据。
    #:
    #: ★ 用 `start_station_km` 排序（而非 `element_seq`）：调用方是按桩号切的，
    #:   返回顺序必须是桩号序；`element_seq` 只在同一路段内有意义，跨路段会乱。
    #:   `idx_alignment_elem_station` 正是 `(start_station_km, end_station_km)`，
    #:   这个 ORDER BY 能直接吃到索引。
    ELEMENTS_BY_STATION_SQL = """
        SELECT e.id, e.section_id, e.element_seq, e.element_type,
               e.start_station_km, e.end_station_km, e.length_m,
               e.azimuth_deg, e.end_azimuth_deg,
               e.radius_start_m, e.radius_end_m,
               -- ★ 截到查询区间的长度。与 length_m 并存：length_m 是单元全长，
               --   span_m 是「落在本区间里的那一段」。只给 length_m 的话，
               --   按 50 m 采样的人会把 200 m 的长度当真，里程轴直接错。
               (LEAST(e.end_station_km, %(to_km)s::numeric)
                - GREATEST(e.start_station_km, %(from_km)s::numeric)) * 1000 AS span_m
          FROM alignment_element e
         WHERE e.section_id = %(sid)s
           AND e.end_station_km   >= %(from_km)s::numeric
           AND e.start_station_km <= %(to_km)s::numeric
         ORDER BY e.start_station_km, e.element_seq
         LIMIT %(limit)s
    """

    #: 逐桩段 → **怎么拿到这一段的桩号**。**不是所有表都叫 `station_km`**，见下。
    #:
    #: ★ 这几张表里大多数是「变化点表」（`station_km`），而
    #:   `station_sequence` 用的是**局部桩号** `station_local_km`（它是基准本身，
    #:   不是“某处的一个变化点”）。混用会得到一个**永远为空**的查询 ——
    #:   而且不报错，只是没数据。
    #:
    #: ★★★★★ 2026-10-13 修正（此前这张表里 6 条错了 2 条，两条一调就 500）：
    #:
    #:   ① `superelev_transition` 原先写 `start_station_km`，注释还振振有词
    #:      「它一行覆盖一段」。**实测是错的**：那张表有 `station_km`
    #:      （6 号段 76 行，桩号 0.000 → 5.805，递增），是**逐桩行**，
    #:      没有 `start_station_km` 这个列。SQL 直接报列不存在 → 500。
    #:   ② `profile_ground_point` 原先写 `station_km`，但那张表**根本没有桩号列**
    #:      —— 它只有 `station_id`，外键指向 `station_sequence(id)`。
    #:      桩号得 JOIN 出来，改个列名不管用。
    #:
    #:   ⚠⚠ 两条错误当时都是**看不出来**的：
    #:     · 契约 `m6-gateway.v0.3.yaml` 里也写着 `start_station_km`，
    #:       所以「拿契约对代码」两边一致，一起错。
    #:     · 契约测试只调过 `station_sequence` 一个段，另外五个从没被调过。
    #:   ⇒ 修完之后补了一条**逐个段真调一遍**的检查（见
    #:     `tests/contract/test_station_query.py`）。**一张手工维护的对照表
    #:     必须有人读它**，否则它只是看起来对。
    #:
    #: 值的形状：`(桩号表达式, FROM 子句, SELECT 列表)`。
    #:   · 桩号列就在本表时，FROM 写表名、SELECT 写 `*`。
    #:   · 要 JOIN 时，FROM 写整段 JOIN、桩号带别名、SELECT 写 `<别名>.*`
    #:     —— **不写 `*`**，否则 JOIN 进来的列会混进响应里。
    SAMPLE_ANCHORS: dict[str, tuple[str, str, str]] = {
        "station_sequence": ("station_local_km", "station_sequence", "*"),
        "profile_ground_point": (
            "s.station_local_km",
            "profile_ground_point g JOIN station_sequence s ON s.id = g.station_id",
            "g.*"),
        "profile_grade_point": ("station_km", "profile_grade_point", "*"),
        "roadbed_width": ("station_km", "roadbed_width", "*"),
        "superelev_transition": ("station_km", "superelev_transition", "*"),
        "cross_section_ground_point": ("station_km", "cross_section_ground_point", "*"),
    }

    #: FR-014：按固定间隔**采样**。★ 这是「采样」，不是「桩号」。
    #:
    #: ★★★ 与 `stations()` 分清是本方法的**全部意义**：
    #:
    #:     `stations()` 返回**设计给的桩号**（`.STA` 导入），**不等间隔**。
    #:       毕设这段实测 332 个桩号，其中只有 **291** 个落在 20 m 整桩上，
    #:       另 **41** 个是加桩（33 个线元终点 + 8 个交点曲中点）。
    #:       桩号是**设计出来的**，不是网格。
    #:
    #:     本方法返回**等间隔采样点** —— 「只有具体使用时才采样」，
    #:       用时才算、不落表。弯沉要 20 m、IRI 要 10 m、破损要 100 m~1 km，
    #:       各给各的间隔。⇒ **不存在一个平台级的「采样间隔」**；
    #:       `design_project.station_interval_m` 是「设计文件里桩号序列的步长」，
    #:       那是关于**设计文件**的事实，不是采样默认值。
    #:
    #: ★★ 线元终点桩号**不在本方法返回值里** —— 它是
    #:   `alignment_element.end_station_km` **那一列属性**，直接读表就有。
    #:   交点曲中点同理，读 `alignment_pi`。
    #:   把这两样合成进采样结果，就是把「采样」和「设计桩号」搅在一起。
    #:   ★ 本仓第一版就是这么写的，20 m 下恰好等于 `.STA` 的 332 点 ——
    #:     那是**巧合**，因为这份设计的间隔正是 20 m。已收掉（见 D-014）。
    #:
    #: ★ 定义域是**路段范围** `[start_station_km, end_station_km]`；
    #:   也可用 `from_km` / `to_km` 只取其中一段（具体使用时的常见形态）。
    #:
    #: ★ 末点**不一定**落在 `to` 上：等间隔网格的定义就是「最后一个不超过
    #:   `to` 的整倍数」。毕设 20 m 采样只到 5.800，而路段终点是 5.805421 ——
    #:   这是**等间隔**的性质，不是丢尾巴。需要终点就自己补上。
    SAMPLE_UNIFORM_SQL = """
    WITH b AS (
      SELECT coalesce(%(from)s::numeric, start_station_km) AS a,
             coalesce(%(to)s::numeric,   end_station_km)   AS z
      FROM road_section WHERE id = %(sid)s::bigint
    )
    SELECT round(b.a + (n::numeric * %(iv)s::numeric) / 1000.0, 6) AS station_km
    FROM b,
         generate_series(0,
           floor(((b.z - b.a) * 1000.0) / %(iv)s::numeric)::int) AS n
    ORDER BY 1
    LIMIT %(limit)s::integer
    """

    def sample_uniform(self, section_id: int, interval_m: float, *,
                       from_km: float | None = None,
                       to_km: float | None = None,
                       limit: int = 20000) -> list[dict[str, Any]]:
        """按 `interval_m` **等间隔采样**。返回的是采样点，**不是桩号**。

        每行是 `{station_km}`。范围默认取路段全长，也可用 `from_km`/`to_km`。

        ★ **要「设计给的桩号」请用 `stations()`** —— 那个**不等间隔**，
          与本方法不是一回事。
        ★ **要「线元终点桩号」请读 `alignment_element.end_station_km`** ——
          它已经是一列属性，不在本方法返回值里。

        `interval_m` 必须为正 —— 0 或负数**报错**，不静默。
        因为静默的后果是「一个点」或「空」，而两者都会被下游读成
        「这段路没有采样点」，那是另一回事。
        """
        if (not isinstance(interval_m, (int, float))
                or isinstance(interval_m, bool) or interval_m <= 0):
            raise ValueError(
                f"采样间隔必须为正数，收到 {interval_m!r}；"
                "0 或负数不静默处理 —— 静默会得到一个点或空列表，"
                "而下游会把它读成「这段路没有采样点」")
        if (from_km is not None and to_km is not None
                and float(from_km) >= float(to_km)):
            raise ValueError(
                f"采样区间为空：from_km={from_km} >= to_km={to_km}；"
                "这不是「这段路没有采样点」，是调用方把两端写反了 —— "
                "不静默返回空列表")
        # ★★ 多取一行当探针，用来发现截断。
        #
        #   为什么这里**必须报错**、不能返回一个 `truncated` 标志：
        #   截断掉的是**尾部** —— 拿一个残缺网格做积分会得到偏小的结果，
        #   而结果看起来完全正常。这比返回空还危险。
        #
        #   ★ 这个洞是我自己先踩出来的：第一版把特征点也并进来，
        #     0.1 m 时 LIMIT 把尾部的点吃掉了一大半，而且**一声不吭**。
        rows = self._dao.query(self.SAMPLE_UNIFORM_SQL, {
            "sid": section_id,
            # ★ 传字符串而不是 float：走 `::numeric` 时 float 的二进制误差会
            #   进到网格里（0.1 存不下），变成「第 7 位小数对不上」这种查不动的红。
            "iv": str(interval_m),
            "from": None if from_km is None else str(from_km),
            "to": None if to_km is None else str(to_km),
            "limit": limit + 1,
        })
        if len(rows) > limit:
            raise ValueError(
                f"采样结果超过 limit={limit}（至少 {len(rows)} 个点），"
                f"已拒绝返回 —— 截断会丢掉尾部的点，"
                f"而下游拿一个残缺网格算出来的结果看起来是正常的。"
                f"请调大 limit，或把间隔放粗（section_id={section_id}、"
                f"interval_m={interval_m}）")
        return rows

    def elements_by_station(self, section_id: int, from_km: float, to_km: float,
                            *, limit: int = 2000) -> list[dict[str, Any]]:
        """**与**桩号区间 `[from_km, to_km]` **相交**的线形单元（FR-018）。

        给 Chrono / FEM 按里程切片用。`span_m` = 落在本区间内的长度（米），
        单元被区间截断时它与 `length_m` 不等 —— 拿它做积分才是对的。

        越界不报错、返回空列表：**查询**不是**校验**。校验在导入侧（FR-006）。
        """
        self._require_section(section_id)
        if from_km > to_km:
            raise ValueError(f"区间反了：from_km={from_km} > to_km={to_km}")
        return self._dao.query(self.ELEMENTS_BY_STATION_SQL, {
            "sid": section_id, "from_km": from_km, "to_km": to_km, "limit": limit,
        })

    def samples_by_station(self, section_id: int, seg: str, from_km: float,
                           to_km: float, *, limit: int = 5000) -> list[dict[str, Any]]:
        """某**逐桩段**落在桩号区间内的行（FR-018）。

        `seg` 取 `SAMPLE_ANCHORS` 的键。传未知段名**报错**而不是返回空 ——
        空列表会被读成「这段在区间内没有数据」，而实际是「段名写错了」。
        两者在下游是两回事：前者是数据缺口，后者是调用错误。
        """
        self._require_section(section_id)
        if seg not in self.SAMPLE_ANCHORS:
            raise ValueError(
                f"未知段 {seg!r}；可用：{sorted(self.SAMPLE_ANCHORS)}"
                "（不接受未知段名 —— 静默返回空会被读成「区间内无数据」）")
        station_col, from_clause, select_cols = self.SAMPLE_ANCHORS[seg]
        # 三个片段都来自上面的白名单常量，不是外部输入，故 f-string 拼接是安全的；
        # 参数（区间、路段）一律走占位符。
        # ★ 2026-10-13：以前这里只有「列名」一个片段，现在多了一个**整段 FROM**
        #   —— 因为 `profile_ground_point` 的桩号在另一张表里，得 JOIN。
        #   `select_cols` 跟着来是为了**别把 JOIN 进来的列混进响应**。
        return self._dao.query(
            f"SELECT {select_cols} FROM {from_clause}"
            " WHERE section_id = %(sid)s"
            f"   AND {station_col} >= %(from_km)s::numeric"
            f"   AND {station_col} <= %(to_km)s::numeric"
            f" ORDER BY {station_col}"
            " LIMIT %(limit)s",
            {"sid": section_id, "from_km": from_km, "to_km": to_km, "limit": limit})

_BUILDERS = {"LO": LoRepository, "GE": GeRepository}


# ---------------------------------------------------------------------- 全域盘点
#: 物理表 → 分区键。**只有声明式分区表（分区根表）才有条目**，其余按普通表算。
#:
#: 为什么需要这条注释级的常量：``wim_axle_record`` 是**声明式分区表**，物理下落成
#: 1 个父表 ＋ N 个月分区 ＋ 1 个 pdefault。若把物理表逐张列出来，界面上会冒出
#: 几十张 ``wim_axle_record_p202511`` 这类**恒空**的表 —— 那是 PostgreSQL 的实现细节，
#: 不是平台的对象类型。平台对外只有一个"WIM 过车记录"，它按 ``pass_time`` 落进
#: 不同月份格子里。所以口径是：**逻辑表 = 父表**，分区不单独计数
#: （父表的 count(*) 本来就含全部分区的行，重复列出来既冗余又误导）。
#:
#: 这里**刻意不写死分区数**：分区随数据增长增删，把 "39" 写进代码，第一次滚动
#: 分区就过期。只记"它是分区根表"这个**稳定事实**，分区数在需要时从
#: ``pg_inherits`` 现查（见 :meth:`Dao.partition_counts`）。
PARTITIONED_ROOTS: dict[str, str] = {
    "wim_axle_record": "pass_time",
}


def list_tables() -> tuple[str, ...]:
    """平台对外可数的**逻辑表**全集：7 域已建表 ＋ 跨域支撑表，与 ``ALL_TABLES`` 同一份。

    存在的意义是给"盘点有哪些表"一个**有名字的入口**，而不是让每处调用方自己去拼
    ``DOMAINS + CROSS_TABLES`` —— 每多一处拼接，就多一次"忘了算跨域表"的机会。
    """
    return ALL_TABLES


def build_repository(code: str, dao: "Dao") -> DomainRepository:
    """域仓储工厂。没有专属子类的域先用通用仓储（接口一致，后续按需下沉）。"""
    return _BUILDERS.get(code, DomainRepository)(dao, DOMAINS[code])


class CrossRepository:
    """跨域支撑表仓储 —— 字典 / 日志 / 台账 / 登记簿的唯一读出口。

    ★★★ 为什么它不是 ``DomainRepository`` 的子类，也不复用 ``_check``：

    ``DomainRepository._check()`` 是**域内**白名单 —— 它要求表在 ``self.domain.tables``
    里。跨域支撑表**按定义不属于任何域**（``domain_of()`` 对它们返回 ``None``），
    所以域仓储**天然取不到它们**，这不是配置问题、是类型问题。

    ⇒ 修法不是放宽 ``_check``（那会把「M3 不许越域乱读」这条守卫拆掉），
      而是**承认它们是域外的**，另开一条**明说的**通道 —— 与 ``catalog.py`` 里
      ``REFERENCE_TABLES`` / ``GeRepository.srid_exists`` 对 ``spatial_ref_sys``
      的处理同一个道理（那里的注释原话：「正确的做法是**承认它是域外的**，
      然后开一条明说的通道，而不是放宽白名单」）。

    ★ 读守卫的口径与**写侧**（``write.py``）对齐：那里判的是 ``table not in ALL_TABLES``，
      是**表级**白名单；这里判的是 ``table not in CROSS_TABLES``，同样是表级。
      两侧都**默认拒绝**，都没有「按前缀猜」的余地。
    """

    #: 本仓储允许访问的表。**只读**，且只读这一份。
    tables: tuple[str, ...] = CROSS_TABLES

    def __init__(self, dao: "Dao") -> None:
        self._dao = dao

    def _check(self, table: str) -> str:
        """表级白名单：本仓储**只**能碰 CROSS_TABLES。

        ★ 两种拒绝都是 **404**，不是 400：
          · 域对象表走错门 → 404 + 指路（`/v1/objects/{table}`）
          · 完全没登记的表 → 404 + 指路（`/v1/support`）

        ★★ 为什么域对象那一支也必须是 404，而不是「用错了门 ⇒ 400」：
          与 `/v1/objects/{object_type}` 的先例对齐 —— 那里对**不在白名单里的
          对象类型**一律 404（判词「未登记的对象类型」）。
          `wim_axle_record` 对本出口而言就是**不存在的资源**，不是「请求格式错」。
          ★ 实跑对读时发现的：初版这里抛 ContractViolation ⇒ 400，
            而同一份契约里写着 404 —— **契约与实现当场不一致**。
            判词是对的、码值错了，这种错最难看出来：信息全对，只有数字错。
        """
        if table not in CROSS_TABLES:
            owner = domain_of(table)
            if owner is not None:
                raise NotFound(
                    f"跨域支撑表出口没有 {table}（该表是 {owner} 域对象，不走本出口）；"
                    f"请改用 /v1/objects/{table}"
                )
            raise NotFound(
                f"未登记的跨域支撑表：{table}；本出口的表见 /v1/support"
            )
        return quote_ident(table)

    def order_key(self, table: str) -> str:
        """本表的稳定排序键。字典是 ``code``，台账/日志/规则是 ``id``。"""
        self._check(table)
        return CROSS_ORDER_KEY[table]

    def list_rows(self, table: str, *, limit: int = 100,
                  offset: int = 0) -> list[dict[str, Any]]:
        """按本表的排序键稳定翻页。

        ★ 排序键**逐表不同**且必须是确定的：字典没有 ``id`` 列（``code`` 主键），
          一律 ``ORDER BY id`` 会让字典出口直接报错。键来自 ``catalog.CROSS_ORDER_KEY``，
          由 ``selfcheck()`` 断言与 ``CROSS_TABLES`` 逐张对齐。
        """
        t = self._check(table)
        key = CROSS_ORDER_KEY[table]
        return self._dao.query(
            f"SELECT * FROM {t} ORDER BY {key} LIMIT %(limit)s::int OFFSET %(offset)s::int",
            {"limit": limit, "offset": offset})

    def count(self, table: str) -> int:
        t = self._check(table)
        return int(self._dao.scalar(f"SELECT count(*) AS n FROM {t}") or 0)

    def catalog(self) -> list[dict[str, Any]]:
        """跨域支撑表清单：逐张给表名、类别、排序键、行数。

        ★ 这张清单是**出口的一部分**，不是运维装饰：客户端拿到一个码之后，
          要能自己判断该去哪张表解 —— 靠 ``dict_`` 前缀去猜是命名习惯，不是契约。
        """
        out: list[dict[str, Any]] = []
        for t in CROSS_TABLES:
            out.append({
                "table": t,
                "kind": CROSS_KIND[t],
                "order_key": CROSS_ORDER_KEY[t],
                "row_count": self.count(t),
            })
        return out

    def __repr__(self) -> str:
        return f"<CrossRepository 表{len(self.tables)}张>"
