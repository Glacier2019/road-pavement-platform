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
TESTS=(
  "m2-报文|报文 Schema 与 Pydantic 双实现一致性|M2|PY|tests/contract/test_wim_contract.py"
  "m2-造数器|造数器产出的报文必须过契约|M2|PY|tests/contract/test_simulator_contract.py"
  "m3-dao|DAO 契约（7 域 repository、越域拦截）|M3|DAO|tests/contract/test_dao_contract.py"
  "m6-路由|M6 HTTP 路由契约|M6|API|tests/contract/test_api_routes.py"
  "m2-违约分类|违约分类码 + M2/M4 跨层命名一致性|M2|PY|tests/contract/test_violation_codes.py"
  "m4-治理|质量规则契约 + 真值晋升契约 + 配置↔契约一致性|M4|PY|tests/contract/test_governance_contract.py"
  "m5-m10|M5–M10 模块产出契约（诊断/映射/养护/登记/工单）|M5–M10|PY|tests/contract/test_module_contracts.py"
  "m3-写权|契约③写入侧：表级写权守卫（应通过/应拒绝两侧）+ M2 不绕契约|M2/M3|PY|tests/contract/test_write_guard.py"
  "m1-表数|契约②：DDL ↔ 数据字典 ↔ catalog 三处表数一致|M1/M3|PY|tests/contract/test_ddl_dict_catalog.py"
  "design-导入|契约⑤：设计导入 IR + 纬地适配器 + 落库器（应通过/应拒绝两侧）|M2|PYDAO|tests/contract/test_design_import.py"
  "m9-集成面|M9 集成面：页面只经 /gw 取数、不直连库、转发的边界（应通过/应拒绝两侧）|M9|CONSOLE|tests/contract/test_console.py"
  "m1-装库一致|契约②补漏：全新装 DDL 与 DDL+migration 升级必须得到同一套 schema|M1/M3|DAO|tests/contract/test_ddl_migration_parity.py"
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
