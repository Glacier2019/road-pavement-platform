#!/usr/bin/env bash
# =============================================================================
# WIM 全链端到端冒烟测试
# -----------------------------------------------------------------------------
# 它做的事只有一件：**把一条真的 WIM 报文推进活着的链路，然后逐跳问"到了吗"。**
#
# 与另两个脚本的分工（三者互补，不重复）：
#   · run_contract_tests.sh —— **离线**。不启容器、不连库，只查源文件与契约是否自洽。
#   · verify.sh             —— **只读**。看容器在不在、库里有几张表、网页通不通。
#   · 本脚本                 —— **在线**。真发 MQTT、真写库、真读 API，然后清理干净。
#
# 为什么必须有这一个：
#   前两个都绿，链路仍然可以是断的。契约测试查的是**源文件里的字符串**，
#   而线上跑的是**容器里已构建的镜像** —— 两者可以不一致，且没有任何东西会提醒你。
#   这不是假想：本仓就发生过契约测试 18/18 全绿、而部署的容器缺少已提交端点。
#   一个只读源文件的检查，永远无法回答"报文到了吗"。
#
# 用法：
#     cd scaffold
#     ./run_e2e_smoke.sh              # 跑
#     ./run_e2e_smoke.sh --keep       # 跑完不清理（留给人工看库里的行）
#     ./run_e2e_smoke.sh --verbose    # 打印每一条 curl / psql 的原始返回
#
# 退出码：
#     0  全链通过
#     1  某一跳断了（脚本会明确打印断在第几跳）
#     2  前置条件不满足（容器没起 / 依赖缺失）—— 这**不是**通过
#
# 前置：
#     docker compose 已起 pg / mqtt / ingest / api / console / grafana
#     python3 能 import paho.mqtt.client
# =============================================================================
set -uo pipefail

cd "$(dirname "$0")"

# ── 参数 ────────────────────────────────────────────────────────────────────
KEEP=0
VERBOSE=0
for a in "$@"; do
  case "$a" in
    --keep)    KEEP=1 ;;
    --verbose) VERBOSE=1 ;;
    -h|--help) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$a（用 --help 看用法）" >&2; exit 2 ;;
  esac
done

