# 契约：`GET /v1/design/capabilities`（M2 出口）

**所属契约**：④服务接口　**真源**：服务运行后的 `/openapi.json`
**新增模块产出契约**：M2 的对外契约，同样走工单、同样要有契约测试覆盖。

## 语义

报告**每个设计文件段**的支持状态与源文件状态。
回答："这个段你们处理得了吗？源在不在？"

**为什么这个接口必须存在**：`IMPLEMENTED` / `SEGMENT_FILES` / 导入目录
三件事**只有 M2 同时知道**。M6 需要它们来做缺口归因，但 M6 **不许** import M2
（宪法原则 II：跨模块只认契约）。走 HTTP 才是契约。

## 请求

```
GET /v1/design/capabilities
```

## 响应 200

```json
{
  "project_dir": "纬地工程项目文件",
  "checked_at": "2026-09-25T14:30:02+08:00",
  "segments": [
    {
      "segment": "earthwork_transfer",
      "implemented": true,
      "supported": true,
      "suffix": ".tsftxt",
      "kind": "土石方调配文件（.tsf 转换文本）",
      "source_present": true,
      "source_file": "052201341刘其立道路毕设土石方调配文件.tsftxt",
      "duplicates": []
    },
    {
      "segment": "geometry_point",
      "implemented": true,
      "supported": true,
      "suffix": ".3DR",
      "kind": "横断面三维数据文件",
      "source_present": false,
      "source_file": null,
      "duplicates": []
    }
  ]
}
```

### 字段约束（对应 `data-model.md`）

- `supported`（∈ `CAPABILITIES`）与 `implemented`（∈ `IMPLEMENTED`）
  **必须都报** —— 两者故意不同，差异如实反映进度（INV-8）。
  出现 `supported=true ∧ implemented=false` 是**正常状态**（"声明支持、还没解析器"）。
- `source_present` **必须现算**，不得缓存 —— 源文件会随时出现（SC-004）。
- `duplicates` **必须报出**：同一后缀有多个文件时，说明用了哪个、其余没进 IR（INV-9）。

## 措辞约束（重要）

`source_present=true` **只**表示"磁盘上有这个后缀的文件"，
**不**表示"导入一定能成功"（文件可能损坏、魔数不符、内容为空）。

故所有对外文案（含 M9 页面）必须说 **"源已具备"**，
**不得**说 **"可以导入"** 或 **"数据已就绪"**。
把前者说成后者，是给用户一个会落空的承诺。

## 响应 503

导入目录不可读（配置错误或挂载缺失）。
**必须**在 `detail` 里给出配置键名，不得只说"不可用"。

## 安全边界

- 返回的是**文件名**，不是完整路径（路径可能含主机信息）。
- `project_dir` 只返回**目录名**，不返回绝对路径。
- 只读接口，不提供任何写操作。
