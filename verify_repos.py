#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按数据流节点验证 GitHub 仓库真实存在，输出可直接调用/复制的开源代码清单"""
import json, urllib.request, time, os

os.environ["PYTHONPATH"] = ""

# (数据流节点, owner/repo, 用途说明)
REPOS = [
    # —— 感知/边缘 ——
    ("感知边缘·MQTT网关", "emqx/emqx", "EMQX Broker：MQTT 接入+规则引擎直写双库"),
    ("感知边缘·边缘网关", "emqx/nanomq", "NanoMQ：轻量边缘 MQTT（私有协议采集仪转 MQTT）"),
    ("感知边缘·备选", "eclipse-mosquitto/mosquitto", "Mosquitto：轻量单机 Broker"),
    # —— 数据接入 ——
    ("数据接入·缓冲", "apache/kafka", "Kafka：大数据缓冲/削峰"),
    # —— 双库存储 ——
    ("存储·关系库", "postgres/postgres", "PostgreSQL 官方源码/镜像"),
    ("存储·空间扩展", "postgis/postgis", "PostGIS 空间扩展"),
    ("存储·PG镜像", "postgis/docker-postgis", "PostGIS+PG 官方 Docker 镜像构建"),
    ("存储·时序库", "apache/iotdb", "Apache IoTDB：高频时序"),
    ("存储·对象存储", "minio/minio", "MinIO：影像/3D/文件"),
    ("存储·时序备选", "timescale/timescaledb", "TimescaleDB：PG 超表时序"),
    ("存储·时序再备选", "taosdata/TDengine", "TDengine：国产时序"),
    ("存储·时序再备选", "influxdata/influxdb", "InfluxDB"),
    # —— 数据治理·真数据门 ——
    ("治理·校验", "great-expectations/great_expectations", "Great Expectations：声明式数据质量校验"),
    ("治理·调度", "apache/airflow", "Airflow：批处理调度/重跑"),
    ("治理·校验备选", "sodadata/soda-core", "Soda Core：轻量数据质量"),
    ("治理·同步", "alibaba/DataX", "DataX：异构数据批量同步"),
    ("治理·轻流", "n8n-io/n8n", "n8n：轻量自动化流"),
    # —— 融合辨析 ——
    ("融合·图谱", "apache/age", "Apache AGE：PostgreSQL 图扩展（Cypher）"),
    ("融合·数据分析", "pandas-dev/pandas", "pandas"),
    ("融合·并行计算", "dask/dask", "Dask：并行 DataFrame"),
    # —— API 服务与指标语义 ——
    ("服务·API框架", "fastapi/fastapi", "FastAPI：REST/OpenAPI"),
    ("服务·网关", "apache/apisix", "APISIX：API 网关"),
    ("服务·网关备选", "Kong/kong", "Kong 网关"),
    ("服务·监控大屏", "grafana/grafana", "Grafana：可视化/告警"),
    ("服务·语义层参考", "cube-js/cube", "Cube：开源指标语义层（参考指标注册表设计）"),
    ("服务·Text2SQL参考", "Vanna-ai/vanna", "Vanna：Text-to-SQL RAG 参考（服务 Copilot 思路）"),
    # —— 语义中枢/引擎 ——
    ("引擎·Agent运行时", "deepseek-ai/deepseek-harness", "DeepSeek Harness：MIT agent 运行时（P4 可选）"),
    ("引擎·MCP桥", "modelcontextprotocol/python-sdk", "MCP Python SDK：平台工具面协议桥"),
    # —— 应用 ——
    ("应用·有限元", "FEniCS/dolfinx", "FEniCSx：开源 FEM 求解（Python API）"),
    ("应用·有限元备选", "OpenSees/OpenSees", "OpenSees：开源结构有限元（校核）"),
    ("应用·机器学习", "scikit-learn/scikit-learn", "scikit-learn：承载力反演/ML 代理模型"),
    ("应用·优化", "google/or-tools", "OR-Tools：养护序列优化"),
    ("应用·三维可视化", "CesiumGS/cesium", "CesiumJS：三维孪生可视化"),
    ("应用·三维备选", "mrdoob/three.js", "Three.js"),
    ("应用·可视化备选", "visgl/deck.gl", "deck.gl：大数据图层"),
    ("应用·2D备选", "Leaflet/Leaflet", "Leaflet 2D 地图"),
    ("应用·2D备选", "openlayers/openlayers", "OpenLayers 2D 地图"),
    # —— 部署编排参考 ——
    ("部署·编排参考", "docker/awesome-compose", "docker-compose 官方示例合集"),
]

def gh(repo):
    url = f"https://api.github.com/repos/{repo}"
    req = urllib.request.Request(url, headers={"User-Agent": "repo-verify", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"error": str(e)}

out = []
fail = []
for node, repo, note in REPOS:
    d = gh(repo)
    if "error" in d or "full_name" not in d:
        fail.append((node, repo, d.get("error", "?")))
        out.append({"node": node, "repo": repo, "note": note, "ok": False})
    else:
        lic = (d.get("license") or {}).get("spdx_id", "?")
        out.append({"node": node, "repo": repo, "note": note, "ok": True,
                    "stars": d.get("stargazers_count", 0), "lic": lic,
                    "desc": (d.get("description") or "")[:90], "url": d.get("html_url", "")})
    time.sleep(0.3)

lines = ["# 数据流节点 × 真实 GitHub 开源代码库清单（GitHub API 实测验证）\n",
         "> 验证时间：2026-09-05 ｜ 验证方式：GitHub REST API（存在性/许可证/star 实查）｜ 形态：服务=官方 Docker 镜像直接调用；库=官方包/clone 直接使用；均不改源码。\n",
         "| 数据流节点 | 仓库 | 许可证 | Stars | 用途与调用 |",
         "|---|---|---|---|---|"]
for o in out:
    if not o["ok"]:
        lines.append(f"| {o['node']} | ❌{o['repo']}（验证失败 {o['note']}） | - | - | - |")
        continue
    lines.append(f"| {o['node']} | [{o['repo']}]({o['url']}) | {o['lic']} | {o['stars']} | {o['note']} |")
lines.append("")
lines.append("## 说明\n")
lines.append("- 非 GitHub 发行：**CalculiX**（开源 FEM，官方站 calculix.de 下载源码，无官方 GitHub 仓库；FEniCSx/OpenSees 已覆盖同类需求）；达梦/金仓/ABAQUS 为商业软件（非开源，验收期按信创条款采购）。")
lines.append("- 语义中枢：服务 Copilot 可直接复用的参考实现 = Cube（指标语义层思想）＋Vanna（Text2SQL RAG 管线）＋MCP SDK（工具桥）；引擎层 = DeepSeek Harness（MIT）。")
lines.append("- 自研薄壳部分（规则→LLM→人工、心跳表、工单闭环）无现成同构开源，以本清单组件的官方 API 组合实现（数百行），属平台业务代码。")
txt = "\n".join(lines)
path = "/data/cy/shujuku/output/数据流节点-真实GitHub代码库清单.md"
with open(path, "w", encoding="utf-8") as f:
    f.write(txt)
print(f"总仓库 {len(out)}，验证通过 {len(out)-len(fail)}，失败 {len(fail)}")
for f_ in fail:
    print("  FAIL:", f_)
print("输出:", path)