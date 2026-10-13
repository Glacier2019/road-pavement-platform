# Quickstart: 坐标基准跟着项目走

**Date**: 2026-10-11 | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

★ 本文件是**验收剧本**，不是实现说明。每一步都写清「跑什么」「该看到什么」「看不到就是没做对」。

★★ 每一步的期望值都是**数出来的**，不是「应该没问题」。004 的教训：quickstart 第 3 步的三个期望状态码全写错了，而**错误是从契约里抄来的**——契约把参数名写成 `station_km`（实现收 `station_m`）。**所以本文件的每个字段名都来自活体实测。**

---

## 前置

```bash
cd /data/cy/shujuku/scaffold
export DOCKER_CONFIG=/data/cy/shujuku/.dockerhome
set -a; . ./.env; set +a
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'rp-(pg|api|console)'
# 期望：rp-pg / rp-api / rp-console 三者都 healthy
```

★ **端口**：api 宿主 **8001**，console 宿主 **8024**，pg 宿主 **55432**。

---

## 第 1 步：列已经存在，而且可空

```bash
docker exec -i -e PGPASSWORD="$PG_PASSWORD" rp-pg psql -U "$PG_USER" -d "$PG_DB" -tAc "
SELECT table_name || '.' || column_name || ' ' || data_type
FROM information_schema.columns
WHERE column_name LIKE 'crs\_%'
ORDER BY table_name, column_name"
```

**期望**：`design_project` 与 `road_section` **各六行**，共 **12 行**（`crs_assumed` / `crs_central_meridian_deg` / `crs_epsg` / `crs_evidence` / `crs_zone_number` / `crs_zone_width_deg`）。

**看不到就是没做对**：少一行说明列没加全；一行都没有说明迁移没跑。

★ 这一步**验不了权威性** —— 列存在与否，跟「谁是权威」是两件事。
权威性由 T011 的不变量校验和 T043 的「算不了要报错」来验。

★ **可空是必须的** —— 现有两行路段、一行项目都不带基准，加列后**它们必须依然合法**。

★★ **这一版的权威字段是 `crs_epsg`**（2026-10-12 按宪法原则 VI 定权）。

另外三个（`crs_zone_number` / `crs_central_meridian_deg` / `crs_zone_width_deg`）是**可空注释**。

**为什么**：UTM 与 Lambert **没有「带号」这个概念**。把带号做成必填字段，
等于把数据模型钉死在中国高斯-克吕格上 —— 而这是要卖到国外时第一个要拆掉的东西。

---

## 第 2 步：两级一致的不变量真的会拦

这是 Q1b = B 的核心。**要在库里直接试，而不是相信它会拦。**

★ **前提**：守卫本身要先装上 —— `scaffold/sql/25_crs_two_level_guard.sql`。
全新装由 initdb.d 自动跑；已装的库手动跑一次（幂等，可重复执行）。
★ **迁移从不自动执行**，所以这个守卫**刻意不放在迁移里** —— 放迁移里，
全新装的库会一点保护都没有，而文件在、代码也「做了」，谁都发现不出来。

★★ **路段级的真实样本**：路段 6（`section_id=6`，`design_project_id=2`）已经填了
**与项目相同**的基准（39 / 117 / 4548），这是 T022 用
`scaffold/sql/99_seed_crs_39band.sql` §② 造出来的「两级都填且一致」的活样本
（data-model E2 第 1 态）。它同时是判据 2b 那个「文件与库一致」的比对对象。
⇒ **所以 2a 不要整行覆写**，那会把 8 条 `crs_evidence` 冲成 1 条，
连带把判据 2b 搞红。下面改成只补缺的字段。

