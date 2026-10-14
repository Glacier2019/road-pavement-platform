#!/usr/bin/env bash
# ============================================================================
# 契约测试总运行器
# ----------------------------------------------------------------------------
# 一次跑完全部契约测试。**离线**：不需要 Docker、不需要数据库、不需要网络。
#
# 用法：
#     cd scaffold
#     ./run_contract_tests.sh              # 跑全部
#     ./run_contract_tests.sh m4           # 只跑 M4 的
#     ./run_contract_tests.sh --list       # 列出所有可跑的契约测试
#
# 纪律（MODULE-ONBOARDING.md §4）：
#     「下游不启动，直到上游的两个契约测试都绿。」
#     改了契约或改了实现，先跑这个脚本，绿了再往下走。
# ============================================================================
set -uo pipefail

cd "$(dirname "$0")"

# uv 的缓存/解释器放到仓库内，避免污染 HOME（CI 上更明显）
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/../.uvcache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$PWD/../.uvpython}"

# ★★★★★ 共享模块目录必须可 import（2026-10-13，T045 收尾）。
#   各 Dockerfile 都把 `modules/_shared/jsonnum.py` 拷到镜像的 `/app/`，
#   于是容器里 `from jsonnum import ...` 能用。测试进程里没有那一步，
#   所以这里补上 —— **一处**，覆盖全部测试以及**以后新增的每一条**。
#
#   ★ 为什么不改在各测试里：全仓库有 24 处手写 `sys.path.insert(0, …)`，
#     照着补 4 处就是又抄一份**没人校的清单** —— 正是我们栽过三次的缺陷类。
#     改在跑测器上，新测试自动进网。
#   ⚠ 直接 `uv run python tests/contract/xxx.py` 单跑时**没有**这一层，
#     需要自己带 `PYTHONPATH=modules/_shared`；见 tests/README.md。
export PYTHONPATH="$PWD/modules/_shared${PYTHONPATH:+:$PYTHONPATH}"

PY_DEPS=(--with jsonschema --with pyyaml --with pydantic)
DAO_DEPS=(--with "psycopg[binary,pool]==3.2.3")
# ★★ PYDAO = PY 依赖 + psycopg。
#   加它是因为一个**真实发生过的漏跑**：`design-导入` 原先标的是 `PY`，
#   而 PY_DEPS 里没有 psycopg —— 于是契约⑤ 的第 12 组（落库器端到端，
#   含"批次备注必须写明导入当时"等 25 条断言）在总运行器下**从未执行**，
#   汇总却只说"通过 489 ｜ 失败 0"，和全绿读起来一模一样。
#   实测：不装 psycopg → 通过 489；装了 → 通过 514。差的 25 条就是它们。
PYDAO_DEPS=("${PY_DEPS[@]}" --with "psycopg[binary,pool]==3.2.3")
API_DEPS=(--with fastapi==0.115.6 --with httpx --with "psycopg[binary,pool]==3.2.3")
# M9 集成面：要 fastapi+httpx 起 TestClient，还要 pyyaml 读模块登记表；
# 它**不需要** psycopg —— M9 不直连库（这正是 test_console.py 第 1 组钉的事）。
CONSOLE_DEPS=(--with fastapi==0.115.6 --with httpx --with pyyaml==6.0.2)

# name | 说明 | 负责模块 | 依赖 | 测试文件
  # ★★ 005（坐标基准绑定）**没有新增一行**，这是有意的，不是漏了：
  #   它新增的判据全部落在**已经注册的文件里** ——
  #     · test_region_frame.py §18a–§18h（区域帧那条套件）
  #     · test_console.py（m9-集成面）
  #     · test_dao_contract.py 第 4 组（m3-dao）
  #   ⇒ 不需要新桶、不需要新依赖，加进来即跑。
  #   ★ 同一条性质不许有两份定义 —— 另起一个文件反而会漂。
  #
  # ★★ 而 **005 的验收探针不在本表里，也不能在本表里**：
  #     tests/e2e/quickstart_probe.py（八条判据，含「两级不一致必须被写入侧拒」）
  #   它要**活着的 pg + api + console**，而本运行器的约定是**离线**
  #   （见文件头：不启容器、不连库）。把它塞进来会让离线套件依赖在线服务，
  #   那是拿一个契约换一个更差的契约。
  #   跑法（需要容器已起）：
  #     export DOCKER_CONFIG=$PWD/../.dockerhome
  #     uv run --with httpx --with jsonschema --with pyyaml \
  #       python tests/e2e/quickstart_probe.py
  #   ★ 它在 tests/e2e/ 下、由 run_e2e_smoke.sh 那一类**在线**运行器管。
