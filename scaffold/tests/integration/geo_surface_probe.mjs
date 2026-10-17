// 区域帧页（/geo）曲面面板的验收探针 —— 见 工程决策记录.md D-036。
// 做法：把 geo.html 的真实内联脚本装进 Node 跑（Canvas2D 用记录型桩替代），
//       从外部量它画了什么。页面代码一个字不改。
// ★ 它**不是**注册门：不进 run_contract_tests.sh 的 TESTS=，删掉 #win 不会有
//   任何测试变红。要跑请先起 console（:8024）。
// 跑法：node scaffold/tests/integration/geo_surface_probe.mjs
// 把 geo.html 的真实内联脚本装进 Node 跑一遍，从外部量它到底画了什么。
// 不重写任何几何 —— 只为 document / canvas 提供桩，让原码原样执行。
import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

// ★ 上游地址只有一个来源：CHECK_BASE_URL（见 scaffold/run_checks.sh）。
//   原先写死 —— 换台机器就得改源码，那不叫能跑。
const BASE = process.env.CHECK_BASE_URL || "http://127.0.0.1:8024";
const realFetch = globalThis.fetch;

/* ------------------------------------------------------------ 前置：活栈 */
// ★★ 三态退出码的第 2 位：**没跑成**。
//    本检查把页面的内联脚本跑起来，而那段脚本会自己去 /gw 取数。
//    栈没起时它取不到 —— 那**不是页面算错了**，是这里没跑成。
//    混进 1（没过）会把环境问题报成代码问题；混进 0 就是假绿。
try {
  const r = await realFetch(`${BASE}/geo`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
} catch (e) {
  console.error(`○ 没跑成：console 不可达（${BASE}）${e.message}`);
  process.exit(2);
}

// ★ 每张画布各自记账。共用一份就等于把平面图和曲面图混在一起量。
//   ★ 账本与代理必须一起存：只存账本而把代理交出去，探针拿到的就是账本，
//   setTransform 会是 undefined —— 那是桩的错，不是页面的错。
const CTXS = new Map();
function mkCtx(id) {
  const rec = { pts: [], fills: 0, strokes: 0, texts: [], styles: new Set() };
  const ctx = new Proxy({}, {
    get(t, k) {
      switch (k) {
        case "moveTo": case "lineTo": return (x, y) => rec.pts.push([x, y]);
        case "fill": return () => { rec.fills++; };
        case "stroke": return () => { rec.strokes++; };
        case "fillText": return (s, x, y) => rec.texts.push(String(s));
        default: return () => {};
      }
    },
    set(t, k, v) {
      if (k === "fillStyle" || k === "strokeStyle") rec.styles.add(v);
      t[k] = v; return true;
    },
  });
  CTXS.set(id, { rec, ctx });
  return ctx;
}
const ctxOf = (id) => { if (!CTXS.has(id)) mkCtx(id); return CTXS.get(id).ctx; };
const recOf = (id) => { if (!CTXS.has(id)) mkCtx(id); return CTXS.get(id).rec; };

function el(id) {
  return {
    id, style: {}, innerHTML: "", textContent: "", className: "", value: "",
    clientWidth: 1200, clientHeight: 224, width: 0, height: 0,
    offsetWidth: 10, offsetHeight: 10,
    getContext: () => ctxOf(id),
    addEventListener() {}, appendChild() {}, setPointerCapture() {},
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 1200, height: 224 }),
    querySelector: () => el(id + "-q"),
  };
}
const REG = new Map();
// ★ 表单控件的初始值必须照 HTML 写。浏览器给 <select> 的 value 是选中项的值
//   （#win 上写着 selected），给 range 的是 value 属性。桩给空串的话，
//   页面会读到 0，量出来的东西和真实页面不是一回事。
const SEED = { win: "200", zex: "3", w0: "0", step: "20" };
globalThis.document = {
  getElementById: (id) => {
    if (!REG.has(id)) { const e = el(id); if (SEED[id] !== undefined) e.value = SEED[id]; REG.set(id, e); }
    return REG.get(id);
  },
  querySelector: (s) => { if (!REG.has(s)) REG.set(s, el(s)); return REG.get(s); },
  createElement: (t) => el("new-" + t),
};
globalThis.window = { devicePixelRatio: 1, addEventListener() {} };
// ★ 页面会读 location.search（跨页深链 /geo?u=…）。Node 的全局里**没有** location ——
//   不补桩，页面里读它的那一段就会抛 ReferenceError。
//   （URLSearchParams 是 Node 自带的，不用补。）
globalThis.location = { search: "", hash: "" };
globalThis.fetch = (u, o) => realFetch(u.startsWith("http") ? u : BASE + u, o);