```bash
# 2a. 确认「毕设」项目的基准在（若已由 99 号种子填好，这一步是空操作）
docker exec -i -e PGPASSWORD="$PG_PASSWORD" rp-pg psql -U "$PG_USER" -d "$PG_DB" -c "
UPDATE design_project SET crs_zone_number=39, crs_central_meridian_deg=117,
       crs_epsg=4548, crs_zone_width_deg=3.00, crs_assumed=false
 WHERE id=2
   AND (crs_epsg IS DISTINCT FROM 4548 OR crs_evidence IS NULL)"

# 2b. ★ 给它的路段配一个**不同**的基准 —— 这条必须失败
#     ★ 各起一个事务：触发器报错会中止整个事务块，几条挤在一起，
#       第二条起只会报「current transaction is aborted」，
#       那样测的就不是守卫，是事务语义。
docker exec -i -e PGPASSWORD="$PG_PASSWORD" rp-pg psql -U "$PG_USER" -d "$PG_DB" <<'SQL'
BEGIN;
UPDATE road_section SET crs_zone_number=38, crs_central_meridian_deg=114,
       crs_epsg=4547, crs_zone_width_deg=3.00, crs_assumed=false,
       crs_evidence='故意写错，测一致性'
 WHERE id=6;
ROLLBACK;
SQL
```

**期望**：
- 2a **成功**（已配好的话则是 `UPDATE 0`）
- 2b ★ **失败**，报错信息里**同时出现 4548 和 4547**，**并且两级的主体名都在**
  —— 形如：
  ```
  坐标基准两级不一致：
      项目「毕设」（id=2）是 EPSG:4548
      路段「毕设」（id=6）是 EPSG:4547
    两级都有值时必须相等。改法：把两级改成同一个，或者清掉其中一级。
  ```

**看不到就是没做对**：
- 2b 成功了 ⇒ **不变量不存在**，Q1b 没落地
- 2b 失败了但只报「基准冲突」⇒ ★ **也算没做对**（SC-009 要求说得出两级的取值各是什么）

★★ **还要试反向**：只会红不会绿的守卫，等于让平台不能用了。
一样的写法，把 2b 的 `4547` 换成 `4548`（与项目相同）→ 必须 `UPDATE 1`。
把整段 `SET crs_epsg=NULL` 只清掉路段级 → 也必须成功（E2 态③④ 是合法状态，
守卫**只拦第 ② 态**，不做「必须两级都填」）。

★ **这一步必须在写入侧拦**（DB 触发器 / M2 写路径），**不能在读路径拦** ——
读路径拦会让一条路段的坏数据把**整个列表接口**搞挂。
★★ 而 `psql`、`COPY`、后续任何一个模块、任何运维脚本都能写这两张表；
只在 M2 拦等于只拦住了**门**、没拦住**墙**。所以守卫落在库里，**两张表都挂**：
只挂 `road_section` 拦不住 `UPDATE design_project` —— 而它改的是全项目所有路段的依据。

★★★ **本条被测到过**：005 实施期间实测 `UPDATE 1`（写入成功了）——
触发器、check 约束、M2 写路径三处**都没有**这个不变量，
只有读取侧的 503 在兜底。⇒ 补上了
`scaffold/sql/25_crs_two_level_guard.sql`。
行为判据落在 `tests/e2e/quickstart_probe.py` **判据 2c**（含「摘掉触发器后同一句就不再被拒」的元测试），
静态判据落在 `tests/contract/test_region_frame.py` **§18g**（守「文件在」**且**「有人会跑它」）。

---

## 第 3 步：两级都没配 = 有类型的「没有」，不是 39 带

★ **用真实数据**：路段 1「滨海大通道试验段」没有所属项目（`design_project_id` 为 NULL），也从未配过基准。

```bash
curl -s -w '\n%{http_code}\n' 'http://127.0.0.1:8001/v1/geometry/sections/1/geo?station_m=1000'
```

**期望**：**HTTP 503**（或等价的、说得出原因的拒绝），响应里**没有 `lat_deg` / `lon_deg`**，并且**有一句话说清为什么没有**。

**看不到就是没做对** —— 以下每一条都是错的：
- 返回了经纬度 ⇒ ★★ **回落到默认带号了**，这是最危险的一种
- 返回了 `lat_deg: null` ⇒ 那不是「有类型的没有」，是「没去看」
- 报「内部错误」⇒ 类型丢了

★★ **理由**：一个没有基准的路段，如果悄悄用 39 带算出一个经纬度，那个坐标**看起来完全正常**，而它可能偏几百公里。**看着正常的错值比缺失更危险。**

---

## 第 4 步：路段级基准在没有项目时**单独生效**

