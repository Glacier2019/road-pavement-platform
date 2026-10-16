// 按 geo.html 的同一套公式出一张三视距对比图 —— 见 工程决策记录.md D-036。
// rangeBox / surfBasis / kmTxt 三个函数**从页面源码整段抽出**，这里没有第二套投影，
// 并核对页面里几条关键行仍逐字存在（页面改了就会报错，而不是悄悄画错）。
// 产物：.amhome/pages/fig-geo-surf.svg（再经 inkscape 转 png）
// 跑法：node scaffold/tests/integration/geo_surface_figure.mjs
// 按 geo.html 的同一套公式作图 —— 函数从页面源码里原样抽出，不重写。
// 目的：本模型看不了网页截图，但可以把「页面会画成什么样」用数据算出来。
import fs from "node:fs";
import vm from "node:vm";

const ROOT = "/data/cy/shujuku";
const html = fs.readFileSync(`${ROOT}/scaffold/modules/M9-console/geo.html`, "utf8");

// 从源码里整段抠出一个函数，括号配对为止。
function grab(name) {
  const i = html.indexOf(`function ${name}(`);
  if (i < 0) throw new Error(`页面里找不到 ${name}()`);
  let d = 0;
  for (let k = html.indexOf("{", i); k < html.length; k++) {
    if (html[k] === "{") d++;
    else if (html[k] === "}" && --d === 0) return html.slice(i, k + 1);
  }
  throw new Error(`${name}() 括号不配对`);
}
const kmSrc = html.match(/const kmTxt = (\(m\) =>[^;]+);/);
if (!kmSrc) throw new Error("页面里找不到 kmTxt");

// ★ 这些函数**照抄自页面**，不是我另写一份。页面改了，这里不会偷偷跟上 ——
//   所以下面还要逐条核对关键那几行。
vm.runInThisContext(`
let SURF = null;
const SVIEW = { az: 0, el: 0.22, zex: 3, zoom: 1 };
const kmTxt = ${kmSrc[1]};
${grab("rangeBox")}
${grab("surfBasis")}
globalThis.__F = { kmTxt, rangeBox, surfBasis, SVIEW, useSURF(s) { SURF = s; } };
`, { filename: "geo.html<抽取>" });
const F = globalThis.__F;

// 关键几行必须还在页面上，一字不差 —— 否则这张图画的就不是页面画的东西。
const MUST = [
  "const a = e * K.rx + n * K.ry;",
  "const b = -(e * K.ux + n * K.uy + z * K.uz);",
  "const k = Math.min((W - 2 * PAD) / Math.max(x1 - x0, 1e-6),",
  "SCTX.strokeStyle = \"rgba(255,255,255,0.42)\"; SCTX.lineWidth = 1;",
  "run(0, \"#4da3ff\", 2.2);",
];
for (const s of MUST) if (!html.includes(s)) throw new Error(`页面已改，这张图不再对应：${s}`);

// ── 数据：与页面同一个来源 ────────────────────────────────────────────
const mf = JSON.parse(fs.readFileSync(`${ROOT}/artifacts/sim/manifest.json`, "utf8"));
const art = mf.artifacts.find((a) => a.kind === "terrain_mesh" && /overview/.test(a.name));
const buf = fs.readFileSync(`${ROOT}/artifacts/sim/${art.name}`);
const { nu, nv, u_step_m: uStep } = art.mesh;
const vS = art.mesh.v_samples_m;
const pos = new Float32Array(buf.buffer, buf.byteOffset, nu * nv * 3);
if (buf.byteLength < nu * nv * 3 * 4) throw new Error("网格字节数不够");
F.useSURF({ nu, nv, pos, vSamples: vS, uStep, uEnd: mf.crg.u_end_m });

// 视向与默认窗 —— 与 loadSurface() 同一套算法。
const j0 = vS.findIndex((v) => Math.abs(v) < 1e-9);
const iL = nu - 1;
const dE = pos[(iL * nv + j0) * 3] - pos[j0 * 3];
const dN = pos[(iL * nv + j0) * 3 + 1] - pos[j0 * 3 + 1];
const roadAng = Math.atan2(dN, dE);
F.SVIEW.az = roadAng + 0.96;
let bestI = 0, bestR = -1;
for (let i = 0; i < nu; i++) {
  let lo = Infinity, hi = -Infinity;
  for (let j = 0; j < nv; j++) {
    const z = pos[(i * nv + j) * 3 + 2];
    if (z < lo) lo = z; if (z > hi) hi = z;
  }
  if (hi - lo > bestR) { bestR = hi - lo; bestI = i; }
}
const bestU = bestI * uStep;

