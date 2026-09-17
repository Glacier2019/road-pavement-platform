"""设计数据导入 —— 异常类型。

为什么要单独定义异常而不是直接 `ValueError`
-------------------------------------------------------------------------------
导入失败必须**可分类**：是"源里根本没有这一段"还是"有但解不开"还是"数据坏了"，
三者对用户的行动完全不同（前者无需处理、中者要换工具、末者要修源文件）。
混成一个 ValueError，上层只能显示"导入失败"，用户无从下手。

因此：
  · `ParseBlocked`  —— 有文件但解不开（二进制 / 需专有工具）。**不是错误，是已知缺口**，
     应登记进 IR 的 gaps（reason=parse_blocked），导入流程照常继续。
  · `SourceInvalid` —— 源数据本身不合法（表头不对、字段数不对、桩号倒退…）。
     这是**真错误**，必须拒绝整批，因为它说明"我们理解错了这个文件"。
"""
from __future__ import annotations


class ImportError_(Exception):
    """导入相关异常的基类（名字带下划线以免遮蔽内建 ImportError）。"""


class SourceInvalid(ImportError_):
    """源数据不合法 —— 说明适配器理解错了文件，必须拒绝整批。

    ★ 关键取舍：**宁可拒绝，不要猜**。
    解析器猜错一个字段的后果是静默写入错误几何，而几何是下游一切桩号对齐的基准——
    错一条桩号，挂在上面的所有逐桩数据全部错位，且**不会有任何报错**。
    """

    def __init__(self, message: str, *, file: str | None = None, line_no: int | None = None) -> None:
        self.file = file
        self.line_no = line_no
        where = ""
        if file:
            where = f"[{file}" + (f":{line_no}" if line_no is not None else "") + "] "
        super().__init__(where + message)


class ParseBlocked(ImportError_):
    """有文件但解不开（二进制 / 压缩 / 专有格式）。

    这不是失败——是**已知缺口**。调用方应把它登记进 IR 的 gaps，继续解析其余文件。
    """


class UnsupportedSource(ImportError_):
    """没有任何适配器认得这个来源（例如只有图纸、或未知厂商格式）。

    上层应据此走**降级路径**（人工录入模板 / 反推骨架），而不是报"格式不支持"了事。
    """