★★ **这是 FR-010 的判据，而且它也能用真实数据跑。**

```bash
# 4a. 给路段 1 单独配基准（它没有项目，所以这是它唯一的基准来源）
docker exec -i -e PGPASSWORD="$PG_PASSWORD" rp-pg psql -U "$PG_USER" -d "$PG_DB" -c "
UPDATE road_section SET crs_zone_number=39, crs_central_meridian_deg=117,
       crs_epsg=4548, crs_zone_width_deg=3.00, crs_assumed=true,
       crs_evidence='滨海段实测反推，来源级别=路段'
WHERE id=1"

# 4b. 再问一次 —— 这次应该能算
curl -s 'http://127.0.0.1:8001/v1/geometry/sections/1/geo?station_m=1000'
```

**期望**：
- 4a **成功**（★ **不能报错** —— 路段没有项目不是错误，是它的常态）
- 4b 返回 `crs_source` 等于 **`"section"`**，且 `crs_assumed` 等于 **`true`**

**看不到就是没做对**：
- 4a 报错 ⇒ ★★ **那个路段永远无法定位** —— 把一条能用的数据锁死了
- 4b 的 `crs_source` 是 `"project"` ⇒ 来源级别报错了
- 4b 的 `crs_assumed` 是 `false` ⇒ ★ 「推断的」被当成了「确认的」，**而它看起来是确认过的**

★ 注意 4b 里路段 1 没有线元（`alignment_element` 为 0 行），所以它可能仍然报 404「没有线形单元」。**那是另一回事，不是本 feature 的失败** —— 判据看的是**基准这一层**有没有给出正确的来源级别。

---

## 第 5 步：溯源字段在**全部**相关路径上都出现

```bash
python3 - <<'PY'
import json, urllib.request
B = "http://127.0.0.1:8001"
paths = [
    "/v1/geometry/sections",
    "/v1/geometry/sections/6/geo?station_m=2900",
    "/v1/geometry/sections/6/geo/reverse?lat=25.19&lon=117.36",
    "/v1/geometry/sections/6/geo/route",
    "/v1/geometry/sections/6/monitor-sections",
]
for p in paths:
    d = json.load(urllib.request.urlopen(B+p))
    row = d["items"][0] if "items" in d else d
    has = [k for k in ("crs_source", "crs_assumed") if k in row]
    ok = "✅" if len(has) == 2 else "✗"
    print(f"{ok} {p:58s} {has}")
PY
```

**期望**：**五行全是 ✅**。

**看不到就是没做对**：少一行 ⇒ ★ **有的路径知道基准从哪来、有的不知道** —— 而调用方无法预知哪一种，这正是 004 那条「字段名写错一次、下游抄错两次」的同型缺陷。

---

## 第 6 步：两个不同基准的项目能共存（★ 必须在造出来的第二个项目上证明）

★★ **实测：平台今天只有一个项目**（`design_project` 1 行），而且**只有一条路段有坐标**，它属于那个项目。**多项目这件事没有任何真实数据在跑** —— 所以这一步只能造。

```bash
# 6a. 造第二个项目（36 带 / CM 108）和它的一个路段
docker exec -i -e PGPASSWORD="$PG_PASSWORD" rp-pg psql -U "$PG_USER" -d "$PG_DB" -c "
INSERT INTO design_project (project_uid, project_name, project_type, station_interval_m,
                            crs_zone_number, crs_central_meridian_deg, crs_epsg,
                            crs_zone_width_deg, crs_assumed, crs_evidence)
VALUES (gen_random_uuid(), '多基准验证项目', '公路主线', 20,
        36, 108, 4545, 3.00, true, '为了验证多基准共存而造的数据')
RETURNING id"
```

**期望**：
- **两条 UPDATE 都成功**，谁都不影响谁
- 6b 的两行：同一个桩号，**`zone_number` 一个 39 一个 36**，`lon_deg` **相差约 9°**（3 个带 × 3°）
- 6c：两个项目的 `crs_source` 都等于 `"project"`

**看不到就是没做对**：如果两个项目的 `lon_deg` 相同 ⇒ ★★ **基准根本没有跟着项目走**，005 的核心判据失败。

