"""M3 对象域目录：7 大核心对象域 ↔ 物理表的映射（契约③ 的机器可读部分）。

============================ 口径与依据 ============================
1) **7 域的口径**来自报告第五章（原文）：GE 5 表 / SU 4 表 / RE 2 表 / LO 5 表 /
   WE 1 表 / TE 3 表 / DE 5 表，共 **25 表**核心逻辑视图。
2) **物理表名**逐个取自 scaffold/sql/10_ddl_v0.5.sql 的 A–I 分组，
   已在本机 PostgreSQL 实测确认为 55 张基表。
3) 两边**不是一一对应**，差异是真实存在的，故本文件分成两个字段，
   绝不把"逻辑视图里有"当成"物理表已建"：

   · ``tables``     —— 已在物理 DDL 里存在的表（可直接查询）
   · ``logical``    —— 报告逻辑视图里有、但物理 DDL 尚未建的表（查询会报 UnknownTable）

4) 44 张域内物理表 ＋ 11 张跨域支撑表 = **55**，与 DDL 实测数吻合，可作为自检。
   历史：v0.2 = 21 域内 ＋ 11 跨域 = 32；v0.3 = 33 域内 ＋ 11 跨域 = 44。
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
        ("road_line", "road_section", "structure_layer", "monitor_cross_section",
         # ↓ v0.3 新增 10 张：纬地 HintCAD 设计工程文件接入（契约变更工单 #2）
         "design_project", "design_file", "section_design_attr",
         "station_sequence", "station_equation",
         "alignment_pi", "alignment_element",
         "profile_grade_point", "profile_ground_point", "geometry_point",
         # ↓ 第 11 张：超高过渡。原列在 v0.4 待办（"超高路幅"），
         #   因读纬地教程 §13.5 发现 geometry_point 的 superelev_pct 单列存不下
         #   "左右各一个行车道横坡"，故提前落地，与 .SUP 变化点一一对应。
         "superelev_transition",
        # ↓ 第 12 张：路幅宽度。原列在 v0.4 待办，因 section_design_attr.roadway_width_m
        #   是**标量**、而路幅宽度本来就随桩号变（加宽/匝道/交叉口/变速车道），
        #   一个标量装不下分段变化 → 按「存设计输入、导出派生量」提前落地。
        "roadbed_width",
        # ↓ v0.4 新增 9 张：设计参数控制文件（.CTR）接入。
        #   为什么不并进 A 节：A 节是「空间与档案」（几何实体本身），这 9 张是
        #   **设计控制参数**（边坡/边沟/路槽/构造物等"怎么修"的控制量）—— 两者都由
        #   .STA 桩号寻址，但性质不同，故 DDL 里单开一节 I。
        #   依据：纬地教程 v5.88 §13.10（18 类格式 / 36 个关键字）。本工程 .CTR 实测
        #   36 个关键字：19 个有数据、17 个为空 —— 这 9 张覆盖**有数据的全部**。
        "slope_segment", "ditch_segment", "standard_cross_section",
        "roadbed_trench", "structure_control", "earthwork_composition",
        "land_use_width", "extra_fill", "design_control_text",
        # ── J 节（v0.5）：逐桩土方断面 / 逐桩路基设计断面（教程 §13.9 / §13.6）──
        "earthwork_section", "roadbed_design_point",
        # ── K 节（v0.5 后补）：逐桩横断面地面线测点（教程 §13.8）──
        #    原记录「经确认不做」，2026-09 用户改判为要做。理由与定性无关：
        #    M2 的 .HDM 解析器**能**解析出 cross_section 段，而平台没有对应的表，
        #    解析出来的数据没有地方存 ——「段能解析、表不存在」= 静默丢数据。
        #    表名照内容走：内容实测是**外业测量**的地面线（高差 −31 ~ +23 m），
        #    与 profile_ground_point（← .DMX 纵断面地面线）一纵一横同构。
        "cross_section_ground_point",
        # ── L 节（v0.5 后补）：土石方调配（.tsf，纬地 **HintTF** 的成果）──
        #    源文件是**另一个产品**的：I 节全是 .CTR（HintCAD 的文本控制参数），
        #    这是 .tsf（Microsoft Access / Jet 4 数据库）。一个是"设计时定的控制量"，
        #    一个是"设计完成后算出来的调配成果"，故 DDL 里单开一节 L。
        #    ★ 本版只落最小的一张（土石系数 1 行 6 列），其余真有数据的
        #      （过程 27 行 / 统计扩展 334×73 / 土石计算 334×137）待做 ——
        #      一次只做一张，是为了把整条链路先走通一遍再照抄。
        #    ★ 也是 `design_control` 之后**第二个「段名 ≠ 单张物理表名」的例外**：
        #      .tsf 是 1 文件 ↔ 多表（实测 20 张）。
        "earthwork_factor",
        "earthwork_transfer",
        "borrow_pit",
        "spoil_pit"),
        # geometry_point 已于 v0.3 落地为物理表（原为逻辑占位），故此处不再登记
        (),
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
        # 不绑版本号：曾写"待 v0.2"，v0.2 已发布而这两张仍未建 —— 版本号写进注释
        # 就会在图件上变成过期标注。是否落地由工单决定，不由注释预告。
        ("交通量 v85 统计表（逻辑视图，物理未建）", "轮迹分布表（逻辑视图，物理未建）"),
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

# DDL 的物理表总数。**这个数字必须与 DDL、数据字典三处一致**，
# 由 tests/contract/test_ddl_dict_catalog.py 强制核对——不允许各自漂移。
# 历史：v0.1 = 30 表；v0.2 = 32 表（+quality_rule +mapping_set，契约变更工单 #1）；
#       v0.3 = 44 表（+GE 域 12 张，契约变更工单 #2 ＋ 纬地教程 §13.5/§13.4 校正）。
#       v0.4 = 53 表（+I 节 9 张：.CTR 设计参数控制，纬地教程 §13.10）。
#       v0.5 = 55 表（+J 节 2 张：earthwork_section .tf 土方断面、
#                       roadbed_design_point .lj 路基设计断面，教程 §13.9/§13.6）。
#       第二批（横断面/路基土方/构造物）顺延至 v0.5；其中「横断面地面线 .HDM」经确认不做。
#       （原为 11 张："超高"已按教程 §13.5 提前落到 v0.3 的 superelev_transition，
#         "路幅宽度"已按教程 §13.4 提前落到 v0.3 的 roadbed_width）
EXPECTED_PHYSICAL_TABLES = 60

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
    # GE 域设计数据（v0.3）：来源＝纬地设计工程文件，写入口同样是 M2 数据接入。
    # 注意：不登记于此的表是**只读表**（写权守卫默认拒绝），故导入目标表必须显式登记。
    "design_project": "M2", "design_file": "M2", "section_design_attr": "M2",
    "station_sequence": "M2", "station_equation": "M2",
    "alignment_pi": "M2", "alignment_element": "M2",
    "profile_grade_point": "M2", "profile_ground_point": "M2", "geometry_point": "M2",
    "superelev_transition": "M2", "roadbed_width": "M2",
    # v0.4：.CTR 设计参数控制 9 张（同源：都来自设计文件，写入口仍是 M2 数据接入）
    "slope_segment": "M2", "ditch_segment": "M2", "standard_cross_section": "M2",
    "roadbed_trench": "M2", "structure_control": "M2", "earthwork_composition": "M2",
    "land_use_width": "M2", "extra_fill": "M2", "design_control_text": "M2",
    "earthwork_section": "M2", "roadbed_design_point": "M2",
    "cross_section_ground_point": "M2",   # v0.5 K 节：.HDM 横断面地面线，同源（设计文件）
    "earthwork_factor": "M2",
    "earthwork_transfer": "M2",
    "borrow_pit": "M2",                 # v0.5 N 节：.tsf 取土坑，同源（设计文件）
    "spoil_pit": "M2",                  # v0.5 N 节：.tsf 弃土坑，同源         # v0.5 M 节：.tsf 土石方调配过程，同源
             # v0.5 L 节：.tsf 土石方调配，同源（设计文件）
    # GE 域**骨架四表**（v0.1 起就有，原先未登记 → 只读，任何服务都写不了）。
    # 为何现在必须放开：导入一条**新道路**必然要创建 road_line / road_section，
    # 原先只靠 90_seed_skeleton.sql 种入 —— 那就等于"只能导别人已经种好的路"，
    # 与「以后有新的道路文件可以同样导入」直接冲突。
    # 写入口仍是 M2 数据接入（与上面 10 张同源：都来自设计文件）。
    "road_line": "M2", "road_section": "M2",
    "structure_layer": "M2", "monitor_cross_section": "M2",
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