// ── 一个面板 = 一次 drawSurface（只去掉三脚架与角标）─────────────────
const W = 1200, H = 224, PAD = 14;   // 高与探针的桩一致（224）—— 否则同一件事会有两个数
function panel(el, winLen) {
  const S = F.SVIEW; S.el = el;
  const K = F.surfBasis();
  const i0 = Math.max(0, Math.min(nu - 1, Math.floor((winLen ? Math.max(0, bestU - winLen / 2) : 0) / uStep)));
  const i1 = winLen > 0
    ? Math.min(nu - 1, Math.round((Math.max(0, bestU - winLen / 2) + winLen) / uStep)) : nu - 1;
  const B = F.rangeBox(i0, i1), zex = S.zex;
  const M = (i1 - i0 + 1) * nv;
  const ax = new Float32Array(M), ay = new Float32Array(M), dp = new Float32Array(M);
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (let i = i0; i <= i1; i++) for (let j = 0; j < nv; j++) {
    const p = (i * nv + j) * 3, m = (i - i0) * nv + j;
    const e = pos[p] - B.ce, n = pos[p + 1] - B.cn;
    const z = (pos[p + 2] - B.cz) * zex;
    const a = e * K.rx + n * K.ry;
    const b = -(e * K.ux + n * K.uy + z * K.uz);
    ax[m] = a; ay[m] = b;
    if (a < x0) x0 = a; if (a > x1) x1 = a;
    if (b < y0) y0 = b; if (b > y1) y1 = b;
    dp[m] = e * K.fx + n * K.fy + z * K.fz;
  }
  const k = Math.min((W - 2 * PAD) / Math.max(x1 - x0, 1e-6),
                     (H - 2 * PAD) / Math.max(y1 - y0, 1e-6)) * S.zoom;
  const cx = W / 2 - (x0 + x1) / 2 * k, cy = H / 2 - (y0 + y1) / 2 * k;
  const sx = new Float32Array(M), sy = new Float32Array(M);
  for (let m = 0; m < M; m++) { sx[m] = ax[m] * k + cx; sy[m] = ay[m] * k + cy; }
  const IX = (i, j) => (i - i0) * nv + j;

  // 路面在屏幕上多宽：窗心断面 9 个采样点两两最大距离。
  const mid = Math.round((i0 + i1) / 2);
  let ribbon = 0;
  for (let a = 0; a < nv; a++) for (let b = a + 1; b < nv; b++) {
    ribbon = Math.max(ribbon, Math.hypot((sx[IX(mid, b)] - sx[IX(mid, a)]),
                                         (sy[IX(mid, b)] - sy[IX(mid, a)])));
  }

  const g = [];
  const cols = [];
  for (let i = i0; i < i1; i += 4) cols.push(i);
  cols.push(i1);
  const quads = [];
  for (let a = 0; a < cols.length - 1; a++) {
    const iA = cols[a], iB = cols[a + 1];
    for (let j = 0; j < nv - 1; j++) {
      const p0 = IX(iA, j);
      quads.push([(dp[p0] + dp[p0 + 1] + dp[IX(iB, j + 1)] + dp[IX(iB, j)]) / 4,
                  p0, p0 + 1, IX(iB, j + 1), IX(iB, j), iA, j]);
    }
  }
  quads.sort((a, b) => b[0] - a[0]);
  const zspan = Math.max(B.z1 - B.z0, 1e-6);
  for (const q of quads) {
    const t = (pos[(q[5] * nv + q[6]) * 3 + 2] - B.z0) / zspan;
    const col = `rgb(${Math.round(36 + 150 * t)},${Math.round(60 + 132 * t)},${Math.round(94 + 132 * t)})`;
    const d = `M${sx[q[1]].toFixed(1)},${sy[q[1]].toFixed(1)}L${sx[q[2]].toFixed(1)},${sy[q[2]].toFixed(1)}` +
              `L${sx[q[3]].toFixed(1)},${sy[q[3]].toFixed(1)}L${sx[q[4]].toFixed(1)},${sy[q[4]].toFixed(1)}Z`;
    g.push(`<path d="${d}" fill="${col}" stroke="${col}" stroke-width="0.6"/>`);
  }
  // 直母线：约每 20 m 一条。
  const rs = Math.max(1, Math.round(20 / uStep));
  const ru = [];
  for (let i = i0; i <= i1; i += rs) for (let j = 0; j < nv - 1; j++) {
    const a = IX(i, j);
    ru.push(`M${sx[a].toFixed(1)},${sy[a].toFixed(1)}L${sx[a + 1].toFixed(1)},${sy[a + 1].toFixed(1)}`);
  }
  g.push(`<path d="${ru.join("")}" fill="none" stroke="#ffffff" stroke-opacity="0.42" stroke-width="1"/>`);
  const run = (j, col, lw, dash) => {
    const d = [];
    for (let i = i0; i <= i1; i++) {
      const a = IX(i, j);
      d.push(`${i > i0 ? "L" : "M"}${sx[a].toFixed(1)},${sy[a].toFixed(1)}`);
    }
    g.push(`<path d="${d.join("")}" fill="none" stroke="${col}" stroke-width="${lw}"` +
           `${dash ? ` stroke-dasharray="${dash}"` : ""}/>`);
  };
  run(0, "#4da3ff", 2.2);
  run(nv - 1, "#4da3ff", 2.2);
  run(j0 >= 0 ? j0 : 4, "#ff9f43", 1.6, "5 4");

  return { svg: g.join("\n"), ribbon, uA: i0 * uStep, uB: i1 * uStep, cols: cols.length };
}