TESTS=(
  "m2-报文|报文 Schema 与 Pydantic 双实现一致性|M2|PY|tests/contract/test_wim_contract.py"
  "m2-造数器|造数器产出的报文必须过契约|M2|PY|tests/contract/test_simulator_contract.py"
  "m3-dao|DAO 契约（7 域 repository、越域拦截）|M3|DAO|tests/contract/test_dao_contract.py"
  "m6-路由|M6 HTTP 路由契约|M6|API|tests/contract/test_api_routes.py"
  "区域帧|004 桩号↔经纬度：几何内核不漂移 + 投影往返 + 落点 + 两侧非格点判据 + 换带/丢转向两个对照|M6/M9|CONSOLE|tests/contract/test_region_frame.py"
  "m2-违约分类|违约分类码 + M2/M4 跨层命名一致性|M2|PY|tests/contract/test_violation_codes.py"
  "m4-治理|质量规则契约 + 真值晋升契约 + 配置↔契约一致性|M4|PY|tests/contract/test_governance_contract.py"
  "m5-m10|M5–M10 模块产出契约（诊断/映射/养护/登记/工单）|M5–M10|PY|tests/contract/test_module_contracts.py"
  "m3-写权|契约③写入侧：表级写权守卫（应通过/应拒绝两侧）+ M2 不绕契约|M2/M3|PY|tests/contract/test_write_guard.py"
  "m1-表数|契约②：DDL ↔ 数据字典 ↔ catalog 三处表数一致|M1/M3|PY|tests/contract/test_ddl_dict_catalog.py"
  # ★ v0.6 补漏（2026-10-14）：桩号类列**必须有注释** —— 注释是桩号语义的唯一载体。
  #   起因实测：62 个桩号类列只有 18 个有注释，而同构的 ditch_segment / slope_segment
  #   一个有、一个没有 —— 说明「写不写全靠人记得」。本门是**棘轮**（清单只许缩短），
  #   并顺带钉住 sql/ ↔ 活库的注释一致（装库一致那条实测**不比注释**）。
  "列注释|契约② 补漏：桩号类列必须有注释（棘轮 + DDL↔活库逐字一致 + 元测试）|M1/M3|DAO|tests/contract/test_column_comments.py"
  "design-导入|契约⑤：设计导入 IR + 纬地适配器 + 落库器（应通过/应拒绝两侧）|M2|PYDAO|tests/contract/test_design_import.py"
  "m9-集成面|M9 集成面：页面只经 /gw 取数、不直连库、转发的边界（应通过/应拒绝两侧）|M9|CONSOLE|tests/contract/test_console.py"
  "m1-装库一致|契约②补漏：全新装 DDL 与 DDL+migration 升级必须得到同一套 schema|M1/M3|DAO|tests/contract/test_ddl_migration_parity.py"
  "缺口归因|契约④：空表缺口归因（判定短路顺序 / 排序确定性 / 硬线）|M6|PY|tests/contract/test_gap_attribution.py"
  "交付物口径|契约⑤：图件/文档宣称的表数必须等于真源（含元测试）|全组|PY|tests/contract/test_deliverable_truth.py"
  # ★ v0.6 新增。它**打真库**（删路段、造第二条路线），所以必须排在打库的那些之后，
  #   且用 DAO 依赖。它不是"再来一组几何断言"，而是钉工单 #3 四项改动的落地 ——
  #   每一条都配了前置断言，防止「删一个本来就没桩号的路段，然后说桩号还在」。
  "桩号基线|契约② v0.6：桩号脱离路段重锚路线（FR-002/003/004 + Q1，含元测试）|M1/M3|DAO|tests/contract/test_station_baseline.py"
  "覆盖区间|契约② FR-005~008：实际覆盖区间回填 + 声明vs实际校验 + 原子性（离线，含元测试）|M2|PY|tests/contract/test_coverage_check.py"
  "桩号文本|契约② FR-009~011：桩号文本↔数值双向还原（全 332 条）+ 加桩不得规整（含元测试）|M2|PY|tests/contract/test_station_text.py"
  "桩号区间查询|契约② FR-018：按桩号区间查询（相交≠包含，含前置断言与证伪）|M3/M6|API|tests/contract/test_station_query.py"
  # ★ v0.7 新增（003 局部帧查看器 US2–US5）。它钉的是**交付目录里的东西**：
  #   轨迹 CSV 的四列位姿、沿程量「无数据 ≠ 实测 0」的编码、产物的登记与降级、
  #   视频 Range、带号这个假定的可见性。与 m9-集成面 的失效方式不同 ——
  #   一边是"M9 越权了"，一边是"少了一列/少了一个产物"，所以分开成组。
  #   它**不打真库、不起容器**，只读交付目录 + 用 TestClient 走 M9 路由。
  "仿真轨迹|003 US2–US5：轨迹四列位姿、沿程量无数据编码、产物降级、视频 Range、带号假定|M9|CONSOLE|tests/contract/test_sim_track.py"
  # ★ Node 套件（见上面 NODE 那一档）。它把 sim.html 的**内联脚本原样**跑起来，
  #   用真实产物喂它，然后读它算出来的数 —— 补的正是 test_sim_track.py 补不上的那一块：
  #   那一组检查"页面有没有越权、产物在不在"，这一组检查"页面的算术对不对"。
  #   `node --check` 只证明能被解析；页面里的解网格、算曲率、按里程取轮荷、
  #   投影求符号位置，是可以拿真实数据独立跑一遍的。
  "仿真页算术|003 页面内联脚本：网格解码/曲率/按里程取轮荷/投影符号位（真产物，无浏览器）|M9|NODE|tests/integration/sim_inline_script.mjs"
  # ★ v0.7 新增（宪法 1.1.1）。**这是本仓库唯一一条读 `.specify/` 的检查。**
  #   它钉的不是平台代码，是**流程自己**：宪法加了原则，模板与 plan 里必须产生新行。
  #   ★★ 起因：重跑 005 的 Constitution Check 时发现原则 VI 是 `##`、而 I–V 是 `###`，
  #      而 `plan-template.md` 的门禁表位置只有一个不列原则的占位符 ——
  #      所以「新增原则需进检查表」这件事**没有任何东西会发现**。
  #   ★ 它自带两条元测试（把层级改坏、把模板的 VI 行删掉），所以它不会退化成摆设。
  #   ★ 它是**离线**的（纯文本解析，无数据库、无网络），所以放 PY 桶。
  "宪法一致|宪法 ↔ 模板 ↔ plan：逐原则覆盖 + 层级一致 + 历史名单只许缩短（含元测试）|全组|PY|tests/contract/test_spec_kit_consistency.py"

  # ★★ v0.7 补登记（2026-10-13）。**这份文件从 002 起就在仓库里，却没有任何一行会跑它。**
  #   引用它的**全是文档**（002 的 tasks.md / plan.md、contracts/design-import/README.md），
  #   运行器没有它、run_e2e_smoke.sh 没有它、compose 也没有它。
  #   ★ 直接补登记是**不够**的：它当时写的是
  #       os.environ.get("PG_DSN") or os.environ.get("RPP_PG_DSN")
  #     —— 压根不读 scaffold/.env，而运行器**从不传 PG_DSN**
  #     （实测：本脚本里搜 PG_DSN 零命中）。所以它第 5 组只会走 [SKIP]，
  #     而**第 5 组的 `return fails` 会把第 6/7/8 组一并吞掉** ——
  #     八组只跑一半，报告写「全部通过」。
  #   ★★ 这正是本仓反复栽的那类**假绿**，也正是 test_station_baseline.py
  #      当初写下「为什么要读 .env 而不是没 PG_DSN 就跳过」那段注释的原因。
  #      ⇒ 解析已收到 tests/contract/_env.py 一份，本文件改为 import 它。
  #   ★ 桶选 DAO：它只要 psycopg（已实测「不加 pyyaml 也能全过」）。
  "几何重建|逐桩几何可重建（重算 ≡ 已存）+ 桩号1:1 + ★篡改必须被发现|M2|DAO|tests/contract/test_geometry_solver.py"

  # ★★ v0.7 新增（2026-10-13）。守的**不是功能，是两份清单的完整性**：
  #    ① run_contract_tests.sh 的 TESTS=()  ←→ tests/**/*.py
  #    ② docker-compose 的 initdb.d 挂载表  ←→ sql/*.sql
  #    两份都是**手写的**，而原先**没有任何东西在读它们** ——
  #    于是「新增一个测试文件 / 一个 sql 文件」不产生任何信号。
  #    ★ 起因就是上一行那个孤儿：test_geometry_solver.py 提交了很久，
  #      引用它的全是文档，**没有任何一行会跑它**。
  #    ★ 它自带三条元测试（拿掉一行登记、拿掉一行挂载、拿掉一个种子的挂载），
  #      所以它不会退化成「读一遍文件然后永远打印 OK」的那种摆设。
  #    ★ 离线（纯文本解析），放 PY 桶。
  #    ★★ 它还**显式记下自己查不了什么**（迁移 vs 种子没有机器判据，
  #       实测三条候选规则全不成立）—— 见文件里 §2c 的注释。
  "清单完整|三份手写清单都要有人校：运行器 ↔ tests/ + initdb.d ↔ sql/ + compose ↔ .env.example|全组|PY|tests/contract/test_runner_completeness.py"
  # ── v0.6（T045/T046）数字必须以数字出去 ────────────────────────────────
  #   ★ 起因：FastAPI 把**带返回注解**的路由交给 pydantic 序列化，而 pydantic 的
  #     `Decimal` JSON 序列化保精度 ⇒ 输出**字符串**。M6 的 20 条路由全带
  #     `-> dict[str, Any]`，实测 **10 条**把数字发成了字符串。
  #   ★★ 而它此前**全绿通过** —— 因为 §13 只比字段名，不比类型。
  #      一份声明了 `type: number` 却没人读的契约，和没声明是一样的。
  #   ★ 修法是结构性的：`app.router.route_class = NoResponseModel`（见 jsonnum.py），
  #     一条改动覆盖全部路由 + 以后新增的每一条。
  #   ⚠ 这个测试走 API 桶（要 fastapi/httpx）。§1 §2 离线always-run；
  #     §3 打活服务，服务没起会**大声跳过**并在结果里说明，不假装通过。
  "数字类型|numeric 列必须以 JSON 数字出去：机制 + 全部路由 response_model 为 None + 活服务实测|M6|API|tests/contract/test_json_numeric_types.py"
)

