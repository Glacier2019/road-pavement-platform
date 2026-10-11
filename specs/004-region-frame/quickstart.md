# Quickstart：怎么验证区域帧真的做出来了

本文件是**验收步骤**，不是实现说明。
它回答一个问题：**拿到这份代码的人，怎么自己确认它是对的。**

★ 全部步骤**离线可跑**，不需要浏览器、不需要地图服务（SC-007）。

---

## 0. 前置条件

| 项 | 要求 |
|---|---|
| 栈 | `rp-pg`（含毕设路段数据）、`rp-api`（profile `m4`）已起 |
| 数据 | `alignment_element` 有 33 行、`section_id=6`（毕设 K0+000→K5+805.421） |
| 配置 | `modules/M9-console/config/crs.yaml` 中 `ready: true`、`assumed: false`、`epsg_after_confirm: 4548` |
| 依赖 | **无新增第三方依赖**（投影手写） |

起栈：

```bash
cd /data/cy/shujuku/scaffold
export DOCKER_CONFIG=/data/cy/shujuku/.dockerhome
docker compose -f docker-compose.skeleton.yml --env-file .env --profile m4 up -d --build api
docker inspect -f '{{.State.Health.Status}}' rp-api     # 期望 healthy
```

---

## 1. 换算能力能用（FR-001 / FR-002 / SC-001b）

拿一个已知桩号，算出经纬度，再算回来。

```bash
# 桩号 → 经纬度（毕设 K2+900）
curl -s 'http://localhost:8001/v1/geometry/sections/6/geo?station_km=2.9' | python3 -m json.tool

# 经纬度 → 桩号（把上一步的 lat/lon 填回去）
curl -s 'http://localhost:8001/v1/geometry/sections/6/geo/reverse?lat=25.18596&lon=117.35926' | python3 -m json.tool
```

**期望**

| 字段 | 期望值 |
|---|---|
| `epsg` | **`4548`** —— ★ 必须原样出现，**只写"39 带"不算通过**（FR-003） |
| `zone_number` | `39` |
| `central_meridian_deg` | `117` |
| `assumed` | `false`（从 `crs.yaml` **读**出来的，不是写死的） |
| `lat` / `lon` | 落在 **25.16–25.21°N / 117.34–117.38°E** |
| 往返回算的 `station_km` | 与 `2.9` 相差 **≤ 1 m** |

★ **往返闭合只是必要条件**（SC-001b）。它闭合了**不代表换算是对的** ——
中央子午线取错一整带，它照样完美闭合。所以还要看第 2 步。

---

## 2. 换算真的对（SC-001 / SC-002）

### 2a. 包围盒（SC-002）

```bash
curl -s 'http://localhost:8001/v1/geometry/sections/6/geo/route' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['bbox'])"
```

**期望**：`lat` 在 25.16–25.21、`lon` 在 117.34–117.38。
长宽比应为约 **4.55 : 3.11**（南北 : 东西）。

### 2b. 与独立估计一致（SC-001）

`geometry_point` 表里每 20 m 一个格点。**在非格点桩号**上，
`point_at`（精确积分）与「相邻两格点线性插值」应相差 **≤ 1 m**。

★ **为什么不直接拿 `geometry_point` 比：** 那张表**就是 `point_at` 自己写进去的**
（`geometry_solver.py` 委托 `adapters.geom`）。在格点上比 = 拿函数跟自己对，
**又是一条空转检查**。只有在非格点上，两种估计才是独立的。

★ 这个差有量级可算：R=255 m、20 m 弦的矢高 `255 − √(255²−10²)` ≈ **0.196 m**。
所以检查会通过 —— 但它**不是空转**：投影参数错、桩号取错、插值端点取错，它都会红。

### 2c. ★ 对照：故意把中央子午线取错一整带（SC-008）

把 `crs.yaml` 的 `central_meridian_deg` 临时改成 **114**（38 带），重跑 2a。

**期望**：**包围盒检查必须失败。**

