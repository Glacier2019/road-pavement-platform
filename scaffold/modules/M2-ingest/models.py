"""WIM 轴载载荷契约的 Python 侧实现。

与 contracts/messages/wim_axle.v1.schema.json 一一对应，供三处复用：
  ① M2 接入服务做报文校验（modules/M2-ingest/app.py 导入）
  ② 契约一致性测试（tests/contract/test_wim_contract.py 导入）
  ③ 模拟器造数（simulator/wim_simulator.py 导入）

纪律：**改这个文件 = 改契约**。必须同时改 JSON Schema，并按
     MODULE-ONBOARDING.md §4 走契约变更工单（评审后才能合并）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "wim_axle.v1"

# 单轴标准轴载 100 kN ≈ 8.16 t（BZZ-100），ESAL 四次方律的基准
STANDARD_AXLE_KG = 8160.0

# 车辆总重限值的近似值（GB1589 常见车型），**须由 B 按现行规范最终校准后再冻结**
GROSS_LIMIT_KG: dict[str, float] = {
    "A2": 18_000.0,
    "T3": 25_000.0,
    "T4": 31_000.0,
    "T5": 43_000.0,
    "T6": 49_000.0,
}


class AxleDetail(BaseModel):
    """单轴明细（对应 wim_axle_detail 一行）。"""

    model_config = ConfigDict(extra="forbid")

    axle_seq: int = Field(ge=1)
    weight_kg: float = Field(ge=0)
    group_seq: int | None = Field(default=None, ge=1)
    group_weight_kg: float | None = Field(default=None, ge=0)
    dist_mm: float | None = Field(default=None, ge=0)


class WimPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lane_no: int = Field(ge=1, le=8)
    direction: Literal["up", "down"]
    axle_type_code: Literal["A2", "T3", "T4", "T5", "T6"]
    axle_num: int = Field(ge=2, le=9)
    speed_kmh: float = Field(gt=0, le=200)
    gross_weight_kg: float = Field(ge=0)
    overload_flag: bool | None = None
    plate_no: str | None = Field(default=None, max_length=16)
    axles: list[AxleDetail]

    @model_validator(mode="after")
    def _cross_check(self) -> "WimPayload":
        if len(self.axles) != self.axle_num:
            raise ValueError(
                f"axles 长度({len(self.axles)}) 与 axle_num({self.axle_num}) 不一致"
            )
        seqs = [a.axle_seq for a in self.axles]
        if seqs != list(range(1, len(seqs) + 1)):
            raise ValueError(f"axle_seq 必须从 1 连续编号，实际 {seqs}")
        # 总重与各轴重之和的合理性（≤2% 偏差，超出说明设备标定有问题）
        axle_sum = sum(a.weight_kg for a in self.axles)
        if axle_sum > 0 and abs(axle_sum - self.gross_weight_kg) / axle_sum > 0.02:
            raise ValueError(
                f"总重({self.gross_weight_kg}) 与各轴重之和({axle_sum:.1f}) 偏差 >2%"
            )
        return self


class WimEvent(BaseModel):
    """一条 MQTT 报文（信封 + 载荷）。"""

    model_config = ConfigDict(extra="forbid")

    device_code: str = Field(min_length=2, max_length=64)
    ts: datetime
    seq: int = Field(ge=0)
    schema_version: str
    payload: WimPayload

    @model_validator(mode="after")
    def _version_check(self) -> "WimEvent":
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version 必须为 {SCHEMA_VERSION}，实际 {self.schema_version}")
        return self

    # ---------------------------------------------------------------- 派生量
    def axle_weights(self) -> list[float]:
        return [a.weight_kg for a in self.payload.axles]

    def esal(self) -> float:
        """当量轴次（四次方律，按轴计）。骨架用简化式，标定后由 B 替换。"""
        return round(sum((w / STANDARD_AXLE_KG) ** 4 for w in self.axle_weights()), 4)

    def gross_limit_kg(self) -> float:
        return GROSS_LIMIT_KG.get(self.payload.axle_type_code, 49_000.0)

    def overload_rate(self) -> float:
        """超载率 %（负数=未超载）。"""
        limit = self.gross_limit_kg()
        return round((self.payload.gross_weight_kg - limit) / limit * 100.0, 2)

    def is_overload(self) -> bool:
        flag = self.payload.overload_flag
        return bool(flag) if flag is not None else self.overload_rate() > 0
