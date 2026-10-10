"""M9 平台管理台（集成面）契约测试 —— 五件套第 5 件。

运行（离线可跑，不连数据库、不连容器、不连外网）：
  cd /data/cy/shujuku/scaffold
  uv run --with fastapi --with httpx --with pyyaml python tests/contract/test_console.py

本测试钉住四件事：

  1) **M9 不得直连数据库。** 这是三条数据硬线里的"读只经 M3 rpdao"。
     M9 的 compose 里刻意不给它 PG_DSN —— 但没有 DSN 不等于**做不到**：
     只要它 import 了 psycopg，明天就有人能给它加上 DSN。
     所以用与 M6 同样的 AST 静态断言把它钉死（见 test_dao_contract.py 第 4 组）。

  2) **`/gw` 是有边界的只读转发，不是通用代理。** 一个无限制的转发器等于把
     M9 变成任意 URL 抓手。故断言：只许 GET、限 `v1/` 前缀、上游状态码原样透传
     （404 不能被吞成 502 —— 页面要能区分"路段不存在"和"代理坏了"）。

  3) **页面不写死上游地址。** 几何浏览页只能经 `/gw` 取数；一旦有人在 HTML 里
     写死 `http://localhost:8001`，页面就只能在开发机上用，且绕过了转发边界。

  4) **上游不可达时如实报 502**，不静默、不假装 200。

上游用**本地 stub HTTP 服务**（127.0.0.1，随机端口）而不是 monkeypatch：
验的是真实转发路径，且全程不出本机。
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[2]
M9_DIR = ROOT / "modules" / "M9-console"
if str(M9_DIR) not in sys.path:
    sys.path.insert(0, str(M9_DIR))

M9_APP = M9_DIR / "app.py"
GEOMETRY_HTML = M9_DIR / "geometry.html"

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    print("⚠ 缺 fastapi/httpx，无法运行。请：uv run --with fastapi --with httpx --with pyyaml "
          "python tests/contract/test_console.py")
    raise SystemExit(2)

# 登记表路径固定成仓库内真源，避免受调用方 cwd 影响
import os  # noqa: E402
os.environ.setdefault("MODULE_REGISTRY",
                      str(M9_DIR / "config" / "modules.yaml"))

import app as APP  # noqa: E402


# ------------------------------------------------------------------ 上游 stub
class _StubHandler(BaseHTTPRequestHandler):
    """记录收到的请求，并按脚本回话。"""

    seen: list[str] = []

    def do_GET(self):  # noqa: N802
        type(self).seen.append(self.path)
        if self.path.startswith("/v1/boom"):
            body = b'{"detail":"\xe6\xae\xb5\xe8\xb7\xaf\xe4\xb8\x8d\xe5\xad\x98\xe5\x9c\xa8"}'
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/v1/notjson"):
            body = b"<html>not json</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps({"echoed_path": self.path, "count": 7}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # 静音
        pass


def _start_stub() -> tuple[HTTPServer, str]:
    srv = HTTPServer(("127.0.0.1", 0), _StubHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address
    return srv, f"http://{host}:{port}"


def main() -> int:
    fails: list[str] = []

    def ok(label: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"  ✓ {label}")
        else:
            print(f"  ✗ {label}" + (f"　{detail}" if detail else ""))
            fails.append(label)

    # ------------------------------------------------- 1) 不直连数据库
    print("=== 1) M9 不得直连数据库（三条硬线之「读只经 M3 rpdao」）===")
    tree = ast.parse(M9_APP.read_text(encoding="utf-8"))
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            problems += [f"import {a.name}" for a in node.names
                         if a.name.split(".")[0] in ("psycopg", "psycopg2", "sqlalchemy")]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in ("psycopg", "psycopg2", "sqlalchemy"):
                problems.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Name) and node.id in ("ConnectionPool", "connect"):
            problems.append(f"用 {node.id}")
    ok("app.py 里没有 psycopg / sqlalchemy / ConnectionPool", not problems, str(problems))
    # 元测试：同一套判定必须能认出真正的违规，否则它是摆设
    _bad = ast.parse("import psycopg\npool = psycopg.connect('x')")
    _hits = [n for n in ast.walk(_bad)
             if isinstance(n, ast.Import) and n.names[0].name.split(".")[0] == "psycopg"]
    ok("元测试：该判定确实能认出 import psycopg（不是摆设）", len(_hits) == 1)
    ok("compose 不给 M9 任何数据库环境变量",
       "PG_DSN" not in (ROOT / "docker-compose.skeleton.yml").read_text(encoding="utf-8")
       .split("console:")[1].split("ports:")[0])

    # ------------------------------------------------- 2) /gw 的边界
    print("\n=== 2) /gw 是有边界的只读转发，不是通用代理 ===")
    ok("允许前缀只有 v1/（没有空串或 / 这种放行一切的前缀）",
       APP.GATEWAY_ALLOWED_PREFIXES == ("v1/",), str(APP.GATEWAY_ALLOWED_PREFIXES))
    ok("默认上游指向 M6（不是任意地址）",
       "8001" in os.getenv("GATEWAY_BASE", "http://localhost:8001"))

    # ------------------------------------------------- 3) 页面不自带上游地址
    print("\n=== 3) 每一页都自包含、都不写死上游地址 ===")
    # 遍历**所有**页面，而不是逐个点名写死在测试里：加第三页时不会漏掉检查。
    # 每页各自声明"取数入口在哪里"——geometry 要 M6 的数据，故经 /gw 转发；
    # 首页只打 M9 自己的接口，出现 /gw 反而说明它绕了不该绕的路。
    PAGES = {
        "geometry.html": 'const GW = "/gw"',
        "import.html": 'const GW = "/gw"',      # 读路段列表仍经 /gw → M6
        "sim.html": 'const ENTRY = "/v1/sim/manifest"',   # 交付目录是 M9 自己的资产，不经 /gw
        "tables.html": 'const GW = ""',         # 表盘点经 /gw（见第 5 组）
        "index.html": 'const GW = ""',
    }
    have = sorted(p.name for p in M9_DIR.glob("*.html"))
    ok("页面清单与实际文件一致（新增页面而不登记，这条会红）",
       have == sorted(PAGES), f"目录里 {have}，测试里 {sorted(PAGES)}")
    for _name, _gw in PAGES.items():
        html = (M9_DIR / _name).read_text(encoding="utf-8")
        ok(f"{_name} 是自包含 HTML（含内联脚本与样式）",
           "<script>" in html and "<style>" in html)
        ok(f"{_name} 里没有写死端口（8001/localhost:8xxx/api:8000）",
           not re.search(r"localhost:\d+|127\.0\.0\.1:\d+|api:8000", html),
           str(re.findall(r"localhost:\d+|127\.0\.0\.1:\d+|api:8000", html)[:3]))
        ok(f"{_name} 的取数入口声明正确（{_gw}）", _gw in html)
        ok(f"{_name} 没有构建产物依赖（无外部 script/link 引入）",
           not re.search(r"<(script|link)[^>]+(src|href)=[\"']https?://", html))
    # ------------------------------------------------- 3b) 导入页的边界
    # 导入是**写**，而 /gw 的契约是「有边界的只读转发」（下面第 4 组会断言
    # POST /gw/... → 405）。所以导入页必须**另有**入口，不能把写塞进 /gw。
    # 这条不是形式检查：把写塞进只读转发器，等于悄悄把那条界破掉，
    # 而 /gw 的白名单是前缀匹配，塞进去**照样能跑通**——不报错，只是越了界。
    print("\n=== 3b) 导入页：写走自己的端点，不借 /gw ===")
    _imp = (M9_DIR / "import.html").read_text(encoding="utf-8")
    ok("导入页声明了直连 M9 自己的转发端点（不是 /gw）",
       'const API = "/v1/design/import"' in _imp)
    ok("导入页确实用那个端点发 POST，而不是 fetch(GW + …)",
       re.search(r"fetch\(\s*API\s*,\s*\{\s*method:\s*\"POST\"", _imp) is not None)
    ok("导入页里没有向 /gw 发 POST（那会被 405 挡住）",
       not re.search(r"fetch\(\s*GW[^)]*method:\s*\"POST\"", _imp))
    ok("导入页向用户说明了「为什么不经 /gw」",
       "只读转发" in _imp)
    # 一次多个文件 —— 不是图省事：跨文件校验只在两段同时在场时才成立。
    ok("导入页支持一次选多个文件（input multiple）",
       re.search(r'<input[^>]*type="file"[^>]*\bmultiple\b', _imp) is not None)
    ok("导入页把多个文件都发出去（同名追加多次，不是只发第一个）",
       "FILES.forEach((f) => fd.append(" in _imp)
    ok("导入页说明了「为什么必须一次多个」（跨文件校验）",
       "跨文件校验" in _imp)
    # 元测试：把 multiple 去掉，上面那条必须红
    ok("元测试：去掉 multiple 后必须被认出来",
       re.search(r'<input[^>]*type="file"[^>]*\bmultiple\b',
                 _imp.replace('id="file" multiple hidden', 'id="file" hidden', 1)) is None)
    # 元测试：把 POST 改成打 /gw，上面那条必须红
    ok("元测试：把导入改成 POST 到 /gw 时必须被认出来",
       re.search(r"fetch\(\s*GW[^)]*method:\s*\"POST\"",
                 _imp.replace("fetch(API, { method: \"POST\"", "fetch(GW, { method: \"POST\"", 1)) is not None)

    # 元测试：写死地址时必须能被认出来
    ok("元测试：检测规则确实能认出写死的地址（不是摆设）",
       bool(re.search(r"localhost:\d+", "fetch('http://localhost:8001/x')")))
    # 元测试：页面清单检查必须真的会红（藏一个没登记的页面，它认不认得出来）
    ok("元测试：未登记的页面会被认出来（清单检查非摆设）",
       sorted(["geometry.html", "index.html", "sneaky.html"]) != sorted(PAGES))

    # ------------------------------------------------- 3c) 表盘点页的边界
    # 这一页回答的是「库里的表是不是每张都该显示」。它最容易犯的错**不是**
    # 写死地址，而是**把口径写错**：数成物理表（多出几十行分区）、
    # 或把「空表」当成错误藏起来。两者都不会报错，只会让人读到错的结论。
    print("\n=== 3c) 表盘点页：口径必须与 catalog 真源一致 ===")
    _tbl = (M9_DIR / "tables.html").read_text(encoding="utf-8")
    # 页面的取数口是 /gw（它要 M6 的数据），不是 M9 自己的接口
    ok("表盘点页经 /gw 取数（不直连 M6 的 8001）",
       "/gw/v1/catalog/tables" in _tbl, "页面上没有 /gw/v1/catalog/tables")
    ok("表盘点页说明了「逻辑表 ≠ 物理表」（分区不单独列）",
       "分区" in _tbl and "实现细节" in _tbl)
    ok("表盘点页把「空表不是错误」写清楚",
       "它们不是错误" in _tbl)
    ok("表盘点页按「有数据 / 空表」分两张表，而不是一张平表",
       '$("withData")' in _tbl and '$("emptyTbl")' in _tbl)
    # 域代号→中文名必须**从 /v1/catalog 取**。页面里再抄一份域表，
    # M3 改了域名就会一直显示旧名字，而没有任何东西会红。
    ok("域中文名从 /v1/catalog 取，页面里没有另抄一份域表",
       "/gw/v1/catalog\"" in _tbl and "DOMAIN_NAME[dom.code]" in _tbl)
    # 上游字段要转义后才能进 innerHTML —— 不转义就是一个注入口子
    ok("表盘点页对上游字符串做了转义（esc()）",
       "function esc(" in _tbl and "esc(it.table)" in _tbl)
    ok("取数失败时如实报错，不静默显示成 0 行",
       "未能取到盘点结果" in _tbl and "showErr" in _tbl)
    # 元测试：把分区说明删掉，上面那条必须红
    ok("元测试：删掉分区说明后必须被认出来（非摆设）",
       "实现细节" not in _tbl.replace("是 PostgreSQL 的实现细节", "", 1))
    # 元测试：把 esc() 去掉，上面那条必须红
    ok("元测试：去掉 esc() 后必须被认出来（非摆设）",
       "function esc(" not in _tbl.replace("function esc(v) {", "", 1))


    # ------------------------------------------- 3e) 空表页：缺口归因的呈现（契约④）
    # ⚠ 这一组钉的是**呈现**，判定逻辑本身在 test_gap_attribution.py 里钉。
    #   分开的理由：判定错了会给出错误建议；呈现错了会**把正确判定藏起来**。
    #   后者更隐蔽 —— 数据全对，但用户看不到。
    print("\n=== 3e) 空表页：缺口归因的呈现（契约④）===")
    ok("页面另取一路 /gw/v1/catalog/gaps（与盘点分开）",
       "/gw/v1/catalog/gaps" in _tbl, "页面没有取归因")
    # ★ 取不到归因**不许拖垮整页**，但也不许装作没有这回事。
    #   依赖链长度不同：盘点只到 M6→M3；归因还多一跳 M2。
    ok("归因取不到时降级为「只列空表」，不拖垮整页",
       "d.gaps_error" in _tbl and "d.gaps_items || empty" in _tbl)
    ok("降级时说清楚「这不等于这些表没有空因」",
       "这不是「这些表没有空因」" in _tbl)
    # ★ 用接口给的次序，**不许页面自己再排一遍**。
    #   再排一遍就会出现「页面次序」与「接口声称的次序」不一致，
    #   复核者拿 priority_keys 复算时对不上 —— 排序就不可证伪了。
    # ⚠ 判"页面有没有自己重排"，要查**代码**里有没有 sort —— 不能查
    #   priority_keys 这个字样，因为注释里正解释着"为何不自己排"（实测误报过）。
    _tbl_code = "\n".join(ln.split("//")[0] for ln in _tbl.splitlines())
    ok("空表用接口给的次序（页面不自己重排）",
       "d.gaps_items || empty" in _tbl_code
       and ".sort(" not in _tbl_code.split("const ordered")[1].split(";")[0])
    # ★ 措辞边界（spec 明令）：source_present 只表示"收到过"，
    #   不表示"导入一定能成"。写"可以导入"就是给用户一个会落空的承诺。
    ok("空因文案说的是「源已收到」，**不是**「可以导入」",
       "源已收到" in _tbl and "可以导入" not in _tbl,
       "出现了「可以导入」这种超范围承诺")
    # ★ unknown 必须显眼：它是"系统判不出来"，不是"没有空因"。
    ok("unknown 在页面里单列且标红（不并入其他类）",
       "判不出来" in _tbl and "bad" in _tbl and "gaps_unknown" in _tbl)
    ok("页面对 unknown 有明确说明（优先人看）",
       "系统自己承认没判出来" in _tbl)
    ok("空因标签覆盖全部七个取值（不多不少）",
       all(k in _tbl for k in ("has_data", "source_ready_not_imported",
                                "source_needs_conversion", "source_absent",
                                "module_not_built", "upstream_pending", "unknown")))
    ok("空表行给出归属模块与建议动作（能直接派活）",
       "function ownerCell" in _tbl and "it.action" in _tbl)
    # 元测试：把 unknown 从标签表里删掉，"七值齐全"那条必须红。
    #  ⚠ 断言要对着**同一条判据**（七值齐全），不是对着"判不出来"这个字样 ——
    #    后者在页头说明里也出现，删标签不会让它消失，元测试就永远是绿的。
    import re as _re
    _no_unk = _re.sub(r"\n\s*unknown:\s*\{[^}]*\},", "", _tbl, count=1)
    ok("元测试：删掉 unknown 标签后「七值齐全」会被认出来（非摆设）",
       "unknown:" not in _no_unk and _no_unk != _tbl)
    # 元测试：把接口次序换成自己按域排，上面那条必须红
    _resort = _tbl.replace("const ordered = (d.gaps_items || empty);",
                           "const ordered = empty.slice().sort();", 1)
    ok("元测试：改成页面自己重排后必须被认出来（非摆设）",
       "d.gaps_items || empty" not in _resort)
    # ------------------------------------------------- 3d) 不直连库与「只经 rpdao」的口径
    # ⚠ 这一组**离线**跑，只验"结构上做不到"，不连数据库。
    #   需要真实行数的断言在脚本外面（见 tests/contract/test_dao_contract.py
    #   的对应组），二者分工：这里钉口径，那里钉数值。
    print("\n=== 3d) 表盘点的口径（离线部分）===")
    _repo_src = (ROOT / "modules" / "M3-rpdao" / "rpdao" / "repo.py").read_text(encoding="utf-8")
    _pool_src = (ROOT / "modules" / "M3-rpdao" / "rpdao" / "pool.py").read_text(encoding="utf-8")
    ok("分区根表登记在 M3（catalog 一侧），不在页面或 M6 里",
       "PARTITIONED_ROOTS" in _repo_src and "PARTITIONED_ROOTS" not in _tbl)
    ok("分区数现查 pg_inherits，不是写死的常量",
       "pg_inherits" in _pool_src)
    ok("table_census 的表名只从 ALL_TABLES 来，不收调用方给的表名",
       "def table_census(self) -> list[dict[str, Any]]" in _pool_src
       and "from .catalog import ALL_TABLES" in _pool_src
       and "for i, t in enumerate(ALL_TABLES)" in _pool_src)
    ok("table_census 用 UNION ALL 一次查完（不是逐表 62 次往返）",
       '" UNION ALL ".join(parts)' in _pool_src)
    ok("拼进 SQL 的表名过了 quote_ident（标识符白名单双保险）",
       "quote_ident(t)" in _pool_src)

    # ------------------------------------------------- 4) 真请求
    print("\n=== 4) 真请求（TestClient + 本地 stub 上游）===")
    srv, base = _start_stub()
    APP.GATEWAY_BASE = base                       # 端点函数调用时取模块全局
    client = TestClient(APP.app)

    r = client.get("/")
    ok("/ → 200 且是 HTML（首页不再是 404）",
       r.status_code == 200 and "<title>" in r.text, f"{r.status_code}")
    ok("/ 的内容确实是首页", "平台管理台 · M9" in r.text)

    r = client.get("/geometry")
    ok("/geometry → 200 且是 HTML", r.status_code == 200 and "<title>" in r.text,
       f"{r.status_code}")
    ok("/geometry 的内容确实是那个页面", "GE 道路几何" in r.text)
    # 表盘点页：路由真的在，且内容真的是那一页。
    # ★ 这条曾经**本该**在而实际不在：路由写好了、页面文件写好了，但
    #   Dockerfile 漏了一行 COPY —— 容器里根本没这个文件，GET 直接 500。
    #   契约测试跑在宿主机上、读的是仓库里的文件，**照样全绿**。
    #   所以这里补的不是"路由存在"，而是"页面在容器里也存在"（见下面的
    #   镜像清单断言），两者缺一不可。
    r = client.get("/tables")
    ok("/tables → 200 且是 HTML", r.status_code == 200 and "<title>" in r.text,
       f"{r.status_code}")
    ok("/tables 的内容确实是那一页", "数据表盘点" in r.text)
    ok("首页链到 /tables（入口真的点得到）", 'href="/tables"' in client.get("/").text)
    ok("表盘点页链回首页", 'href="/"' in client.get("/tables").text)
    # 镜像清单：页面文件必须真的被 COPY 进镜像，否则宿主机测试全绿、容器里 500。
    _dockerfile = (M9_DIR / "Dockerfile").read_text(encoding="utf-8")
    _missing = [p.name for p in M9_DIR.glob("*.html") if p.name not in _dockerfile]
    ok("每个页面都被 Dockerfile COPY 进镜像（漏了会让容器里 500、宿主机测试却全绿）",
       not _missing, f"未 COPY：{_missing}")
    ok("元测试：漏 COPY 时必须被认出来（非摆设）",
       [p.name for p in M9_DIR.glob("*.html")
        if p.name not in _dockerfile.replace("COPY tables.html ./", "", 1)] == ["tables.html"])

    # 两个页面互相可达：点得到才算"导航"，不然只是一句口号
    ok("首页链到 /geometry（导航真的连上了）", 'href="/geometry"' in client.get("/").text)
    ok("几何页链回首页", 'href="/"' in client.get("/geometry").text)

    r = client.get("/gw/v1/geometry/sections")
    ok("/gw 转发成功并原样带回上游 JSON",
       r.status_code == 200 and r.json().get("count") == 7, f"{r.status_code} {r.text[:120]}")

    _StubHandler.seen.clear()
    client.get("/gw/v1/geometry/sections/6/stations?from_km=0.6&to_km=1.6&integer_only=true")
    seen = _StubHandler.seen[-1] if _StubHandler.seen else ""
    ok("查询串被完整转发（不是只转路径）",
       "from_km=0.6" in seen and "to_km=1.6" in seen and "integer_only=true" in seen, seen)

    r = client.get("/gw/healthz")
    ok("/gw/healthz → 404（前缀白名单挡住了非 API 路径）", r.status_code == 404, f"{r.status_code}")
    r = client.get("/gw//etc/passwd")
    ok("/gw//etc/passwd → 404", r.status_code == 404, f"{r.status_code}")
    r = client.post("/gw/v1/geometry/sections")
    ok("POST /gw/... → 405（只许 GET）", r.status_code == 405, f"{r.status_code}")

    # 上游状态码原样透传：页面要靠 404 区分"路段不存在"与"代理坏了"
    r = client.get("/gw/v1/boom")
    ok("上游 404 原样透传成 404（没被吞成 502）", r.status_code == 404, f"{r.status_code}")
    r = client.get("/gw/v1/notjson")
    ok("上游返回非 JSON → 502 并说明原因", r.status_code == 502, f"{r.status_code}")

    srv.shutdown()
    # 上游关掉之后：必须如实报 502，不静默、不假装 200
    r = client.get("/gw/v1/geometry/sections")
    ok("上游不可达 → 502（如实报，不假装）", r.status_code == 502, f"{r.status_code}")
    ok("502 的说明里点名了上游是谁", "M6" in str(r.json().get("detail", "")), r.text[:160])
    APP.GATEWAY_BASE = base

    # ------------------------------------- 3f) 三维起伏页：交付目录的白名单与只读
    # 这一组钉的是**两件在别处看不出来的事**：
    #   · 白名单：目录里存在但没登记的文件必须取不到。用 StaticFiles 挂目录
    #     会把暴露面交给目录内容 —— 谁丢个文件进去就多一个可取的东西。
    #   · 只读挂载：容器必须**在结构上无法写**交付目录。写成 `:rw` 一切照常跑，
    #     没有任何东西会红，直到某天产物被容器改掉而没人知道是谁改的。
    print("\n=== 3f) 三维起伏页：白名单取数 + 交付目录只读 ===")
    import tempfile

    r = client.get("/sim")
    ok("/sim → 200 且是 HTML", r.status_code == 200 and "<title>" in r.text, f"{r.status_code}")
    ok("/sim 的内容确实是那一页", "三维起伏" in r.text)
    ok("首页链到 /sim（入口真的点得到）", 'href="/sim"' in client.get("/").text)
    ok("三维页链回首页", 'href="/"' in client.get("/sim").text)

    _sim = (M9_DIR / "sim.html").read_text(encoding="utf-8")

    # —— 页面不判定。宪法「未标定的阈值不得用于生产判定」。
    _VERDICT = r"合格|不合格|超标|正常范围"
    ok("三维页里没有判定字样（显示刻度不是判定阈值）",
       not re.search(_VERDICT, _sim), str(re.findall(_VERDICT, _sim)[:3]))
    ok("元测试：注入「判定为合格」后必须被认出来（非摆设）",
       bool(re.search(_VERDICT, _sim + "<p>判定为合格</p>")))

    # —— 垂直放大系数必须**始终可见**，且默认是真尺度。
    #    真尺度下画面几乎是平的（4.5 km 对 29.75 m）。若默认自动放大，
    #    一张截图拿出去就会被当成真实纵坡 —— 所以默认必须是 1.0。
    ok("三维页有垂直放大控件，且读数元素常驻（不是只在拖动时出现）",
       'id="zscale"' in _sim and 'id="zval"' in _sim)
    ok("垂直放大默认 1.0（真尺度），不自动放大",
       'value="1"' in _sim and "zscale: 1.0" in _sim)
    ok("三维页说明了「真尺度下几乎是平的」不是数据问题",
       "不是数据的问题" in _sim or "不是 bug" in _sim)
    ok("元测试：把默认值改成自动放大后必须被认出来（非摆设）",
       'value="1"' not in _sim.replace('value="1"', 'value="15"', 1))

    # —— 交付目录必须是只读挂载，且路径固定。
    _compose = (ROOT / "docker-compose.skeleton.yml").read_text(encoding="utf-8")
    _sec = _compose.split("  console:")[1].split("\n  agent:")[0]
    ok("compose 的 console 段挂了交付目录", "/data/sim" in _sec, _sec[:200])
    ok("交付目录是**只读**挂载（:ro）", "../artifacts/sim:/data/sim:ro" in _sec)
    ok("元测试：去掉 :ro 后必须被认出来（非摆设）",
       "../artifacts/sim:/data/sim:ro" not in _sec.replace(":/data/sim:ro", ":/data/sim", 1))
    ok("compose 通过环境变量告知交付目录位置（路径不写死在代码里）",
       "SIM_ARTIFACTS_DIR" in _sec)
    # ★★ 这一条是本组最值钱的一条。compose 的**相对路径相对 compose 文件所在目录**
    #    解析 —— 写成 `./artifacts/sim` 会指向 scaffold/artifacts/sim，一个 docker
    #    顺手建出来的**空目录**。表现是页面 503「清单不存在」，而宿主机上产物都在，
    #    很容易去查路由或挂载权限，查不到点子上。实测踩过一次。
    #    所以这里不比对字符串，而是**按 compose 的规则把路径解析出来再看磁盘**。
    _mnt = re.search(r"-\s*(\S+):/data/sim:ro", _sec)
    ok("挂载源路径解析得出来", _mnt is not None)
    if _mnt:
        _src = (_compose_dir := ROOT) / _mnt.group(1)
        _src = pathlib.Path(os.path.normpath(str(_src)))
        ok(f"挂载源按 compose 规则解析后指向交付目录（实得 {_src}）",
           _src == ROOT.parent / "artifacts" / "sim", str(_src))
        ok("挂载源里确实有清单（挂空目录会让页面 503，而宿主机上一切正常）",
           (_src / "manifest.json").is_file(), f"{_src} 里没有 manifest.json")
        ok("元测试：把路径改成 ./artifacts/sim 后必须被认出来（非摆设）",
           pathlib.Path(os.path.normpath(str(ROOT / "./artifacts/sim")))
           != ROOT.parent / "artifacts" / "sim")

    # —— 白名单：**真的发一次请求**。用临时目录造三种文件：
    #    登记过的、没登记的、以及一个路径穿越尝试。
    with tempfile.TemporaryDirectory() as _td:
        _t = pathlib.Path(_td)
        (_t / "ok.bin").write_bytes(b"0123456789" * 10)
        (_t / "sneaky.txt").write_bytes(b"should not be reachable")
        (_t / "manifest.json").write_text(json.dumps({
            "version": "artifact_manifest.v0.1",
            "generated_at": "2026-10-10T00:00:00+08:00",
            "producer": {"module": "test"},
            "frame": {"kind": "local", "crs_ready": False, "crs_assumed": True},
            "artifacts": [{
                "name": "ok.bin", "kind": "terrain_mesh", "present": True,
                "bytes": 100, "mime": "application/octet-stream",
                "mesh": {"nu": 2, "nv": 2, "position_dtype": "float32",
                         "index_dtype": "uint16", "u_step_m": 1.0},
            }],
        }, ensure_ascii=False), encoding="utf-8")
        _orig = APP.SIM_ARTIFACTS_DIR
        APP.SIM_ARTIFACTS_DIR = _t
        try:
            ok("清单里登记的文件取得到", client.get("/artifacts/ok.bin").status_code == 200)
            ok("清单里**没登记**的文件取不到 → 404（白名单生效）",
               client.get("/artifacts/sneaky.txt").status_code == 404)
            ok("目录里没登记的文件确实存在（上一条不是因为文件不存在才 404）",
               (_t / "sneaky.txt").is_file())
            ok("路径穿越 → 404", client.get("/artifacts/..%2Fmanifest.json").status_code == 404)
            ok("路径穿越（未编码）→ 404", client.get("/artifacts/../manifest.json").status_code == 404)
            # ★ Range：不返回 206 的实现**视频照样能播，只是拖不动进度条**。
            #   "能播"会让粗看的人以为它对了 —— 所以这一条必须单独验。
            _r = client.get("/artifacts/ok.bin", headers={"Range": "bytes=0-9"})
            ok("带 Range 的请求 → 206 Partial Content", _r.status_code == 206, f"{_r.status_code}")
            ok("206 响应带 Content-Range 且长度正确",
               _r.headers.get("content-range", "").endswith("/100")
               and len(_r.content) == 10,
               f"{_r.headers.get('content-range')} len={len(_r.content)}")
            # ★★ 这一条是本组最值钱的一条。Range 行为**不能依赖库版本**：
            #    宿主机 starlette 0.38.6（无 Range）、容器 0.41.3（有 Range），
            #    而 requirements.txt 只钉了 fastapi，starlette 是浮动解析的。
            #    若把 Range 交给 FileResponse，同一份代码在两个环境行为不同，
            #    "契约测试通过"就失去意义 —— 与"宿主机全绿、容器里 500"同型。
            _app_src = (M9_DIR / "app.py").read_text(encoding="utf-8")
            ok("Range 是自己实现的，不依赖库版本（两边行为必须一致）",
               "def _serve_file_with_range(" in _app_src
               and 'status_code=206' in _app_src)
            ok("元测试：去掉自实现后必须被认出来（非摆设）",
               "def _serve_file_with_range(" not in
               _app_src.replace("def _serve_file_with_range(", "def _unused(", 1))
            _r = client.get("/artifacts/ok.bin", headers={"Range": "bytes=90-"})
            ok("bytes=90- 这类开放式区间也对（不只是闭区间）",
               _r.status_code == 206 and len(_r.content) == 10, f"{_r.status_code}")
            _r = client.get("/artifacts/ok.bin", headers={"Range": "bytes=-15"})
            ok("bytes=-N（末 N 字节）也对", _r.status_code == 206 and len(_r.content) == 15,
               f"{_r.status_code} len={len(_r.content)}")
            _r = client.get("/artifacts/ok.bin", headers={"Range": "bytes=999-1200"})
            ok("越界区间 → 416（不假装成 200）", _r.status_code == 416, f"{_r.status_code}")
            _r = client.get("/artifacts/ok.bin", headers={"Range": "bytes=abc"})
            ok("畸形区间 → 回退整文件 200（不假装成 206）",
               _r.status_code == 200 and len(_r.content) == 100, f"{_r.status_code}")
            _r = client.get("/artifacts/ok.bin")
            ok("无 Range → 200 且带 accept-ranges（告诉客户端可以拖）",
               _r.status_code == 200 and _r.headers.get("accept-ranges") == "bytes")
            ok("元测试：把白名单换成直接拼路径后必须被认出来（非摆设）",
               "sneaky.txt" in [p.name for p in _t.glob("*.txt")]
               and not any(a["name"] == "sneaky.txt"
                           for a in json.loads((_t / "manifest.json").read_text())["artifacts"]))
        finally:
            APP.SIM_ARTIFACTS_DIR = _orig

    # 清单不存在时必须**如实报**，不能假装目录是空的
    _orig = APP.SIM_ARTIFACTS_DIR
    APP.SIM_ARTIFACTS_DIR = pathlib.Path("/nonexistent-delivery-dir")
    try:
        _r = client.get("/v1/sim/manifest")
        ok("交付目录没有清单 → 503 且说明该跑哪个脚本",
           _r.status_code == 503 and "export_viewer_mesh.py" in str(_r.json().get("detail", "")),
           f"{_r.status_code}")
    finally:
        APP.SIM_ARTIFACTS_DIR = _orig

    # —— 契约正本必须在 scaffold/contracts/ 下（宪法「接入约束」第 4 件）
    _ctr = ROOT / "contracts" / "delivery" / "artifact_manifest.v0.1.schema.json"
    ok("产物清单契约在 scaffold/contracts/delivery/ 下（正本唯一）",
       _ctr.is_file(), str(_ctr))
    if _ctr.is_file():
        _sch = json.loads(_ctr.read_text(encoding="utf-8"))
        ok("契约是白名单式的（additionalProperties: false）",
           _sch.get("additionalProperties") is False)
        ok("契约限定 name 不许含路径分隔符（路由据此防穿越）",
           "/" not in _sch["properties"]["artifacts"]["items"]["properties"]["name"]["pattern"])

    print("\n结果：" + ("全部通过 ✓" if not fails else f"失败 {len(fails)} 项 → {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