```bash
cd scaffold
CRS=modules/M9-console/config/crs.yaml
cp "$CRS" /tmp/crs.bak                       # 先备份，后面要用
sed -i 's/central_meridian_deg: 117/central_meridian_deg: 114/' "$CRS"

# ★★★ 必须 **rebuild**，不是 restart
docker compose -f docker-compose.skeleton.yml --env-file .env \
  --profile m6 up -d --build api
sleep 8
curl -s 'http://localhost:8001/v1/geometry/sections/6/geo/route' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['bbox'])"   # 必须失败

# ★ 验证完立刻改回并**再 rebuild 一次**
cp /tmp/crs.bak "$CRS"
docker compose -f docker-compose.skeleton.yml --env-file .env \
  --profile m6 up -d --build api
```

> #### ⚠️ 本条原写作 `docker compose restart api` —— **那是错的，会静默空转**
>
> `crs.yaml` 是**构建时** `COPY` 进镜像的（`modules/M6-api/Dockerfile` 第 46 行），
> `restart` 只是把同一个容器重开，**不会重新 COPY**。
>
> 2026-10-11 实测（三条都记下来，因为差别的形状本身是证据）：
>
> | 做法 | 容器里 `central_meridian_deg` | 包围盒经度 | SC-002 |
> |---|---|---|---|
> | 基线 | 117 | 117.34292..117.37376 | 通过 |
> | 改宿主 → **restart** | **117（没变）** | **逐位相同** | **通过** ← 什么都没做 |
> | 改宿主 → **rebuild** | **114** | 114.34292..114.37376 | **失败** ✓ |
>
> ★★ 危险在于它**静默**：2c 期望"检查失败"，
> 而空转的结果是"检查通过"—— 操作者会据此认为
> **SC-002 量不到错带号**，从而误判整个 feature 坏了。
> **一个不改变参数就报结论的对照，比没有对照更容易骗人。**
>
> ★ 顺带一条正面证据：错一整带后经度**正好平移 3.000°**
> （114.34292 − 117.34292），即一个带宽；纬度 25.16789..25.20878 不变。
> 这正是 3 度带应有的行为。
>
> ★★ **这一步是本 feature 最要紧的验收动作。**
> 如果改错了带号它**照样通过**，那说明 SC-002 量不到它声称在量的东西 ——
> 而本 feature 的原始 SC-001 正是栽在这个坑里（见 `research.md` R5）。

---

## 3. 越界必须报错，不能"假装成功"（FR-004 / FR-010 / SC-006）

```bash
# 超范围桩号：必须报错，不得返回最近点、不得返回原点、不得返回空对象
curl -s -w '\n%{http_code}\n' \
  'http://localhost:8001/v1/geometry/sections/6/geo?station_m=9999'
# 期望 400

# 不存在的路段
curl -s -w '\n%{http_code}\n' \
  'http://localhost:8001/v1/geometry/sections/999/geo?station_m=1000'
# 期望 404
```

**2026-10-11 实测**：

```
station_m=9999  → 400  {"detail":"桩号 9999.000 m 落在线元链之外
                        （链覆盖 0.000 .. 5805.421 m）。不外推。"}
sections/999    → 404  {"detail":"路段 999 没有线形单元（alignment_element 无行）"}
```

> #### ⚠️ 参数名原本写作 `station_km` —— **那是错的**
>
> `/geo` 收的是 **`station_m`（米）**（`app.py:499`）。
> 写成 `station_km` 会得到 **422 `Field required: station_m`**。
>
> ★★ 这个错**特别容易蒙混过去**：422 也是 4xx，看上去"被拒了"。
> 但它拒的理由是**参数没给**，跟桩号越界毫无关系 ——
> **两条命令其实从来没测到它们要测的东西。**
>
> ★ 教训：一个期望 400 的检查，拿到 422 时**必须看响应体**，
> 不能只看"是不是 4xx"。**同一个状态码区间里的两个原因不能互相顶替。**

### 3b. ★★★ 带外经度：**已裁决（读法 B）—— 不拒绝，但如实分带**

> #### ⚠️ 本节原写作「spec 说报错，实现返回 200 —— 两边不一致，**尚未裁决**」
> **2026-10-11 平台方裁决为读法 (B)**：改 spec，保留 200，把阈值写出来。
> 下面的实测表是裁决的**依据**，原样保留。

