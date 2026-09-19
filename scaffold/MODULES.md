# 模块索引：M 编号 ↔ 目录 ↔ 负责人

> **这是"我的模块文件夹在哪"的唯一答案。**
> 代码仓库只装代码与契约；**每个模块一个目录**，扩展只动自己的目录，
> 完成后靠契约测试 + 分支合并整合（见《学生上手指南》§8）。

## 总表

| M | 模块 | 代码目录 | 组 | 状态 | 门禁 |
|---|---|---|---|---|---|
| M1 | 平台基础设施与编排 | `docker-compose.skeleton.yml` + `sql/` + `ops/` | A | ✅ 已落地（IoTDB 未接） | 无（地基） |
| M2 | 数据接入与设备自管 | `modules/M2-ingest/` | B | ✅ 已落地 | 无 |
| M3 | 对象域数据访问层 | `modules/M3-rpdao/` | A | ✅ 已落地 | 无 |
| M4 | 数据治理·真数据门 | `modules/M4-governance/` | A | 🟡 骨架 | ← **P1 关键路径** |
| M5 | 融合辨析引擎 | `modules/M5-fusion/` | C | 🟡 骨架 | ⛔ 等 M4 质量门绿 |
| M6 | API 与指标语义层 | `modules/M6-api/` | D | ✅ 已落地（动作层 501） | 无 |
| M7 | 语义中枢 | `modules/M7-semantic/` | ⚠️ 待指派 | 🟡 骨架 | 无 |
| M8 | 应用层 | `modules/M8-apps/` | D | 🟡 骨架 | 可先做假数据 demo |
| M9 | 平台管理台 | `modules/M9-console/` | D | 🟡 骨架 | 无（现在就能做） |
| M10 | Agent 执行引擎 | `modules/M10-agent/` | 二期 | 🟡 骨架 | 本阶段不排期 |

## 每个模块目录里有什么（骨架模板）

```
modules/<模块>/
├── app.py              # 服务本体（FastAPI），含 /healthz /metrics
├── config/             # 全部配置外置（地址/口令/阈值不写死在代码里）
├── Dockerfile          # 容器定义（含 HEALTHCHECK）
├── requirements.txt    # Python 依赖
└── README.md           # 本模块说明（怎么起、怎么调、怎么测）
```

## 学生工作区

**草稿与实验不进代码目录**。每个模块在 `work/<模块>/` 下有自己的工作区：

| 目录 | 放什么 |
|---|---|
| `work/<模块>/` | 实验脚本、造数记录、标定草稿、临时数据、个人笔记 |
| `modules/<模块>/` | **正式代码**——能通过契约测试、要提交入库的才放这里 |

> `work/` 的内容已被 `.gitignore` 排除（只留各目录的 README），
> 所以你在工作区里随便造草稿，**不会污染仓库、不会造成合并冲突**。
> 同一模块多人协作时，在 `work/<模块>/` 下建自己的子目录（如 `work/M4/李明/`）。

## 门禁速查

```
M1 → M3 → M4 → M5 → M6 → M8
                ⛔ C 组现在绝对不能动 M5 业务逻辑
```

## 契约承诺方（改契约先找谁）

| 契约 | 真源 | 承诺方 |
|---|---|---|
| ① 报文 | `contracts/topics.yaml` + `messages/*.schema.json` | M2（B 组） |
| ② 表结构 | `sql/10_ddl_v0.5.sql` | M1（A 组） |
| ③ 数据出口 | `modules/M3-rpdao/` | M3（A 组） |
| ④ 服务接口 | `modules/*/openapi.json` | M6（D 组） |
