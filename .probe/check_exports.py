import ast
src = open("scaffold/modules/M3-rpdao/rpdao/__init__.py").read()
tree = ast.parse(src)
imported = set()
for n in ast.walk(tree):
    if isinstance(n, ast.ImportFrom):
        for a in n.names: imported.add(a.asname or a.name)
    elif isinstance(n, ast.Import):
        for a in n.names: imported.add((a.asname or a.name).split(".")[0])
allnames = None
for n in tree.body:
    if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "__all__":
        allnames = [e.value for e in n.value.elts]
missing = [x for x in allnames if x not in imported and x != "__version__"]
print("__all__ 共", len(allnames), "个名字")
print("声明了却没 import 的:", missing or "无")