spec 的 Edge Cases 与 `data-model.md` §5 原先都写着
「经纬度落在带外 → **报错，不硬算**」，而实现里**从来没有这道检查**：

| 传入经度 | 离中央子午线 | HTTP | `station_m` | `offset_m` |
|---|---|---|---|---|
| **117.36** | **0.36°** | 200 | 3671.3 | **371.7** |
| 118.5 | 1.5° | 200 | 5805.4（钳到终点） | 113 555.4 |
| 119.0 | 2.0° | 200 | 5805.4 | 163 975.6 |
| 120.0 | 3.0° | 200 | 5805.4 | 264 857.9 |
| 125.0 | 8.0° | 200 | 5805.4 | 770 844.5 |
| 130.0 | 13.0° | 200 | 5805.4 | 1 281 679.4 |

★★★ **第一行就是裁决的理由，也是为什么没选读法 (A)。**

`117.36°E` **完全在 39 带内**（离中央子午线只有 0.36°），
残差却是 **371.7 m** —— 这个点**根本不在路上**，
而**带号检查会放过它**。

⇒ 「带外」只是"点不在路上"的**一种成因**。
用带号当闸，管住的是一种成因，**放过的是同一类错误的其余全部**。
`offset_m` 量的是"离路线多远"，对成因不敏感 —— 它才是那道闸。

**两道阈值不是拍的，是数据自己给的**（2026-10-11 实测）：

| `offset_band` | 边界 | 出处（实测） |
|---|---|---|
| `on_roadbed` | ≤ **5.000 m** | `roadbed_design_point`：土路肩 0.750 + 硬路肩 0.750 + 分隔带 0.000 + 车道 3.500 = **5.000 m**（全路幅 10.000 m，二级公路 ✓） |
| `in_corridor` | ≤ **50.000 m** | `cross_section_ground_point` **2215 点**横向覆盖 0.019 .. **50.000 m** ⇒ 勘察走廊就是 ±50 m |
| `off_corridor` | > 50.000 m | 走廊之外，**残差就是全部答案** |

★ 宪法 Principle（`constitution.md:91`）要求「**未标定的阈值不得用于生产判定**」。
这两条阈值**不是标定的，是从实测数据推出来的** —— 因此它们满足那条要求，
而不是绕过它。

```bash
# 裁决依据：带内的远点 —— 带号检查会放过它，offset 不会
curl -s 'http://localhost:8001/v1/geometry/sections/6/geo/reverse?lat=25.18&lon=117.36'
#   offset_m = 371.7        offset_band = off_corridor
#   station_m = 3671.265    ← ★ 一个看着完全正常的桩号

# 金标准对照：路线自己的点
curl -s 'http://localhost:8001/v1/geometry/sections/6/geo?station_m=2000'
#   → 25.1930952 / 117.3510247，再把这个经纬度喂回去：
curl -s 'http://localhost:8001/v1/geometry/sections/6/geo/reverse?lat=25.193095215719268&lon=117.35102470621275'
#   station_m = 2000.0000    offset_m = 3.98e-05    offset_band = on_roadbed
```

★★ **`station_m` 是"最近点"的桩号，不是"这个点的桩号"。**
只有 `on_roadbed` 时两者才重合；`station_is_nearest` 显式说出这个区别 ——
否则上表第 2–6 行会各自拿到一个**看着很正常的桩号**，
而调用方无从知道那个桩号根本不是它的。

★ **最要紧的一条仍在**：`monitor_cross_section.lat/lon` 全空（0/3 行有值）。
若实现把 `NULL` 读成 `0`，3 个断面会一起堆在几内亚湾外的 `(0°,0°)`
而**不报任何错** —— 它是**静默的**，所以必须有显式检查。
★ 现状：已按「有类型的没有」修好，由第 5 步与 SC-009 / SC-010 守。

---

## 4. 曲线是弯的，不是折线（FR-006 / SC-005）

