# Phase 1 Quickstart: 局部帧三维起伏查看器

本文件是**可跑通的验证步骤**，不是实现指南。
实现细节见 `tasks.md` 与实现阶段。

---

## 前置

```bash
cd /data/cy/shujuku/scaffold
export DOCKER_CONFIG=/data/cy/shujuku/.dockerhome
```

需要一个已构建的路面 CRG 文件（`scaffold/simulator/crg/route_0p1m.crg`，约 96 MiB）。
它是 `.gitignore` 的，若不存在见 `scaffold/simulator/crg/README.md` 的生成步骤。

---

## 步骤 1：抽稀出网格，写进交付目录

```bash
cd /data/cy/shujuku/scaffold/simulator/crg
PYTHONPATH=/data/cy/shujuku/.pylibs python3 export_viewer_mesh.py \
  route_0p1m.crg --out /data/cy/shujuku/artifacts/sim
```

**期望结果**

```limits
terrain_overview.bin | 约 296 / 296 | KB
terrain_near.bin | 约 1185 / 1185 | KB
overview 顶点数 | 13068 / 13068 | 个
overview 索引位宽 | 16 / 16 | 位
```

★ **必看**：脚本必须打印它断言的 `nu*nv < 65536`。
若顶点数越界而脚本没有报错就退出，这是一个缺陷 ——
`Uint16` 会静默截断，几何会以一种"看起来只是有点错"的方式坏掉。

---

## 步骤 2：产出清单

同一脚本或随后一步写出 `artifacts/sim/manifest.json`。

```bash
python3 -m json.tool /data/cy/shujuku/artifacts/sim/manifest.json | head -30
```

**校验清单符合契约**

```bash
cd /data/cy/shujuku/scaffold
python3 - <<'PY'
import json, jsonschema, pathlib
s = json.loads(pathlib.Path("../specs/003-local-terrain-viewer/contracts/artifact_manifest.v0.1.schema.json").read_text())
m = json.loads(pathlib.Path("../artifacts/sim/manifest.json").read_text())
jsonschema.validate(m, s); print("清单符合契约")
# 反向：清单里每一个 present:true 的文件，大小必须与磁盘一致
import os
for a in m["artifacts"]:
    if a["present"]:
        p = pathlib.Path("../artifacts/sim") / a["name"]
        assert p.stat().st_size == a["bytes"], f"{a['name']} 大小不符"
print("清单与磁盘一致")
PY
```

**期望结果**：两行都打印。★ 第二条是真正有用的那条 ——
只校验 schema 而不核磁盘，就会出现"清单说 289 KB、实际 0 字节"这种两边都绿的错。

---

## 步骤 3：起控制台并打开页面

```bash
cd /data/cy/shujuku/scaffold
docker compose -f docker-compose.skeleton.yml --profile m9 up -d console
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8024/sim      # 期望 200
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8024/         # 期望 200，且含 href="/sim"
```

**期望结果**：两个 200；首页 HTML 里能搜到 `href="/sim"`。

---

## 步骤 4：白名单与只读（本 feature 的两条硬性质）

```bash
# 4a 未登记的文件取不到
echo "sneaky" > /data/cy/shujuku/artifacts/sim/sneaky.txt
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8024/artifacts/sneaky.txt
#   期望 404 ★ 文件在磁盘上，但不在清单里 —— 这就是白名单在起作用

# 4b 路径穿越取不到
curl -s -o /dev/null -w '%{http_code}\n' 'http://localhost:8024/artifacts/../manifest.json'
curl -s -o /dev/null -w '%{http_code}\n' 'http://localhost:8024/artifacts/%2e%2e%2fmanifest.json'
#   期望都是 404

# 4c 容器对交付目录只读
docker exec rp-console sh -c 'touch /data/sim/should-fail' ; echo "退出码=$?"
#   期望非 0 —— 只读挂载在结构上挡住了写入

rm -f /data/cy/shujuku/artifacts/sim/sneaky.txt
```

★ **4c 是本 feature 唯一"结构上做不到"的验证。**
宪法原则 II 的口径是"结构上做不到"优于"约定不这么做"，
所以这条必须真的试一次，而不是读 compose 文件确认写了 `:ro`。

---

## 步骤 5：视频能播且能拖

```bash
curl -s -D- -o /dev/null -H 'Range: bytes=0-1023' \
  http://localhost:8024/artifacts/vehicle_on_crg_lane_right_10x.mp4 | head -5
```

**期望结果**：`206 Partial Content` 与 `Content-Range`。

★ **为什么这条必须单独验**：一个不返回 206 的实现**视频照样能播** ——
只是拖不动进度条。而"能播"会让粗看的人以为它对了。

---

## 步骤 6：契约测试

```bash
cd /data/cy/shujuku/scaffold && ./run_contract_tests.sh
```

**期望结果**：总运行器全绿，且套件数**比改动前多**。
★ 若套件数没变，说明新增的检查没有真的挂进总运行器 ——
那是宪法原则 V 说的空转。

---

## 步骤 7：确认"不判定"

```bash
docker exec rp-console sh -c 'cat /app/sim.html' | grep -nE '合格|不合格|超标|正常范围' ; echo "退出码=$?"
#   期望：无匹配（退出码 1）
```

★ 页面只显示，不判定。一旦出现"合格"两个字，
一个未标定的阈值就变成了生产判定。

---

## 端到端可跑通的判据

| # | 判据 | 命令 |
|---|---|---|
| 1 | 网格抽稀成功且索引为 16 位 | 步骤 1 的打印 |
| 2 | 清单符合契约**且**与磁盘一致 | 步骤 2 |
| 3 | 页面 200，首页链得到 | 步骤 3 |
| 4 | 未登记文件 404、路径穿越 404 | 步骤 4a/4b |
| 5 | 容器对交付目录写入**确实失败** | 步骤 4c |
| 6 | 视频返回 206 | 步骤 5 |
| 7 | 契约测试全绿且套件数增加 | 步骤 6 |
| 8 | 页面不含判定字样 | 步骤 7 |

★ **八条里第 4、5、7、8 条是"反向"判据** —— 它们验证的是系统**拒绝**
什么，而不是它做了什么。只有正向判据的测试证明不了系统会拒绝坏输入。
