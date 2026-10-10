"""仿真轨迹与交付产物契约 —— 五件套第 5 件的**内容侧**。

运行（离线可跑，不连数据库、不连容器、不连外网）：
  cd /data/cy/shujuku/scaffold
  uv run --with fastapi --with httpx --with pyyaml python tests/contract/test_sim_track.py

为什么单独一组（而不是塞进 test_console.py）：

  `test_console.py` 钉的是**M9 这个模块的集成面**（不直连库、`/gw` 的边界、
  页面不写死上游）。本文件钉的是**交付目录里的东西**：轨迹 CSV 的列、
  沿程量的编码、产物的登记与降级、带号这个假定的可见性。
  两者的失效方式完全不同 —— 一边是"M9 越权了"，一边是"少了一列/少了一个产物"。
  混在一起，一红起来得先猜是哪一类。

本文件钉住四件事：

  1) **US2：三列平面位姿。** CSV 表头必须有 `x_m / y_m / s_m / heading_deg`，
     且**删掉任一列都要被认出**（逐列元测试）。缺列时页面必须**报告**，
     不得把位置默认成 (0,0) —— 后者会画出一张看起来正常、实际全错的图。

  2) **US3：没有数据 ≠ 实测 0。** 无数据走条纹，且**不参与色带**。
     元测证据：把无数据分支改成色带颜色后必须被认出来。

  3) **US4：交付目录的内容侧。** 清单登记的项与磁盘一致；`present: false`
     表现为「未产出」而不是报错；视频的 Range 请求回 **206 + Content-Range**
     （不回 206 也能播，只是拖不动 —— 所以这一条必须单独验）。

  4) **US5：带号是一个看得见的假定。** `ready` 与 `assumed` 是**两个**标记，
     不能合并；推断依据要能一条条读到；删掉 `assumed` 标记必须被认出。

★ 本文件**不打真库、不起容器**。它读交付目录 + 用 TestClient 走 M9 的路由，
  所以容器不可用时它照样能跑 —— 这是它能在 CI 上守住这几条的**前提**。
"""
from __future__ import annotations

import json
import pathlib
import re
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]        # → scaffold/
REPO = ROOT.parent                                        # → 仓库根
M9_DIR = ROOT / "modules" / "M9-console"
DELIVERY = REPO / "artifacts" / "sim"                     # ★ 交付目录（仓库根下）
if str(M9_DIR) not in sys.path:
    sys.path.insert(0, str(M9_DIR))

try:
    from fastapi.testclient import TestClient
except Exception as exc:                                   # pragma: no cover
    print(f"!! 需要 fastapi + httpx：{exc}")
    raise SystemExit(70)

import app as APP                                          # noqa: E402

client = TestClient(APP.app)


def mesh_bbox(path: pathlib.Path, mesh: dict):
    """按清单里的网格元数据解出顶点，返回 ((xmin,xmax),(ymin,ymax))。

    ★ 尺寸从**清单**取，再从**磁盘上的文件长度**反推三角形数 —— 不猜布局。
      猜布局的测试会在布局变了之后继续绿，那比没有测试更坏。
    """
    nu, nv = mesh["nu"], mesh["nv"]
    nvtx = nu * nv
    pos_bytes = nvtx * 3 * 4
    id_bytes = 4 if mesh["index_dtype"] == "uint32" else 2
    raw = path.read_bytes()
    n_tri = (len(raw) - pos_bytes) // (3 * id_bytes)
    assert pos_bytes + n_tri * 3 * id_bytes == len(raw), "文件长度与清单不符"
    pos = struct.unpack_from(f"<{nvtx * 3}f", raw, 0)
    xs, ys = pos[0::3], pos[1::3]
    return (min(xs), max(xs)), (min(ys), max(ys))


