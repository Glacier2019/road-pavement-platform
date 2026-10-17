#!/usr/bin/env bash
# ============================================================================
# 非契约检查的运行器 —— 读 checks.tsv，跑，如实报三态。
#
# 为什么要有它：
#   有一类检查要 console 起着、要真接口，跑不了那么干净。此前它们靠**人记得跑**，
#   于是会静默腐烂。有了册子和入口，才谈得上「谁没跑」。
#
# ★★ 本脚本**不知道「console」是什么**。
#      前置满不满足，由**检查自己**判定（它退 2）。运行器只把 2 认成 SKIP。
#      ⇒ 将来加第五个服务、第十条检查，这个文件一行都不用改。
#      这就是它能在别人机器上跑的原因：它不含任何本项目的知识。
#
# 用法:
#   ./run_checks.sh                跑全部
#   ./run_checks.sh --list         只列清单
#   ./run_checks.sh --offline      只跑不需要前置的（CI 可跑）
#   ./run_checks.sh 深链互认        只跑选中的（可给多个）
#   ./run_checks.sh --base-url http://10.0.0.5:8000
#
# ★ 退出码（与检查本身的约定一致）:
#     0  全部跑过
#     1  有检查跑了但没过
#     2  有检查**没跑成**（前置不足）—— 默认算不通过
#     3  用法错
#
# ★★ 为什么「没跑成」默认算不通过：
#      不加 --allow-skip 时，SKIP 会让本次运行非零退出。
#      否则「栈没起」会长得和「全绿」一模一样 —— 正是本仓反复栽的那类假绿。
#      要显式放行，用 --allow-skip。
# ============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"          # 仓库根，与调用方 cwd 无关
# ★ 清单路径可覆盖。契约门 test_live_checks.py 用它指向一份**临时清单**，
#   实测三态（通过/失败/没跑成）各自的退出码 —— 那比读源码可信。
MANIFEST="${CHECK_MANIFEST:-$HERE/checks.tsv}"

BASE_URL="${CHECK_BASE_URL:-http://127.0.0.1:8024}"
ALLOW_SKIP=0
OFFLINE_ONLY=0
LIST_ONLY=0
WANTED=()

die() { echo "用法错：$*" >&2; echo "  跑 ./run_checks.sh --help 看用法" >&2; exit 3; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --list)      LIST_ONLY=1; shift ;;
    --offline)   OFFLINE_ONLY=1; shift ;;
    --allow-skip) ALLOW_SKIP=1; shift ;;
    --base-url)
      [[ $# -ge 2 ]] || die "--base-url 后面要给地址"
      BASE_URL="$2"; shift 2 ;;
    --base-url=*) BASE_URL="${1#*=}"; shift ;;
    -h|--help)
      sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    -*) die "不认识的选项 $1" ;;
    *)  WANTED+=("$1"); shift ;;
  esac
done

[[ -f "$MANIFEST" ]] || die "找不到清单 $MANIFEST"

