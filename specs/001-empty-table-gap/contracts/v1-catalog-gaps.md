# 契约：`GET /v1/catalog/gaps`（M6 出口）

**所属契约**：④服务接口　**真源**：服务运行后的 `/openapi.json`；本文是人工摘要
**登记要求**：必须同时登记进 `scaffold/contracts/openapi/m6-gateway.v0.3.yaml`，
否则 `m6-路由` 契约测试报「实现缺契约」。

## 语义

逐张**逻辑表**给出缺口归因。回答三个问题：这张表为什么空、归谁、先动哪条。

## 请求

```
GET /v1/catalog/gaps
GET /v1/catalog/gaps?empty_only=true    # 只返回空表
```

## 响应 200

```json
{
  "logical_table_count": 62,
  "with_data_count": 36,
  "empty_count": 26,
  "unknown_count": 0,
  "reason_counts": {
    "has_data": 36,
    "source_ready_not_imported": 5,
    "source_absent": 10,
    "upstream_pending": 8,
    "module_not_built": 3,
    "unknown": 0
  },
  "items": [
    {
      "table": "earthwork_transfer",
      "domain": "GE",
      "row_count": 0,
      "has_data": false,
      "owner": "M2",
      "phase": "P1",
      "empty_reason": "source_ready_not_imported",
      "segments": ["earthwork_transfer"],
      "suffixes": [".tsftxt"],
      "source_present": true,
      "action": "跑一次设计导入（.tsftxt 已在导入目录中）",
      "priority_keys": [1, true, 0]
    }
  ]
}
```

### 字段约束（对应 `data-model.md`）

- `reason_counts` 之和 **必须** == `logical_table_count`（INV-7）。
- `unknown_count` **必须**单独报出，不得并入任何其它计数（INV-4）。
- `source_present` 是**三态**：`true` / `false` / `null`（INV-5，`null` = 无对应段）。
- `priority_keys` **必须**暴露，以便复核者独立复算排序（SC-003）。
- `items[]` 按 `priority_keys` 升序，完全确定（同输入同次序）。

## 响应 503

M2（段能力）或 M3（表事实）任一不可达。

**必须**在 `detail` 里点名是**哪一个**不可达 ——
两个上游的故障含义不同：M3 挂了是所有数据都没有，M2 挂了是只有归因不确定。
把两者混成一句"服务不可用"，会让人去查错方向。

## 反例（本契约明确不做的事）

| 不做 | 为什么 |
|---|---|
| 返回月分区作为独立条目 | 分区是 PostgreSQL 实现细节，不是平台对象类型（`PARTITIONED_ROOTS` 已登记） |
| 把 `unknown` 折叠进 `upstream_pending` | INV-4：静默吞掉未知＝假装知道 |
| 返回行数以外的质量判断 | 质量归 M4 的 `truth_flag`；本接口越界会给错的安全感 |
| 支持写操作 | 只读接口。要改数据经 M2 |
