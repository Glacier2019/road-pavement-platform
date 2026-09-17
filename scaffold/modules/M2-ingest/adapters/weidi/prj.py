"""纬地 HintCAD `.PRJ` 总项目文件解析器。

它跟 `.STA`/`.JD`/`.pm` 不是一类东西
-------------------------------------------------------------------------------
那三个是**几何段**（`road_geometry_ir` 的 `segments`，`additionalProperties: false`
的 8 个键之一），这个不是。`.PRJ` 是**项目档案**：

* 项目身份      → ``design_project``        （1 行）
* 分段设计属性  → ``section_design_attr``    （每个分段 1 行）
* 声明用到的文件 → ``design_file``            （每个文件 1 行）

三张表都在 GE 域、``TABLE_OWNER`` 都是 M2，DDL v0.3 里**早就为它留好了列**
（``design_project.station_interval_m`` 的注释直接写着「毕设工程＝20」，
``design_file.file_kind_code`` 写着「.PRJ〔文件名〕键号（101/102/…）」）。
所以它不该塞进 `segments`——那是几何段的地盘，塞进去会破坏 schema。
调用方式是两段式：先 ``parse()`` 建档案与路段，再拿 ``section_id`` 走几何落库器。

文件格式（实测自 052201341 毕设工程，457 行）
-------------------------------------------------------------------------------
::

    第 1 行     HINTCAD6.00_PRJ_SHUJU        ← 魔数。**6.00**，比 .STA 的 5.84 新
    [项目设置]2                               ← 组名 + 声明条数
    201项目名 = 毕设                          ← 字段号 + 中文名 + " = " + 值
    202项目类型 = 101|公路主线                ← 值可带枚举码："码|文本"
    204桩号小数精度 =                         ← 值可以为空
    [项目分段1]4                              ← 分段可以有多个
    301起点桩号 = 0.000
    …
    [文件名]1
    101平面线形文件(*.PM) = .\\毕设.pm
    …
    [LONG]101                                ← ⚠ 以下三组是**二进制块**
    9240611 = 1                                 键是内存地址，不是字段名
    [DOUBLE]102
    2147418221 = 997128085.000000
    [STRING]103
    2147419246 = HZTXT
    [相关项目]13
    [SaveTimes]
    810369842 = 2026/06/01 20:40

⚠ 两个必须写下来的坑
-------------------------------------------------------------------------------
1. **编码是 GBK，不是 UTF-8。** 这是这批文件里唯一的 GBK 文件
   （`.STA`/`.JD`/`.pm` 全是 ASCII）。按 UTF-8 读会抛 `UnicodeDecodeError`，
   而调用方通常把它解释成"二进制文件、不可解析" —— **这个结论是错的**，
   它是纯文本，只是编码不同。区分"编码不对"与"根本不可解析"很重要：
   前者换个编码就行，后者要等适配器。
2. **`[LONG]/[DOUBLE]/[STRING]` 不是字段，是二进制块。** 它们的键是内存地址
   （``9240611``、``2147418221``），逐行取值毫无意义；而且**组头上声明的条数
   （101/102/103）与实际行数并不相等**，所以连"按条数切块"这条路都不通。
   本解析器**显式跳过**这三组，并把声明的条数记进 ``ignored_blobs`` ——
   记下来是为了让"我没解析这部分"这件事**可见**，而不是悄悄丢掉。
"""
from __future__ import annotations

import re
from typing import Any

from ..errors import SourceInvalid

# HINTCAD6.00_PRJ_SHUJU —— 注意是 6.00，与 .STA(5.84)、.JD/.pm(5.83) 都不同版本
MAGIC_RE = re.compile(r"^HINTCAD([0-9][0-9.]*)_PRJ_SHUJU$")

#: ★ 这个适配器要求用 GBK 读文件（见模块 docstring 的坑 1）
ENCODING = "gbk"

SEGMENT = "design_project"      # 不是几何段，仅作标识用（见 docstring）
FILE_KIND = "总项目文件"

#: 组头：``[项目设置]2``
SECTION_RE = re.compile(r"^\[([^\]]+)\]\s*(\d*)\s*$")
#: 字段行：``201项目名 = 毕设`` / ``涵洞数据文件(*.hda) = .\毕设.hda``（可以没有字段号）
FIELD_RE = re.compile(r"^(\d*)([^=\d][^=]*?)\s*=\s*(.*)$")
#: 值里的枚举前缀：``101|公路主线``
ENUM_RE = re.compile(r"^(\d+)\|(.*)$")

#: 只解析这三组。其余组一律跳过（其中三组是二进制块）。
READABLE_GROUPS = ("项目设置", "文件名")
SEGMENT_GROUP_RE = re.compile(r"^项目分段(\d+)$")

