# Phase 1 数据模型：桩号 ↔ 经纬度

本文件定义换算的**输入输出形状**与它读的每一张表。
★ 本 feature **不新增表、不改表结构** —— 全部形状都是既有数据的新读法。

---

## 1. 实体关系

```text
road_section（路段）
  │  桩号的命名域。换算的必要输入之一 —— 桩号单独出现是歧义的。
  │  ★ 两个路段的桩号语义不同：毕设 K0+000 与 滨海 K4635+000 是两个不同位置。
  │
  └─< alignment_element（线形单元，33 段，section_id=6）
        │  带 start_x/start_y/end_x/end_y、azimuth_deg、
        │  radius_start_m/radius_end_m、turn_flag
        │  ★ 这是 (x, y) 的唯一来源。
        │
        ├─ 经 adapters.geom.point_at  → (x, y)(s)          【桩号 → 平面坐标】
        ├─ 经 adapters.geom.azimuth_at → θ(s)              【方位角】
        └─ 经 geo.py（本 feature 新写）→ (lat, lon)        【平面坐标 → 经纬度】

geometry_point（332 行，派生缓存）
  └─ ★ 只作 SC-001 的**独立对照**用，不作数据源。
     它在格点上等于 point_at 的输出，直接比就是拿函数跟自己对。

monitor_cross_section（3 个断面）
  └─ 带桩号，lat/lon 列**全空** ⇒ 位置必须由换算得到（FR-009）。
```

---

## 2. 坐标基准配置（`crs.yaml`）—— 投影参数的唯一出处

**本 feature 不修改这个文件，只读它。** 现状：`version: "0.2"`，`ready: true`，`assumed: false`。

| 字段 | 值 | 本 feature 怎么用 |
|---|---|---|
| `geographic_frame.ellipsoid` | `CGCS2000` | 椭球参数（a、f）按此选 |
| `geographic_frame.zone_width_deg` | `3` | 带内范围检查（半宽 1.5°） |
| `geographic_frame.zone_number` | `39` | 输出里必须带上它 |
| `geographic_frame.central_meridian_deg` | `117` | ★ **SC-008 的对照参数** |
| `geographic_frame.assumed` | `false` | ★ 为 `true` 时输出必须标注「值是推断的」（FR-005） |
| `geographic_frame.epsg_after_confirm` | `4548` | ★ 输出里必须带上它，**不得只写"39 带"**（FR-003） |

★★ **两个必须同时满足的不变量**（既有契约测试已强制，本 feature 不得破坏）：

| 不变量 | 含义 |
|---|---|
| `assumed=false` ⇒ `epsg_after_confirm` 非空且与 `zone_number` 对应 | 确认过就必须有编码 |
| `assumed=true` ⇒ `epsg_after_confirm` 必须为空 | **猜的时候不许填 EPSG** |

★ **FR-005 要求两种状态下都能工作**：`assumed: true` 时照常换算，
但输出里把「值是推断的」这个事实带出去。

---

## 3. 读 `alignment_element` 的约定

| 列 | 本 feature 的读法 |
|---|---|
| `element_seq` | 1..33，排序键 |
| `element_type` | `line` / `transition` / `circular` |
| `start_station_km` / `end_station_km` | ★ **公里**。换算的桩号域 |
| `length_m` | 米。`length_m == (end − start) × 1000` 在 33 行上**精确成立** |
| `start_x` / `start_y` | ★★ **`x` 是北坐标（约 278 万米）、`y` 是东坐标（约 53.5 万米）** |
| `azimuth_deg` / `end_azimuth_deg` | 度。SC-003 的对照 |
| `radius_start_m` / `radius_end_m` | 米。直线段为空 |
| `turn_flag` | 圆曲线与缓和曲线的转向符号 |

★★★ **`x`/`y` 读反的失败形态是刺眼的**：调换后逆算落点
**4.54°N / 137.16°E —— 太平洋里，偏约 2300 km**。
SC-004 就钉这一条：读反必须产生肉眼可见的荒谬结果，不能伪装成合理的近邻。

★ **单位陷阱（本会话踩过一次）**：`start_station_km` 是**公里**，
而设计文件 `.DMX` 的桩号是**米**。两者相减前必须换算。
这个错的表现是「插值结果恒定」—— 33 个点全取到第 0 点。

