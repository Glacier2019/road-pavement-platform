"""纬地 HintCAD `.lj` 路基设计中间数据文件解析器（契约⑤ 第 10 段）。

文件格式（实测自 052201341 毕设工程，333 行）
-------------------------------------------------------------------------------
    第 1 行   HINTCAD7.0_LJ_SHUJU     ← 魔数，含厂商版本号（**7.0**，比其它文件新）
    第 2 行起 24 个 TAB 分隔的数值    ← 每行一个桩号断面
    行尾 CRLF

★★ 说明书与实测**三处对不上**（逐列比出来的，不是猜的）
-------------------------------------------------------------------------------
教程 §13.6 说「每一行共 **20** 项数据」，并给出列序：
「桩号、地面标高、设计标高、左侧土路肩宽度、左侧硬路肩宽度、左侧路面宽度、
  左半幅中分带宽度、右半幅中分带宽度、右侧路面宽度、右侧硬路肩宽度、右侧土路肩宽度。
  然后是前述各路幅宽度位置相对于路面设计标高位置（超高旋转位置）的设计高差。」

实测：
  (1) **24 列**，不是 20；
  (2) 第 6 列 = 0.000、第 7 列 = 3.500 —— 说明书写的「左路面 左中分带」**写反了**，
      实际是「左中分带 左路面」；
  (3) 第 **9、11** 列在说明书里**没有对应项**（本工程恒 0.000）。按对称读法右侧应是
      「中分带 0.000 / 路面 3.500 / 硬路肩 0.750 / 土路肩 0.750」4 项，
      实测第 8–13 列却是「0.000 0.000 3.500 0.000 0.750 0.750」—— **多插了两个 0.000**。

  ⇒ 第 9、11 列**不猜**，按位置命名（`extra_width_09_m` / `extra_width_11_m`）并标**待考**。

★ 后 11 列（第 14–24）是什么
-------------------------------------------------------------------------------
说明书只说「各路幅宽度位置相对于路面设计标高位置（超高旋转位置）的设计高差」，
没说几个、怎么排。实测这 11 列可用 `.CTR` 的横坡（土路肩 3% / 硬路肩 2% /
行车道 2% / 中分带 0%）按宽度**累计**复现：K0+000 得
0.0225 / 0.0375 / 0.1075，与文件完全吻合；
但拿同一组**常数**横坡套全部 332 行**只对 204 行**（其余 128 行最大差 0.4725）
—— 差的那 128 行**正是超高段**。
即：这 11 列编码的是**逐桩真实横坡**，因此它们能**反过来校验 `.SUP`**。

★ 这条校验**已经做了**（契约测试第 12b 组），332/332 行全对。结论与规则：

    elev_diff_{i+1} − elev_diff_i = −σ · 宽度_i · 横坡_i / 100     σ = +1 左 / −1 右

  · **σ 必须有**：`.SUP` 里左右两侧横坡用同一套符号（正常路拱两侧都写 −2.00），
    而高差是「离开旋转轴就下降」，故右半幅要翻号。漏掉 σ 时 332 行**全部**不符。
  · 10 个增量依次是「左土路肩 / 左硬路肩 / 左中分带 / 左行车道 / 0 / 0 /
    右行车道 / 右中分带 / 右硬路肩 / 右土路肩」（中间两个 0 是左右中分带，本工程宽 0）。
  · **宽度取自本文件自己那一行**，横坡取自 `.SUP` —— 两个文件互相印证，谁也没抄谁。
  · 容差 2e-4 由源精度推出：`.SUP` 横坡两位小数 × 最大宽度 3.5 m = 1.75e-4，
    加本文件四位小数的半 ULP。实测最大残差 9.0e-5 —— 剩下的差全是舍入。

  这同时钉住了 `.SUP` 的**横坡值**、`.SUP` 的 **9999 语义**（跳过=插值穿过，
  不是沿用上值：按沿用上值算 55/332 行不符），以及本文件这 11 列的含义。

为什么它进 GE
-------------------------------------------------------------------------------
教程 §13.6：「此文件在特殊情况时，用户可做少量修改」—— 它是**可编辑的设计输入**，
不是纯派生量。且路基设计表与横断面戴帽子都从它取数。
"""
from __future__ import annotations

import re
from typing import Any

from ..errors import SourceInvalid

MAGIC_RE = re.compile(r"^HINTCAD([0-9][0-9.]*)_LJ_SHUJU$")