# ── 配置（都可用环境变量覆盖）───────────────────────────────────────────────
PG_CT=${PG_CT:-rp-pg}
PG_USER=${PG_USER:-rp}
PG_DB=${PG_DB:-road_pavement}
MQTT_HOST=${MQTT_HOST:-127.0.0.1}
MQTT_PORT=${MQTT_PORT:-18883}
TOPIC=${TOPIC:-g228/lo/wim01/axle}
DEVICE=${DEVICE:-WIM01}
API=${API:-http://127.0.0.1:8001}
CONSOLE=${CONSOLE:-http://127.0.0.1:8024}
GRAFANA=${GRAFANA:-http://127.0.0.1:3001}
INGEST_CT=${INGEST_CT:-rp-ingest}
WAIT_S=${WAIT_S:-15}          # 每一跳最多等多久
# paho 的位置。优先用调用者给的 PYTHONPATH，其次仓库内约定目录。
export PYTHONPATH="${PYTHONPATH:-}:$PWD/../.pylibs"

# ── 输出 ────────────────────────────────────────────────────────────────────
HOP=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
hop()  { ((HOP++)); printf '\n\033[1m第 %d 跳  %s\033[0m\n' "$HOP" "$*"; }
info() { printf '      %s\n' "$*"; }
V()    { [ "$VERBOSE" = 1 ] && printf '      \033[2m%s\033[0m\n' "$*"; return 0; }

fail_at() {   # fail_at <说明>
  echo
  printf '\033[31m✗ 全链在第 %d 跳断了：%s\033[0m\n' "$HOP" "$1"
  echo "  上面那一跳的最后几条输出就是病根所在。"
  exit 1
}

psql_() { docker exec -i "$PG_CT" psql -U "$PG_USER" -d "$PG_DB" -t -A "$@"; }

# ── 本次运行的唯一标记 ──────────────────────────────────────────────────────
# ★ 为什么需要它：M2 的去重键是 (device_code, seq)，而造数器的 seq 每次从 0 重来。
#   直接重跑造数器会被当成重复报文静默丢掉 —— 你会以为"链路没通"，
#   其实是被自己的去重挡了。所以冒烟必须用**本次运行独有**的 seq 与车牌。
STAMP=$(date +%s)
MARK="SMK$(printf '%04x' $(( (STAMP ^ $$) & 0xFFFF )))"     # 车牌，≤16 字符
SEQ=$(( (STAMP % 1000000000) ))                              # seq，本次运行独有
TS=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)

echo "==============================================================================="
echo " WIM 全链端到端冒烟"
echo "   标记车牌 = $MARK      seq = $SEQ"
echo "   目标     = $MQTT_HOST:$MQTT_PORT  topic=$TOPIC  device=$DEVICE"
echo "==============================================================================="

# ═════════════════════════════════════════════════════════════════════════════
hop "前置：链路容器在跑"
# ─────────────────────────────────────────────────────────────────────────────
missing=()
for c in rp-pg rp-mqtt rp-ingest rp-api rp-console rp-grafana; do
  st=$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null || echo missing)
  if [ "$st" = "true" ]; then ok "$c 在跑"; else bad "$c 未运行（$st）"; missing+=("$c"); fi
done
if (( ${#missing[@]} > 0 )); then
  echo
  echo "  前置不满足：${missing[*]}"
  echo "  先起栈：cd scaffold && docker compose -f docker-compose.skeleton.yml up -d"
  echo "  ⚠ 这不是「通过」，是「没跑成」。"
  exit 2
fi
python3 -c 'import paho.mqtt.client' 2>/dev/null \
  && ok "python3 能 import paho.mqtt.client" \
  || { bad "python3 缺 paho.mqtt.client（PYTHONPATH=$PYTHONPATH）"; exit 2; }

# ═════════════════════════════════════════════════════════════════════════════
hop "基线：数一下冒烟前的行数"
# ─────────────────────────────────────────────────────────────────────────────
BASE=$(psql_ -c "SELECT count(*) FROM wim_axle_record;")
BASE_DETAIL=$(psql_ -c "SELECT count(*) FROM wim_axle_detail;")
ok "wim_axle_record 现有 $BASE 行；wim_axle_detail 现有 $BASE_DETAIL 行"
if psql_ -c "SELECT count(*) FROM wim_axle_record WHERE plate_no='$MARK';" | grep -qv '^0$'; then
  fail_at "车牌 $MARK 已存在（上一次 --keep 留下的？先手工删掉再跑）"
fi

# ═════════════════════════════════════════════════════════════════════════════
hop "发报文：往 $TOPIC 推一条 wim_axle.v1"
# ─────────────────────────────────────────────────────────────────────────────
# 4 轴货车，总重 32800 kg（含超载），字段口径照 contracts/messages/wim_axle.v1.schema.json
#
# ★ axle_type_code 是**枚举**，只能取 A2 / T3 / T4 / T5 / T6（dict_axle_type）。
#   我第一版随手写了 "1+2+2+2"（轴组排布写法），ingest 当场按契约违约拒收。
#   留这段注释是因为它正好说明本脚本的用处：
#   编造的报文活不过第一跳，而拒收原因会直接打在屏幕上。
PAYLOAD=$(cat <<JSON
{
  "device_code": "$DEVICE",
  "ts": "$TS",
  "seq": $SEQ,
  "schema_version": "wim_axle.v1",
  "payload": {
    "lane_no": 2, "direction": "up", "axle_type_code": "T4",
    "axle_num": 4, "speed_kmh": 62.5, "gross_weight_kg": 32800.0,
    "plate_no": "$MARK",
    "axles": [
      {"axle_seq": 1, "weight_kg": 6200.0, "dist_mm": 0},
      {"axle_seq": 2, "weight_kg": 9800.0, "group_seq": 2, "dist_mm": 3600},
      {"axle_seq": 3, "weight_kg": 8500.0, "group_seq": 2, "dist_mm": 1350},
      {"axle_seq": 4, "weight_kg": 8300.0, "group_seq": 3, "dist_mm": 1350}
    ]
  }
}
JSON
)
V "$PAYLOAD"

pub_out=$(MARK="$MARK" SEQ="$SEQ" TS="$TS" DEVICE="$DEVICE" TOPIC="$TOPIC" \
          MQTT_HOST="$MQTT_HOST" MQTT_PORT="$MQTT_PORT" PAYLOAD="$PAYLOAD" \
          python3 - <<'PY' 2>&1
import os, sys, time
import paho.mqtt.client as mqtt
got = {}
c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"smoke-{os.getpid()}")
c.connect(os.environ["MQTT_HOST"], int(os.environ["MQTT_PORT"]), 30)
c.loop_start()
r = c.publish(os.environ["TOPIC"], os.environ["PAYLOAD"], qos=1)
r.wait_for_publish(timeout=10)
time.sleep(0.3)
c.loop_stop(); c.disconnect()
if r.rc != mqtt.MQTT_ERR_SUCCESS:
    print(f"publish rc={r.rc}"); sys.exit(1)
print(f"published mid={r.mid} qos=1")
PY
)
pub_rc=$?
V "$pub_out"
if [ $pub_rc -ne 0 ]; then fail_at "MQTT 发布失败：$pub_out"; fi
ok "已发布（qos 1，已确认送达 broker）：$(echo "$pub_out" | tail -1)"

# ═════════════════════════════════════════════════════════════════════════════
hop "落库：M2 ingest 是否把主记录写进 wim_axle_record"
# ─────────────────────────────────────────────────────────────────────────────
# ★ 轮询而不是 sleep 固定秒数：固定等待要么慢要么脆。
row=""
for _ in $(seq "$WAIT_S"); do
  row=$(psql_ -c "SELECT id||'|'||cross_section_id||'|'||speed_kmh||'|'||gross_weight_kg||'|'||overload_flag||'|'||overload_rate||'|'||esal||'|'||data_source||'|'||quality_code FROM wim_axle_record WHERE plate_no='$MARK';")
  [ -n "$row" ] && break
  sleep 1
done
if [ -z "$row" ]; then
  echo "      最近 20 行 ingest 日志："
  docker logs --tail 20 "$INGEST_CT" 2>&1 | sed 's/^/        /'
  fail_at "等 ${WAIT_S}s 也没等到 $MARK 落库 —— MQTT→ingest→pg 这一跳断了"
fi
IFS='|' read -r RID RCS RSPD RGW ROVL ROVR RESAL RSRC RQC <<<"$row"
ok "主记录已落库 id=$RID"
info "cross_section_id=$RCS  speed=$RSPD km/h  总重=$RGW kg  超载=$ROVL ($ROVR%)  ESAL=$RESAL"
info "data_source=$RSRC  quality_code=$RQC"

# ═════════════════════════════════════════════════════════════════════════════
hop "派生：重算字段是不是 ingest 自己算出来的（不是我们传的）"
# ─────────────────────────────────────────────────────────────────────────────
# ★ 这一跳在钉"入库时重算"这件事：报文里**没有** overload_flag / overload_rate / esal
#   三个字段，它们必须由 M2 从总重与轴组推出来。若哪天有人把它们改成直接透传，
#   这一跳会响。
if [ "$ROVL" = "t" ] || [ "$ROVL" = "true" ]; then ok "overload_flag=$ROVL（32800 kg 判为超载，合理）"; else
  fail_at "overload_flag=$ROVL，但总重 32800 kg 应判超载 —— 重算逻辑变了？"; fi
if awk "BEGIN{exit !($ROVR > 0)}"; then ok "overload_rate=$ROVR% > 0（由总重推出，非报文透传）"; else
  fail_at "overload_rate=$ROVR 应为正数"; fi
if awk "BEGIN{exit !($RESAL > 0)}"; then ok "esal=$RESAL > 0（由轴组推出，非报文透传）"; else
  fail_at "esal=$RESAL 应为正数"; fi

# ═════════════════════════════════════════════════════════════════════════════
hop "原子性：轴组明细是否与主记录同事务落库"
# ─────────────────────────────────────────────────────────────────────────────
# ★ 主记录有、明细没有，是一条**下游看不出来的坏数据**：它看起来完全合法。
#   所以条数必须等于 axle_num（本报文 4）。
DCNT=$(psql_ -c "SELECT count(*) FROM wim_axle_detail WHERE record_id=$RID;")
if [ "$DCNT" = "4" ]; then ok "wim_axle_detail 有 4 条，等于 axle_num"; else
  fail_at "wim_axle_detail 有 $DCNT 条，应为 4 条 —— 主记录与明细没有同事务"; fi
DSUM=$(psql_ -c "SELECT sum(axle_weight_kg) FROM wim_axle_detail WHERE record_id=$RID;")
if awk "BEGIN{exit !($DSUM == 32800)}"; then ok "轴重合计 $DSUM kg = 总重 32800 kg（自洽）"; else
  fail_at "轴重合计 $DSUM kg ≠ 总重 32800 kg"; fi
DETAILS=$(psql_ -c "SELECT axle_seq||':'||axle_weight_kg||':'||coalesce(axle_dist_mm::text,'-') FROM wim_axle_detail WHERE record_id=$RID ORDER BY axle_seq;" | tr '\n' ' ')
info "明细 $DETAILS"

# ═════════════════════════════════════════════════════════════════════════════
hop "去重：同一条报文再发一次，必须只留一行"
# ─────────────────────────────────────────────────────────────────────────────
# ★ 这条**必须**有：去重键是 (device_code, seq)，契约又规定 clean_session=false
#   （真设备重连会重放）。去重一旦坏掉，上游每重连一次就多一条过车记录，
#   而下游完全看不出来 —— 过车数、ESAL、超载率会一起虚高。
MARK="$MARK" SEQ="$SEQ" TS="$TS" DEVICE="$DEVICE" TOPIC="$TOPIC" \
MQTT_HOST="$MQTT_HOST" MQTT_PORT="$MQTT_PORT" PAYLOAD="$PAYLOAD" \
python3 - <<'PY' >/dev/null 2>&1
import os, time
import paho.mqtt.client as mqtt
c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"smoke-dup-{os.getpid()}")
c.connect(os.environ["MQTT_HOST"], int(os.environ["MQTT_PORT"]), 30)
c.loop_start()
r = c.publish(os.environ["TOPIC"], os.environ["PAYLOAD"], qos=1)
r.wait_for_publish(timeout=10); time.sleep(0.3)
c.loop_stop(); c.disconnect()
PY
sleep 3
DUP=$(psql_ -c "SELECT count(*) FROM wim_axle_record WHERE plate_no='$MARK';")
if [ "$DUP" = "1" ]; then ok "重发后仍是 1 行，去重生效"; else
  fail_at "重发后变成 $DUP 行 —— (device_code, seq) 去重失效，重连会造出重复过车记录"; fi
# 光看行数还不够：行数没变也可能因为报文根本没到。日志必须留下去重那一条。
if docker logs --tail 60 "$INGEST_CT" 2>&1 | grep -q "重复报文.*seq=$SEQ"; then
  ok "ingest 日志里有本次 seq=$SEQ 的去重记录（证明确实收到了）"
else
  echo "      最近 10 行 ingest 日志："
  docker logs --tail 10 "$INGEST_CT" 2>&1 | sed 's/^/        /'
  fail_at "行数没变，但日志里找不到 seq=$SEQ 的去重记录 —— 无法区分「被去重」和「根本没收到」"
fi

# ═════════════════════════════════════════════════════════════════════════════
hop "注册闸门：未注册设备发来的报文必须被拒"
# ─────────────────────────────────────────────────────────────────────────────
# ★ 用不存在的 device_code。注意选它是**故意的**：ingest 在设备未注册时
#   "无法写质量日志"（见 write_reject），所以这一跳不会在库里留下垃圾。
BEFORE=$(psql_ -c "SELECT count(*) FROM wim_axle_record;")
MARK="$MARK" SEQ="$((SEQ+1))" TS="$TS" TOPIC="$TOPIC" \
MQTT_HOST="$MQTT_HOST" MQTT_PORT="$MQTT_PORT" \
PAYLOAD="$(echo "$PAYLOAD" | sed 's/"device_code": "[^"]*"/"device_code": "NOSUCHDEV"/')" \
python3 - <<'PY' >/dev/null 2>&1
import os, time
import paho.mqtt.client as mqtt
c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"smoke-unreg-{os.getpid()}")
c.connect(os.environ["MQTT_HOST"], int(os.environ["MQTT_PORT"]), 30)
c.loop_start()
r = c.publish(os.environ["TOPIC"], os.environ["PAYLOAD"], qos=1)
r.wait_for_publish(timeout=10); time.sleep(0.3)
c.loop_stop(); c.disconnect()
PY
sleep 3
AFTER=$(psql_ -c "SELECT count(*) FROM wim_axle_record;")
if [ "$BEFORE" = "$AFTER" ]; then ok "行数未变（$BEFORE → $AFTER），未注册设备被挡住"; else
  fail_at "行数从 $BEFORE 变成 $AFTER —— 未注册设备的报文竟然入库了"; fi
if docker logs --tail 60 "$INGEST_CT" 2>&1 | grep -q "设备未注册"; then
  ok "ingest 日志里有「设备未注册」告警"
else
  info "⚠ 日志里没找到「设备未注册」—— 行数是对的，但拒收原因没留痕"
fi

# ═════════════════════════════════════════════════════════════════════════════
hop "出库：M6 API 详情接口读得到这条"
# ─────────────────────────────────────────────────────────────────────────────
DET=$(curl -s -m 10 "$API/v1/objects/wim_axle/$RID")
V "$DET"
# ★ 用 JSON 解析断言，不用 grep 拼字符串。
#   第一版我写的是 grep '"object_type": "wim_axle_record"'（带空格），
#   而 FastAPI 输出的是紧凑 JSON（无空格）—— 于是断言失败，而接口其实是对的。
#   字符串匹配会把「格式变了」误报成「功能坏了」。
#
# ★ 用 <<'PY'（带引号的 heredoc）而不是 python3 -c '...'：
#   后者要把 Python 塞进单引号里，f-string 里一出现反斜杠就 SyntaxError。
#   带引号的 heredoc 不做任何 shell 展开，Python 源码可以照常写。
det_ok=$(DET="$DET" RID="$RID" MARK="$MARK" python3 - <<'PY' 2>&1
import json, os, sys
try:
    d = json.loads(os.environ["DET"])
except Exception as e:
    print("JSON 解析失败: %s" % e); sys.exit(1)
row = d.get("item", d)
want_id, want_plate = os.environ["RID"], os.environ["MARK"]
if str(row.get("id")) != want_id:
    print("id=%s 应为 %s" % (row.get("id"), want_id)); sys.exit(1)
if row.get("plate_no") != want_plate:
    print("plate_no=%s 应为 %s" % (row.get("plate_no"), want_plate)); sys.exit(1)
print("id=%s plate_no=%s data_source=%s" % (row["id"], row["plate_no"], row.get("data_source")))
PY
)
if [ $? -eq 0 ]; then ok "详情接口: $det_ok"; else fail_at "详情接口返回不对：$det_ok"; fi

# ═════════════════════════════════════════════════════════════════════════════
hop "出库：列表接口的最新一条就是它"
# ─────────────────────────────────────────────────────────────────────────────
LST=$(curl -s -m 10 "$API/v1/objects/wim_axle?limit=1")
V "$LST"
lst_ok=$(LST="$LST" MARK="$MARK" python3 - <<'PY' 2>&1
import json, os, sys
try:
    d = json.loads(os.environ["LST"])
except Exception as e:
    print("JSON 解析失败: %s" % e); sys.exit(1)
if d.get("object_type") != "wim_axle_record":
    print("object_type=%s" % d.get("object_type")); sys.exit(1)
items = d.get("items") or []
if not items:
    print("items 为空"); sys.exit(1)
want = os.environ["MARK"]
if items[0].get("plate_no") != want:
    print("最新一条 plate_no=%s 应为 %s" % (items[0].get("plate_no"), want)); sys.exit(1)
print("object_type=%s count=%s 最新 id=%s" % (d["object_type"], d.get("count"), items[0].get("id")))
PY
)
if [ $? -eq 0 ]; then ok "列表接口: $lst_ok"; else fail_at "列表接口返回不对：$lst_ok"; fi

# ═════════════════════════════════════════════════════════════════════════════
hop "出库：指标接口把这条算进了本小时"
# ─────────────────────────────────────────────────────────────────────────────
MET=$(curl -s -m 10 "$API/v1/metrics/wim_hourly")
V "$MET"
HOUR=$(date +%Y-%m-%dT%H)
if echo "$MET" | grep -q "$HOUR"; then ok "wim_hourly 含本小时桶 $HOUR*"; else
  fail_at "wim_hourly 里找不到本小时桶 $HOUR：$(echo "$MET" | head -c 300)"; fi

# ═════════════════════════════════════════════════════════════════════════════
hop "展示层：M9 控制台与 Grafana 在线"
# ─────────────────────────────────────────────────────────────────────────────
CC=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$CONSOLE/")
[ "$CC" = "200" ] && ok "console $CONSOLE → 200" || fail_at "console 返回 $CC"
GH=$(curl -s -m 10 "$GRAFANA/api/health")
V "$GH"
# ★★ 这里我连着错了两次，两次都是**同一个病**：拿 grep 去匹配 JSON。
#   第一次：API 返回紧凑 JSON（无空格），我按带空格匹配 → 误报失败。
#   第二次：Grafana 返回 pretty JSON（带空格），我按紧凑匹配 → 又误报失败。
#   两次接口都是好的，两次都是断言坏了。
#   教训：**JSON 只能解析，不能拿字符串去猜。** 序列化格式是实现的自由，
#   断言不该依赖它。所以这里和上面两处一样，一律 json.loads。
gh_ok=$(GH="$GH" python3 - <<'PY' 2>&1
import json, os, sys
try:
    d = json.loads(os.environ["GH"])
except Exception as e:
    print("JSON 解析失败: %s" % e); sys.exit(1)
if d.get("database") != "ok":
    print("database=%s 应为 ok" % d.get("database")); sys.exit(1)
print("database=ok version=%s" % d.get("version"))
PY
)
if [ $? -eq 0 ]; then ok "grafana: $gh_ok"; else fail_at "grafana 健康检查异常：$gh_ok"; fi

# ═════════════════════════════════════════════════════════════════════════════
hop "清理：把本次写入的行删掉"
# ─────────────────────────────────────────────────────────────────────────────
if [ "$KEEP" = 1 ]; then
  info "--keep：保留 id=$RID（车牌 $MARK），人工看完再手工删"
else
  # 先删明细再删主记录：wim_axle_detail 有复合外键指向 wim_axle_record。
  psql_ -c "DELETE FROM wim_axle_detail WHERE record_id=$RID;" >/dev/null
  psql_ -c "DELETE FROM wim_axle_record WHERE id=$RID;" >/dev/null
  N1=$(psql_ -c "SELECT count(*) FROM wim_axle_record;")
  N2=$(psql_ -c "SELECT count(*) FROM wim_axle_detail;")
  if [ "$N1" = "$BASE" ] && [ "$N2" = "$BASE_DETAIL" ]; then
    ok "已清理干净（主记录 $BASE 行、明细 $BASE_DETAIL 行，与冒烟前一致）"
  else
    fail_at "清理后行数对不上：主记录 $N1（应 $BASE）、明细 $N2（应 $BASE_DETAIL）"
  fi
fi

echo
echo "==============================================================================="
printf '\033[32m✓ WIM 全链端到端冒烟通过（%d 跳全部命中）\033[0m\n' "$HOP"
echo "  MQTT → ingest → pg → API 详情/列表/指标 → console/grafana，逐跳都有实测证据。"
echo "==============================================================================="
exit 0