if [[ "${1:-}" == "--list" ]]; then
  printf "%-12s %-8s %s\n" "名称" "负责模块" "说明"
  echo "---------------------------------------------------------------"
  for t in "${TESTS[@]}"; do
    IFS='|' read -r name desc mod deps file <<<"$t"
    printf "%-12s %-8s %s\n" "$name" "$mod" "$desc"
  done
  echo
  echo "跑法：./run_contract_tests.sh            # 全部"
  echo "      ./run_contract_tests.sh m4         # 只跑名称含 m4 的"
  exit 0
fi

FILTER="${1:-}"
pass=0; fail=0; skipped=0; matched=0; failed_names=()

printf '%-12s %-8s %s\n' "契约" "模块" "结果"
echo "---------------------------------------------------------------"

for t in "${TESTS[@]}"; do
  IFS='|' read -r name desc mod deps file <<<"$t"
  [[ -n "$FILTER" && "$name" != *"$FILTER"* ]] && continue
  ((matched++))

  if [[ ! -f "$file" ]]; then
    printf '%-12s %-8s %s\n' "$name" "$mod" "跳过（文件不存在）"
    ((skipped++)); continue
  fi

  case "$deps" in
    PY)  dep_args=("${PY_DEPS[@]}") ;;
    PYDAO) dep_args=("${PYDAO_DEPS[@]}") ;;
    DAO) dep_args=("${DAO_DEPS[@]}") ;;
    API) dep_args=("${API_DEPS[@]}") ;;
    CONSOLE) dep_args=("${CONSOLE_DEPS[@]}") ;;
    *)   dep_args=("${PY_DEPS[@]}") ;;
  esac

  # ★ Node 套件走 node 直跑，不过 uv。
  #   为什么要有这一档：M9 的页面是**自包含 HTML（内联脚本）**，
  #   而 `node --check` 只证明它能被解析、不证明它算得对。
  #   sim_inline_script.mjs 把那段内联脚本**原样**跑起来、用真实产物喂它，
  #   所以它属于契约测试（离线、无 Docker），但不属于 Python。
  if [[ "$deps" == "NODE" ]]; then
    if out=$(node "$file" 2>&1); then
      last=$(echo "$out" | tail -1)
      printf '%-12s %-8s %s\n' "$name" "$mod" "$last"
      ((pass++))
    else
      last=$(echo "$out" | tail -1)
      printf '%-12s %-8s %s\n' "$name" "$mod" "${last:-失败}"
      ((fail++)); failed_names+=("$name")
    fi
    continue
  fi

  if out=$(uv run --quiet "${dep_args[@]}" "$file" 2>&1); then
    last=$(echo "$out" | tail -1)
    printf '%-12s %-8s %s\n' "$name" "$mod" "$last"
    ((pass++))
  else
    last=$(echo "$out" | tail -1)
    printf '%-12s %-8s %s\n' "$name" "$mod" "${last:-失败}"
    ((fail++)); failed_names+=("$name")
  fi
