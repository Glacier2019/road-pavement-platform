#!/usr/bin/env bash
# =============================================================================
# 一键核查：容器 / 数据库 / 网页端 / 契约测试
#
#   cd scaffold && ./verify.sh            # 全部核查
#   cd scaffold && ./verify.sh db         # 只看数据库
#   cd scaffold && ./verify.sh web        # 只看网页端
#
# 这份脚本**只读**：不写库、不改文件、不重启容器。放心跑。
# =============================================================================
set -uo pipefail

PG_CT=${PG_CT:-rp-pg}
PG_USER=${PG_USER:-rp}
PG_DB=${PG_DB:-road_pavement}
CONSOLE=${CONSOLE:-http://127.0.0.1:8024}
GW=${GW:-http://127.0.0.1:8001}

part=${1:-all}
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# 直接对容器里的 psql 说话，省得你配客户端
psql_() { docker exec -i "$PG_CT" psql -U "$PG_USER" -d "$PG_DB" "$@"; }

# ── 1. 容器 ──────────────────────────────────────────────────────────────────
if [ "$part" = all ] || [ "$part" = containers ]; then
  head_ "① 容器"
  docker ps --filter "name=rp-" --format '  {{.Names}}\t{{.Status}}' | sort
fi

# ── 2. 数据库 ────────────────────────────────────────────────────────────────
if [ "$part" = all ] || [ "$part" = db ]; then
  # ⚠ 数"44 张表"必须用契约目录自己的口径：relkind='r' 且**非分区子表**。
  #   information_schema 会把分区子表和分区父表也数进去（实测 84 = 44 + 39 + 1），
  #   拿 84 去比契约里的 EXPECTED_PHYSICAL_TABLES = 44 会以为对不上。
  head_ "② 数据库（物理表 44 张 = 域内 35 + 跨域 9；另有时序分区子表，属设计使然）"
  psql_ -c "
    select count(*) filter (where not c.relispartition) as 契约口径_物理表数,
           count(*) filter (where c.relispartition)     as 分区子表
      from pg_class c join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public' and c.relkind = 'r';"
  psql_ -c "
    select count(*) as 契约目录期望值 from (select 1) x
     where (select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
             where n.nspname='public' and c.relkind='r' and not c.relispartition) = 44;"

  head_ "③ 你关心的两张设计输入表"
  psql_ -c "
    select 'superelev_transition' as 表, count(*) as 行数 from superelev_transition
    union all select 'roadbed_width', count(*) from roadbed_width;"

  head_ "④ 两表的唯一键 —— 都应当是**桩号**进键"
  psql_ -c "
    select r.relname as 表, c.conname as 约束, c.contype as 类型
      from pg_constraint c join pg_class r on r.oid = c.conrelid
     where r.relname in ('superelev_transition','roadbed_width')
       and c.contype in ('u','p')
     order by 1, 2;"

  head_ "⑤ 超高变化点（A16，76 行；键 = section_id + station_km）"
  psql_ -c "
    select transition_seq as 序号, station_km as 桩号,
           lane_left_pct as 左侧车道横坡, lane_right_pct as 右侧车道横坡,
           hard_shoulder_left_pct as 左硬路肩, hard_shoulder_right_pct as 右硬路肩,
           earth_shoulder_left_pct as 左土路肩, earth_shoulder_right_pct as 右土路肩
      from superelev_transition order by station_km limit 8;"

  head_ "⑥ 路幅宽度（A17，4 行 = 左右各 2 个桩号；一行一个桩号，不是区间）"
  psql_ -c "
    select side as 侧别, seq_no as 行序, group_seq as 组号, station_km as 桩号,
           median_width_m as 中央分隔带, half_carriageway_width_m as 半侧路面,
           extra_lane_flag as 附加车道, hard_shoulder_width_m as 硬路肩,
           earth_shoulder_width_m as 土路肩
      from roadbed_width order by side, station_km;"

  head_ "⑦ 数据现状总览"
  psql_ -c "
    select 'station_sequence' t, count(*) n from station_sequence
    union all select 'alignment_pi', count(*) from alignment_pi
    union all select 'alignment_element', count(*) from alignment_element
    union all select 'profile_grade_point', count(*) from profile_grade_point
    union all select 'profile_ground_point', count(*) from profile_ground_point
    union all select 'superelev_transition', count(*) from superelev_transition
    union all select 'roadbed_width', count(*) from roadbed_width
    union all select 'geometry_point', count(*) from geometry_point;"
fi

# ── 3. 网页端 ────────────────────────────────────────────────────────────────
if [ "$part" = all ] || [ "$part" = web ]; then
  head_ "⑧ 网页端"
  printf '  M9 控制台首页        %s/\n' "$CONSOLE"
  printf '  M9 GE 道路几何页     %s/geometry\n' "$CONSOLE"
  printf '  M6 网关接口文档      %s/docs\n' "$GW"
  printf '  Grafana 监控         http://127.0.0.1:3001\n'
  printf '  MinIO 控制台         http://127.0.0.1:9003\n'
  printf '  EMQX 控制台          http://127.0.0.1:18083\n\n'

  for u in "$CONSOLE/" "$CONSOLE/geometry" "$CONSOLE/healthz" "$GW/healthz"; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$u" || echo 000)
    if [ "$code" = 200 ]; then ok "$code  $u"; else bad "$code  $u"; fi
  done

  head_ "⑨ 控制台健康检查（逐项）"
  curl -s --max-time 5 "$CONSOLE/healthz" | python3 -m json.tool 2>/dev/null \
    || bad "取不到 /healthz"

  head_ "⑩ 几何端点（M6 网关，只读）"
  for p in /v1/geometry/sections \
           /v1/geometry/sections/6/summary \
           /v1/geometry/sections/6/stations?limit=3; do
    echo "  GET $GW$p"
    curl -s --max-time 5 "$GW$p" | head -c 400
    echo
  done
fi

# ── 4. 契约测试 ──────────────────────────────────────────────────────────────
if [ "$part" = all ] || [ "$part" = test ]; then
  head_ "⑪ 契约测试（11 套）"
  if [ -x ./run_contract_tests.sh ]; then
    ./run_contract_tests.sh 2>&1 | tail -18
  else
    bad "找不到 ./run_contract_tests.sh（请在 scaffold/ 下跑）"
  fi
fi

printf '\n核查完毕。\n'