// ★ 页面源码从这里现抽 —— 早先这一行读的是 /data/cy/shujuku/.scratch/geo.js，
//   那是个**没人管的临时文件**。后果有两个，都很难看：
//   ① 探针会安安静静地测一个**旧版本**的页面，全绿也说明不了出货的那份是好的；
//   ② 换台机器就根本跑不起来（那文件在本仓是未跟踪的）。
//   改成从出货的 geo.html 里抽内联脚本 —— **测的就是出货的那段代码**。
const HERE = path.dirname(fileURLToPath(import.meta.url));
const HTML = path.resolve(HERE, "../../modules/M9-console/geo.html");
const PAGE = fs.readFileSync(HTML, "utf8");
const blocks = [...PAGE.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
if (!blocks.length) { console.error("✗ geo.html 里找不到内联 <script>"); process.exit(2); }
const src = blocks.reduce((a, b) => (b.length > a.length ? b : a));
vm.runInThisContext(src + `
;globalThis.__T = {
  loadSurface, drawSurface, rangeBox, surfBasis, SVIEW, WIN, syncWin, aimAt,
  get SURF() { return SURF; },
};`, { filename: "geo.html<inline>" });

const T = globalThis.__T;
const rec = recOf("cv3");
const out = [];
const chk = (n, ok, extra = "") => out.push(`    ${ok ? "✓" : "✗"} ${n}${extra ? "  " + extra : ""}`);
const clear = () => { rec.pts.length = 0; rec.fills = 0; rec.texts.length = 0; rec.styles.clear(); };
const W = 1200, H = 224, PAD = 14;

await new Promise((r) => setTimeout(r, 2500));
clear();
await T.loadSurface();

const S = T.SURF;
out.push("  ── 取回来的曲面 ──");
chk("曲面取到了", !!S);
chk(`网格 ${S.nu} 站 × ${S.nv} 点，u 步长 ${S.uStep} m`, S.nu === 1453 && S.nv === 9 && S.uStep === 4);
chk(`里程窗 ${T.WIN.len} m（默认）`, T.WIN.len === 200,
    `窗 = ${T.WIN.start.toFixed(1)} .. ${(T.WIN.start + T.WIN.len).toFixed(1)} m`);

out.push("  ── 窗口是按数据挑的，不是写死的 ──");
chk(`默认落在全段横向极差最大处 ${(S.bestU / 1000).toFixed(3)} km（极差 ${(S.bestR * 1000).toFixed(0)} mm）`,
    Math.abs(S.bestR * 1000 - 495) < 1 && Math.abs(S.bestU - 1472) < 4,
    `窗心与最大处相差 ${Math.abs(T.WIN.start + T.WIN.len / 2 - S.bestU).toFixed(1)} m`);

out.push("  ── 它画出来的东西（只算曲面这张画布）──");
const nan = rec.pts.filter(([x, y]) => !Number.isFinite(x) || !Number.isFinite(y));
chk("没有 NaN / Infinity", nan.length === 0, `坏点 ${nan.length}`);
// ★ 三脚架是 drawSurface 里最后画的东西：3 条臂 = 6 个点（moveTo + lineTo）。
//   量曲面范围必须把它剔掉，但**不能靠矩形猜** —— 曲面铺满画面时也会经过左下角，
//   猜就会把真实曲面点一起删掉。改成按结构认，并且当场自证认对了。
const TRIAD_O = [40, H - 34];
const tail = rec.pts.slice(-6);
const startsOk = [0, 2, 4].every(
  (i) => Math.hypot(tail[i][0] - TRIAD_O[0], tail[i][1] - TRIAD_O[1]) < 1e-9);
const armsOk = [1, 3, 5].every((i) => {
  const d = Math.hypot(tail[i][0] - tail[i - 1][0], tail[i][1] - tail[i - 1][1]);
  return d > 1 && d <= 24.5;
});
chk("三脚架能按结构认出（3 臂 6 点，全部从 (40,190) 出发）", startsOk && armsOk,
    `起点 ${startsOk ? "对" : "不对"}，臂长 ${armsOk ? "对" : "不对"}`);
const spts = startsOk && armsOk ? rec.pts.slice(0, -6) : rec.pts;
const xs = spts.map(p => p[0]), ys = spts.map(p => p[1]);
const bx0 = Math.min(...xs), bx1 = Math.max(...xs);
const by0 = Math.min(...ys), by1 = Math.max(...ys);
chk("全部落在画布内", bx0 >= -1 && bx1 <= W + 1 && by0 >= -1 && by1 <= H + 1,
    `实录 x ${bx0.toFixed(1)}..${bx1.toFixed(1)}  y ${by0.toFixed(1)}..${by1.toFixed(1)}`);

// 页面自己的取景公式，探针照着复算一遍 —— 复算得对不对，用实录范围反证。
const i0 = Math.max(0, Math.floor(T.WIN.start / S.uStep));
const i1 = T.WIN.len > 0
  ? Math.min(S.nu - 1, Math.round((T.WIN.start + T.WIN.len) / S.uStep)) : S.nu - 1;
const B = T.rangeBox(i0, i1), K = T.surfBasis();
const p1 = (i, j) => {
  const p = (i * S.nv + j) * 3;
  const e = S.pos[p] - B.ce, n = S.pos[p + 1] - B.cn, z = (S.pos[p + 2] - B.cz) * T.SVIEW.zex;
  return [e * K.rx + n * K.ry, -(e * K.ux + n * K.uy + z * K.uz)];
};
let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
for (let i = i0; i <= i1; i++) for (let j = 0; j < S.nv; j++) {
  const q = p1(i, j);
  if (q[0] < x0) x0 = q[0]; if (q[0] > x1) x1 = q[0];
  if (q[1] < y0) y0 = q[1]; if (q[1] > y1) y1 = q[1];
}
const k = Math.min((W - 2 * PAD) / (x1 - x0), (H - 2 * PAD) / (y1 - y0)) * T.SVIEW.zoom;
const cx = W / 2 - (x0 + x1) / 2 * k, cy = H / 2 - (y0 + y1) / 2 * k;
const pr = [x0 * k + cx, x1 * k + cx, y0 * k + cy, y1 * k + cy];
chk("复算的取景与实录一致（取景公式没说谎）",
    Math.abs(pr[0] - bx0) < 2 && Math.abs(pr[1] - bx1) < 2 && Math.abs(pr[3] - by1) < 2,
    `复算 x ${pr[0].toFixed(1)}..${pr[1].toFixed(1)}  y ${pr[2].toFixed(1)}..${pr[3].toFixed(1)}`);
chk("横向居中", Math.abs((pr[0] + pr[1]) / 2 - W / 2) < 1, `中心 ${((pr[0] + pr[1]) / 2).toFixed(1)}`);
chk("竖向居中", Math.abs((pr[2] + pr[3]) / 2 - H / 2) < 1, `中心 ${((pr[2] + pr[3]) / 2).toFixed(1)}`);
chk("取景贴边（长边用满）",
    Math.abs((pr[1] - pr[0]) - (W - 2 * PAD)) < 1 || Math.abs((pr[3] - pr[2]) - (H - 2 * PAD)) < 1,
    `占宽 ${(((pr[1] - pr[0]) / W) * 100).toFixed(0)}%  占高 ${(((pr[3] - pr[2]) / H) * 100).toFixed(0)}%`);

out.push("  ── ★「看得见」的硬指标：路面在屏幕上有多宽（px）──");
let ribbon = 0;
{
  const m = Math.round((i0 + i1) / 2);
  for (let a = 0; a < S.nv; a++) for (let b = a + 1; b < S.nv; b++) {
    const qa = p1(m, a), qb = p1(m, b);
    ribbon = Math.max(ribbon, Math.hypot((qb[0] - qa[0]) * k, (qb[1] - qa[1]) * k));
  }
}
chk(`路面在屏幕上宽 ${ribbon.toFixed(1)} px（不是一条线）`, ribbon >= 8);
const cols = [];
for (let i = i0; i < i1; i += 4) cols.push(i);
cols.push(i1);
chk(`面片数 ${rec.fills} = (${cols.length - 1} 段 × ${S.nv - 1})`,
    rec.fills === (cols.length - 1) * (S.nv - 1));
chk(`颜色随高程变化（${rec.styles.size} 种）`, rec.styles.size > 5);
// 角标必须同时给出「竖向放大倍数」和「仰角」：只看放大倍数不够，
// 仰角本身也在压缩横向 —— 少一个，图就能被当成真实断面比例读。
chk("角标写明了竖向放大倍数与仰角",
    rec.texts.some(t => /竖向放大 \d+×/.test(t) && /仰角 \d+°/.test(t)),
    JSON.stringify(rec.texts.filter(t => /K\d/.test(t)).slice(-1)));

out.push("  ── ★ z 真的进了式子（不受取景干扰的直接检验）──");
const dS = (zex, za, zb) => {
  const f = (Z) => -(0 * K.ux + 0 * K.uy + (Z - B.cz) * zex * K.uz);
  return f(zb) - f(za);
};
const d1 = dS(1, B.z0, B.z1), d20 = dS(20, B.z0, B.z1);
chk("同一 (东,北) 改高程 → 屏幕点确实移动", Math.abs(d1) > 1e-6, `Δ = ${d1.toFixed(4)} px`);
chk("位移与放大倍数成正比（线性）", Math.abs(d20 / d1 - 20) < 1e-9, `比值 ${(d20 / d1).toFixed(9)}`);
chk("位移量 = Δz·cos(仰角)（正交投影该有的样子）",
    Math.abs(-d1 - (B.z1 - B.z0) * Math.cos(T.SVIEW.el)) < 1e-9,
    `实得 ${(-d1).toFixed(6)}  应为 ${((B.z1 - B.z0) * Math.cos(T.SVIEW.el)).toFixed(6)}`);

out.push("  ── ★ 窗口是这个面板的全部理由 ──");
// 只改仰角与窗长，量「路面在屏幕上多宽」「画面占多大」。
// 不动 WIN.start、不调 syncWin —— syncWin 会把 #win 的值写回 WIN.len。
function metrics(el, winLen) {
  const oEl = T.SVIEW.el;
  T.SVIEW.el = el;
  // ★ winLen=0 就是「全段」，起点必须真的取 0 —— 不能沿用默认窗的 WIN.start，
  //   否则标着「全段」量的其实是 1372 m 到终点那一段。
  const a0 = winLen > 0 ? Math.max(0, Math.floor(T.WIN.start / S.uStep)) : 0;
  const a1 = winLen > 0 ? Math.min(S.nu - 1, Math.round((T.WIN.start + winLen) / S.uStep)) : S.nu - 1;
  const Bx = T.rangeBox(a0, a1);
  const KK = T.surfBasis();          // ★ 必须在这里重算：外层的 K 是 el=0.60 时的
  const px = (i, j) => {
    const p = (i * S.nv + j) * 3;
    const e = S.pos[p] - Bx.ce, n = S.pos[p + 1] - Bx.cn;
    const z = (S.pos[p + 2] - Bx.cz) * T.SVIEW.zex;
    return [e * KK.rx + n * KK.ry, -(e * KK.ux + n * KK.uy + z * KK.uz)];
  };
  let X0 = Infinity, X1 = -Infinity, Y0 = Infinity, Y1 = -Infinity;
  for (let i = a0; i <= a1; i++) for (let j = 0; j < S.nv; j++) {
    const q = px(i, j);
    if (q[0] < X0) X0 = q[0]; if (q[0] > X1) X1 = q[0];
    if (q[1] < Y0) Y0 = q[1]; if (q[1] > Y1) Y1 = q[1];
  }
  const kk = Math.min((W - 2 * PAD) / (X1 - X0), (H - 2 * PAD) / (Y1 - Y0));
  const m = Math.round((a0 + a1) / 2);
  let r = 0;
  for (let a = 0; a < S.nv; a++) for (let b = a + 1; b < S.nv; b++) {
    const qa = px(m, a), qb = px(m, b);
    r = Math.max(r, Math.hypot((qb[0] - qa[0]) * kk, (qb[1] - qa[1]) * kk));
  }
  T.SVIEW.el = oEl;
  return { r, w: (X1 - X0) * kk, h: (Y1 - Y0) * kk };
}
const mAll = metrics(0.60, 0), m2000 = metrics(0.60, 2000), m200 = metrics(0.60, 200);
chk(`全段铺满时路面只有 ${mAll.r.toFixed(2)} px —— 这就是"画出来了但看不见"`, mAll.r < 2);
chk(`2000 m 窗 ${m2000.r.toFixed(2)} px，200 m 窗 ${m200.r.toFixed(2)} px`,
    m200.r > m2000.r && m2000.r > mAll.r);
chk(`200 m 比全段宽 ${(m200.r / mAll.r).toFixed(0)} 倍`, m200.r >= 8);
out.push("     仰角 × 窗长：路面宽(px) ／ 画面占宽×占高");
for (const e of [0.60, 0.45, 0.32, 0.22, 0.15]) {
  const row = [600, 200, 120, 80].map((L) => {
    const m = metrics(e, L);
    return `${String(L).padStart(3)}m ${m.r.toFixed(1).padStart(5)}px ` +
           `${(m.w / W * 100).toFixed(0).padStart(3)}×${(m.h / H * 100).toFixed(0).padStart(3)}%`;
  });
  out.push(`       el=${e.toFixed(2)}  ${row.join("  |")}`);
}

out.push("  ── 平面图上点一段线元，里程窗跟着挪 ──");
const before = T.WIN.start;
T.aimAt(5000);
chk(`aimAt(5000) → 窗起点 ${T.WIN.start.toFixed(1)} m（窗心对准 5000）`,
    Math.abs(T.WIN.start - 4900) < 0.01, `窗 = 4900.0 .. 5100.0 m`);
T.aimAt(9999);
chk(`aimAt(9999) → 窗起点 ${T.WIN.start.toFixed(1)} m（撞到上界就夹住，不越界）`,
    T.WIN.start <= 5605.5, `上界 = 5805.4 − 200 = 5605.4 m`);
T.aimAt(1000);
chk(`aimAt(1000) → 窗起点 ${T.WIN.start.toFixed(1)} m（窗心对准 1000）`,
    Math.abs(T.WIN.start + T.WIN.len / 2 - 1000) < 0.01);
T.WIN.start = before; T.syncWin();

console.log(out.join("\n"));
const bad = out.filter(l => l.includes("✗")).length;
console.log(`\n  ${bad === 0 ? "全部通过 ✓" : `有 ${bad} 条不过 ✗`}`);
process.exit(bad === 0 ? 0 : 1);
