"""设计数据导入 —— 采集适配器包（契约⑤ 的落地侧）。

每个来源一个子包，各自把**自己的**文件格式翻译成**同一个** IR：
    weidi/      纬地 HintCAD（file 来源，首个适配器）
    （待加）hongye/    鸿业
    （待加）manual/    人工录入（含图纸数字化）
    （待加）inferred/  由既有监测数据反推骨架

新增一家厂商 = 新增一个子包 + 在 `REGISTRY` 登记，**校验与落库不用改**。
这就是"格式无关"的全部含义。
"""
from __future__ import annotations

from . import base, errors, weidi

# 厂商标识 → 适配器模块。探测顺序即登记顺序。
REGISTRY: dict[str, object] = {
    weidi.VENDOR: weidi,
}


def detect_vendor(text: str) -> str | None:
    """按魔数猜测来源厂商（只看内容，不看扩展名）。"""
    if weidi.sta.detect(text):
        return weidi.VENDOR
    return None


__all__ = ["base", "errors", "weidi", "REGISTRY", "detect_vendor"]
