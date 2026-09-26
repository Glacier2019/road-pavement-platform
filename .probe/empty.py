import urllib.request, json
d = json.loads(urllib.request.urlopen("http://localhost:8000/v1/catalog/gaps?empty_only=true", timeout=20).read())
print("空表数:", len(d["items"]))
print()
for i in d["items"]:
    print(f'{i["table"]:<26} {i["empty_reason"]:<26} owner={str(i["owner"]):<4} {i["action"]}')