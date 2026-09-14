#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按数据流节点验证 GitHub 仓库存在性（HTML 方式，无 API 配额限制）+ star 数解析"""
import re, time, os
from concurrent.futures import ThreadPoolExecutor
import urllib.request

os.environ["PYTHONPATH"] = ""

REPOS = [
    ("感知边缘·MQTT网关", "emqx/emqx", "EMQX：MQTT 接入+规则引擎直写双库"),
    ("感知边缘·边缘网关", "emqx/nanomq", "NanoMQ：轻量边缘 MQTT"),
    ("感知边缘·备选", "eclipse-mosquitto/mosquitto", "Mosquitto：轻量 Broker"),
    ("数据接入·缓冲", "apache/kafka", "Kafka：大数据缓冲/削峰"),
    ("存储·关系库", "postgres/postgres", "PostgreSQL 官方"),
    ("存储·空间扩展", "postgis/postgis", "PostGIS 空间扩展"),
    ("存储·PG镜像", "postgis/docker-postgis", "PostGIS+PG 官方 Docker"),
    ("存储·时序库", "apache/iotdb", "Apache IoTDB：高频时序"),
    ("存储·对象存储", "minio/minio", "MinIO"),
    ("存储·时序备选", "timescale/timescaledb", "TimescaleDB"),
    ("存储·时序再备选", "taosdata/TDengine", "TDengine"),
    ("存储·时序再备选", "influxdata/influxdb", "InfluxDB"),
    ("治理·校验", "great-expectations/great_expectations", "Great Expectations"),
    ("治理·调度", "apache/airflow", "Apache Airflow"),
    ("治理·校验备选", "sodadata/soda-core", "Soda Core"),
    ("治理·同步", "alibaba/DataX", "DataX"),
    ("治理·轻流", "n8n-io/n8n", "n8n"),
    ("融合·图谱", "apache/age", "Apache AGE"),
    ("融合·数据分析", "pandas-dev/pandas", "pandas"),
    ("融合·并行计算", "dask/dask", "Dask"),
    ("服务·API框架", "fastapi/fastapi", "FastAPI"),
    ("服务·网关", "apache/apisix", "APISIX"),
    ("服务·网关备选", "Kong/kong", "Kong"),
    ("服务·监控大屏", "grafana/grafana", "Grafana"),
    ("服务·语义层参考", "cube-js/cube", "Cube（指标语义层参考）"),
    ("服务·Text2SQL参考", "Vanna-ai/vanna", "Vanna（Text2SQL 参考）"),
    ("引擎·Agent运行时", "deepseek-ai/deepseek-harness", "DeepSeek Harness"),
    ("引擎·MCP桥", "modelcontextprotocol/python-sdk", "MCP Python SDK"),
    ("应用·有限元", "FEniCS/dolfinx", "FEniCSx 求解器"),
    ("应用·有限元备选", "OpenSees/OpenSees", "OpenSees"),
    ("应用·机器学习", "scikit-learn/scikit-learn", "scikit-learn"),
    ("应用·优化", "google/or-tools", "OR-Tools"),
    ("应用·三维可视化", "CesiumGS/cesium", "CesiumJS"),
    ("应用·三维备选", "mrdoob/three.js", "Three.js"),
    ("应用·可视化备选", "visgl/deck.gl", "deck.gl"),
    ("应用·2D备选", "Leaflet/Leaflet", "Leaflet"),
    ("应用·2D备选", "openlayers/openlayers", "OpenLayers"),
    ("部署·编排参考", "docker/awesome-compose", "docker-compose 示例"),
]

def check(repo):
    url = f"https://github.com/{repo}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) repo-check"})
            with urllib.request.urlopen(req, timeout=15) as r:
                html = r.read().decode("utf-8", "ignore")
            m = re.search(r'repo-stars-counter-star[^>]*?title="([\d,]+)"', html)
            stars = m.group(1) if m else "?"
            desc = re.search(r'<meta name="description" content="([^"]{0,120})', html)
            return {"repo": repo, "ok": True, "stars": stars, "desc": (desc.group(1) if desc else "")[:100]}
        except Exception as e:
            if attempt == 2:
                return {"repo": repo, "ok": False, "err": str(e)[:80]}
            time.sleep(1.5)

with ThreadPoolExecutor(max_workers=6) as ex:
    results = list(ex.map(check, [r[1] for r in REPOS]))