#: 二进制块组名。**必须跳过**——它们的键是内存地址。
BLOB_GROUPS = ("LONG", "DOUBLE", "STRING", "相关项目")

#: 项目设置里的字段号 → 输出键
PROJECT_FIELDS = {
    "201": ("project_name", "str"),
    "202": ("project_type", "enum"),
    "204": ("station_decimals", "num"),
    "205": ("station_interval_m", "num"),
    "209": ("earthwork_method", "enum"),
    "214": ("project_uid", "str"),
    "251": ("design_org", "str"),
    "252": ("client_org", "str"),
    "253": ("design_stage", "str"),
    "254": ("designer", "str"),
    "255": ("checker", "str"),
}

#: 分段里的字段号 → 输出键
SEGMENT_FIELDS = {
    "301": ("start_station_m", "num"),
    "302": ("end_station_m", "num"),
    "303": ("road_region", "str"),
    "304": ("design_speed_kmh", "num"),
    "305": ("road_grade", "str"),
    "306": ("cross_section_form", "str"),
    "307": ("roadway_width_m", "num"),
    "308": ("carriageway_crossfall_pct", "pct"),
    "309": ("shoulder_crossfall_pct", "pct"),
    "310": ("median_width_m", "num"),
    "311": ("max_superelev_pct", "pct_in_text"),
    "312": ("superelev_rotate_mode", "enum"),
    "313": ("superelev_gradient_mode", "enum"),
    "314": ("widening_mode", "str"),
    "315": ("widening_method", "str"),
    "316": ("widening_gradient_mode", "enum"),
}

#: ``306横断面形式 = 2车道`` → 车道数。**是派生量**，见 derive_lane_count。
LANE_RE = re.compile(r"^(\d+)\s*车道$")
LANE_WORDS = {"单车道": 1, "双车道": 2, "四车道": 4, "六车道": 6, "八车道": 8}


def detect(text: str) -> bool:
    """格式探测：只认魔数，不靠扩展名（扩展名可以改，魔数不会）。"""
    lines = text.splitlines()
    first = lines[0].lstrip("\ufeff").strip() if lines else ""
    return bool(MAGIC_RE.match(first))


def decode(raw: bytes) -> str:
    """按 GBK 解码。**唯一的入口** —— 免得每处调用各写一遍编码。"""
    try:
        return raw.decode(ENCODING)
    except UnicodeDecodeError as exc:
        raise SourceInvalid(f"不是合法 GBK 文本：{exc}") from None


def derive_lane_count(cross_section_form: str | None) -> int | None:
    """``"2车道"`` → ``2``。**派生量**，不是文件里的字段。

    认不出就回 ``None`` —— 不回 2、也不回 0。车道数是下游分析的分母，
    猜错会一路错到底且不报错。中文字面量只认白名单里那几个，
    因为"几车道"写成中文时没有统一的解析规则，硬猜不如不猜。
    """
    if not cross_section_form:
        return None
    s = cross_section_form.strip()
    m = LANE_RE.match(s)
    if m:
        return int(m.group(1))
    return LANE_WORDS.get(s)


def _as_num(text: str) -> float | None:
    try:
        return float(text.strip())
    except (TypeError, ValueError):
        return None


def _split_enum(value: str) -> tuple[str | None, str]:
    """``"101|公路主线"`` → ``("101", "公路主线")``；无码则 ``(None, 原值)``。"""
    m = ENUM_RE.match(value.strip())
    return (m.group(1), m.group(2).strip()) if m else (None, value.strip())


def _convert(kind: str, value: str) -> Any:
    """按字段类型取值。**认不出就回 None**，绝不拿近似值顶上。"""
    v = value.strip()
    if v == "":
        return None
    if kind == "str":
        return v
    if kind == "enum":
        return _split_enum(v)[1] or None
    if kind == "num":
        return _as_num(v)
    if kind == "pct":                     # "2.0%" → 2.0
        return _as_num(v.rstrip("%"))
    if kind == "pct_in_text":             # "最大超高8%" → 8.0
        m = re.search(r"(-?\d+(?:\.\d+)?)", v)
        return float(m.group(1)) if m else None
    raise AssertionError(f"未知字段类型 {kind!r}")


