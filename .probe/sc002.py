import urllib.request, json
d = json.loads(urllib.request.urlopen("http://localhost:8000/v1/catalog/gaps", timeout=20).read())
FIVE = ["earthwork_transfer","borrow_pit","spoil_pit","earthwork_haul_stat","earthwork_fill_stat"]
print("=== SC-002 硬断言 ===")
bad = []
for t in FIVE:
    it = next((i for i in d["items"] if i["table"] == t), None)
    if it is None:
        bad.append((t, "表中不存在")); continue
    ok = it["empty_reason"] not in ("module_not_built", "source_absent")
    print(f'  {t:<22} {it["empty_reason"]:<26} {"PASS" if ok else "FAIL"}')
    print(f'     -> {it["action"]}')
    print(f'     -> 段={it["segments"]} 后缀={it["suffixes"]} source_present={it["source_present"]}')
    if not ok: bad.append((t, it["empty_reason"]))
print()
print("判错的:", bad or "无 ✓")
print()
print("=== 被误判成 module_not_built 的表 ===")
for i in d["items"]:
    if i["empty_reason"] == "module_not_built":
        print(f'  {i["table"]}: 段={i["segments"]} 后缀={i["suffixes"]}')