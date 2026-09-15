"""M3 对象域数据访问层（DAO）——平台里唯一接触存储的地方。

为什么需要它（对应报告 6.2 的 M3）：
    报告 6.1 的接口契约是「应用不直连存储」。但骨架期没有 M3，M6（api）只能自己
    持有 psycopg 连接池并内联 SQL——**架构上写着不许，代码上却只能这么写**。
    本包把存储访问收口到一处，使 M4/M5/M6/M8 只有 ``rpdao`` 这一个入口。

对外只暴露这些：
    Dao                     门面（连接池生命周期）
    dao.ge / dao.su / ...   7 个域仓储（GE SU RE LO WE TE DE）
    DAO 异常                 语义化异常，不泄漏 psycopg 类型
    DOMAINS / CROSS_TABLES   7 域 ↔ 物理表目录
    selfcheck()              目录自检

契约③ 真源：本目录的 ``README.md``
"""
from __future__ import annotations

from .catalog import ALL_TABLES, CROSS_TABLES, DOMAINS, Domain, domain_of, selfcheck
from .errors import (
    ContractViolation,
    DaoError,
    NotFound,
    StorageUnavailable,
    UnknownDomain,
    UnknownTable,
)
from .pool import Dao
from .repo import DomainRepository, LoRepository

__version__ = "0.1.0"

__all__ = [
    "Dao",
    "DomainRepository",
    "LoRepository",
    "DOMAINS",
    "CROSS_TABLES",
    "ALL_TABLES",
    "Domain",
    "domain_of",
    "selfcheck",
    "DaoError",
    "StorageUnavailable",
    "UnknownDomain",
    "UnknownTable",
    "NotFound",
    "ContractViolation",
    "__version__",
]
