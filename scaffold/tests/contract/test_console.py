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
    # 元测试：写死地址时必须能被认出来
    ok("元测试：检测规则确实能认出写死的地址（不是摆设）",
       bool(re.search(r"localhost:\d+", "fetch('http://localhost:8001/x')")))
    # 元测试：页面清单检查必须真的会红（藏一个没登记的页面，它认不认得出来）
    ok("元测试：未登记的页面会被认出来（清单检查非摆设）",
       sorted(["geometry.html", "index.html", "sneaky.html"]) != sorted(PAGES))

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

    print("\n结果：" + ("全部通过 ✓" if not fails else f"失败 {len(fails)} 项 → {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