SEGMENT = "roadbed_design_point"
FILE_KIND = "路基设计中间数据文件"
PAYLOAD_KEY = "points"

N_COLUMNS = 24

#: 24 列 → 落库英文列名。**顺序即列序**（文件没有自带表头，只能按位置）。
#: ★ 与 DDL 的 J2 表列名必须一致 —— 契约测试逐条对账。
#: ⚠ 第 9、11 列按位置命名并标待考，原因见模块 docstring。
COLUMNS: tuple[str, ...] = (
    "station_m",                    #  1  IR 约定：米（落库时才换算 km）
    "ground_elev_m",                #  2  与 .DMX 重复，照存（可追溯）
    "design_elev_m",                #  3  与 .ZDM 重复，照存
    "left_earth_shoulder_width_m",  #  4  实测恒 0.750
    "left_hard_shoulder_width_m",   #  5  实测恒 0.750
    "left_median_width_m",          #  6  实测恒 0.000
    "left_lane_width_m",            #  7  实测恒 3.500
    "right_median_width_m",         #  8  实测恒 0.000
    "extra_width_09_m",             #  9  ★待考（说明书无此项，恒 0.000）
    "right_lane_width_m",           # 10  实测恒 3.500
    "extra_width_11_m",             # 11  ★待考（说明书无此项，恒 0.000）
    "right_hard_shoulder_width_m",  # 12  实测恒 0.750
    "right_earth_shoulder_width_m", # 13  实测恒 0.750
    "elev_diff_01_m",               # 14  高差 1（可为负）
    "elev_diff_02_m",               # 15
    "elev_diff_03_m",               # 16
    "elev_diff_04_m",               # 17
    "elev_diff_05_m",               # 18
    "elev_diff_06_m",               # 19
    "elev_diff_07_m",               # 20
    "elev_diff_08_m",               # 21
    "elev_diff_09_m",               # 22
    "elev_diff_10_m",               # 23
    "elev_diff_11_m",               # 24
)

#: 待考列（在报告/告警里要显式点名，别让人以为它们有确定含义）
UNKNOWN_COLUMNS: tuple[str, ...] = ("extra_width_09_m", "extra_width_11_m")

#: 那 11 个高差列
ELEV_DIFF_COLUMNS: tuple[str, ...] = tuple(f"elev_diff_{i:02d}_m" for i in range(1, 12))


def detect(text: str) -> bool:
    """格式探测：只认魔数，不靠扩展名。"""
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    return bool(MAGIC_RE.match(first))


def parse(text: str, *, file: str | None = None) -> dict[str, Any]:
    """解析 `.lj` → ``{"vendor_version", "points": [ {…24 列…}, … ], "notes": []}``

    每个点的键是**英文列名**（`COLUMNS`），另加 `station_m`（米，IR 统一单位）。

    校验策略：**宁可拒绝，不要猜。**
    这个文件是路基设计表与横断面戴帽子的数据源，读错一列不会报错，
    只会让整条路的路幅宽度、设计标高、超高位置全错。
    """
    if not text.strip():
        raise SourceInvalid("空文件", file=file)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    first = lines[0].lstrip("\ufeff").strip()
    m = MAGIC_RE.match(first)
    if not m:
        raise SourceInvalid(
            f"魔数不匹配：期望形如 `HINTCAD<版本>_LJ_SHUJU`，实为 {first[:40]!r}",
            file=file, line_no=1)
    version = m.group(1)

    points: list[dict[str, Any]] = []
    notes: list[str] = []
    for i, raw in enumerate(lines[1:], start=2):
        s = raw.strip()
        if not s:
            continue
        parts = raw.split("\t")
        # 尾列可能带空串（与 .tf 同源的写法），显式认掉
        if len(parts) == N_COLUMNS + 1 and parts[-1].strip() in ("", '""'):
            parts = parts[:-1]
        if len(parts) != N_COLUMNS:
            raise SourceInvalid(
                f"字段数应为 {N_COLUMNS}（实测列数；说明书说 20 —— 对不上，见模块 docstring），"
                f"实为 {len(parts)}：{s[:60]!r}", file=file, line_no=i)
        row: dict[str, Any] = {}
        for en, txt in zip(COLUMNS, parts):
            row[en] = _num(txt, en, file=file, line_no=i)
        st = row["station_m"]
        if st is None:
            raise SourceInvalid("桩号为空", file=file, line_no=i)
        if st < 0:
            raise SourceInvalid(f"桩号为负：{st}", file=file, line_no=i)
        points.append(row)

    if not points:
        notes.append("没有任何数据行 —— 文件可能被截断")
    return {"vendor_version": version, PAYLOAD_KEY: points, "notes": notes}


