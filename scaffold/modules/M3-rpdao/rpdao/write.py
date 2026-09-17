"""契约③的写入侧：**表级写权守卫** ＋ DAO 写方法。

背景（为什么需要这个文件）
------------------------------------------------------------------------------
在它之前，契约③只覆盖了**读**：M6（api）通过 rpdao 取数，自身不持有连接池；
但**写完全没有收口**——M2（ingest）用 4 条裸 SQL 直连写库，而 rpdao 一个写方法都没有。
后果是「写入只经 M2」这条硬线**没有被代码强制**，只写在文档里：任何模块
``import psycopg`` 就能绕过契约直接写库，且不会有任何报错。

本文件把这条硬线变成**结构上做不到**，办法有三条性质（每一条都配契约测试）：

1. **默认拒绝**：``TABLE_OWNER`` 没登记的表一律写不进去。宁可报错让人来登记，
   也不要"默认允许"——因为默认允许意味着**新增一张表就自动多一个无人看守的写入口**。
2. **越权报错**：``writer="M5"`` 去写 ``wim_axle_record`` 直接抛
   :class:`WriteGuardError`，而不是静默成功。这是"模块可拔插"的前提：
   没有这条，任何模块都能悄悄改别人的数据。
3. **空写入报错**：0 行的写入不得当作成功返回（否则上游坏了也看不出来）。

关于「写入只经 M2」这条硬线的口径
------------------------------------------------------------------------------
硬线约束的是**七域业务数据**（只有 M2 能写）；``quality_rule``（M4）、
``mapping_set``（M7）属**平台元数据**，由该模块自己写。故守卫按**表级写权**设计，
而不是按"模块级"——一个模块可以拥有若干张表的写权，也可以一张都没有。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Sequence

from .catalog import ALL_TABLES, TABLE_OWNER
from .errors import UnknownTable, WriteGuardError
from .pool import Dao, quote_ident

# 需要去重的海量表：这些表的写入**必须**给出冲突键。
#
# ★ 这里的键必须**逐个对得上库里的唯一索引**，否则生成的
#   ``ON CONFLICT (…)`` 会让 PostgreSQL 直接报
#   "there is no unique or exclusion constraint matching the ON CONFLICT specification"
#   —— 是**运行时**才炸，语法编译与静态检查都看不出来。
#
#   本字典的第一版就写错了一张：给 ``wim_axle_record`` 填了
#   ``("device_code", "pass_time")``。两处都错：``device_code`` **根本不是该表的列**
#   （设备信息存在 ``data_source`` 里，形如 ``mqtt:<device_code>``），
#   且该表唯一索引只有 ``(id, pass_time)``——而 id 是自增的，**不可能当业务去重键**。
#   真正拦住重复的是 M2 的**内存去重**（``State.mark_seen``），不是数据库约束。
#
#   现在这件事由 ``tests/contract/test_write_guard.py`` 第 6b 组兜底：
#   它解析 DDL，断言这里的每个键都有唯一索引背书。**加错键会让契约测试失败**，
#   而不是等到线上第一次写库才炸。
DEDUPE_REQUIRED: dict[str, tuple[str, ...]] = {
    "wim_axle_detail": ("record_id", "axle_seq"),   # DDL: UNIQUE (record_id, axle_seq)
    "data_import_batch": ("batch_no",),             # DDL: batch_no varchar(64) UNIQUE
}


def allowed_writer(table: str) -> str | None:
    """返回某张表**唯一**允许的写入方；只读表返回 ``None``。"""
    return TABLE_OWNER.get(table)


def assert_writer(table: str, writer: str) -> None:
    """写权守卫：不通过就抛异常，绝不返回"放行"。

    三层判断，顺序有意为之——**先判表是否存在，再判是否只读，最后判越权**，
    这样报错信息能指出读者真正该修的那一处：

    * 表不在白名单          → :class:`UnknownTable`（表名写错，或新表没登记进 catalog）
    * 表在白名单但无写权登记 → :class:`WriteGuardError`（这是一张只读表，本就不该写）
    * 表有写权但写入方不符   → :class:`WriteGuardError`（越权，报文里同时给出应写方）
    """
    if table not in ALL_TABLES:
        raise UnknownTable(
            f"{table!r} 不在表白名单里（catalog.ALL_TABLES 共 {len(ALL_TABLES)} 张）。"
            "若这是新版本新增的表，请先登记进 catalog，再重新走契约测试。"
        )
    owner = TABLE_OWNER.get(table)
    if owner is None:
        raise WriteGuardError(
            f"{table!r} 是**只读表**（catalog.TABLE_OWNER 未登记写权），拒绝写入。"
            "如确需可写，请走契约变更工单为其登记唯一写入方——"
            "默认拒绝是刻意的：默认允许会让每张新表都自动多一个无人看守的写入口。"
        )
    if writer != owner:
        raise WriteGuardError(
            f"越权写入：{table!r} 的写权属于 {owner}，{writer} 无权写。"
            "这是「模块可拔插」的前提——模块只能改自己名下的数据。"
        )


def _guard_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """空写入一律拒绝。

    「写了 0 行」与「写成功」在上层看来都是"没报错"，但前者通常意味着上游已经坏了
    （筛选条件写错、报文全被丢、批次为空）。静默返回 0 会让这种坏被当成成功，
    本项目的既有教训正是：**一个永远不会失败的检查，比没有检查更糟**。
    """
    if not rows:
        raise WriteGuardError("拒绝空的批量写入：0 行的写入不得当作成功返回")
    return [dict(r) for r in rows]


class WriteMixin:
    """给 :class:`Dao` 混入的写方法。**所有写操作都必须经过它**。

    设计上刻意不提供"随便执行一段 SQL"的公开入口：``Dao.query`` 保留给读，
    写只能走下面几个方法，且每个方法第一件事就是过守卫。
    """

    # ------------------------------------------------------------------ 内部
    def _build_insert(
        self,
        table: str,
        row: Mapping[str, Any],
        on_conflict: Sequence[str] | None,
    ) -> tuple[str, dict[str, Any]]:
        cols = list(row.keys())
        col_sql = ", ".join(quote_ident(c) for c in cols)
        val_sql = ", ".join(f"%({c})s" for c in cols)
        sql = f"INSERT INTO {quote_ident(table)} ({col_sql}) VALUES ({val_sql})"
        params = dict(row)
        if on_conflict:
            keys = ", ".join(quote_ident(c) for c in on_conflict)
            others = [c for c in cols if c not in on_conflict]
            if others:
                sets = ", ".join(f"{quote_ident(c)} = EXCLUDED.{quote_ident(c)}" for c in others)
                sql += f" ON CONFLICT ({keys}) DO UPDATE SET {sets}"
            else:
                sql += f" ON CONFLICT ({keys}) DO NOTHING"
        return sql, params

    # ------------------------------------------------------------------ 公开
    def insert(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        writer: str,
        on_conflict: Sequence[str] | None = None,
    ) -> int:
        """插入若干行，返回实际写入行数。写权不符会先抛异常，一行都不会落库。"""
        assert_writer(table, writer)
        payload = _guard_rows(rows)
        if table in DEDUPE_REQUIRED and not on_conflict:
            raise WriteGuardError(
                f"{table!r} 是海量表，写入必须给出冲突键（on_conflict），"
                f"通常为 {DEDUPE_REQUIRED[table]}——否则重放同一批数据会产生重复行"
            )
        with self.cursor() as cur:                     # type: ignore[attr-defined]
            for row in payload:
                sql, params = self._build_insert(table, row, on_conflict)
                cur.execute(sql, params)
        return len(payload)

    def upsert(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        writer: str,
        key: Sequence[str],
    ) -> int:
        """按 ``key`` 幂等写入（存在即更新）。"""
        return self.insert(table, rows, writer=writer, on_conflict=key)

    def insert_returning(
        self,
        table: str,
        row: Mapping[str, Any],
        *,
        writer: str,
        returning: str = "id",
        on_conflict: Sequence[str] | None = None,
    ) -> Any:
        """插入一行并返回指定列（如自增主键），供需要外键串联的场景使用。

        ``on_conflict`` 给出后即为**幂等**写入：已存在则更新并仍返回该行的
        ``returning`` 列。原先这里没有这个参数，而 ``insert`` 又有冲突键却拿不到 id
        —— 结果是"按业务键幂等 + 需要拿回主键去挂外键"这件事**根本表达不出来**。
        M2 的设计数据落库器就卡在这上面：重放同一批会撞
        ``UNIQUE (section_id, pi_seq)``，而单元表又必须拿到交点 id 才能填 ``pi_id``。

        ⚠ 若 ``on_conflict`` 覆盖了该行的**全部**列，``_build_insert`` 会退化成
        ``DO NOTHING``，此时冲突行不会返回，本方法返回 ``None``。需要拿到 id 时，
        请保证冲突键之外至少还有一列（本项目 ``alignment_pi`` 即属此情况）。
        """
        assert_writer(table, writer)
        payload = _guard_rows([row])[0]
        sql, params = self._build_insert(table, payload, on_conflict)
        sql += f" RETURNING {quote_ident(returning)}"
        with self.cursor() as cur:                     # type: ignore[attr-defined]
            cur.execute(sql, params)
            got = cur.fetchone()
        if got is None:
            return None
        return next(iter(got.values())) if isinstance(got, dict) else got[0]

    def execute_write(
        self,
        table: str,
        sql_tail: str,
        params: Mapping[str, Any],
        *,
        writer: str,
    ) -> int:
        """受守卫保护的自定义写（``UPDATE``、需要 ``列 = 列 + 增量`` 的场合）。

        保留这个口子是因为通用 UPSERT 表达不了"累加"语义，
        但它**同样先过守卫**——把"表名"与"写入方"绑定，
        堵住"绕过守卫手写 SQL"这条路。
        """
        assert_writer(table, writer)
        with self.cursor() as cur:                     # type: ignore[attr-defined]
            cur.execute(sql_tail, dict(params))
            return cur.rowcount


class TxnWriter:
    """一个事务内的写句柄：多次写入**同成同败**。

    为什么非要它：收口之前，M2 把「wim_axle_record ＋ 其 wim_axle_detail 轴组明细」
    放在**同一个连接、同一个事务**里提交——这是对的，主记录与明细必须原子。
    如果收口时简单地改成两次 ``dao.insert(...)``，两次会各取一条池连接，
    **原子性就悄悄没了**：进程在两条中间挂掉，就会留下一条没有轴组明细的过车记录，
    而这种"半条数据"在下游看来完全合法，不会报错。

    所以收口不是"把 SQL 换成方法调用"那么简单——**要连事务边界一起搬过来**。
    """

    def __init__(self, dao: "WriteDao", conn: Any, cur: Any, writer: str) -> None:
        self._dao = dao
        self._conn = conn
        self._cur = cur
        self._writer = writer

    def insert(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        on_conflict: Sequence[str] | None = None,
    ) -> int:
        assert_writer(table, self._writer)
        payload = _guard_rows(rows)
        if table in DEDUPE_REQUIRED and not on_conflict:
            raise WriteGuardError(
                f"{table!r} 是海量表，写入必须给出冲突键（on_conflict），"
                f"通常为 {DEDUPE_REQUIRED[table]}"
            )
        for row in payload:
            sql, params = self._dao._build_insert(table, row, on_conflict)
            self._cur.execute(sql, params)
        return len(payload)

    def insert_returning(
        self,
        table: str,
        row: Mapping[str, Any],
        *,
        returning: str = "id",
        on_conflict: Sequence[str] | None = None,
    ) -> Any:
        """插入一行并返回指定列；``on_conflict`` 给出时按业务键幂等（同 :meth:`WriteMixin.insert_returning`）。"""
        assert_writer(table, self._writer)
        payload = _guard_rows([row])[0]
        sql, params = self._dao._build_insert(table, payload, on_conflict)
        sql += f" RETURNING {quote_ident(returning)}"
        self._cur.execute(sql, params)
        got = self._cur.fetchone()
        if got is None:
            return None
        return next(iter(got.values())) if isinstance(got, dict) else got[0]

    def execute(self, table: str, sql_tail: str, params: Mapping[str, Any]) -> int:
        """受守卫保护的自定义写（需要 ``列 = 列 + 增量`` 这类语义时用）。"""
        assert_writer(table, self._writer)
        self._cur.execute(sql_tail, dict(params))
        return self._cur.rowcount


class WriteDao(WriteMixin, Dao):
    """同时具备读与写能力的 Dao。**只有 M2 与元数据模块该用它**。

    其余模块请继续用 :class:`rpdao.Dao`（读）——它们连写方法都拿不到，
    这比"有方法但不许调"更彻底。
    """

    @contextmanager
    def write_txn(self, *, writer: str) -> Iterator[TxnWriter]:
        """开一个事务，拿到 :class:`TxnWriter`。**多表写入必须用它**。

            with dao.write_txn(writer="M2") as tx:
                rid = tx.insert_returning("wim_axle_record", rec)
                tx.insert("wim_axle_detail", details,
                          on_conflict=("record_id", "axle_seq"))
            # 出块即提交；块内抛异常则整体回滚
        """
        from psycopg.rows import dict_row as _dict_row
        with self._pool.connection() as conn:            # type: ignore[attr-defined]
            try:
                with conn.cursor(row_factory=_dict_row) as cur:
                    yield TxnWriter(self, conn, cur, writer)
                conn.commit()
            except Exception:
                conn.rollback()
                raise


__all__ = [
    "WriteDao",
    "WriteMixin",
    "TxnWriter",
    "allowed_writer",
    "assert_writer",
    "DEDUPE_REQUIRED",
]
