# contracts/ —— 指针

本目录**不持有契约副本**。正本在：

```text
scaffold/contracts/openapi/m6-gateway.geo.v0.1.yaml
```

## 为什么只有一份

宪法「接入约束」要求契约文件提交到 `scaffold/contracts/`。
speckit 的设计稿习惯把契约放在 spec 目录下。

两个位置各放一份会**漂移**，而漂移的方向永远是**设计稿过期** ——
改的人改正本，忘改副本，后来的人读到的是过期的约束。
一份正本加一个指针，没有这个问题。
（同 003 的 `specs/003-local-terrain-viewer/contracts/README.md`。）

## 为什么是**新文件**，不是把 v0.3 改成 v0.4

★★ 宪法 Principle I 写着「契约**禁止就地改**」。

本次要加的三条路径（桩号↔经纬度、整条路线折线）确实是对 M6 网关契约的扩展，
就地升版看起来更省事。但那正是该原则要挡的做法：
**改掉 v0.3，就再没人能读到 v0.3 说过什么了。**

所以新增一份增量契约 `m6-gateway.geo.v0.1.yaml`，
只描述本 feature 新增的三条路径，并显式声明**不改** v0.3 的任何路径。

## 契约内容

**M6 网关 —— 区域帧扩展 v0.1**，三条路径：

| 路径 | 作用 |
|---|---|
| `GET /v1/geometry/sections/{id}/geo` | 桩号 → 经纬度（FR-001） |
| `GET /v1/geometry/sections/{id}/geo/reverse` | 经纬度 → 桩号（FR-002） |
| `GET /v1/geometry/sections/{id}/geo/route` | 整条路线折线 + bbox（US2、SC-002） |

★ 三条都必须回带 `epsg`（`4548`）—— **不得只写"39 带"**（FR-003）。
39 带有两个 EPSG 编码，差别在 Y 值带不带带号前缀，取错东坐标差 **39,000,000 m**。

## 两侧测试

| 侧 | 测试 |
|---|---|
| M6（提供方） | `scaffold/tests/contract/test_region_frame.py` |
| M9（消费方） | `scaffold/tests/contract/test_console.py` —— 断言新页面只经 `/gw/` 取数、自己不碰库 |

★ 宪法：「契约测试必须覆盖**应通过 / 应拒绝两侧**。」
所以三条路径各自都要有「正常输入通过」与「越界输入报错」两组用例
（越界 = 桩号超范围、经纬度出带、`section_id` 不存在）。