by_repo = {r["repo"]: r for r in results}
lines = ["# 数据流节点 × 真实 GitHub 开源代码库清单（GitHub 实测验证）\n",
         "> 验证：2026-09-05 ｜ 方式：GitHub 仓库页面 HTTP 实测（存在性＋star）｜ 许可证以各仓库 LICENSE 文件为准（下表为一般标识）｜ 形态：服务类=官方 Docker 镜像直接调用；库类=官方包/源码 clone 直接使用；均不改源码。\n",
         "| 数据流节点 | 仓库（实测存在） | 许可证(一般标识) | Stars | 用途/调用方式 |",
         "|---|---|---|---|---|"]
LIC = {
    "emqx/emqx": "Apache-2.0", "emqx/nanomq": "Apache-2.0", "eclipse-mosquitto/mosquitto": "EPL-2.0/EDL",
    "apache/kafka": "Apache-2.0", "postgres/postgres": "PostgreSQL License", "postgis/postgis": "GPL-2.0",
    "postgis/docker-postgis": "见仓库", "apache/iotdb": "Apache-2.0", "minio/minio": "AGPL-3.0",
    "timescale/timescaledb": "TSL+Apache", "taosdata/TDengine": "AGPL-3.0", "influxdata/influxdb": "MIT(1.x)/商业3.x",
    "great-expectations/great_expectations": "Apache-2.0", "apache/airflow": "Apache-2.0", "sodadata/soda-core": "Apache-2.0",
    "alibaba/DataX": "Apache-2.0", "n8n-io/n8n": "fair-code(源码可见)", "apache/age": "Apache-2.0",
    "pandas-dev/pandas": "BSD-3", "dask/dask": "BSD-3", "fastapi/fastapi": "MIT", "apache/apisix": "Apache-2.0",
    "Kong/kong": "Apache-2.0", "grafana/grafana": "AGPL-3.0", "cube-js/cube": "Apache-2.0(核心)",
    "Vanna-ai/vanna": "MIT", "deepseek-ai/deepseek-harness": "MIT", "modelcontextprotocol/python-sdk": "MIT",
    "FEniCS/dolfinx": "LGPL-2.1", "OpenSees/OpenSees": "开源(自定义)", "scikit-learn/scikit-learn": "BSD-3",
    "google/or-tools": "Apache-2.0", "CesiumGS/cesium": "Apache-2.0", "mrdoob/three.js": "MIT",
    "visgl/deck.gl": "MIT", "Leaflet/Leaflet": "BSD-2", "openlayers/openlayers": "BSD-2",
    "docker/awesome-compose": "各镜像自定",
}
n_ok = 0
for node, repo, note in REPOS:
    r = by_repo.get(repo, {})
    if r.get("ok"):
        n_ok += 1
        lines.append(f"| {node} | [{repo}](https://github.com/{repo}) ✅ | {LIC.get(repo,'?')} | {r['stars']} | {note} |")
    else:
        lines.append(f"| {node} | ❌ {repo}（{r.get('err','?')}） | - | - | - |")
lines += ["",
          "## 可直接复制的用法示例",
          "```bash",
          "# 服务类（官方镜像，零改码）：",
          "docker run -d --name emqx -p 1883:1883 emqx/emqx:5.8",
          "docker run -d --name iotdb -p 6667:6667 apache/iotdb:1.3.3-standalone",
          "docker run -d --name pg -p 5432:5432 postgis/postgis:16-3.4",
          "docker run -d --name minio -p 9000:9000 minio/minio server /data",
          "# 库类（pip/git 直接使用）：",
          "pip install fastapi 'great_expectations' apache-airflow pandas dask scikit-learn ortools",
          "git clone https://github.com/FEniCS/dolfinx.git  # 有限元（需编译/容器）",
          "git clone https://github.com/deepseek-ai/deepseek-harness.git  # 引擎(P4可选)",
          "```",
          "## 说明",
          "- 非 GitHub 发行：CalculiX（开源 FEM，官方站 calculix.de 下载，无官方 GitHub）；达梦/金仓/ABAQUS 商业软件（验收期按信创条款）。",
          "- 语义中枢参考实现：Cube（指标语义层思想）＋Vanna（Text2SQL RAG）＋MCP Python SDK（工具桥）；引擎=DeepSeek Harness。",
          "- 自研薄壳（规则→LLM→人工确认、device_status 心跳、工单闭环、mapping_set 持久化）无现成同构开源，以本清单组件官方 API 组合实现（数百行），属平台业务代码。"]
open("/data/cy/shujuku/output/数据流节点-真实GitHub代码库清单.md", "w", encoding="utf-8").write("\n".join(lines))
print(f"通过 {n_ok}/{len(REPOS)}")
for node, repo, note in REPOS:
    r = by_repo.get(repo, {})
    if not r.get("ok"):
        print("  FAIL:", repo, r.get("err"))