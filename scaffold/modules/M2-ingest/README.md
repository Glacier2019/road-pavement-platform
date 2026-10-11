# M2 接入服务

G228 路面性能数智化平台的**唯一写入入口**。MQTT 报文、设计文件导入、
质量日志，全部经此处落库；其余模块只有读权（见 `tests/contract/test_write_guard.py`）。

```
python3 -m uvicorn app:app --host 0.0.0.0 --port 8010      # 本地
docker compose --profile default up -d --build ingest       # 容器（容器内端口 8000，映射 8010）
```

## 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/healthz` | 健康检查 ＋ 依赖自检 ＋ 计数器快照 |
| GET | `/stats` | 计数器快照（不含依赖自检） |
| GET | `/metrics` | Prometheus 文本 |
| GET | `/v1/design/capabilities` | 设计导入支持的文件类型 |
| POST | `/v1/design/import` | 设计文件导入 |

`/healthz` 的 `status` 取 `ok` / `degraded`，判据是 **PG ping 通 且 MQTT 已连**
（`healthy = pg_ok and STATE.mqtt_connected`）。`degraded` 时两个依赖各自报
`ok` 与 `error`，所以**不要只看 status** —— 它把两个独立故障压成了一个值。

## 报文链路

订阅 `g228/+/+/axle`（qos 1）。`on_message` 有六条出口，**每一条都记日志**：

| 出口 | 级别 | 计数器 |
|---|---|---|
| JSON 解析失败 | `warning` | `rejected` |
| 契约违约（含精确规则码） | `warning` | `rejected` |
| 重复报文（`device_code`+`seq`） | `info` | `duplicated` |
| 设备未注册 | `error` | `rejected` |
| 入库失败 | `error` | `db_errors` |
| **入库成功** | `info` | `inserted` ＋ `pending_valid` |

`insert_event()` 把主记录与轴组明细放在**同一个 `write_txn`** 里。这不是洁癖：
拆成两次 `dao.insert` 会各取一条池连接，进程在中间挂掉就留下一条
**没有轴组明细的过车记录** —— 而下游看它完全合法，不会报错。

---

## 坑

### 1. 成功路径不出声，一条通了的链路读起来像没通

**2026-10-10 实测踩到，代价是一次说错的结论。**

原先 `on_message` 的**五条失败分支全都记日志**，唯独成功那条只有两行计数器自增：

```python
    with STATE.lock:
        STATE.inserted += 1
        STATE.pending_valid += 1
```

于是 `docker logs rp-ingest` 里**只剩 `/healthz` 的 200 行**。据此曾断言
「M2 从没消费过一条报文、链路没通」—— 而真实的 `/healthz` 计数器是：

```json
{"received": 3, "inserted": 1, "rejected": 1, "duplicated": 1}
```

三个分支都被一次故意的否定测试走到了，链是通的。

> ★ **教训：从日志里得出「X 从未发生」之前，先确认这个系统在 X 成功时会记日志。**
> 一个只记失败的服务，健康时的样子和一个从没跑过的服务一模一样。
> 一个 handler 的每条失败分支都记日志、唯独成功分支静默 —— 这个**不对称**
> 一定会被读成故障。

现在成功路径记：

```
入库 device=WIM01 seq=42 → wim_axle_record#34 plate=闽A·12345 总重=18000kg
```

**为什么带 `record_id`**：这条日志要能被追到具体的行。只说「又入了一条」，
复核者还得再去查库；带上主键，一行日志就能和 `wim_axle_record` 对上。
为此 `insert_event()` 的返回类型从 `None` 改成了 `int`。

### 2. 去重是**内存**的，重启即失忆

`mark_seen((device_code, seq))` 用 `deque(maxlen=50_000)` ＋ 一个 `set`。
容器一重启，窗口清空 —— 同一 `seq` 的报文会被**再入一次**。
骨架期如此，P1 要换 Redis 或表。

判断一条历史记录是否重复时，不能依赖「进程当时认为它是重复的」。

### 3. `pending_raw` / `pending_valid` 只在批表里，不在 `/stats` 里

内存计数每 10 秒（`batch_flusher`）刷进 `data_import_batch`，避免每条报文都写批表。
代价是：`/healthz` 与 `/stats` 里的 `received` / `inserted` 是**实时值**，
而批表里的是**最多滞后 10 秒的值**。两个数对不上是正常的。

`batch_flusher` 也只在失败时记日志（`批次刷新失败：%s`）——
和坑 1 同一类问题，只是影响面小：批表滞后不会让链路看起来是死的。

### 4. 别用 `grep` 读 JSON

FastAPI 输出的是紧凑 JSON，Grafana 的 `/api/health` 输出是美化过的。
同一个 `grep` 在一处能命中、在另一处命不中。**一律 `json.loads`。**

### 5. 「设备未注册」有**两个**原因，而第二个不报错

`device_code` 就是 `sensor_install.serial_no`。但映射不是查一张表就够的：

```sql
SELECT si.serial_no, si.cross_section_id, sc.id, sc.quantity_code, sc.channel_no
FROM sensor_install si
JOIN sensor_channel sc ON sc.install_id = si.id      -- ★ JOIN，不是 LEFT JOIN
WHERE si.status = 'active'
```

★★ **只登记设备、不登记通道，这台设备根本进不了映射表** —— 而它看起来
登记得好好的。`sensor_channel` 那一行不是可选的附加信息，它是**让设备可见的那一行**。
排查「设备未注册」时先跑上面这条 SQL，而不是先怀疑报文格式。

好消息：`device_of()` 遇到未知设备会**立刻重载一次**映射表（新设备接入场景），
所以登记完**不必重启** ingest。实测日志里
「设备映射已加载：3 台」正是第一条新设备报文到达的那一刻打出来的。

★ 登记脚本见 `scaffold/sql/98_seed_sim_device.sql`。它**不挂**
`docker-entrypoint-initdb.d` —— 它要挂靠的路段是导入流程建的，
容器初始化时还不存在，挂上去只会静默插零条。

### 6. 记录落在**哪条路**上，由设备决定 —— 而记录自己不会反驳

`wim_axle_record` 的位置来自**设备映射**（`cross_section_id`），
**不是**来自报文：`wim_axle.v1` 里没有桩号字段。

后果是一条位置错得离谱的记录，和一条正确的记录**长得一模一样** ——
`data_source`、轴重、车速、`quality_code` 全都对，没有任何字段会与之矛盾。

★ 规则：把数据挂到共享表上之前，先问「**这条记录会声称自己在哪**」，
再核对那个位置是不是真的。位置由外键（设备 / 断面 / 标段）带进来时，
校验必须发生在**写入之前**。

★★ 报文契约的三层（信封 / payload / axles[]）都是 `additionalProperties: false`，
**没有**任何位置可以标注「这是仿真数据」。所以 `device_code` 是唯一的溯源通道 ——
给仿真数据一个自己的设备（`SIMWIM01`），不要占用真设备（`WIM01`）。
详见 `scaffold/simulator/crg/README.md` 坑二十四。