```bash
curl -s 'http://localhost:8001/v1/geometry/sections/6/geo/route' \
  | python3 -c "
import sys,json
d=json.load(sys.stdin)
for e in d['elements']:
    print(e['element_seq'], e['element_type'], len(e['points']))
"
```

**期望**：`element_type` 为 `circular` 与 `transition` 的段，`points` 点列**长度 > 2**
（即给了中间点），直线段可以为 2。

**2026-10-11 实测**（`step_m=20`，点总数 337）：

```
circular    中间点有 : 8 段     ★ 曲线段缺中间点的: 无 ✓
transition  中间点有 : 16 段
line        中间点有 : 9 段
```

★ 字段名原本写作 `latlon` —— 实际是 **`points`**（`app.py` 的 `/geo/route`）。
写错会直接 `KeyError` 崩掉，**不会静默**，所以是良性错误。

★ 理由：圆曲线的矢高最大 **12.588 m**、缓和曲线最大 **1.736 m**。
只连首末点会让曲线变成折线 —— 这正是 1 m 判据要挡的东西。

---

## 5. 页面能看（US2 / US3）

```bash
open http://localhost:8024/geo      # 或在浏览器里打开
```

**期望**

| 项 | 期望 |
|---|---|
| 路线走向 | 与第 2 步的包围盒一致，**南北东西不颠倒**（SC-004 的姊妹检查） |
| 圆曲线 | **是弯的** |
| 点任意一段 | 弹出桩号区间、长度、半径、方位角 |
| 3 个断面 | **逐个给出位置来源**：`K2+900` 算出坐标；路段 1 的两个显示"算不出来"并给出理由（FR-009） |
| 位置标注 | 界面写明「位置由桩号换算得到，库中经纬度列为空」 |
| `SIMWIM01` | 与真实设备**视觉上可区分**（依据 `install_mode == 仿真`） |
| `TEST-PRJ-*`（3 条 0 路段的 road_line） | **不出现**，或明确标为"无几何数据"（FR-008） |
| 底图 | **没有** —— 本 feature 不声称地理位置（用户 2026-10-11 裁决） |

★ 页面约束（既有测试强制，不是本 feature 新加）：
内联 JS/CSS、**无外部 `script`/`link`**、**不写死端口**、**不接触数据库**。

---

## 6. 一条命令跑完全部契约测试

```bash
cd /data/cy/shujuku/scaffold
export DOCKER_CONFIG=/data/cy/shujuku/.dockerhome
./run_contract_tests.sh
```

**期望**：全部套件绿（2026-10-11 实测 **通过 21 ｜ 失败 0 ｜ 跳过 0**）。
本 feature 新增的是 `test_region_frame.py`，并扩充 `test_console.py`。

★ 注意：`test_region_frame.py` 里**每条检查都要配一条元测试** ——
即「把被检查的东西改坏，这条检查必须红」。
只写检查不写元测试，等于又造了一批空转检查。

---

## 7. 三条"不许漂移"的守卫

| 守卫 | 怎么验 |
|---|---|
| 搬进 M6 的 `geom.py` / `errors.py` 与 M2 原件**逐字节相同** | 改原件一个字节，测试必须红 |
| `crs.yaml` 的 `assumed` / `epsg_after_confirm` 一致 | 既有契约测试已强制（两个方向） |
| **镜像不读那份被跟踪的副本**（`M6-api/m2geom/`） | `Dockerfile` 里不许出现 `COPY … M6-api/m2geom` |

★ 第一条是本 feature 唯一的结构性风险：**同一份数学存在于两个位置**。
不靠"记得同步"，靠"忘了会红"。

★★ 第三条容易被忽略，但它是第一条的前提：
`M6-api/m2geom/` 里那份副本**镜像是完全不用的**
（镜像由 `Dockerfile` 第 39–41 行从 M2 现场生成），
它只服务于**宿主机上跑的契约测试**。

⇒ 所以：**改那份副本不会改变容器里跑的任何东西。**
而且**不能在那两个 `.py` 里写注释** ——
加一行注释就破坏了正在被比对的逐字节相等。
说明只能放在旁边的 `m2geom/README.md`（测试也盯着它）。
