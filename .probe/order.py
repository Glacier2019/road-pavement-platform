import urllib.request, json
d = json.loads(urllib.request.urlopen(
    "http://localhost:8000/gw/v1/catalog/gaps?empty_only=true", timeout=25).read())
ks = [tuple(i["priority_keys"]) for i in d["items"]]
print("三键单调不减（可复现排序）:", ks == sorted(ks))
print()
print("全 26 张空表，页面次序：")
for i in d["items"]:
    print("  {:24} {:26} {:4} {}".format(
        i["table"], i["empty_reason"], str(i["owner"]), i["priority_keys"]))