# ---------------------------------------------------------------- 读清单
# 字段以**竖线**分隔。
# ★★ 为什么不是 TAB：bash 的 `read` 把 IFS 空白类字符（TAB 属此类）的
#    **连续分隔符折成一个**，于是「前置」为空的那一行整行**错位一格** ——
#    命令槽拿到说明、前置槽拿到命令。它不报错，只给一个看不懂的结果。
#    竖线不是空白类，空字段因此保得住；代价是前后空格要自己修。
trim() { local x="$1"; x="${x#"${x%%[![:space:]]*}"}"; x="${x%"${x##*[![:space:]]}"}"; printf '%s' "$x"; }

NAMES=(); DESCS=(); NEEDS=(); CMDS=()
lineno=0
while IFS= read -r line; do
  lineno=$((lineno+1))
  [[ -z "$(trim "$line")" || "$line" == \#* ]] && continue
  # 字段数硬校验：少一个或多一个都**当场报错**，不让它错位着往下跑。
  # ★ 错位不报错、只给一个看不懂的结果 —— 那比崩溃难查得多。
  bars="${line//[^|]/}"
  if (( ${#bars} != 3 )); then
    die "清单第 $lineno 行有 ${#bars} 个竖线（应为 3）：$line"
  fi
  IFS='|' read -r n d need cmd <<< "$line"
  n="$(trim "$n")"; d="$(trim "$d")"; need="$(trim "$need")"; cmd="$(trim "$cmd")"
  [[ -z "$n" ]]   && die "清单第 $lineno 行没有名字：$line"
  [[ -z "$cmd" ]] && die "清单第 $lineno 行没有命令：$line"
  NAMES+=("$n"); DESCS+=("$d"); NEEDS+=("$need"); CMDS+=("$cmd")
done < "$MANIFEST"

N=${#NAMES[@]}
(( N > 0 )) || die "清单是空的（$MANIFEST）"

# ---------------------------------------------------------------- --list
if (( LIST_ONLY )); then
  echo "非契约检查清单（$MANIFEST）—— 共 $N 条"
  echo
  for (( i=0; i<N; i++ )); do
    pre="${NEEDS[$i]}"; [[ -z "$pre" ]] && pre="无（离线）"
    printf '  %-10s 前置 %-8s %s\n' "${NAMES[$i]}" "$pre" "${CMDS[$i]}"
    printf '             %s\n' "${DESCS[$i]}"
  done
  echo
  echo "★ 契约门在 run_contract_tests.sh 的 TESTS= 里（离线、进 CI）。本表各管一类，不重叠。"
  exit 0
fi

# ---------------------------------------------------------------- 选谁
SELECTED=()
if (( ${#WANTED[@]} == 0 )); then
  for (( i=0; i<N; i++ )); do SELECTED+=("$i"); done
else
  for w in "${WANTED[@]}"; do
    hit=-1
    for (( i=0; i<N; i++ )); do [[ "${NAMES[$i]}" == "$w" ]] && { hit=$i; break; }; done
    (( hit >= 0 )) || die "清单里没有叫「$w」的检查。--list 看全部"
    SELECTED+=("$hit")
  done
fi

# ★ --offline：跳过所有带前置的。带前置的就是跑不了干净的那些。
if (( OFFLINE_ONLY )); then
  KEEP=()
  for i in "${SELECTED[@]}"; do [[ -z "${NEEDS[$i]}" ]] && KEEP+=("$i"); done
  SELECTED=("${KEEP[@]:-}")
  # bash 空数组展开会留下一个空串，过滤掉
  TMP=(); for i in "${SELECTED[@]}"; do [[ -n "$i" ]] && TMP+=("$i"); done; SELECTED=("${TMP[@]:-}")
fi

(( ${#SELECTED[@]} > 0 )) || { echo "没有选中任何检查（--offline 时可能全部都有前置）"; exit 0; }

# ---------------------------------------------------------------- 跑
# ★ 地址统一从 CHECK_BASE_URL 走。检查自己读它，运行器不替它们拼 URL。
export CHECK_BASE_URL="$BASE_URL"
unset GEO_BASE 2>/dev/null || true   # 旧名，别再让两条路并存

echo "非契约检查 —— 共 ${#SELECTED[@]} 条　上游 $BASE_URL"
echo "------------------------------------------------------------------------"

npass=0; nfail=0; nskip=0
failed_names=(); skipped_names=()

cd "$ROOT" || die "进不去仓库根 $ROOT"

for i in "${SELECTED[@]}"; do
  name="${NAMES[$i]}"; cmd="${CMDS[$i]}"; need="${NEEDS[$i]}"

  # 命令的第一个词是文件本身。相对路径按**仓库根**解析（与调用方 cwd 无关）；
  # ★ 绝对路径照用 —— 装在别处（/opt/…）的检查也该能登记进来。
  set -- $cmd
  target="$1"
  case "$target" in
    /*) full="$target" ;;
    *)  full="$ROOT/$target" ;;
  esac
  if [[ ! -f "$full" ]]; then
    printf '  %-10s %s\n' "$name" "✗ 找不到 $full"
    nfail=$((nfail+1)); failed_names+=("$name")
    continue
  fi

  case "$target" in
    *.mjs|*.js) runner=(node) ;;
    *.py)       runner=(python3) ;;
    *)          runner=() ;;
  esac

  out="$("${runner[@]}" "$full" "${@:2}" 2>&1)"; rc=$?
  last="$(echo "$out" | tail -1)"

  case "$rc" in
    0) printf '  %-10s %s\n' "$name" "✓ ${last:-通过}"
       npass=$((npass+1)) ;;
    2) printf '  %-10s %s\n' "$name" "○ 没跑成（前置不足${need:+：$need}）　$last"
       nskip=$((nskip+1)); skipped_names+=("$name") ;;
    *) printf '  %-10s %s\n' "$name" "✗ ${last:-失败（退出码 $rc）}"
       nfail=$((nfail+1)); failed_names+=("$name") ;;
  esac
done

echo "------------------------------------------------------------------------"
line="通过 $npass ｜ 失败 $nfail ｜ 跳过 $nskip"
if (( nskip > 0 )) && (( ! ALLOW_SKIP )); then
  line+="　（★ 跳过未放行 —— 默认算不通过。确知前置缺失时用 --allow-skip）"
fi
echo "$line"

(( nfail > 0 )) && { echo "失败：${failed_names[*]}"; exit 1; }
(( nskip > 0 )) && (( ! ALLOW_SKIP )) && { echo "跳过：${skipped_names[*]}"; exit 2; }
exit 0
