import sys, json
sys.path.insert(0, "/data/cy/shujuku/scaffold/modules/M6-api")
sys.path.insert(0, "/data/cy/shujuku/scaffold/modules/M2-ingest")
import gaps
from adapters.weidi import segment_tables_map, IMPLEMENTED, CAPABILITIES, SEGMENT_FILES

seg_tables = segment_tables_map()
# 段事实：source_state 由 design_file 决定（这里先用实测结果）
received = {"ok": "received", "pending": "pending", "absent": "absent", "blocked": "received"}
# .tsftxt 从未收到；.tsf 收到但 pending
seg_facts = {}
for seg in set(CAPABILITIES) | set(seg_tables):
    suf = SEGMENT_FILES.get(seg, ("(none)", ""))[0]
    if suf == ".tsftxt":
        st = "absent"   # 该后缀从未收到
    else:
        st = "received"
    seg_facts[seg] = {"suffix": suf, "implemented": seg in IMPLEMENTED, "source_state": st}

# 五张土方表：源是什么状态？
for t in ["earthwork_transfer","borrow_pit","spoil_pit","earthwork_haul_stat","earthwork_fill_stat"]:
    f = seg_facts[t]
    print(f"{t:<22} implemented={f['implemented']} source_state={f['source_state']}")

# 现在测 _classify
print()
print("=== 判定 ===")
for t in ["earthwork_transfer","borrow_pit"]:
    r = gaps._classify(row_count=0, segments=[t], seg_facts=seg_facts)
    print(f"  {t:<22} -> {r}")
# 有数据的表
print(f"  {'road_line'}             -> {gaps._classify(row_count=5, segments=[], seg_facts=seg_facts)}")
# 下游产出表
print(f"  {'alarm_record'}         -> {gaps._classify(row_count=0, segments=[], seg_facts=seg_facts)}")