---

## 4. 换算的输出形状

### FR-001 桩号 → 经纬度

| 字段 | 类型 | 说明 |
|---|---|---|
| `section_id` | int | 回显输入 |
| `station_km` | float | 回显输入 |
| `lat` / `lon` | float | 十进制度，**至少 7 位小数**（1 m ≈ 1×10⁻⁵ 度） |
| `x` / `y` | float | 中间量，一并返回 —— **让复核者能只看平面坐标先排掉几何错误** |
| `epsg` | int | `4548`（FR-003，**不得省略**） |
| `zone_number` | int | `39` |
| `central_meridian_deg` | float | `117` |
| `assumed` | bool | 从 `crs.yaml` **读取**，不写死（FR-005） |

★ `x`/`y` 一并返回是刻意的：出错时能一眼看出是**几何**错了还是**投影**错了。

### FR-002 经纬度 → 桩号

| 字段 | 类型 | 说明 |
|---|---|---|
| `section_id` | int | 回显输入 |
| `lat` / `lon` | float | 回显输入 |
| `station_km` | float | 算出的桩号 |
| `on_route` | bool | 是否落在该路段的桩号域内 |
| `distance_m` | float | 到路线的垂距 —— 允许输入不在线上（断面有宽度） |
| `epsg` / `zone_number` / `assumed` | — | 同 FR-001 |

### US2 整条路线（页面用）

| 字段 | 类型 | 说明 |
|---|---|---|
| `section_id` | int | |
| `elements` | array | 33 项，每项含 `element_seq`、`element_type`、桩号区间、长度、半径、方位角、**以及该段在经纬度下的折线点列** |
| `bbox` | object | `lat_min/lat_max/lon_min/lon_max` —— SC-002 的载体 |
| `epsg` / `zone_number` / `assumed` | — | 同 FR-001 |

★★ **每段必须给点列，不能只给首末点。** 圆曲线的矢高最大 **12.588 m**、
缓和曲线最大 **1.736 m** —— 只连首末点会让曲线**变成折线**，
这正是 1 m 判据要挡的东西（FR-006）。

### US3 断面位置（3 个）

| 字段 | 类型 | 说明 |
|---|---|---|
| `section_id` / `station_km` | — | 输入 |
| `lat` / `lon` | float | **由桩号算出** |
| `source` | 枚举 | 固定为 `"computed_from_station"` —— ★ 界面必须能据此标注「位置由桩号换算」（FR-009） |
| `device_code` | string \| null | 如 `SIMWIM01`；仿真设备据此与真实设备**在视觉上可区分** |

★ `source` 这个字段是**刻意的**：库里的 `lat/lon` 列为空，
若将来有人手工填了值，`source` 会变成 `"stored"`，
界面就能如实区分「算出来的」与「库里存的」。

---

## 5. 错误形状（FR-004、FR-010）

| 情形 | 行为 |
|---|---|
| 桩号超出该路段范围（如 `K9+999`） | **明确报错**。不得返回最近点、不得返回原点、不得返回空对象冒充成功 |
| `section_id` 不存在 | 404 |
| 经纬度落在带外 | **报错**，不硬算（3 度带半宽 1.5°；本路线距中央子午线仅 0.34–0.38°） |
| 库里 `lat/lon` 是 `NULL` | ★ **不得读成 `(0, 0)`** —— 那样 3 个断面会一起堆在几内亚湾外而**不报任何错** |

★ 最后一条的失败形态值得单独记：`NULL → 0` 是**静默的**，
它不报错、不抛异常，只是把数据搬到地球另一边。所以它必须有一条**显式**检查。

---

## 6. 本 feature 不碰的数据

| 表 | 为什么 |
|---|---|
| `disease_record`（0 行） | 没有数据。显式排除（见 spec「不做什么」） |
| `media_file`（0 行） | 同上 |
| `cross_section_ground_point` | 密度不足以建三维（003 已排除） |
| 任何迁移 / DDL | 本 feature 不改表结构 |

★ **`geometry_point` 的定位要说清楚**：它是**派生缓存**，
由 `geometry_solver.py` 在导入时写出。本 feature **不写它、不依赖它做数据源**，
只在测试里把它当**另一个代码路径的产物**来对照 —— 而且只在**非格点**上对照，
理由见 `research.md` 的 R5。
