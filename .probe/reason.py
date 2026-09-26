import sys
sys.path.insert(0, "/data/cy/shujuku/scaffold/modules/M6-api")
sys.path.insert(0, "/data/cy/shujuku/scaffold/modules/M2-ingest")
import gaps as G
from adapters.weidi import IMPLEMENTED, SEGMENT_FILES
print("geometry_point 在 IMPLEMENTED 里吗:", "geometry_point" in IMPLEMENTED)
print("geometry_point 后缀:", SEGMENT_FILES.get("geometry_point"))
print()
print("实况：.3DR 收到了（design_file 有，parse_status=absent），但没写解析器")
r = G._classify(row_count=0, segments=["geometry_point"],
                seg_facts={"geometry_point": {"suffix": ".3dr",
                                              "implemented": False,
                                              "source_state": "received"}})
print("  -> ", r)
r2 = G._classify(row_count=0, segments=["geometry_point"],
                 seg_facts={"geometry_point": {"suffix": ".3dr",
                                               "implemented": False,
                                               "source_state": "absent"}})
print("  源缺失+未实现 ->", r2)