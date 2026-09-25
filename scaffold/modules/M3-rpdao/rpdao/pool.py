"""M3 连接池与执行原语——**本文件是整个平台里唯一 import psycopg 的地方**。

为什么要把这件事锁死在一个文件里：
  报告 6.1 的接口契约写着「应用不直连存储」。骨架期 M6（api）自己持有连接池，
  实际上违反了这条铁律——但没有 DAO，它别无选择。M3 落地后：
    · 连接池、SQL 执行、行工厂、参数绑定 全部收口到 Dao；
    · M4/M5/M6/M8 只 import rpdao，拿不到 cursor，也就**没有能力**直连库。
  这是把「约定」变成「结构上做不到」的关键一步。
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .errors import StorageUnavailable

DEFAULT_DSN = "postgresql://rp:rp_change_me@localhost:55432/road_pavement"


class Dao:
    """DAO 门面：持有唯一连接池，并给出各对象域的仓储。

    典型用法（M6）：

        from rpdao import Dao
        dao = Dao(os.environ["PG_DSN"], app_name="rp-api")
        dao.open()
        rows = dao.lo.passages(limit=100)      # LO 交通荷载域
        dao.close()
    """

    def __init__(
        self,
        dsn: str | None = None,
        *,
        app_name: str = "rp-dao",
        min_size: int = 1,
        max_size: int = 6,
        timeout: float = 15.0,
    ) -> None:
        self.dsn = dsn or os.getenv("PG_DSN", DEFAULT_DSN)
        self.app_name = app_name
        self._pool = ConnectionPool(
            self.dsn,
            min_size=min_size,
            max_size=max_size,
            open=False,
            timeout=timeout,
            kwargs={"application_name": app_name},
        )
        self._repos: dict[str, Any] = {}

    # ---------------------------------------------------------------- 生命周期
    def open(self) -> "Dao":
        self._pool.open()
        return self

    def close(self) -> None:
        self._pool.close()

    def __enter__(self) -> "Dao":
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- 执行原语
    @contextmanager
    def cursor(self) -> Iterator[Any]:
        """给仓储用的游标上下文。**不对外暴露**（上层拿到它就等于能直连库）。"""
        try:
            with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
                yield cur
        except psycopg.OperationalError as exc:      # 连不上/超时/池耗尽
            raise StorageUnavailable(str(exc)) from exc

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.fetchall()

    def query_one(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.fetchone()

    def scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        row = self.query_one(sql, params)
        return None if row is None else next(iter(row.values()))

    def ping(self) -> bool:
        """健康探针。不抛异常，只回真假——供 /healthz 用。"""
        try:
            return self.scalar("SELECT 1") == 1
        except Exception:  # noqa: BLE001  探针不该把任何异常带出去
            return False

    # 连接池自身的信息，供运维端点观察（不泄漏 DSN 里的口令）
    # ------------------------------------------------------------- 全域盘点
    def table_census(self) -> list[dict[str, Any]]:
        """逐张**逻辑表**数行数 —— 平台「哪些表有数据」的唯一数据源。

        为什么这件事必须在 M3 做，而不是在 M6/M9
        ------------------------------------------------------------------
        「读只经 rpdao」这条硬线的意思是：**任何模块都不许自己拼表名、自己发 SQL**。
        盘点表数听着像最简单的查询，可它恰恰是最容易破戒的 ——「就一条 count(*)，
        直接在 M6 里拼一下算了」。一旦开了这个口子，M6 就有了任意表名的查询能力，
        写权守卫、白名单、越域检查全部形同虚设。所以它落在这里，且**表名只从
        catalog 的 ``ALL_TABLES`` 来**，调用方给不进任何表名。

        口径（三件事，每一件都对应一个真实会踩的坑）
        ------------------------------------------------------------------
        1) **数的是逻辑表，不是物理表**：``ALL_TABLES`` 共 62 张，其中
           ``wim_axle_record`` 是分区根表。它的几十个月分区与兜底分区**不单独列出** ——
           父表 ``count(*)`` 已含全部分区，重复列出会让「表数」虚高到 100+，
           而且那几十张全是空的，看上去像平台死了一半。
        2) **一行一条，父表优先**：结果顺序严格跟 ``ALL_TABLES`` 走，
           不做任何排序 —— 排序是展示层的事，DAO 不该替前端决定怎么排。
        3) **用 UNION ALL 一次查完，不是 62 次往返**：逐表查询在本地测不出问题，
           真机上 62 个来回的网络延迟会把这个接口拖到不可用。
           代价是 SQL 变成动态拼接 —— 所以拼进去的每一个表名都必须先过
           ``quote_ident`` 与白名单，这也是本方法不收表名参数的原因。

        ⚠ ``count(*)`` 在 PostgreSQL 上要走全表扫（MVCC 决定它不能只读统计信息）。
          62 张表里最大的一张实测 2215 行，代价可接受。**表长到千万级时这里要改**：
          届时改用 ``pg_class.reltuples`` 给估计值，并**同时**把「这是估计值」
          这个事实透出去，而不是让调用方以为拿到的还是精确计数。
        """
        from .catalog import ALL_TABLES
        from .repo import PARTITIONED_ROOTS

        if not ALL_TABLES:
            return []

        parts = [
            f"SELECT {i} AS ord, '{t}' AS table_name, count(*) AS row_count "
            f"FROM {quote_ident(t)}"
            for i, t in enumerate(ALL_TABLES)
        ]
        rows = self.query(" UNION ALL ".join(parts) + " ORDER BY ord")
        partitions = self.partition_counts()
        for r in rows:
            r["is_partitioned"] = r["table_name"] in PARTITIONED_ROOTS
            r["partition_key"] = PARTITIONED_ROOTS.get(r["table_name"])
            r["partition_count"] = partitions.get(r["table_name"], 0)
        return rows

    def partition_counts(self) -> dict[str, int]:
        """分区根表 → 它底下挂了几个分区（含 ``pdefault`` 兜底分区）。

        现查 ``pg_inherits`` 而不是写死常量：分区随数据增长而滚动增删，
        任何写进代码的数字都会在下一次滚动时变成过期标注。这里返回的数是**事实**，
        不是配置。
        """
        from .repo import PARTITIONED_ROOTS

        if not PARTITIONED_ROOTS:
            return {}
        rows = self.query(
            "SELECT parent.relname AS table_name, count(*) AS n "
            "FROM pg_inherits i "
            "JOIN pg_class parent ON parent.oid = i.inhparent "
            "JOIN pg_class child  ON child.oid  = i.inhrelid "
            "JOIN pg_namespace ns  ON ns.oid = parent.relnamespace "
            "WHERE ns.nspname = 'public' AND parent.relname = ANY(%(names)s) "
            "GROUP BY 1",
            {"names": list(PARTITIONED_ROOTS)},
        )
        return {r["table_name"]: int(r["n"]) for r in rows}

    def pool_stats(self) -> dict[str, int]:
        return {
            "pool_size": self._pool.get_stats().get("pool_size", 0),
            "pool_available": self._pool.get_stats().get("pool_available", 0),
        }

    # ---------------------------------------------------------------- 域仓储
    def domain(self, code: str) -> Any:
        """按域代号取仓储（GE/SU/RE/LO/WE/TE/DE）。"""
        from .catalog import DOMAINS
        from .repo import DomainRepository, build_repository

        code = code.upper()
        if code not in DOMAINS:
            from .errors import UnknownDomain
            raise UnknownDomain(f"未登记的对象域：{code}；合法值 {'/'.join(DOMAINS)}")
        if code not in self._repos:
            self._repos[code] = build_repository(code, self)
        return self._repos[code]

    def __getattr__(self, name: str) -> Any:
        """dao.lo / dao.ge … 直接取域仓储（小写域代号）。"""
        if name.isalpha() and len(name) == 2 and name.upper() in ("GE", "SU", "RE", "LO",
                                                                  "WE", "TE", "DE"):
            return self.domain(name)
        raise AttributeError(name)


def quote_ident(name: str) -> str:
    """把标识符安全地拼进 SQL。

    表名不能走参数绑定（PG 不允许占位符出现在表名位置），所以这里必须自己保证安全：
    只放行 ``[a-z_][a-z0-9_]*``，其余一律拒绝。仓储只对**白名单里的表**调用它，
    双保险。
    """
    if not name or not all(c.islower() or c.isdigit() or c == "_" for c in name):
        from .errors import ContractViolation
        raise ContractViolation(f"非法标识符：{name!r}")
    if name[0].isdigit():
        from .errors import ContractViolation
        raise ContractViolation(f"非法标识符（数字开头）：{name!r}")
    return f'"{name}"'


def as_tuple(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(values)
