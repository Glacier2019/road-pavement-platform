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
API_DEPS=(--with fastapi==0.115.6 --with httpx --with "psycopg[binary,pool]==3.2.3")

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
  "design-导入|契约⑤：设计导入 IR + 纬地 .STA/.JD/.pm 适配器 + 落库器（应通过/应拒绝两侧）|M2|PY|tests/contract/test_design_import.py"
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
pass=0; fail=0; skipped=0; failed_names=()

printf '%-12s %-8s %s\n' "契约" "模块" "结果"
echo "---------------------------------------------------------------"

for t in "${TESTS[@]}"; do
  IFS='|' read -r name desc mod deps file <<<"$t"
  [[ -n "$FILTER" && "$name" != *"$FILTER"* ]] && continue

  if [[ ! -f "$file" ]]; then
    printf '%-12s %-8s %s\n' "$name" "$mod" "跳过（文件不存在）"
    ((skipped++)); continue
  fi

  case "$deps" in
    PY)  dep_args=("${PY_DEPS[@]}") ;;
    DAO) dep_args=("${DAO_DEPS[@]}") ;;
    API) dep_args=("${API_DEPS[@]}") ;;
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
if (( fail > 0 )); then
  echo "失败项：${failed_names[*]}"
  echo
  echo "排查建议：各测试的完整输出请单独跑，例如"
  echo "  uv run --with jsonschema --with pyyaml tests/contract/test_governance_contract.py"
  exit 1
fi
echo "全部通过 ✓"
exit 0
