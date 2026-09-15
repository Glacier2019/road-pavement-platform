"""M3 对象域目录：7 大核心对象域 ↔ 物理表的映射（契约③ 的机器可读部分）。

============================ 口径与依据 ============================
1) **7 域的口径**来自报告第五章（原文）：GE 5 表 / SU 4 表 / RE 2 表 / LO 5 表 /
   WE 1 表 / TE 3 表 / DE 5 表，共 **25 表**核心逻辑视图。
2) **物理表名**逐个取自 output/路面性能数据库-DDL-v0.2.sql 的 A–G 分组，
   已在本机 PostgreSQL 实测确认为 30 张基表。
3) 两边**不是一一对应**，差异是真实存在的，故本文件分成两个字段，
   绝不把"逻辑视图里有"当成"物理表已建"：

   · ``tables``     —— 已在物理 DDL 里存在的表（可直接查询）
   · ``logical``    —— 报告逻辑视图里有、但物理 DDL 尚未建的表（查询会报 UnknownTable）

4) 21 张域内物理表 ＋ 9 张跨域支撑表 = **30**，与 DDL 实测数吻合，可作为自检。
   自检见 ``tests/contract/test_dao_contract.py``。
===================================================================
"""
from __future__ import annotations

from typing import NamedTuple


class Domain(NamedTuple):
    code: str                       # 域代号（图件与报告里的两字母码）
    name: str                       # 中文名
    tables: tuple[str, ...]         # 物理 DDL 里已存在的表
    logical: tuple[str, ...]        # 逻辑视图有、物理未建（附原因）
    storage: str                    # 落在哪套存储


DOMAINS: dict[str, Domain] = {
    "GE": Domain(
        "GE", "道路几何",
        ("road_line", "road_section", "structure_layer", "monitor_cross_section"),
        # 逐桩号线形由设计参数解析/竣工复测得到，DDL v0.1 未单独建表（v0.2 待补）
        ("geometry_point（逐桩号平/竖曲线·纵坡·超高·横坡，待 v0.2）",),
        "关系库",
    ),
    "SU": Domain(
        "SU", "路面表面",
        ("inspect_task", "disease_record", "scan3d_model", "media_file"),
        (),
        "关系库＋对象存储",
    ),
    "RE": Domain(
        "RE", "结构响应",
        ("sensor_install", "sensor_channel"),
        # 毫秒级高频通道直落时序库；关系库只存测点元数据（这一对表就是元数据）
        ("时序数据（应变/土压/振动/挠度波形，落 IoTDB，DDL 不建表）",),
        "时序库＋关系库（元数据）",
    ),
    "LO": Domain(
        "LO", "交通荷载",
        ("wim_axle_record", "wim_axle_detail", "traffic_daily_stat"),
        ("交通量 v85 统计表（待 v0.2）", "轮迹分布表（待 v0.2）"),
        "关系库（按月分区）＋时序",
    ),
    "WE": Domain(
        "WE", "环境气象",
        (),
        ("路侧气象站分钟级数据（落时序库，DDL 不建表）",),
        "时序库",
    ),
    "TE": Domain(
        "TE", "试验检测",
        ("test_project", "test_sample", "test_result"),
        (),
        "关系库＋对象存储（原始曲线/报告）",
    ),
    "DE": Domain(
        "DE", "决策输出",
        ("model_output", "diagnosis_result", "maintenance_advice", "alarm_rule", "alarm_record"),
        (),
        "关系库",
    ),
}

# 跨域支撑表：不属于任何单一对象域，但被所有域共用。
#   字典 5 张（DDL 的 B 组）＋ 治理/闭环 4 张（DDL 的 G1–G4）
#   ＋ v0.2 新增 2 张：M4 的质量规则库（G5）、M7 的语义映射集（H1）
CROSS_TABLES: tuple[str, ...] = (
    "dict_sensor_type", "dict_quantity", "dict_disease_type", "dict_axle_type", "dict_stat_metric",
    "data_quality_log", "calibration_log", "feedback_record", "data_import_batch",
    "quality_rule", "mapping_set",
)

# DDL v0.2 的物理表总数。**这个数字必须与 DDL、数据字典三处一致**，
# 由 tests/contract/test_ddl_dict_catalog.py 强制核对——不允许各自漂移。
# 历史：v0.1 = 30 表；v0.2 = 32 表（+quality_rule +mapping_set，契约变更工单 #1）。
EXPECTED_PHYSICAL_TABLES = 32

# 各表的"设计归属模块"：用于写权守卫（谁有权写）与文档生成。
# 不在本表里的表 = 只读表（catalog/字典/档案），默认拒绝写入。
TABLE_OWNER: dict[str, str] = {
    "wim_axle_record": "M2", "wim_axle_detail": "M2", "traffic_daily_stat": "M2",
    "data_import_batch": "M2", "data_quality_log": "M2",
    "quality_rule": "M4", "calibration_log": "M4",
    "mapping_set": "M7",
    "diagnosis_result": "M5", "model_output": "M5",
    "maintenance_advice": "M8", "alarm_rule": "M8", "alarm_record": "M8",
    "feedback_record": "M9",
    "disease_record": "M2", "inspect_task": "M2", "media_file": "M2", "scan3d_model": "M2",
    "test_project": "M2", "test_sample": "M2", "test_result": "M2",
    # M2 的职责是「数据接入与**设备自管**」，故测点/通道元数据也归它写
    "sensor_install": "M2", "sensor_channel": "M2",
}

# 全部物理表的白名单（域内 ＋ 跨域）。任何查询都必须命中这张表，否则拒绝。
ALL_TABLES: tuple[str, ...] = tuple(
    t for d in DOMAINS.values() for t in d.tables
) + CROSS_TABLES


def domain_of(table: str) -> str | None:
    """反查某张物理表属于哪个域；跨域支撑表返回 ``None``。"""
    for code, d in DOMAINS.items():
        if table in d.tables:
            return code
    return None


def selfcheck() -> dict[str, object]:
    """目录自检：返回计数与是否自洽（供契约测试与 /healthz 扩展使用）。

    设计意图：把"报告说 25 表 / DDL 说 30 表"这两个容易互相打脸的数字，
    变成一条可执行断言，改 DDL 时立刻暴露不一致。
    """
    domain_tables = sum(len(d.tables) for d in DOMAINS.values())
    logical_only = sum(len(d.logical) for d in DOMAINS.values())
    dups = [t for t in ALL_TABLES if ALL_TABLES.count(t) > 1]
    return {
        "domains": len(DOMAINS),
        "domain_physical_tables": domain_tables,
        "cross_tables": len(CROSS_TABLES),
        "physical_total": domain_tables + len(CROSS_TABLES),
        "logical_entries_without_table": logical_only,
        "duplicates": sorted(set(dups)),
        "expected_physical_tables": EXPECTED_PHYSICAL_TABLES,
        "ok": not dups and (domain_tables + len(CROSS_TABLES)) == EXPECTED_PHYSICAL_TABLES,
    }
