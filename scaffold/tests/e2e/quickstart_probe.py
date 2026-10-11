#!/usr/bin/env python3
"""T029：把 quickstart.md §端到端可跑通的判据 的八条逐条跑一遍。

★ 八条里第 4、5、7、8 条是**反向判据** —— 它们要证明的不是"某件事成立"，
  而是"某件事**做不到**"。反向判据最容易写成空转：一个永远为真的否定
  看起来和通过一模一样。所以每一条反向判据都配了同向的对照。
"""
import json
import pathlib
import re
import subprocess
import sys

import httpx
import jsonschema

# ★ 从脚本自身位置推仓库根（原先是写死的 /data/cy/shujuku）。
#   本文件在 scaffold/tests/e2e/ ⇒ parents[3] = 仓库根。
REPO = pathlib.Path(__file__).resolve().parents[3]
DELIV = REPO / "artifacts" / "sim"
CTR = REPO / "scaffold" / "contracts" / "delivery" / "artifact_manifest.v0.1.schema.json"
BASE = "http://localhost:8024"
M9 = REPO / "scaffold" / "modules" / "M9-console"

fails = []


def ok(label, cond, detail=""):
    print(f"  {'✓' if cond else '✗'} {label}" + ("" if cond or not detail else f"　{detail}"))
    if not cond:
        fails.append(label)


c = httpx.Client(base_url=BASE, timeout=30.0)

print("判据 1｜网格抽稀成功，且索引是 16 位")
mani = json.loads((DELIV / "manifest.json").read_text(encoding="utf-8"))
meshes = [a for a in mani["artifacts"] if a["kind"] == "terrain_mesh" and a.get("present")]
ok("清单里有已产出的 terrain_mesh", len(meshes) == 2, str(len(meshes)))
for a in meshes:
    m = a["mesh"]
    ok(f"{a['name']}：索引位宽 {m['index_dtype']}（顶点 {m['nu']}×{m['nv']}="
       f"{m['nu'] * m['nv']}，必须 < 65536）",
       m["index_dtype"] == "uint16" and m["nu"] * m["nv"] < 65536,
       f"{m['index_dtype']} {m['nu'] * m['nv']}")
    ok(f"{a['name']}：位置是 float32", m["position_dtype"] == "float32")
ok("抽稀确有压缩（源 CRG 101 MB → 两个网格合计不到 2 MB）",
   sum(a["bytes"] for a in meshes) < 2_000_000,
   f"{sum(a['bytes'] for a in meshes)} B")

print("\n判据 2｜清单符合契约，且与磁盘一致")
schema = json.loads(CTR.read_text(encoding="utf-8"))
try:
    jsonschema.validate(mani, schema)
    ok("清单通过契约校验（正本唯一）", True)
except jsonschema.ValidationError as e:
    ok("清单通过契约校验（正本唯一）", False, str(e.message)[:200])
disk_mismatch = []
for a in mani["artifacts"]:
    if a.get("present"):
        p = DELIV / a["name"]
        if not p.is_file() or a.get("bytes") != p.stat().st_size:
            disk_mismatch.append(a["name"])
ok("每个 present 的产物都在磁盘上且字节数一致", not disk_mismatch, str(disk_mismatch))
# ★★★ 这条判据原先写的是 `crs_assumed is True and crs_ready is False`
#   —— 即**把「未确认」那一个状态硬编码进了检查**，而它的名字却叫
#   「不是写死的」。2026-10-11 用户批准了真实带号，crs.yaml 翻成
#   ready:true / assumed:false，manifest 如实跟着变，于是这条判据反过来报错。
#   翻脸的是判据自己，不是产物。
#
#   现在改成**比对**：manifest 的两个标记必须等于 crs.yaml 里的两个标记。
#   状态是什么都行 —— 只要两边一致。如果导出器把值写死而 crs.yaml 说了别的，
#   这条才会响。★ 判据的名字说的是"读来的"，检查就得真的去读一遍。
def _yaml_flag(text, key):
    m = re.search(rf"^\s*{key}:\s*(true|false)", text, re.MULTILINE)
    return None if m is None else (m.group(1) == "true")