def main() -> int:
    fails: list[str] = []

    def ok(label: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"  ✓ {label}")
        else:
            print(f"  ✗ {label}" + (f"　{detail}" if detail else ""))
            fails.append(label)

    _sim = (M9_DIR / "sim.html").read_text(encoding="utf-8")

    # ================================================================ US2
    print("=== US2) 车辆轨迹：四列平面位姿，删任一列必须被认出 ===")

    csv_path = DELIVERY / "wheel_loads.csv"
    # ★ 这里**硬要求** CSV 存在，不做"没有就跳过"。
    #   跳过的检查就是宪法原则 V 说的空转 —— 它会永远绿，
    #   而 US2 其实没交付。
    ok("交付目录里有轮荷轨迹 CSV（US2 的交付物）", csv_path.is_file(), str(csv_path))

    for _c in ("s_m", "x_m", "y_m", "heading_deg"):
        ok(f"页面源码引用了 {_c}（不是测试自己在读 CSV）", f'"{_c}"' in _sim, _c)

    _ps = _sim.split("function parseTrack")[1].split("function ")[0] if "function parseTrack" in _sim else ""
    ok("缺平面位姿时返回 missingPose（而不是回退到 0,0）", "missingPose: true" in _ps, _ps[:120])
    ok("缺列时**不**把位置默认成 0（源码里没有 x: 0 之类的兜底）",
       "x: 0" not in _ps and "y: 0" not in _ps)
    ok("元测试：把 missingPose 分支去掉后必须被认出来（非摆设）",
       "missingPose: true" not in _ps.replace("missingPose: true", "missingPose: false"))

    if csv_path.is_file():
        hdr = csv_path.read_text(encoding="utf-8").split("\n")[0].strip().split(",")

        def pose_missing(h: list[str]) -> list[str]:
            """与页面 parseTrack 同一套判据：四列缺一即缺位姿。"""
            return [c for c in ("x_m", "y_m", "s_m", "heading_deg") if c not in h]

        ok(f"CSV 表头含 s_m / x_m / y_m / heading_deg（共 {len(hdr)} 列）",
           not pose_missing(hdr), f"缺 {pose_missing(hdr)}")

        # ★ 元测试：**逐列**删掉，每一列都必须被认出。
        #   一次只测一列是不够的 —— 缺列检查写错成 and 的时候，
        #   单删一列仍然会被抓到，只有逐列才能暴露。
        for _c in ("x_m", "y_m", "s_m", "heading_deg"):
            _mut = [x for x in hdr if x != _c]
            ok(f"元测试：删掉 {_c} 后必须被认出（非摆设）",
               pose_missing(_mut) == [_c], f"认成 {pose_missing(_mut)}")

        # 列顺序：新列必须**追加在尾部**，前 5 列不许动。
        # ★ 重排不会报错 —— 它只会让既有的 plot_wheel_loads.py 静默读错列。
        ok("前三列仍是 t_s,s_m,lat_m（重排会让既有消费方静默读错列）",
           hdr[:3] == ["t_s", "s_m", "lat_m"], str(hdr[:3]))
        ok("x_m / y_m / heading_deg 紧跟在 chassis_z_m 之后（追加，不插队）",
           hdr[4:8] == ["chassis_z_m", "x_m", "y_m", "heading_deg"], str(hdr[4:8]))

        # ★★ US3 的**前提**：轨迹没有覆盖整条路，所以"无数据"这条分支真的会被触发。
        #    如果哪天轨迹覆盖了全程，无数据分支就再也不会出现，
        #    而所有测试照样绿 —— US3 于是退化成一句空话。这条断言守的是这个前提。
        _s = [float(ln.split(",")[hdr.index("s_m")])
              for ln in csv_path.read_text(encoding="utf-8").split("\n")[1:] if ln.strip()]
        ok("轨迹里程没有覆盖整条路（否则『无数据』分支永不触发，US3 变成空话）",
           min(_s) > 0.0 and max(_s) < 5805.4,
           f"轨迹 s∈[{min(_s):.2f},{max(_s):.2f}]，路面 u∈[0,5805.42]")

        # 轨迹的平面位置必须落在网格的平面外接矩形内（T018 的判据）
        _mani_f = DELIVERY / "manifest.json"
        _mo = None
        if _mani_f.is_file():
            _mo = next((a for a in json.loads(_mani_f.read_text(encoding="utf-8"))["artifacts"]
                        if a["name"] == "terrain_overview.bin"), None)
        _bin = DELIVERY / "terrain_overview.bin"
        if _mo and _mo.get("present") and _bin.is_file():
            ix, iy = hdr.index("x_m"), hdr.index("y_m")
            xs, ys = [], []
            for ln in csv_path.read_text(encoding="utf-8").split("\n")[1:]:
                if not ln.strip():
                    continue
                f = ln.split(",")
                xs.append(float(f[ix])); ys.append(float(f[iy]))
            bx, by = mesh_bbox(_bin, _mo["mesh"])
            ok(f"轨迹 {len(xs)} 点全部落在网格平面范围内（T018 判据）",
               min(xs) >= bx[0] and max(xs) <= bx[1] and min(ys) >= by[0] and max(ys) <= by[1],
               f"轨迹 X[{min(xs):.0f},{max(xs):.0f}] Y[{min(ys):.0f},{max(ys):.0f}] "
               f"网格 X[{bx[0]:.0f},{bx[1]:.0f}] Y[{by[0]:.0f},{by[1]:.0f}]")

    # ★ US2 的可见性前提：鸟瞰下车辆实宽只有约 0.44 px，
    #   按真实尺寸画等于什么都没画。所以符号倍数必须是**常驻读数**，
    #   而不是一个藏在代码里的常量 —— 否则图上的车会看起来像一条噪点。
    #   这两条是页面源码检查，不依赖 CSV 是否在，所以放在 if 外面。
    ok("页面有车辆符号放大控件，且倍数常驻显示",
       'id="symscale"' in _sim and 'id="symval"' in _sim)
    ok("符号的 z 取自网格，不是 CSV 的 chassis_z_m（质心高度会让符号穿地或浮起）",
       "meshZAt" in _sim and "p.z = meshZAt" in _sim)
    ok("符号按 heading 定向（朝向来自 CSV，不是写死的）",
       "p.head" in _sim and "Math.cos(th)" in _sim)

    # ================================================================ US3
    print("\n=== US3) 沿程量：无数据与实测 0 必须视觉可分 ===")

    ok("页面有沿程量选择器（高程 / 轮荷 / 曲率）",
       'id="quantity"' in _sim and all(v in _sim for v in ('value="elev"', 'value="load"', 'value="curv"')))
    ok("页面有独立的「无数据」视觉编码（条纹色块）",
       "sno" in _sim and "repeating-linear-gradient" in _sim)
    ok("着色器里无数据走**另一条分支**，不参与色带",
       "vQ.y < 0.5" in _sim and "stripe" in _sim)

    _fs = _sim.split("const FS = [")[1].split("].join")[0]
    _nodata = _fs.split("vQ.y < 0.5")[1].split("return;")[0]
    # ★ 这一条是 US3 的实质：无数据分支里不许出现色带的那种 mix，
    #   否则「没测」就和「测出来是 0」同色了。
    ok("无数据分支里不出现色带的 mix（否则它就和实测 0 同色了）",
       "mix(vec3(0.16,0.36,0.52)" not in _nodata, _nodata.strip()[:160])
    ok("元测试：把无数据分支改成色带颜色后必须被认出来（非摆设）",
       "mix(vec3(0.16,0.36,0.52)" in
       _fs.replace("vec3(0.20,0.22,0.25)",
                   "mix(vec3(0.16,0.36,0.52), vec3(0.85,0.63,0.30), 0.0)")
          .split("vQ.y < 0.5")[1].split("return;")[0])
    ok("图例把「无数据」与刻度分开写（读者能分辨两者）",
       "无数据" in _sim and "实测 0" in _sim)
    ok("轮荷按里程对齐到路面（用的是 s_m 与网格 u 步长，不是另造索引）",
       "sampleLoadAtU" in _sim and "uStep" in _sim)
    ok("沿程查询有读数（悬停/点击读出该处数值与里程）",
       "probeAt" in _sim and "id=\"probe\"" in _sim)

    # 页面只显示、不判定
    ok("页面没有把色带说成阈值（不出现「阈值」）", "阈值" not in _sim)
    ok("元测试：混进「阈值」后必须被认出来（非摆设）",
       "阈值" in _sim + "<p>超过阈值</p>")

    # ================================================================ US4
    print("\n=== US4) 交付产物：登记齐全 + 未产出降级 + 视频 Range ===")

    mani_path = DELIVERY / "manifest.json"
    ok("交付目录里有清单", mani_path.is_file(), str(mani_path))
    if mani_path.is_file():
        mani = json.loads(mani_path.read_text(encoding="utf-8"))
        names = [a["name"] for a in mani["artifacts"]]
        for n in ("terrain_overview.bin", "terrain_near.bin", "wheel_loads.csv",
                  "vehicle_on_crg_lane_right_10x.mp4",
                  "vehicle_on_crg_lane_right_realtime.mp4"):
            ok(f"清单登记了 {n}", n in names, str(names))
        # ★ 清单的 bytes 必须是**从磁盘量的**，不是生产者随手写的常量
        for a in mani["artifacts"]:
            if a.get("present"):
                p = DELIVERY / a["name"]
                ok(f"{a['name']} 的 bytes 与磁盘一致",
                   p.is_file() and a.get("bytes") == p.stat().st_size,
                   f"清单 {a.get('bytes')} 磁盘 {p.stat().st_size if p.is_file() else '—'}")
        # ★ 契约里**已经**声明了 vehicle_track / video —— 契约先于产物
        ctr = ROOT / "contracts" / "delivery" / "artifact_manifest.v0.1.schema.json"
        if ctr.is_file():
            kind_enum = json.loads(ctr.read_text(encoding="utf-8"))[
                "properties"]["artifacts"]["items"]["properties"]["kind"]["enum"]
            ok("契约的 kind 枚举已含 vehicle_track 与 video（契约先于产物）",
               "vehicle_track" in kind_enum and "video" in kind_enum, str(kind_enum))

    ok("页面把 present 为假的产物显示为「未产出」",
       "未产出" in _sim and "tag missing" in _sim)
    ok("页面不因缺失产物而报错（缺失只降级，不进 fatal 分支）",
       "未产出" in _sim and "fatal(" not in _sim.split("if (a.present)")[1].split("}")[0])

    # ★★ 这一整块都必须把 APP.SIM_ARTIFACTS_DIR 指向**真的交付目录**再发请求。
    #    默认值是容器里的 /data/sim，本机测试进程里不存在 —— 不指的话
    #    /artifacts/* 全回 503。第一版这里被 `if status == 200` 包住，
    #    于是整组**静默空转**、全绿。空转的检查比没有检查更坏。
    orig_dir = APP.SIM_ARTIFACTS_DIR
    APP.SIM_ARTIFACTS_DIR = DELIVERY
    try:
        # ★★ 接口的**形状**必须被钉住。页面是
        #    `const { manifest, crs } = await resp.json()` ——
        #    如果哪天接口改成直接返回扁平的清单（磁盘上那份文件就是扁平的，
        #    很容易顺手 `return JSONResponse(manifest)`），页面会 destructure
        #    出 undefined，表现为"载入失败"。
        #    而所有断字面量的测试照样绿 —— 因为它们根本没看形状。
        r0 = client.get("/v1/sim/manifest")
        ok("接口顶层形状是 {manifest, crs}（页面按这个解构）",
           r0.status_code == 200 and {"manifest", "crs"} <= set(r0.json().keys()),
           str(sorted(r0.json().keys())) if r0.status_code == 200 else str(r0.status_code))
        ok("页面确实按 {manifest, crs} 解构（形状与消费方一致）",
           "const { manifest, crs } = await resp.json()" in _sim)
        if r0.status_code == 200:
            ok("manifest 里带 artifacts（页面据此挑网格）",
               isinstance(r0.json()["manifest"].get("artifacts"), list),
               str(type(r0.json()["manifest"].get("artifacts"))))

        vid = DELIVERY / "vehicle_on_crg_lane_right_10x.mp4"
        ok("交付目录里的真 MP4 存在", vid.is_file(), str(vid))
        size = vid.stat().st_size
        rv = client.get("/artifacts/vehicle_on_crg_lane_right_10x.mp4",
                        headers={"Range": "bytes=0-2047"})
        # ★ 不回 206 视频**照样能播**，只是拖不动进度条 ——
        #   所以这条失效是"静默"的，必须单独钉。
        ok("MP4 的 Range 请求 → 206", rv.status_code == 206, f"{rv.status_code}")
        ok("206 带 Content-Range 且区间正确",
           rv.headers.get("content-range") == f"bytes 0-2047/{size}",
           str(rv.headers.get("content-range")))
        ok("206 的实体长度就是请求的区间长度", len(rv.content) == 2048, f"{len(rv.content)}")
        ok("MP4 报的是 video/mp4（否则浏览器不给拖进度条）",
           "video/mp4" in str(rv.headers.get("content-type")), str(rv.headers.get("content-type")))
        ok("MP4 声明 accept-ranges: bytes",
           "bytes" in str(rv.headers.get("accept-ranges")), str(rv.headers.get("accept-ranges")))
        rv2 = client.get("/artifacts/vehicle_on_crg_lane_right_10x.mp4",
                         headers={"Range": f"bytes={size - 10}-"})
        ok("后缀区间（尾部 10 字节）→ 206 且长度 10",
           rv2.status_code == 206 and len(rv2.content) == 10,
           f"{rv2.status_code}/{len(rv2.content)}")
        ok("越界区间 → 416（不假装成 200）",
           client.get("/artifacts/vehicle_on_crg_lane_right_10x.mp4",
                      headers={"Range": f"bytes={size + 10}-"}).status_code == 416)
        ok("元测试：不带 Range 时必须回 200（证明 206 来自 Range 处理，不是永远 206）",
           client.get("/artifacts/vehicle_on_crg_lane_right_10x.mp4").status_code == 200)
        ok("元测试：Range 语法写坏时必须回落到 200 全量（不能报错）",
           client.get("/artifacts/vehicle_on_crg_lane_right_10x.mp4",
                      headers={"Range": "bytes=abc"}).status_code == 200)

        # ============================================================ US5
        print("\n=== US5) 坐标基准：ready 与 assumed 是两个标记 ===")

        crs_f = M9_DIR / "config" / "crs.yaml"
        ok("crs.yaml 存在（带号是配置项，不是代码里的常量）", crs_f.is_file(), str(crs_f))
        if crs_f.is_file():
            c = crs_f.read_text(encoding="utf-8")
            ok("crs.yaml 标了 assumed（推断值必须显式标注）", "assumed: true" in c)
            ok("crs.yaml 记了推断依据", "evidence:" in c)
            ok("元测试：把 assumed 删掉后必须被认出（非摆设）",
               "assumed: true" not in c.replace("assumed: true", "assumed_removed: true"))

        ok("页面把 ready 与 assumed 显示为两个不同的标记",
           "区域帧可用" in _sim or "区域帧未启用" in _sim)
        ok("页面显示当前带号与中央子午线（不写死 39）",
           "gf.zone_number" in _sim and "gf.central_meridian_deg" in _sim)
        ok("页面渲染推断依据（evidence 逐条列出）",
           "gf.evidence" in _sim and "ul class='ev'" in _sim)
        ok("页面说明「确认前不填 EPSG」（不凭记忆填带号编码）", "确认前不填" in _sim)
        # ★★ 元测试：判据必须落在「assumed 这个标记本身」，
        #    而不是那句中文措辞 —— 措辞可以改，标记不能没有。
        ok("元测试：删掉 assumed 标记后必须被认出来（非摆设）",
           "gf.assumed" not in _sim.replace("gf.assumed", "gf.ready"))

        # 接口侧：★ **不**用 `if status == 200` 包住断言 ——
        # 那样一旦接口坏了，整组检查会静默消失，结果照样全绿。
        rm = client.get("/v1/sim/manifest")
        ok("/v1/sim/manifest 可达（不是 503）", rm.status_code == 200, f"{rm.status_code}")
        if rm.status_code == 200:
            gf = rm.json().get("crs", {}).get("geographic_frame", {})
            ok("接口暴露 zone_number / ready / assumed 三个独立字段",
               all(k in gf for k in ("zone_number", "ready", "assumed")), str(sorted(gf)))
            ok("ready 与 assumed 是两个字段，不能合并成一个",
               isinstance(gf.get("ready"), bool) and isinstance(gf.get("assumed"), bool))
            ok("接口带出推断依据（>=4 条）",
               isinstance(gf.get("evidence"), list) and len(gf["evidence"]) >= 4,
               f"{len(gf.get('evidence') or [])} 条")
    finally:
        APP.SIM_ARTIFACTS_DIR = orig_dir

    # —— 本组的元测试：确认「读的是真交付目录」这件事本身被抓着
    ok("元测试：SIM_ARTIFACTS_DIR 被指到不存在的目录后，产物路由必须不再 200（非空转）",
       _dir_probe(client) is not None)

    print("\n结果：" + ("全部通过 ✓" if not fails else f"失败 {len(fails)} 项 → {fails}"))
    return 1 if fails else 0


def _dir_probe(c) -> bool:
    """把交付目录临时指到一个空目录，确认产物路由**确实**依赖它。

    ★ 没有这一条，上面那一整块只要碰巧读到一份缓存的清单就能全绿，
      而"读的到底是不是真目录"从未被验过。
    """
    import tempfile
    orig = APP.SIM_ARTIFACTS_DIR
    try:
        with tempfile.TemporaryDirectory() as td:
            APP.SIM_ARTIFACTS_DIR = pathlib.Path(td)
            r = c.get("/artifacts/vehicle_on_crg_lane_right_10x.mp4")
            return r.status_code == 503
    finally:
        APP.SIM_ARTIFACTS_DIR = orig


if __name__ == "__main__":
    raise SystemExit(main())
