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
# 改完必须重启 api 让它重读配置
docker compose -f docker-compose.skeleton.yml --env-file .env restart api
# …跑 2a，应当失败…
# ★ 验证完立刻改回 117，并再 restart 一次
```

★★ **这一步是本 feature 最要紧的验收动作。**
如果改错了带号它**照样通过**，那说明 SC-002 量不到它声称在量的东西 ——
而本 feature 的原始 SC-001 正是栽在这个坑里（见 `research.md` R5）。

---

## 3. 越界必须报错，不能"假装成功"（FR-004 / FR-010 / SC-006）

```bash
# 超范围桩号：必须报错，不得返回最近点、不得返回原点、不得返回空对象
curl -s -o /dev/null -w '%{http_code}\n' \
  'http://localhost:8001/v1/geometry/sections/6/geo?station_km=9.999'
# 期望 400

# 不存在的路段
curl -s -o /dev/null -w '%{http_code}\n' \
  'http://localhost:8001/v1/geometry/sections/999/geo?station_km=1.0'
# 期望 404

# 经纬度落在带外（把经度挪到 120°E，超出 3 度带半宽 1.5°）
curl -s -o /dev/null -w '%{http_code}\n' \
  'http://localhost:8001/v1/geometry/sections/6/geo/reverse?lat=25.18&lon=120.0'
# 期望 422
```

★ **最后一条特别要紧**：`monitor_cross_section.lat/lon` 全空（0/3 行有值）。
若实现把 `NULL` 读成 `0`，3 个断面会一起堆在几内亚湾外的 `(0°,0°)`
而**不报任何错** —— 它是**静默的**，所以必须有显式检查。

---

## 4. 曲线是弯的，不是折线（FR-006 / SC-005）

```bash
curl -s 'http://localhost:8001/v1/geometry/sections/6/geo/route' \
  | python3 -c "
import sys,json
d=json.load(sys.stdin)
for e in d['elements']:
    print(e['element_seq'], e['element_type'], len(e['latlon']))
"
```

**期望**：`element_type` 为 `circular` 与 `transition` 的段，`latlon` 点列**长度 > 2**
（即给了中间点），直线段可以为 2。

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
| 3 个断面 | 出现在图上，且**标注「位置由桩号换算」**（FR-009） |
| `SIMWIM01` | 与真实设备**视觉上可区分** |
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

**期望**：全部套件绿（当前基线 20 个套件）。
本 feature 新增的是 `test_region_frame.py`，并扩充 `test_console.py`。

★ 注意：`test_region_frame.py` 里**每条检查都要配一条元测试** ——
即「把被检查的东西改坏，这条检查必须红」。
只写检查不写元测试，等于又造了一批空转检查。

---

## 7. 两条"不许漂移"的守卫

| 守卫 | 怎么验 |
|---|---|
| 搬进 M6 的 `geom.py` / `errors.py` 与 M2 原件**逐字节相同** | 改原件一个字节，测试必须红 |
| `crs.yaml` 的 `assumed` / `epsg_after_confirm` 一致 | 既有契约测试已强制（两个方向） |

★ 第一条是本 feature 唯一的结构性风险：**同一份数学存在于两个位置**。
不靠"记得同步"，靠"忘了会红"。
