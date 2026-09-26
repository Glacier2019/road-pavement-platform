# Quickstart: 空表缺口盘点与接入优先级

本文是**可复跑的验收脚本**。每条命令都能直接粘贴执行，输出即为判据。

## 前置

```bash
cd /data/cy/shujuku/scaffold
docker compose -f docker-compose.skeleton.yml --env-file .env up -d
docker ps --format "{{.Names}}\t{{.Status}}" | grep rp-
```

应为 7 个服务全部 healthy。

---

## 验收 1：段能力接口真的报出了「源已具备」（SC-002 前半）

```bash
curl -s http://localhost:8010/v1/design/capabilities | python3 -m json.tool | head -60
```

**期望**：earthwork_transfer / borrow_pit / spoil_pit / earthwork_haul_stat /
earthwork_fill_stat 五个段均为 implemented:true 且 source_present:true。

**若为 false**：先查导入目录有没有 .tsftxt —— 而不是先怀疑代码。

---

## 验收 2：★ SC-002 硬断言（本特性最重要的一条）

```bash
curl -s http://localhost:8024/gw/v1/catalog/gaps -o /tmp/gaps.json
python3 -c "
import json
g = json.load(open('/tmp/gaps.json'))
FIVE = ['earthwork_transfer', 'borrow_pit', 'spoil_pit',
        'earthwork_haul_stat', 'earthwork_fill_stat']
idx = {i['table']: i for i in g['items']}
bad = {t: idx[t]['empty_reason'] for t in FIVE
       if t in idx and idx[t]['empty_reason'] != 'source_ready_not_imported'}
print('判错为「适配器未实现」的表:', bad if bad else '无 OK')
assert not bad, f'SC-002 失败：{bad}'
"
```

**为什么这条最重要**：这五个段的解析器**早已实现**、源文件**早已在磁盘上**，
只是导入没跑过。若判成「适配器未实现」，接手的人会去**重写已经写好的解析器** ——
白干几周，而且不会报任何错。

---

## 验收 3：盘点总数自洽（INV-7）

```bash
python3 -c "
import json
g = json.load(open('/tmp/gaps.json'))
rc = g['reason_counts']
assert sum(rc.values()) == g['logical_table_count'], '分类计数之和 != 逻辑表数'
assert len(g['items']) == g['logical_table_count'], '条目数 != 逻辑表数'
assert g['with_data_count'] + g['empty_count'] == g['logical_table_count'], '有数据+空 != 总数'
print('逻辑表', g['logical_table_count'], '= 有数据', g['with_data_count'], '+ 空', g['empty_count'], 'OK')
print('空因分布:', {k: v for k, v in rc.items() if k != 'has_data'})
print('unknown（必须为 0）:', g['unknown_count'])
"
```

unknown_count **必须为 0**（SC-001）。若不为 0，**不许放过** ——
逐条查清是什么原因判不出来（宪法原则 V：空转不是通过）。

---

## 验收 4：与实库对账（宪法原则 III）

```bash
docker exec rp-pg psql -U rp -d road_pavement -At -c \
  "select count(*) from earthwork_transfer; select count(*) from borrow_pit;"
```

**期望**：两个都是 0 —— 与接口报的 row_count:0 一致。

把整张表逐张对一遍：

```bash
python3 -c "
import json, subprocess
g = json.load(open('/tmp/gaps.json'))
empty = [i['table'] for i in g['items'] if not i['has_data']]
sql = ' union all '.join("select '%s' t, count(*) n from %s" % (t, t) for t in empty)
out = subprocess.run(['docker','exec','rp-pg','psql','-U','rp','-d','road_pavement',
                      '-At','-F','|','-c',sql], capture_output=True, text=True).stdout
bad = [(l.split('|')[0], int(l.split('|')[1])) for l in out.strip().splitlines()
       if int(l.split('|')[1]) != 0]
print('接口说空、实库却有行的表:', bad if bad else '无 OK（逐张一致）')
"
```

---

## 验收 5：硬线未被破坏（宪法原则 II）

```bash
# 1 M9 仍然不许直连库
grep -c "psycopg\|ConnectionPool" modules/M9-console/app.py   # 期望 0
# 2 /gw 仍然只许 GET
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8024/gw/v1/catalog/gaps  # 期望 405
# 3 M9 容器仍然没有 DSN
docker exec rp-console env | grep -c PG_DSN   # 期望 0
# 4 新端点必须已登记进契约
grep -c "/v1/catalog/gaps" contracts/openapi/m6-gateway.v0.3.yaml   # 期望 >= 1
```

