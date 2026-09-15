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
