# 测试目录

三档，**失效方式不同**，所以分开放。混在一起会让"绿了"这件事失去信息量。

```
tests/
├── contract/      契约测试 —— 离线，无 Docker、无数据库、无网络
├── integration/   集成测试 —— 离线，但要读真实的交付产物（node）
└── e2e/           端到端探针 —— **要活的整套栈**（Docker、库、MQTT、端口）
```

## contract/ —— 契约测试

由 `../run_contract_tests.sh` 统一跑（`./run_contract_tests.sh --list` 看全部）。
纪律见 `MODULE-ONBOARDING.md §4`：**下游不启动，直到上游的两个契约测试都绿。**

它们回答的是「**接口两边还一致吗**」——报文 schema 与 Pydantic 两套实现、
DDL 与数据字典与 catalog 三处表数、DAO 的越域拦截、M9 不直连库。

## integration/ —— 把页面自己的算术跑一遍

`sim_inline_script.mjs`。M9 的页面是**自包含 HTML（内联脚本、无构建步骤）**，
所以它没有"能单独 import 的函数"可以测 —— 只能把那段内联脚本**原样**跑起来。

它补的正是 `contract/test_sim_track.py` 补不上的那一块：

| | 回答的问题 |
|---|---|
| `contract/test_sim_track.py` | 页面有没有越权、产物在不在、降级对不对 |
| `integration/sim_inline_script.mjs` | **页面算出来的数对不对** |

★ `node --check` 只证明这段脚本能被解析，不证明它算得对。
而页面里的解网格、算曲率、按里程取轮荷、投影求符号位置，
是**可以拿真实产物独立跑一遍**的 —— 跑出来的数如果荒谬，页面就是错的。

它需要 `artifacts/sim/` 里有产物（跑一次 `simulator/crg/export_viewer_mesh.py` 就有了），
但**不需要**浏览器，也不需要 Docker。

**已知的一处限制**：脚本在 `!t`（轨迹没装载）时**早退**，
所以只破坏 `parseTrack` 会让后面 31 条判据一条都不跑。
两个方向都实测过：破 `parseTrack` 的列名 → 红（早退）；
破 `sampleLoadAtU` 的"无数据"分支 → 红，且**点名**报到具体那一条。
换句话说，**破坏要选在靠后的判据上，才能证明靠后的判据也接着**。

## e2e/ —— 端到端探针

`quickstart_probe.py`。它**不进**契约运行器，故意的：
它要一个真跑起来的栈（`localhost:8024` 的 console、库、MQTT、容器），
而契约运行器的契约是「离线」。

★ 一个要活在真实环境里、但环境不在就只能**跳过**的检查，
在"全绿"里的读数和"没跑"是一样的。所以它待在档位之外，
由 `../run_e2e_smoke.sh` 那条线负责，人工按需跑。

## `_env.py` —— 唯一一份 `.env` / DSN 解析

它**不是测试**（下划线开头，`test_runner_completeness.py` §1 会跳过它），
是给真库套件用的共享模块：

```python
from _env import dsn          # 脚本模式下 sys.path[0] 就是本目录
d = dsn()                     # 环境变量优先，否则读 ../.env；拿不到返回 None
if not d:
    print("未设 PG_DSN 且无 scaffold/.env —— 跳过")
```

**为什么要有它**：契约运行器用 `uv run python tests/contract/<f>.py` **直接执行**，
不经过 pytest，而且**从不传 `PG_DSN`**（实测：`run_contract_tests.sh` 里搜 `PG_DSN` 零命中）。
所以每个要打真库的套件都得自己解决"DSN 从哪来"。

到 2026-10-13，这件事在本目录下被**独立实现了四遍**
（`test_station_baseline` / `test_station_query` / `test_design_import` /
`test_ddl_migration_parity`），四份**语义基本等价**，都踩过并补上了
「`.env` 的行内注释」那个坑（`PG_PORT=55432   # 本机 5432 已被占用`——
天真的 `split("=")` 会得到一个语法上像 DSN、连不上又不报错的字符串）。

★ 而**第五个消费者**（`test_geometry_solver.py`）写的是 `os.environ["PG_DSN"]`，
**压根不读 `.env`** ⇒ 它的真库组在运行器下**从未运行过**，却显示成一行**绿**。

★★ **唯一不读 `.env` 的那份，恰好就是唯一没人跑的那份。**
这不是巧合：一个"没 DSN 就跳过"的套件，没人跑也不会有人发现它跳过了。

⇒ 新写的真库套件请 `from _env import dsn`，**不要再抄第六份**。

★ 已有那四份**暂不改动**：它们的**取默认值策略不同**
（baseline 用默认值，query 要求四个键齐全否则返回 `None`）——
统一策略是另一件事，需要单独决定。所以"同一性质仍有多处定义"这件事
**尚未结清**，工程决策记录 D-009 里如实记着。

## `modules/_shared/` —— 共享模块（跑测器统一加进 `PYTHONPATH`）

`run_contract_tests.sh` 里有一行：

```bash
export PYTHONPATH="$PWD/modules/_shared${PYTHONPATH:+:$PYTHONPATH}"
```

**为什么在这儿而不是各个测试里**：全仓库有 24 处手写 `sys.path.insert(0, …)`。
共享目录要是也照那个办法补，就得在每条会 import 到它的测试里各补一行 ——
又是一份**没人校的清单**，正是我们栽过三次的缺陷类。
设在跑测器上，**以后新增的测试自动进网**。

★ 它对应的是各 Dockerfile 里的 `COPY modules/_shared/jsonnum.py ./`：
容器里 `/app/jsonnum.py` 可 import，测试进程里靠这一行等价。

⚠ **直接单跑**（`uv run python tests/contract/xxx.py`）**没有**这一层，
要自己带上：

```bash
uv run --with "fastapi==0.115.6" --with httpx --with pyyaml==6.0.2 \
  env PYTHONPATH=modules/_shared python tests/contract/test_json_numeric_types.py
```

漏了它的症状很好认：`ModuleNotFoundError: No module named 'jsonnum'`。

## 纪律

- **每条新增的检查必须配一个元测试**（constitution Principle V）：
  把被守护的东西故意改坏一次，看它**点名**报错，然后还原并确认字节一致。
- **空转不是通过**。一个永远不会失败的检查比没有检查更糟 ——
  它会让人以为已经检查过了。
- **手写的清单必须有人校**。凡是"A 声明了要去消费 B"、而那份清单是手写的，
  就必须有一条检查在读它 —— 否则**新增一个 B 不产生任何信号**。
  本仓有两份这样的清单（运行器的 `TESTS=()`、compose 的 `initdb.d` 挂载表），
  由 `test_runner_completeness.py` 守着。
  ★ 起因：`test_geometry_solver.py` 从 002 起就在仓库里、被多处文档引用，
  **却没有任何一行会跑它**。见工程决策记录 D-009。
  ★★ 反过来说：**一条编码了错误规则的检查，比没有检查更糟** ——
  它会把正确的东西报成错的，然后逼你去改那个正确的东西。
  （实例：曾写过「每个 `_migrate_` 文件必须有结构变更语句」，而
  `90_migrate_v05_batch_remark.sql` 的一句「不动表结构、不动任何列」
  就否掉了它。见 D-009 第二节。）
- 运行器里已经钉了三条防空转的判定：零匹配退出 1、
  匹配到但一个都没跑退出 1、失败项列名字。
