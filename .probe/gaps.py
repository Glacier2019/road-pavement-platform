import urllib.request, json
u = "http://localhost:8000/v1/catalog/gaps"
d = json.loads(urllib.request.urlopen(u, timeout=20).read())
print("逻辑表:", d["logical_table_count"], "| 有数据:", d["with_data_count"], "| 空:", d["empty_count"])
print("unknown_count:", d["unknown_count"])
print("空因分布:", {k: v for k, v in d["reason_counts"].items() if v})
print()
print("=== 前 10 条（排序后）===")
for i in d["items"][:10]:
    print(f'  {i["table"]:<26} {i["empty_reason"]:<26} keys={i["priority_keys"]} owner={i["owner"]}')