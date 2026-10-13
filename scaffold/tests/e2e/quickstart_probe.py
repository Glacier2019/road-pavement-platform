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

print("\n判据 2b｜crs.yaml 与库一致（SC-012 / FR-014）")
# ★★ 这一条是 005 的核心后果：`crs.yaml` 从**权威**降为**派生产物**。
#    于是「文件跟着库走了没有」必须有判据 —— 否则派生物会悄悄漂走，
#    而读它的那四个消费者一句警告都不会有。
#
# ★ 三条通路，各管一段（定义只有一份，在 M9-console/crs_drift.py）：
#     · M9 `/v1/crs/drift`   → 文件 vs **契约值**（M9 不得直连数据库）
#     · **本判据**            → 文件 vs **库**（唯一能比全五项的通路）
#     · test_region_frame §18 → 权威判据（离线、带元测试）
#   为什么偏偏是这里能比全五项：`crs_evidence` 在 M6 那边被 `_CRS_ROW_KEYS`
#   剥掉了，两条 HTTP 通路都不带它 ⇒ 只有直接读库这一条路看得到。
sys.path.insert(0, str(M9))
import crs_drift  # noqa: E402

import yaml  # noqa: E402

_crs_doc = yaml.safe_load(_crs_text) or {}


# ★★ 比**哪一行**？—— 不猜，问文件自己。
#    导出器在 `generated_from` 里写明了它是从哪个项目导出来的，
#    那就拿这一行来比。这比"取第一条配了基准的"强：
#      · 后者在多项目共存时会**拿错行**，然后报告一份假的"不一致"；
#      · 而 `generated_from` 是导出时真实落盘的出处，是文件的自述。
#    ★ 顺带：这条判据也就在**替 `generated_from` 背书** —— 它要是写错了，
#      比对会当场红，而不是让一个错的出处静静躺在文件里。
_gen18 = {str(d.get("k")): d.get("v")
          for d in (_crs_doc.get("generated_from") or []) if isinstance(d, dict)}
_proj_id = _gen18.get("design_project.id")
ok("crs.yaml 说了自己是从哪个项目导出来的（`generated_from`）",
   _proj_id is not None, f"generated_from={_gen18}")
_proj_name = _gen18.get("design_project.project_name", "")