_crs_text = (M9 / "config" / "crs.yaml").read_text(encoding="utf-8")
_yaml_ready = _yaml_flag(_crs_text, "ready")
_yaml_assumed = _yaml_flag(_crs_text, "assumed")
ok("crs.yaml 两个标记都标得出来（正则没瞎）",
   _yaml_ready is not None and _yaml_assumed is not None,
   f"ready={_yaml_ready} assumed={_yaml_assumed}")
ok("清单里的 frame 标记是从 crs.yaml 读的（不是写死的）",
   mani["frame"]["crs_ready"] is _yaml_ready and mani["frame"]["crs_assumed"] is _yaml_assumed,
   f"清单 {mani['frame']} ｜ crs.yaml ready={_yaml_ready} assumed={_yaml_assumed}")

print("\n判据 3｜页面 200，且首页链得到")
r = c.get("/sim")
ok("GET /sim → 200", r.status_code == 200, str(r.status_code))
ok("页面标题是三维起伏查看器", "三维起伏" in r.text)
ok("页面去回管理台的链接在", 'href="/"' in r.text)
r0 = c.get("/")
ok("首页链得到 /sim", 'href="/sim"' in r0.text)
rm = c.get("/v1/sim/manifest")
ok("GET /v1/sim/manifest → 200 且形状是 {manifest, crs}",
   rm.status_code == 200 and {"manifest", "crs"} <= set(rm.json().keys()),
   str(rm.status_code))

print("\n判据 4（反向）｜未登记文件 404、路径穿越 404")
# 对照：已登记的必须 200 —— 否则"全 404"也能让这一条通过
_reg = next(a["name"] for a in mani["artifacts"] if a.get("present") and a["kind"] == "terrain_mesh")
ok("对照：已登记的产物可以取到（否则全 404 也能骗过这一条）",
   c.get(f"/artifacts/{_reg}").status_code == 200)
ok("未登记的文件名 → 404", c.get("/artifacts/not_registered.bin").status_code == 404)
ok("目录里存在但未登记的文件 → 404（清单即白名单）",
   c.get("/artifacts/.gitkeep").status_code == 404)
for evil in ["../app.py", "..%2fapp.py", "%2e%2e%2fapp.py", "....//app.py",
             "..%252fapp.py", "sub/dir.bin"]:
    _s = c.get(f"/artifacts/{evil}").status_code
    ok(f"路径穿越 {evil!r} → 404", _s == 404, str(_s))

print("\n判据 5（反向）｜容器对交付目录写入确实失败")
def dexec(cmd):
    return subprocess.run(["docker", "exec", "rp-console", "sh", "-c", cmd],
                          capture_output=True, text=True,
                          env={"DOCKER_CONFIG": str(REPO / ".dockerhome"), "PATH": "/usr/bin:/bin"})
# ★ 对照：容器**能**在别处写 —— 否则"写失败"可能只是整个 fs 只读
_w = dexec("touch /tmp/_probe_ok && echo WROTE")
ok("对照：容器在非挂载路径上可以写（证明不是整个文件系统只读）",
   "WROTE" in _w.stdout, (_w.stdout + _w.stderr).strip()[:120])
dexec("rm -f /tmp/_probe_ok")
# ★ touch 而不是 rm —— rm -f 不管删没删掉都返回 0，当不了权限测试
_t = dexec("touch /data/sim/_probe_should_fail")
ok("touch 交付目录 → 失败（只读挂载）", _t.returncode != 0,
   f"exit={_t.returncode} {(_t.stdout + _t.stderr).strip()[:120]}")
ok("报的是只读文件系统", "Read-only" in (_t.stdout + _t.stderr))
_mk = dexec("mkdir /data/sim/_probe_dir")
ok("mkdir 交付目录 → 失败", _mk.returncode != 0, f"exit={_mk.returncode}")
_rm = dexec("rm -f /data/sim/manifest.json")
ok("删交付目录里的文件 → 失败", _rm.returncode != 0, f"exit={_rm.returncode}")
ok("交付目录里的文件仍在（没被删掉）", (DELIV / "manifest.json").is_file())