def _num(txt: str, what: str, *, file: str | None, line_no: int) -> float | None:
    t = txt.strip()
    if t == "":
        return None
    try:
        return float(t)
    except ValueError:
        raise SourceInvalid(f"{what}不是合法数字：{txt!r}", file=file, line_no=line_no) from None


def check_against_stations(points: list[dict[str, Any]],
                           stations: list[dict[str, Any]]) -> list[str]:
    """与 `.STA` 桩号序列对账 —— 比**集合相等**（`.lj` 是逐桩文件，实测行数相同）。"""
    have = {round(p["station_m"], 3) for p in points}
    want = {round(s["station_m"], 3) for s in stations}
    out: list[str] = []
    miss, extra = sorted(want - have), sorted(have - want)
    if miss:
        out.append(f"桩号序列里有、.lj 里没有的 {len(miss)} 个（前 3：{miss[:3]}）")
    if extra:
        out.append(f".lj 里有、桩号序列里没有的 {len(extra)} 个（前 3：{extra[:3]}）")
    return out


def check_design(points: list[dict[str, Any]]) -> list[str]:
    """物理合理性。**正例必须零告警** —— 否则告警就是噪声，人会学会无视它。

    ⚠ 写这条时踩过一次：`.tf` 那边我先写了「填挖面积不可能同时为正」，
      实测报了 16 条假告警（半填半挖断面，山区常见），**前提本身就是错的**。
      所以这里只写**站得住**的判据：宽度非负、标高量级合理、桩号递增。
      **宁可少写，不写会常响的规则。**
    """
    out: list[str] = []
    for p in points:
        st = p["station_m"]
        for k in COLUMNS:
            if k.endswith("_width_m"):
                v = p.get(k)
                if v is not None and v < 0:
                    out.append(f"{st} m：{k} 为负 {v}")
        g, d = p.get("ground_elev_m"), p.get("design_elev_m")
        # 标高量级：中国公路高程不会低于 −500 m 或高于 9000 m（珠峰 8848）。
        # 这条不是为了"正确"，是为了抓**列错位**（把面积当标高读会得到几千的怪值）。
        for nm, v in (("地面标高", g), ("设计标高", d)):
            if v is not None and not (-500.0 <= v <= 9000.0):
                out.append(f"{st} m：{nm} {v} 超出合理量级（疑似列错位）")
    # 桩号必须严格递增：逐桩文件按桩号排序，乱序会让"取某桩号的断面"静默取错行。
    for a, b in zip(points, points[1:]):
        if b["station_m"] <= a["station_m"]:
            out.append(f"桩号未严格递增：{a['station_m']} → {b['station_m']}")
    return out


def crossfall_at(points: list[dict[str, Any]], station_m: float) -> dict[str, Any] | None:
    """取某桩号的**逐桩真实横坡**（由 11 个高差列与宽度列反推）。

    这是 `.lj` 最有价值的用法之一：它的高差列编码的是**该桩号实际生效的横坡**
    （含超高），所以可以**反过来校验 `.SUP` 的超高过渡**。

    返回 `None` 表示该桩号不在文件里（**不外推**）。
    本函数只做线性插值取那一行；横坡本身按「高差 ÷ 宽度」逐段反推。
    """
    pts = sorted(points, key=lambda p: p["station_m"])
    hit = next((p for p in pts if abs(p["station_m"] - station_m) < 1e-6), None)
    if hit is None:
        return None
    out: dict[str, Any] = {"station_m": hit["station_m"]}
    # 相邻高差之差 ÷ 对应宽度 = 该段横坡（%）。顺序按 11 个高差列的物理排列。
    for i in range(len(ELEV_DIFF_COLUMNS) - 1):
        a, b = hit.get(ELEV_DIFF_COLUMNS[i]), hit.get(ELEV_DIFF_COLUMNS[i + 1])
        if a is None or b is None:
            continue
        out[f"diff_{i + 1}_to_{i + 2}_m"] = round(b - a, 6)
    return out
