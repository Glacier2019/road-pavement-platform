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


class CrsBindingError(ContractViolation):
    """坐标系绑定不可用或自相矛盾（v0.6，契约变更工单 005）。

    三种情形：
      · **两级都有基准、但取值不等** —— 路段说一套、项目说另一套（I3）。
      · **基准查不到** —— `crs_epsg` 不在 `spatial_ref_sys.srid` 里（I1 / FR-005）。
      · **没有基准** —— 两级都没配。这**不是错误**，见下面的「为什么仍然抛异常」。

    ⚠ 为什么「没有基准」也走异常，而不是返回 None
    -------------------------------------------------
    因为 `None` 会被上层读成「随便挑一个默认值就行」。004 已经踩过这个坑：
    一个「找不到就用 117°E」的兜底会让 99% 的调用方正常工作，
    只在换基准时静默算错 —— 而错带号的特征是纬度不变、经度整体平移一个带宽，
    在局部视图里根本看不出来。

    ⇒ 「没有」必须是一个**能被捕获、能被翻译成 503、且自带说法**的东西（FR-003 / E5）。

    ★ 继承 `ContractViolation` 而不是 `DaoError`：这不是「存储挂了」，
      是**数据本身违反了契约**。上层 `_http()` 已有的分支能正确翻译它，
      不必新增分支。

    ★★ 为什么 M3 抛自己的、M6 再翻译一次：宪法 II 禁止 M3 import M6，
      而 `CrsError` 住在 M6。所以两边各有一个异常类，由 M6 的 `_geo_crs()`
      做翻译。**两条信息必须都保留下来**（见 `two_levels_note`）。
    """

    def __init__(self, message: str, *, two_levels_note: str = "") -> None:
        super().__init__(message)
        #: ★ 两级各自取值的原话（SC-009）。空串表示「与两级取值无关」的失败
        #: （例如查不到表、没有基准）。**单独留一个字段而不是塞进 message**，
        #: 是为了让上层不必靠解析字符串就能把话原样带给用户。
        self.two_levels_note = two_levels_note


class WriteGuardError(DaoError):
    """写入被写权守卫拒绝。

    三种情形（见 ``write.assert_writer``）：表无写权登记（只读表）、
    写入方与该表的唯一写权不符（越权）、批量写入为空。
    **这是刻意抛异常而非告警放行**——告警放行等于没管，
    而"以错误的理由通过"比不通过更糟（本项目已有的教训）。
    """
