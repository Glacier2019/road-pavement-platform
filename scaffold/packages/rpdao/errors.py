"""M3 对象域数据访问层（DAO）的异常体系。

设计约定：DAO 向上层抛**语义化异常**，不把 psycopg 的异常类型泄漏出去。
这样 M4（治理）/M5（融合）/M6（出口）不需要 import psycopg，也就无法绕过 DAO 直连库。
"""
from __future__ import annotations


class DaoError(Exception):
    """DAO 层异常基类。"""


class StorageUnavailable(DaoError):
    """存储不可达（连接池取不到连接、库挂了、超时）。上层应转 503 而非 500。"""


class UnknownDomain(DaoError):
    """请求了未登记的对象域（合法域见 catalog.DOMAINS）。"""


class UnknownTable(DaoError):
    """请求了未登记的表/对象类型——对应「越白名单即越界」。"""


class NotFound(DaoError):
    """对象不存在。上层应转 404。"""


class ContractViolation(DaoError):
    """访问方式违反 DAO 契约（例如绕开白名单拼表名、对只读连接发起写操作）。"""


class WriteGuardError(DaoError):
    """写入被写权守卫拒绝。

    三种情形（见 ``write.assert_writer``）：表无写权登记（只读表）、
    写入方与该表的唯一写权不符（越权）、批量写入为空。
    **这是刻意抛异常而非告警放行**——告警放行等于没管，
    而"以错误的理由通过"比不通过更糟（本项目已有的教训）。
    """