def _psql(sql):
    """在 rp-pg 容器里查一行，返回制表符分隔的字符串。

    ★ 换行必须在 **SQL 侧**转义：`crs_evidence` 是多行 text，
      `psql -tAc` 会原样输出真换行，于是下游 `split("\t")` 会被打散成
      七八段、字段全错位 —— 而错位之后**读起来仍然像正常数据**。
    """
    return subprocess.run(
        ["docker", "exec", "rp-pg", "sh", "-c",
         f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc {json.dumps(sql)}'],
        capture_output=True, text=True,
        env={"DOCKER_CONFIG": str(REPO / ".dockerhome"), "PATH": "/usr/bin:/bin"})


# 列顺序：前三列是纯标量，多行 text 放**最后**，即使它意外带换行也只污染自己
_db_row = _psql(
    "select coalesce(crs_epsg::text,'') || E'\\t' || coalesce(crs_zone_number::text,'')"
    " || E'\\t' || coalesce(crs_central_meridian_deg::text,'')"
    " || E'\\t' || coalesce(crs_assumed::text,'')"
    f" || E'\\t' || replace(coalesce(crs_evidence,''), E'\\n', E'\\\\n')"
    f" from design_project where id = {int(_proj_id)};" if _proj_id is not None else "select 1")
if _proj_id is None or _db_row.returncode != 0 or not _db_row.stdout.strip():
    ok("从库里取到 crs.yaml 自述的那个项目（否则下面几条全是空转）", False,
       (_db_row.stdout + _db_row.stderr).strip()[:160])
else:
    _f = _db_row.stdout.strip().split("\t")
    ok("取到的字段个数对得上（多行 text 没把列打散）", len(_f) == 5,
       f"{len(_f)} 段：{[x[:24] for x in _f]}")
    _db_side = {
        "epsg": int(_f[0]),
        "zone_number": int(_f[1]),
        "central_meridian_deg": _f[2],
        "assumed": _f[3] == "t",
        "evidence": _f[4].replace("\\n", "\n"),
    }
    _mis, _skip = crs_drift.compare_detailed(_crs_doc, _db_side)
    ok(f"crs.yaml 与库**五项全部**一致（比的是它自述的出处：项目 {_proj_id}「{_proj_name}」）",
       not _mis and not _skip,
       crs_drift.describe(_mis, source="库里 design_project", skipped=_skip)[:400])
    # ★ 反向判据要配同向对照：证明「五项真的都看了」，不是「碰巧没冲突」。
    ok("对照：五项**一项都没跳过**（`crs_evidence` 走库这条路看得到；"
       "走 M6 那条 HTTP 路看不到 —— 那是通道的局限，不是库的）",
       not _skip, f"未比对 {_skip}")
    # ★ 元测试：把库那一侧改坏一处 → 必须变红。**在这里做**是因为本判据
    #   比的是真库；只在离线测试里做，验不到「这条 SQL 真的取到了值」。
    _bad = dict(_db_side, epsg=2154)
    _mis_bad = crs_drift.compare(_crs_doc, _bad)
    ok("★元测试：库里改成 2154 → 本判据真的会红，且点名 epsg",
       bool(_mis_bad) and "epsg" in " ".join(_mis_bad), (" ".join(_mis_bad))[:120])
    # ★ 元测试②：证据少一条必须红 —— 这是「一条不多一条不少」的执行者
    _bad2 = dict(_db_side, evidence=_db_side["evidence"].splitlines()[1:])
    _mis2 = crs_drift.compare(_crs_doc, _bad2)
    ok("★元测试②：库里证据少一条 → 变红且点名 evidence",
       bool(_mis2) and "evidence" in " ".join(_mis2), (" ".join(_mis2))[:120])

# ★ M9 那条路：证明它**能进容器、能当巡检用**，并证明它如实报出「evidence 没得比」
_rd = c.get("/v1/crs/drift")
ok("GET /v1/crs/drift → 200（M9 经 /gw → M6 → M3，自己不碰库）",
   _rd.status_code == 200, str(_rd.status_code))
if _rd.status_code == 200:
    _jd = _rd.json()
    ok("M9 那条路：五项里看得见的那几项一致，且**如实**把 evidence 列进「未比对」",
       not _jd["mismatches"] and _jd["skipped"] == ["evidence"],
       f"不一致 {_jd['mismatches']}；未比对 {_jd['skipped']}")
    # ★★★ 防滥用：`consistent` 不许在「有未比对项」时为真 ——
    #     否则将来 M6 少给任何一个字段，这条巡检会**由红转绿**而无人察觉。
    ok("★★ 而 `consistent` 仍然是 false —— 「没得比」不许被说成「一致」",
       _jd["consistent"] is False, str(_jd["consistent"]))


print("\n判据 2c｜两级不一致时**写入侧**真的会拒（quickstart 第 2 步 / Q1b）")
# ★★★ 这一条是 005 期间**查出来的漏**，不是照着 spec 抄的。
#     quickstart 第 2 步主张「给路段配一个不同的基准 → 这条必须失败」。
#     实测：`UPDATE 1` —— 写入**成功了**。全仓扫过：
#       · 没有任何触发器；· 没有任何 crs 相关的 check 约束；
#       · M2 写路径完全不碰 crs 字段。
#     ⇒ 那个不变量**根本不存在**，只有读取侧的 503 在兜底，
#       而 quickstart 自己写着「必须在写入侧拦，不能在读路径拦」。
#     ★ 两个真实后果：
#       ① 一条矛盾的记录可以**永久躺在库里**，没有任何一层会报错；
#       ② 更危险 —— **导出器会照着它写出一份错的 `crs.yaml`**（文件是派生产物），
#          下游那四个消费者一句警告都不会有。
#     修法：`sql/25_crs_two_level_guard.sql`（写入侧触发器，两张表都挂）。
#
# ★ 每条 UPDATE **各起一个事务**：触发器报错会中止整个事务块，
#   几条挤在一个 BEGIN 里，第二条起就只会报「current transaction is aborted」，
#   那样测的就不是守卫，是事务语义。


def _try_update(sql_text: str) -> tuple[bool, str]:
    """在一个独立事务里试一次写入，返回 (是否被拒, 合并的错误正文)。"""
    r = subprocess.run(
        ["docker", "exec", "-i", "rp-pg", "sh", "-c",
         'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=0 -f -'],
        input=f"BEGIN;\n{sql_text}\nROLLBACK;\n",
        capture_output=True, text=True,
        env={"DOCKER_CONFIG": str(REPO / ".dockerhome"), "PATH": "/usr/bin:/bin"})
    out = r.stdout + r.stderr
    return ("ERROR" in out), out


_mine = ("UPDATE road_section SET crs_epsg=4547, crs_zone_number=38, "
         "crs_central_meridian_deg=114 WHERE id=6;")
_rejected, _msg = _try_update(_mine)
ok("★ 态②：给路段配 4547（项目是 4548）→ **被拒**（quickstart 第 2 步的主张）",
   _rejected, _msg.strip()[:200])
ok("★ 而且报错里**两级的值都在**（SC-009：只报「基准冲突」不算做对）",
   "4548" in _msg and "4547" in _msg, _msg.strip()[:200])
ok("★ 报错里**两级的主体名都在**（项目名、路段名）—— 接手的人要知道往哪边改",
   "项目" in _msg and "路段" in _msg and "毕设" in _msg, _msg.strip()[:200])

# ★ 对称性：只挂 road_section 是不够的 —— 改**项目**同样能造出一对矛盾，
#   而且它改的是**全项目所有路段**的依据。
_mine2 = "UPDATE design_project SET crs_epsg=4547 WHERE id=2;"
_rej2, _msg2 = _try_update(_mine2)
ok("★★ 态②**对称**：改项目（让已配好的路段失配）→ 也被拒"
   "（只挂一张表的守卫拦不住这条路）",
   _rej2 and "4547" in _msg2 and "4548" in _msg2, _msg2.strip()[:200])

# ★ 反向：只会红不会绿 = 验不出东西。同值的写入**必须照过**。
_ok_rej, _ok_msg = _try_update("UPDATE road_section SET crs_epsg=4548 WHERE id=6;")
ok("★★ 对照：写成**相同**的值 → 必须放行"
   "（一个把所有写入都拒掉的守卫，等于让平台不能用了）",
   (not _ok_rej) and "UPDATE 1" in _ok_msg, _ok_msg.strip()[:200])

# ★ 反向②：清掉一级 → 必须放行（E2 态③④，缺一边不算冲突）
_ok3, _ok3msg = _try_update("UPDATE road_section SET crs_epsg=NULL WHERE id=6;")
ok("★★ 对照：路段级清空（只剩项目级）→ 必须放行 —— E2 态③④ 是合法状态，"
   "守卫只拦第 ② 态，不做「必须两级都填」",
   (not _ok3) and "UPDATE 1" in _ok3msg, _ok3msg.strip()[:200])

# ★ 元测试：证明上面那几条真的连到了守卫 —— 把触发器摘掉，同样的写入
#   必须**变成成功**。否则「被拒」可能是别的原因（外键、权限、语法）。
_drop = ("DROP TRIGGER IF EXISTS crs_two_level_guard_section ON road_section;")
_r = subprocess.run(
    ["docker", "exec", "-i", "rp-pg", "sh", "-c",
     'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -q -c ' + json.dumps(_drop)],
    capture_output=True, text=True,
    env={"DOCKER_CONFIG": str(REPO / ".dockerhome"), "PATH": "/usr/bin:/bin"})
try:
    _norej, _nomsg = _try_update(_mine)
    ok("★元测试：把触发器摘掉后，**同一句 UPDATE 就不再被拒** —— "
       "证明刚才那次拒绝确实出自这个守卫，不是外键或别的原因",
       not _norej, _nomsg.strip()[:200])
finally:
    # ★ 无论上面断言成不成立，守卫都必须装回去 —— 否则这个 probe 会
    #   留下一台**没有守卫**的库，而它自己是绿的。
    _reapply = subprocess.run(
        ["docker", "exec", "-i", "rp-pg", "sh", "-c",
         'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -q -f -'],
        stdin=open(REPO / "scaffold" / "sql" / "25_crs_two_level_guard.sql", encoding="utf-8"),
        capture_output=True, text=True,
        env={"DOCKER_CONFIG": str(REPO / ".dockerhome"), "PATH": "/usr/bin:/bin"})
    ok("★元测试：守卫已**装回**（probe 不许留一台没守卫的库给自己绿）",
       _reapply.returncode == 0, (_reapply.stdout + _reapply.stderr).strip()[:200])
    _back, _backmsg = _try_update(_mine)
    ok("★元测试：装回之后同一句又被拒 —— 收尾闭环",
       _back, _backmsg.strip()[:200])

_after = _psql("select coalesce(crs_epsg::text,'') from road_section where id=6;")
ok("★ 全部试完，库里的值**没被改动**（每条都 ROLLBACK 了）",
   _after.stdout.strip() == "4548", _after.stdout.strip()[:80])


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