done

echo "---------------------------------------------------------------"
echo "通过 $pass ｜ 失败 $fail ｜ 跳过 $skipped"

# ★★ 「一个都没跑」不是「全部通过」。
#
# 加这两条是因为**它们真的骗过我一次**：我用
#     ./run_contract_tests.sh test_design_import
# 去验证一条新加的钉子会不会响，脚本回「通过 0 ｜ 失败 0 ｜ 跳过 0」
# 紧跟着一句「全部通过 ✓」—— 我据此认为"钉子没响"，其实是**根本没跑**。
# 原因是过滤器按**套件名**子串匹配（上面 --list 里的 `design-导入`），
# 而我传的是**文件名**。写错一个字，零匹配，全绿。
#
# 这正是一路在守的那条线：**空转不是通过**。一个永远不会失败的检查，
# 比没有检查更糟 —— 因为它会让人以为已经检查过了。
if (( matched == 0 )); then
  echo "✗ 没有任何套件匹配过滤器：'${FILTER}'"
  echo "  可用套件名（用 ./run_contract_tests.sh --list 看全）："
  for t in "${TESTS[@]}"; do
    IFS='|' read -r _n _d _m _p _f <<<"$t"
    printf '    %s\n' "$_n"
  done
  echo "  ⚠ 注意：过滤器匹配的是**套件名**，不是测试文件名。"
  exit 1
fi
if (( pass == 0 && fail == 0 )); then
  echo "✗ 匹配到 $matched 个套件，但一个都没能跑（全部跳过？）—— 不能算通过"
  exit 1
fi

if (( fail > 0 )); then
  echo "失败项：${failed_names[*]}"
  echo
  echo "排查建议：各测试的完整输出请单独跑，例如"
  echo "  uv run --with jsonschema --with pyyaml tests/contract/test_governance_contract.py"
  exit 1
fi
echo "全部通过 ✓"
exit 0
