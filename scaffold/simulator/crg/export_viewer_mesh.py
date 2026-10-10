#!/usr/bin/env python3
"""CRG → 抽稀三维网格（供 M9 的 /sim 页面用）

为什么需要这一步
----------------
`route_0p1m.crg` 是 96.4 MiB 的 ASCII。**不能发给浏览器。**
本脚本把它抽稀成紧凑二进制，压缩约 341 倍。

为什么不需要带号
----------------
CRG 是**曲线坐标**（u 沿路、v 横向），本身不含 X/Y。
但数据行的第 1 列是**航向 φ（弧度）** —— 于是 X/Y 由积分得到：

    X(u) = ∫ cos φ du        Y(u) = ∫ sin φ du

这是纯局部帧运算，与大地基准无关。本脚本**不读数据库**、不联网。
★ 端点自检：u=0 应为 (0,0)，u=5805.4 应约 (-4521.12, 3119.87)。

数据行结构（已实测，不是猜的）
------------------------------
每行 87 个字段：
    字段 0      航向 φ，弧度      （实测首行 2.6086571868 = 149.46°，与起点方位角一致）
    字段 1..86  高程，米          （v 从 -4.25 到 +4.25，步长 0.1，共 86 个通道）
u 不写出来，由 REFERENCE_LINE_INCREMENT 隐含。

用法
----
    python3 export_viewer_mesh.py route_0p1m.crg --out /data/cy/shujuku/artifacts/sim
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import pathlib
import re
import struct
import sys

# 要取的 9 个横向样点（米，正 = 左）。★ 不是均匀采样 —— 它们落在有物理意义的 v 上，
# 所以网格的边线就是路面边线与车道边线，人一眼能看出车在哪条车道。
V_SAMPLES = [-4.250, -3.500, -2.625, -1.750, 0.000, 1.750, 2.625, 3.500, 4.250]

# 两档 LOD
LODS = {
    "terrain_overview.bin": 4.0,
    "terrain_near.bin": 1.0,
}

UINT16_MAX_VERTICES = 65536

# 伴随产物：本脚本**不生成**它们，但负责登记。缺失时登记为 present: false。
# ★ (文件名, kind, 说明)。kind 必须落在契约的 enum 里。
COMPANION_ARTIFACTS = [
    (
        "wheel_loads.csv",
        "vehicle_track",
        "整车仿真产出的轮荷时间序列（7986 行 / 0.05 s）。"
        "前 5 列 t_s,s_m,lat_m,speed_mps,chassis_z_m 之后是 x_m,y_m,heading_deg，"
        "再往后是四个轮的 Fz/Fy/Fx/pz。heading_deg 用设计表同一套方位角约定"
        "（X=北、Y=东，从 +X 转向 +Y 为正）。",
    ),
    (
        "vehicle_on_crg_lane_right_10x.mp4",
        "video",
        "全路段靠右行驶，10 倍速（797 帧 @ 20 fps，1280×720）。",
    ),
    (
        "vehicle_on_crg_lane_right_realtime.mp4",
        "video",
        "同一段仿真的 2 fps 慢放版本（797 帧，1280×720）。",
    ),
]

COMPANION_MIME = {
    "vehicle_track": "text/csv",
    "video": "video/mp4",
    "figure": "image/png",
    "other": "application/octet-stream",
}

DEFAULT_CRS_CONFIG = pathlib.Path("modules/M9-console/config/crs.yaml")


def _read_crs_flags(path: pathlib.Path | None) -> tuple[bool, bool]:
    """从 crs.yaml 读 (ready, assumed)。

    ★ 不引 PyYAML：本脚本的标准库-only 属性是它能在任意目录直接跑的原因，
      为两个布尔量破这条规矩不划算。所以只做**定点匹配**，匹配不到就
      响亮地说出来 —— 静默 fallback 到 (False, True) 会让"读不到配置"
      伪装成"配置就是这么写的"。
    """
    if path is None or not path.is_file():
        print(f"    ⚠ 读不到 crs.yaml（{path}）—— frame 标记回退为 ready=False assumed=True")
        return False, True
    txt = path.read_text(encoding="utf-8")
    # 只看 geographic_frame 段，避免命中 local_frame 里的同名键
    seg = txt.split("geographic_frame:", 1)
    seg = seg[1] if len(seg) > 1 else txt
    ready = re.search(r"^\s*ready:\s*(true|false)", seg, re.M)
    assumed = re.search(r"^\s*assumed:\s*(true|false)", seg, re.M)
    if not ready or not assumed:
        print(f"    ⚠ crs.yaml 里 ready / assumed 没认出来（{path}）—— 回退为 False/True")
        return False, True
    return ready.group(1) == "true", assumed.group(1) == "true"


def parse_header(path: pathlib.Path) -> dict:
    """读 CRG 头部，返回关键参数。数据段由 `$$$$` 标记。"""
    head = {}
    data_line = None
    with path.open("r", encoding="ascii", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            if line.startswith("$$$$"):
                data_line = lineno + 1
                break
            if "=" in line and not line.startswith("*"):
                k, _, v = line.partition("=")
                head[k.strip()] = v.strip()
    if data_line is None:
        raise SystemExit("!! 找不到数据段标记 $$$$")
    head["_data_line"] = data_line
    return head


def read_crg(path: pathlib.Path):
    """流式读 CRG，产出 (phi, zs) 序列。zs 是 86 个通道的高程。"""
    head = parse_header(path)
    du = float(head["REFERENCE_LINE_INCREMENT"])
    v_right = float(head["LONG_SECTION_V_RIGHT"])
    v_left = float(head["LONG_SECTION_V_LEFT"])
    dv = float(head["LONG_SECTION_V_INCREMENT"])
    nv_chan = int(round((v_left - v_right) / dv)) + 1

    phis: list[float] = []
    zrows: list[list[float]] = []
    with path.open("r", encoding="ascii", errors="replace") as fh:
        for _ in range(head["_data_line"] - 1):
            fh.readline()
        for raw in fh:
            parts = raw.split()
            if len(parts) != nv_chan + 1:
                raise SystemExit(
                    f"!! 数据行列数异常：期望 {nv_chan + 1}（1 个航向 + {nv_chan} 个高程），"
                    f"实得 {len(parts)}"
                )
            phis.append(float(parts[0]))
            zrows.append([float(x) for x in parts[1:]])

    if not phis:
        raise SystemExit("!! 数据段为空")
    return du, v_right, dv, nv_chan, phis, zrows


def integrate_xy(phis: list[float], du: float) -> list[tuple[float, float]]:
    """由航向积分出平面坐标。梯形法。

    ★ 端点自检不在这里做 —— 它在 main() 里做，且**失败即退出**。
      一个只打印警告的自检等于没有自检。
    """
    xs = [0.0]
    ys = [0.0]
    for i in range(1, len(phis)):
        # 用相邻两站的航向均值积分（梯形），与生成端的 0.1 m 解析积分同阶
        c = 0.5 * (math.cos(phis[i - 1]) + math.cos(phis[i]))
        s = 0.5 * (math.sin(phis[i - 1]) + math.sin(phis[i]))
        xs.append(xs[-1] + c * du)
        ys.append(ys[-1] + s * du)
    return list(zip(xs, ys))


def elev_at(zrow: list[float], v_right: float, dv: float, v: float) -> float:
    """在 v 处线性插值高程。

    ★ 插值而不是取最近通道：车道边缘在 v=±3.50，而通道步长是 0.1，
      最近的通道是 ±3.45/±3.55（差 50 mm）。插值让网格边线**正好**落在
      车道边缘上，几何与设计意图一致，不留一个「差一点点」的问题。
    """
    t = (v - v_right) / dv
    i0 = int(math.floor(t))
    i0 = max(0, min(i0, len(zrow) - 2))
    frac = t - i0
    return zrow[i0] * (1.0 - frac) + zrow[i0 + 1] * frac


def build_lod(
    step_m: float,
    du: float,
    v_right: float,
    dv: float,
    phis: list[float],
    zrows: list[list[float]],
    xy: list[tuple[float, float]],
):
    """抽稀出一档 LOD，返回 (positions, indices, nu, actual_step)。"""
    stride = max(1, int(round(step_m / du)))
    idx_u = list(range(0, len(phis), stride))
    # ★ 必须补上最后一站，否则路的终点会短一截，而短掉的那一截看不出来
    if idx_u[-1] != len(phis) - 1:
        idx_u.append(len(phis) - 1)

    nu = len(idx_u)
    nv = len(V_SAMPLES)
    positions: list[float] = []
    for iu in idx_u:
        x, y = xy[iu]
        phi = phis[iu]
        # 切向 (cos φ, sin φ)；左法向 = (-sin φ, cos φ)。v>0 = 左。
        nx, ny = -math.sin(phi), math.cos(phi)
        zrow = zrows[iu]
        for v in V_SAMPLES:
            z = elev_at(zrow, v_right, dv, v)
            positions.extend((x + v * nx, y + v * ny, z))

    indices: list[int] = []
    for iu in range(nu - 1):
        for iv in range(nv - 1):
            a = iu * nv + iv
            b = a + 1
            c = a + nv
            d = c + 1
            indices.extend((a, c, b, b, c, d))

    return positions, indices, nu, nv, stride * du


def main() -> int:
    ap = argparse.ArgumentParser(description="CRG → 抽稀三维网格")
    ap.add_argument("crg", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, required=True, help="交付目录")
    ap.add_argument(
        "--expect-end",
        default="-4521.122,3119.874",
        help="端点自检期望值（X,Y），来自本次会话对 CRG 的实测",
    )
    ap.add_argument(
        "--crs-config",
        type=pathlib.Path,
        default=None,
        help="crs.yaml 路径（默认自动向上找模块目录里的那一份）",
    )
    args = ap.parse_args()

    # crs.yaml 的位置：默认按仓库布局找；找不到就让 _read_crs_flags 说出来。
    if args.crs_config is None:
        here = pathlib.Path(__file__).resolve()
        for up in here.parents:
            cand = up / DEFAULT_CRS_CONFIG
            if cand.is_file():
                args.crs_config = cand
                break

    if not args.crg.is_file():
        print(f"!! 找不到 CRG：{args.crg}", file=sys.stderr)
        return 10

    print(f"==> 读 {args.crg}（{args.crg.stat().st_size / 1048576:.1f} MiB）")
    du, v_right, dv, nv_chan, phis, zrows = read_crg(args.crg)
    print(f"    u 站数 = {len(phis)}    步长 = {du} m    v 通道 = {nv_chan}")

    xy = integrate_xy(phis, du)

    # ★★ 端点自检：失败即退出，不产出任何东西。
    #    一个只打印警告的自检，会在产物已经写出去之后才被人看到。
    ex, ey = (float(t) for t in args.expect_end.split(","))
    ax, ay = xy[-1]
    err = math.hypot(ax - ex, ay - ey)
    print(f"    端点自检：积分得 ({ax:.3f}, {ay:.3f})  期望 ({ex:.3f}, {ey:.3f})  差 {err:.3f} m")
    if err > 5.0:
        print(
            f"!! 端点自检失败（差 {err:.3f} m > 5 m）。"
            "航向积分与生成端不一致，产物不可信 —— 不写任何文件。",
            file=sys.stderr,
        )
        return 11

    args.out.mkdir(parents=True, exist_ok=True)
    entries = []
    for name, step in LODS.items():
        positions, indices, nu, nv, actual_step = build_lod(
            step, du, v_right, dv, phis, zrows, xy
        )
        nvtx = nu * nv
        # ★★ 必须断言。越界时 uint16 会**静默截断**，几何以一种
        #    「看起来只是有点错」的方式坏掉 —— 不报错，只是错。
        if nvtx >= UINT16_MAX_VERTICES:
            print(
                f"!! {name}: 顶点数 {nvtx} 达到/超过 {UINT16_MAX_VERTICES}，"
                "uint16 索引会静默截断。改小步长或改用 uint32。不写文件。",
                file=sys.stderr,
            )
            return 12

        blob = struct.pack(f"<{len(positions)}f", *positions)
        blob += struct.pack(f"<{len(indices)}H", *indices)
        target = args.out / name
        target.write_bytes(blob)
        print(
            f"    {name}: {nu} x {nv} = {nvtx} 顶点, {len(indices) // 3} 三角, "
            f"{len(blob) / 1024:.0f} KB  (u 步长 {actual_step:.1f} m, 索引 uint16)"
        )
        entries.append(
            {
                "name": name,
                "kind": "terrain_mesh",
                "present": True,
                "bytes": len(blob),
                "mime": "application/octet-stream",
                "note": f"u 步长 {actual_step:.1f} m；由 route_0p1m.crg 抽稀",
                "mesh": {
                    "nu": nu,
                    "nv": nv,
                    "position_dtype": "float32",
                    "index_dtype": "uint16",
                    "u_step_m": actual_step,
                    "v_samples_m": V_SAMPLES,
                    "z_source": (
                        "CRG 实测地形高程（86 通道在目标 v 处线性插值）。"
                        "★ 不是中线控制点 z —— 后者比实际路面高约 200 mm 且无路拱。"
                    ),
                },
            }
        )

    # ── 伴随产物：不由本脚本生成，但**必须由本脚本登记** ──────────────────
    # ★ 清单只能有**一个写者**。让仿真程序或搬运脚本各写一份清单，两份就会漂，
    #   而漂的方向永远是"清单说在、磁盘上没有"，页面表现为 503 —— 看起来像路径
    #   问题，其实是登记问题。所以这里只**登记**，不生成。
    # ★ `present: false` 是合法状态，不是错误：契约因此可以先于产物存在。
    for cname, ckind, cnote in COMPANION_ARTIFACTS:
        cpath = args.out / cname
        exists = cpath.is_file()
        entry = {
            "name": cname,
            "kind": ckind,
            "present": exists,
            "note": cnote,
        }
        if exists:
            entry["bytes"] = cpath.stat().st_size
            entry["mime"] = COMPANION_MIME.get(ckind, "application/octet-stream")
        else:
            entry["note"] = cnote + "　★ 当前未产出（present: false），页面据此显示「未产出」"
        entries.append(entry)

    # ★ frame 的两个标记**从 crs.yaml 读**，不写死。写死的话，有人把
    #   assumed 改成 false 之后清单还在说"这是推断值" —— 一个不会被任何人
    #   发现的谎言，因为没有任何东西会因此报错。
    crs_ready, crs_assumed = _read_crs_flags(args.crs_config)

    manifest = {
        "version": "artifact_manifest.v0.1",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(),
        "producer": {
            "module": "simulator/crg",
            "note": f"export_viewer_mesh.py 从 {args.crg.name} 抽稀出两档 LOD",
        },
        "frame": {
            "kind": "local",
            "crs_ready": crs_ready,
            "crs_assumed": crs_assumed,
        },
        "artifacts": entries,
    }
    mpath = args.out / "manifest.json"
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    n_present = sum(1 for e in entries if e["present"])
    print(f"==> 清单已写：{mpath}（{len(entries)} 条，其中 {n_present} 条已产出）")
    print(f"    frame: crs_ready={crs_ready} crs_assumed={crs_assumed}（读自 crs.yaml）")
    print("==> 完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