def parse(text: str, *, file: str | None = None) -> dict[str, Any]:
    """解析 `.PRJ` → 项目档案 + 分段列表 + 文件台账。

    校验策略同其余适配器：**宁可拒绝，不要猜。**
    这里猜错的代价是把毕设的线形挂到 G228 试验段上——数据看着完全正常，
    归属却是错的，而且不会有任何报错。
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines:
        raise SourceInvalid("空文件", file=file)

    first = lines[0].lstrip("\ufeff").strip()
    m = MAGIC_RE.match(first)
    if not m:
        raise SourceInvalid(
            f"魔数不匹配：期望形如 `HINTCAD<版本>_PRJ_SHUJU`，实为 {first[:40]!r}",
            file=file, line_no=1,
        )
    version = m.group(1)

    project: dict[str, Any] = {}
    segments_raw: list[tuple[int, dict[str, Any]]] = []
    files: list[dict[str, Any]] = []
    ignored_blobs: dict[str, int] = {}
    #: 认得出来、但没有对应目标列的字段。**不进 warnings** ——
    #: 它们不是异常，只是没映射。混进 warnings 会让真告警被 13 条噪音淹没，
    #: 而"真告警被淹没"比"没有告警"更危险。放这里，可见但不吵。
    unmapped: dict[str, Any] = {}
    warnings: list[str] = []
    save_time: str | None = None

    group: str | None = None
    seg_idx: int | None = None
    for i, raw in enumerate(lines[1:], start=2):
        line = raw.strip()
        if line == "":
            continue

        hdr = SECTION_RE.match(line)
        if hdr:
            group = hdr.group(1)
            declared = hdr.group(2)
            if group in BLOB_GROUPS:
                # 记下"声明了多少条"，让"这块我没解析"可见（见 docstring 坑 2）
                ignored_blobs[group] = int(declared) if declared else 0
                seg_idx = None
                continue
            sm = SEGMENT_GROUP_RE.match(group)
            if sm:
                seg_idx = int(sm.group(1))
                segments_raw.append((seg_idx, {}))
                continue
            seg_idx = None
            continue

        if group in BLOB_GROUPS:
            continue                       # 二进制块的内容行，一律不看

        fm = FIELD_RE.match(line)
        if not fm:
            if group == "SaveTimes":
                continue
            warnings.append(f"第 {i} 行不是可识别的字段：{line[:40]!r}")
            continue

        code, name, value = fm.group(1), fm.group(2).strip(), fm.group(3)

        if group == "SaveTimes":
            save_time = value.strip()
            continue
        if group == "项目设置":
            spec = PROJECT_FIELDS.get(code)
            if spec:
                project[spec[0]] = _convert(spec[1], value)
            elif code:
                unmapped[f"项目设置.{code}{name}"] = value.strip() or None
        elif seg_idx is not None:
            spec = SEGMENT_FIELDS.get(code)
            if spec:
                segments_raw[-1][1][spec[0]] = _convert(spec[1], value)
            elif code:
                unmapped[f"分段{seg_idx}.{code}{name}"] = value.strip() or None
        elif group == "文件名":
            # 有的行没有字段号（实测：涵洞的两个文件），照收，码记 None
            files.append({
                "kind_code": code or None,
                "kind_name": name,
                "rel_path": value.strip() or None,
            })

    # ---- 校验：宁可拒绝，不要猜 ----
    if not project:
        raise SourceInvalid("没有解析到任何 [项目设置] 字段", file=file)
    if not project.get("project_name"):
        raise SourceInvalid("缺 201项目名", file=file)
    if not project.get("project_uid"):
        # 项目 ID 缺了不致命（可以按项目名认人），但必须说出来
        warnings.append("缺 214项目ID，design_project.project_uid 将为空")
    if not segments_raw:
        raise SourceInvalid("没有任何 [项目分段N] 组", file=file)

    segments = []
    for idx, seg in segments_raw:
        a, b = seg.get("start_station_m"), seg.get("end_station_m")
        if a is None or b is None:
            raise SourceInvalid(f"分段 {idx} 缺起点或终点桩号", file=file)
        if b <= a:
            raise SourceInvalid(f"分段 {idx} 终点桩号不大于起点：{a} → {b}", file=file)
        seg["seq"] = idx
        seg["length_m"] = round(b - a, 6)
        seg["lane_count"] = derive_lane_count(seg.get("cross_section_form"))
        if seg.get("cross_section_form") and seg["lane_count"] is None:
            warnings.append(
                f"分段 {idx} 的横断面形式 {seg['cross_section_form']!r} 认不出车道数，"
                "lane_count 留空（不猜）")
        segments.append(seg)

    return {
        "vendor_version": version,
        "source_file": file,
        "project": project,
        "segments": segments,
        "files": files,
        "save_time": save_time,
        "ignored_blobs": ignored_blobs,
        "unmapped": unmapped,
        "warnings": warnings,
    }


#: 承载载荷的键。**与其余适配器不同**：`.PRJ` 一次产出三类东西
#: （项目 1 行 / 分段 N 行 / 文件 N 行），没有单一"载荷"。
#: 故不给 PAYLOAD_KEY —— 它不参与 IR 的 segments 装配，由调用方直接使用。
PAYLOAD_KEY = None

__all__ = ["detect", "decode", "parse", "derive_lane_count",
           "MAGIC_RE", "ENCODING", "SEGMENT", "FILE_KIND", "PAYLOAD_KEY"]