---

## 验收 6：SC-004 —— 接完一条链**无需改代码**

这是「不落库」这个设计的直接验证：

```bash
# 1 记下当前状态
curl -s http://localhost:8024/gw/v1/catalog/gaps | \
  python3 -c "import sys,json;d=json.load(sys.stdin);print(d['empty_count'])"
# 2 跑一次设计导入（把 .tsftxt 那五个段导进去）
curl -s -X POST http://localhost:8010/v1/design/import -F "files=@<工程目录>"
# 3 重新盘点 —— 空表数应当下降，且**没改过任何代码**
curl -s http://localhost:8024/gw/v1/catalog/gaps | \
  python3 -c "import sys,json;d=json.load(sys.stdin);print(d['empty_count'])"
```

**判据**：第二次的 empty_count 比第一次**小**，且期间 git status 无代码改动。

---

## 验收 7：契约测试全绿

```bash
cd scaffold && ./run_contract_tests.sh
```

**期望**：`通过 13 ｜ 失败 0 ｜ 跳过 0`（原 12 套 ＋ 本特性新增「缺口归因」1 套）。

> ⚠ 注意过滤器的坑：脚本的过滤器匹配**套件名**（--list 里的那种），
> **不是**测试文件名。写错一个字会零匹配 —— 而脚本已加了守卫会报错退出，
> 不会像以前那样打印「全部通过」。

---

## 实测留痕（2026-09-26，实库 + 真实容器）

七组验收**全部实跑过**，不是照着文档默读。原始数字如下，供日后对照。

### 归因结果（`GET /gw/v1/catalog/gaps?empty_only=true`）

```
逻辑表 62 ｜ 有数据 36 ｜ 空 26 ｜ unknown 0
空因分布：
  has_data                   36
  source_needs_conversion     5   ← 五个土方段
  source_ready_not_imported   2   ← design_control_text / extra_fill
  upstream_pending           18   ← 下游产出表，等各模块
  module_not_built            1   ← geometry_point（.3DR 无解析器）
```

`unknown = 0` 是**正确**的，不是没测到：实库 62 张表**每张都有归属登记**，
所以不存在"连归谁都不知道"的表。`unknown` 这一支存在，是为将来新加的表兜底 ——
它的正确性由 `test_gap_attribution.py` 第 10 组用构造数据钉住（T037）。

### 前 7 条（页面次序，即「现在就能动手的」）

| 表 | 空因 | 归属 | 建议动作 |
|---|---|---|---|
| `borrow_pit` | 源已收到·待转换 | M2 | 先跑 `tools/tsf2txt.py` 转换，再导入 |
| `design_control_text` | 源已收到·未导入 | M2 | 跑一次设计导入 |
| `earthwork_fill_stat` | 源已收到·待转换 | M2 | 先跑 `tools/tsf2txt.py` 转换，再导入 |
| `earthwork_haul_stat` | 源已收到·待转换 | M2 | 先跑 `tools/tsf2txt.py` 转换，再导入 |
| `earthwork_transfer` | 源已收到·待转换 | M2 | 先跑 `tools/tsf2txt.py` 转换，再导入 |
| `extra_fill` | 源已收到·未导入 | M2 | 跑一次设计导入 |
| `spoil_pit` | 源已收到·待转换 | M2 | 先跑 `tools/tsf2txt.py` 转换，再导入 |

### SC-006 响应时间（预算 < 3s）

```
/gw/v1/catalog/gaps?empty_only=true   中位  26.9ms   最差  35.2ms   PASS
/gw/v1/catalog/gaps                   中位  30.3ms   最差  36.9ms   PASS
/gw/v1/catalog/tables                 中位   4.0ms   最差   4.9ms   PASS
```

### 与实库对账（宪法原则 III：实库 > DDL > 字典 > 图件）

三处独立测量一致：`EXPECTED_PHYSICAL_TABLES = 62`、目录表数 62、
实库 `pg_class`（relkind ∈ {r,p}、非分区、排除 `spatial_ref_sys`）**62**。
其中 **36 有数据、26 为空**。

> 之所以要三处独立测：**同一份数字抄三遍不叫三处印证**。
> 这里一处来自代码常量、一处来自 `catalog.py` 的登记、一处来自现查 `pg_class`。