const rows = [
  { el: 0.60, len: 0,   tag: "① 全段铺满（5 805 m）· 仰角 34°", cls: "坏" },
  { el: 0.60, len: 200, tag: "② 200 m 里程窗 · 仰角 34°", cls: "中" },
  { el: 0.22, len: 200, tag: "③ 200 m 里程窗 · 仰角 13°　← 上线就是这个", cls: "好" },
];

const CW = 1200, GAP = 30, TOP = 92;
let y = TOP;
const parts = [];
const stats = [];
for (const r of rows) {
  const p = panel(r.el, r.len);
  stats.push({ ...r, ...p });
  parts.push(`<text x="20" y="${y - 9}" fill="#c9d4e0" font-size="15" font-weight="600">${r.tag}</text>`);
  parts.push(`<text x="1220" y="${y - 9}" fill="#7fd4a0" font-size="15" text-anchor="end" ` +
             `font-family="ui-monospace,monospace">路面 ${p.ribbon.toFixed(2)} px</text>`);
  parts.push(`<rect x="20" y="${y}" width="${CW}" height="${H}" rx="6" fill="#0b1017" stroke="#243040"/>`);
  parts.push(`<g transform="translate(20,${y})">${p.svg}</g>`);
  y += H + GAP;
}

const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1240" height="${y + 4}"
  viewBox="0 0 1240 ${y + 4}" font-family="Noto Sans CJK SC,Source Han Sans SC,sans-serif">
<rect width="1240" height="${y + 4}" fill="#070b10"/>
<text x="20" y="38" fill="#e6edf3" font-size="23" font-weight="700">区域帧页上的论文 (2.16) 直纹面 —— 三个视距</text>
<text x="20" y="64" fill="#8b97a6" font-size="14">${
  `数据 ${nu} 站 × ${nv} 点 · 窗默认落在横向极差最大处 ${F.kmTxt(bestU)}（${(bestR * 1000).toFixed(0)} mm）` +
  ` · 路面真宽 8.5 m`}</text>
<text x="1220" y="64" fill="#8b97a6" font-size="14" text-anchor="end">${
  "同一个断面，换个视距就从「看不见」变成「看得见」"}</text>
${parts.join("\n")}
</svg>`;

fs.writeFileSync(`${ROOT}/.amhome/pages/fig-geo-surf.svg`, svg);
for (const s of stats) {
  console.log(`  ${s.tag.padEnd(34)} 路面 ${s.ribbon.toFixed(2).padStart(6)} px　` +
              `窗 ${F.kmTxt(s.uA)} – ${F.kmTxt(s.uB)}`);
}
console.log(`  svg ${svg.length} 字节`);