print("\n判据 6｜视频返回 206")
vid = DELIV / "vehicle_on_crg_lane_right_10x.mp4"
size = vid.stat().st_size
rv = c.get("/artifacts/vehicle_on_crg_lane_right_10x.mp4", headers={"Range": "bytes=0-2047"})
ok("Range → 206", rv.status_code == 206, str(rv.status_code))
ok("Content-Range 正确", rv.headers.get("content-range") == f"bytes 0-2047/{size}",
   str(rv.headers.get("content-range")))
ok("实体长度就是 2048", len(rv.content) == 2048, str(len(rv.content)))
ok("Content-Type 是 video/mp4", "video/mp4" in rv.headers.get("content-type", ""),
   str(rv.headers.get("content-type")))
ok("声明 accept-ranges: bytes", "bytes" in rv.headers.get("accept-ranges", ""))
ok("越界 → 416", c.get("/artifacts/vehicle_on_crg_lane_right_10x.mp4",
                       headers={"Range": f"bytes={size + 10}-"}).status_code == 416)
# ★ 第二段视频也要过，否则"一条通过"可能只是那一条特例
_v2 = "vehicle_on_crg_lane_right_realtime.mp4"
ok("第二段视频同样 206",
   c.get(f"/artifacts/{_v2}", headers={"Range": "bytes=0-1023"}).status_code == 206)
# 整档下载字节数必须与磁盘一致 —— 尾部截断在这里会露出来
_full = c.get("/artifacts/vehicle_on_crg_lane_right_10x.mp4")
ok("整档下载字节数与磁盘一致（尾部截断会在这里露出来）",
   len(_full.content) == size, f"{len(_full.content)}/{size}")

print("\n判据 7（反向）｜契约测试全绿，且套件数比改动前多")
# ★ 必须把**真实环境**传下去：运行器用 `uv run` 起每个套件，
#   第一次这里只给了 PATH/HOME，uv 找不到 → 19 个套件全"失败"，
#   看起来像代码坏了。剥环境比不剥更危险：它伪造出一个失败。
import os as _os
out = subprocess.run(["./run_contract_tests.sh"], capture_output=True, text=True,
                     cwd=str(REPO / "scaffold"),
                     env={**_os.environ, "DOCKER_CONFIG": str(REPO / ".dockerhome")})
_tail = out.stdout.strip().split("\n")[-1]
ok("总运行器退出码 0", out.returncode == 0, f"exit={out.returncode}")
_sum = [ln for ln in out.stdout.split("\n") if ln.startswith("通过 ")]
_n = int(_sum[0].split("｜")[0].replace("通过", "").strip()) if _sum else -1
ok(f"套件数 {_n} > 改动前的 18（新增的检查真的被跑了，不是写在别处）", _n > 18, _sum[0] if _sum else "无汇总")
ok("失败 0、跳过 0", "失败 0" in (_sum[0] if _sum else "") and "跳过 0" in (_sum[0] if _sum else ""),
   _sum[0] if _sum else "")

print("\n判据 8（反向）｜页面不含判定字样")
_page = (M9 / "sim.html").read_text(encoding="utf-8")
_bad = [w for w in ("合格", "不合格", "超标", "正常范围") if w in _page]
ok("页面源码里没有判定字样", not _bad, str(_bad))
# ★ 对照：真塞一句进去，同一条检查必须能抓到 —— 否则这一条是空转
ok("对照：塞一句「判定为合格」进去，同一条检查必须抓到",
   any(w in _page + "<p>判定为合格</p>" for w in ("合格", "不合格", "超标", "正常范围")))
ok("页面里也没有「阈值」二字（刻度就是刻度，不是判据）", "阈值" not in _page)
ok("运行中的页面同样不含判定字样",
   not [w for w in ("合格", "不合格", "超标", "正常范围") if w in r.text])
ok("页面不引用任何外部 CDN（无构建步骤、无新依赖是平台的接入约束）",
   "http://" not in _page and "https://" not in _page)

print("\n" + ("八条判据全部通过 ✓" if not fails else f"失败 {len(fails)} 项 → {fails}"))
sys.exit(1 if fails else 0)