★★ **这一步是把「通用性」变成可观测的东西**：同一个桩号，在两个项目里**本来就该是两个不同的经纬度**。在 004 的形态下这是**做不到**的，而它**不会报任何错**。

★ **`4545` 这个值不是随手写的**（我原本是凭记忆填的，后来去验了）：实测 `spatial_ref_sys` 里 25–45 带全部满足 `SRID = 带号 + 4509`、`lon_0 = 带号 × 3`、`x_0 = 500000`，**21/21 无反例**。

★★ **但这是「观测」，不是可以写进代码的「规律」**（2026-10-12 按宪法原则 VI 收紧）：那个 `3` 是**硬编码常量**，写进代码会违反 `test_sim_track.py` 的既有守卫，而且**只对 3 度带成立**。详见 research.md R11 补记。36 带 ⇒ `4545` / `lon_0=108` ✓。详见 [research.md](./research.md) R10。

---

## 第 7 步：004 的精度一位不变

```bash
cd /data/cy/shujuku/scaffold
export UV_CACHE_DIR="$PWD/../.uvcache" UV_PYTHON_INSTALL_DIR="$PWD/../.uvpython"
uv run --quiet --with pyyaml==6.0.2 python tests/contract/test_region_frame.py
```

**期望**：全部通过，且**精度断言一个都没松**（投影往返仍是 **0.050835 mm / 最大 0.146233 mm**）。

★ **判据是「浮点逐位相同」**，不是「误差在容差内」—— FR-007 就是这么写的。

**看不到就是没做对**：任何精度数变了 ⇒ 通用化过程中碰到了不该碰的东西。

---

## 第 8 步：★ 文件与库的一致性命中检查**真的会叫**（SC-012）

★★ **这一步测的不是「文件生成了」，是「那道检查真的会红」。** 没有它，生成步骤本身会给人安全感的假象 —— 004 已经踩过它的镜像版（`restart` 之后容器里还是旧配置，而验收**通过**了）。

```bash
# 8a. 改库里的基准，故意不动文件
docker exec -i -e PGPASSWORD="$PG_PASSWORD" rp-pg psql -U "$PG_USER" -d "$PG_DB" -c "
UPDATE design_project SET crs_central_meridian_deg=114, crs_zone_number=38, crs_epsg=4547 WHERE id=2"

# 8b. 跑那道一致性检查
#     期望：★ 必须红
```

**期望**：★ **变红**，并且说得出**是哪一处不一致**（哪个字段、库里是什么、文件里是什么）。

**看不到就是没做对**：
- 8b 通过 ⇒ ★★ **那道检查是摆设**，文件会悄悄过期
- 8b 红了但没说清哪里不一致 ⇒ 也偏低（宪法 V：报告必须区分「没去看」与「看了没有」）

★ **别忘了把 8a 改回去**，并在改回去之后确认检查重新变绿 —— **只会红不会绿的检查，同样验不出东西。**

---

## 第 9 步：五个消费者一个都没坏

```bash
cd /data/cy/shujuku/scaffold
./run_contract_tests.sh 2>&1 | tail -20
```

**期望**：★ **通过 21 ｜ 失败 0 ｜ 跳过 0**（新增的套件会让总数上升，但**不许有失败、不许有跳过**）。

★★ 特别要看这三条守卫还在：
- `test_sim_track.py`：「crs.yaml 存在（带号是配置项，不是代码里的常量）」
- `test_region_frame.py` §8：「`assumed` ↔ `evidence` 的不变量」
- `quickstart_probe.py`：「清单里的 frame 标记 == crs.yaml 里的标记」

★ 再加 **两条新的**：
- `geo.py` 里不得出现 psycopg（★ 现有断言**只扫 `app.py`**，而 005 最容易在 `geo.py` 里破线）
- 文件与库一致性检查的**元测试**：故意造不一致，断言必须变红

**看不到就是没做对**：任何一条被删掉或被放宽 ⇒ 通用化不是免费的，代价就是这些守卫。

---

## ★ 一句话验收

**造出两个中央子午线不同的项目，同一个桩号算出两个不同且都正确的经纬度；而原来那条路，一个字节都没变。**
