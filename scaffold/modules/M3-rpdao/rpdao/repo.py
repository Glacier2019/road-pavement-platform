"""M3 各对象域仓储（Repository）——每个域的**唯一**数据出口。

分层约定：
  · ``DomainRepository``：通用能力（按表列/取单条/计数），只允许访问本域的表。
  · ``LoRepository`` 等：域特有查询（语义化命名，如 ``passages()`` 而不是拼 SQL）。
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
_BUILDERS = {"LO": LoRepository}


def build_repository(code: str, dao: "Dao") -> DomainRepository:
    """域仓储工厂。没有专属子类的域先用通用仓储（接口一致，后续按需下沉）。"""
    return _BUILDERS.get(code, DomainRepository)(dao, DOMAINS[code])
