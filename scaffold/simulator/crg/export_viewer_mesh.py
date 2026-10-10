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
    args = ap.parse_args()

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

    manifest = {
        "version": "artifact_manifest.v0.1",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(),
        "producer": {
            "module": "simulator/crg",
            "note": f"export_viewer_mesh.py 从 {args.crg.name} 抽稀出两档 LOD",
        },
        "frame": {
            "kind": "local",
            "crs_ready": False,
            "crs_assumed": True,
        },
        "artifacts": entries,
    }
    mpath = args.out / "manifest.json"
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"==> 清单已写：{mpath}（{len(entries)} 条）")
    print("==> 完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
