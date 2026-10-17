// 跨页深链的验收探针 —— 甲+乙 里「乙」的那一半。见 工程决策记录.md D-040。
//
// 两条链、两个方向，互为回路：
//   ①「GE 道路几何」线元表  →  /geo?u=<桩号米>
//      期望：里程窗对准那一段，并把那一段的详情弹出来。
//   ②「区域帧」元素弹窗      →  /geometry?s=<section_id>#el-<element_seq>
//      期望：预选那个路段，滚到那一行并高亮。
//
// ★ 判据的靶子一律来自**接口自己的数**，不来自页面源码里的字面量：
//   问页面「你写的 u 是多少」，同时问接口「这一段起点桩号是多少」，两边比。
//   搜关键词只能证明词在源码里；这里要比的是数。
//
// ★ 它**不是**注册门：要 console 起着（:8024），所以不进 run_contract_tests.sh 的 TESTS=。
// 跑法：node scaffold/tests/integration/geo_deeplink_probe.mjs

import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DIR = path.resolve(HERE, "../../modules/M9-console");
// ★ 上游地址**只有一个来源**：CHECK_BASE_URL。
//   运行器 scaffold/run_checks.sh 负责它，用 --base-url 覆盖。
//   默认值只是为了让「不走运行器、直接 node 这个文件」也能在我这台机器上跑；
//   换台机器（或交给客户）时，**不许改源码**，给环境变量即可。
const BASE = process.env.CHECK_BASE_URL || "http://127.0.0.1:8024";

/* ------------------------------------------------------------ 页面源码 */
function inlineScript(file) {
  const s = fs.readFileSync(path.join(DIR, file), "utf8");
  const blocks = [...s.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
  if (!blocks.length) throw new Error(file + " 里找不到内联 <script>");
  return blocks.reduce((a, b) => (b.length > a.length ? b : a));
}

/* ------------------------------------------------------------ DOM 桩 */
function mkEl(id) {
  const cls = new Set();
  const added = [];
  return {
    id, style: {}, innerHTML: "", textContent: "", className: "", value: "",
    hidden: false, checked: false,
    clientWidth: 1200, clientHeight: 224, offsetWidth: 10, offsetHeight: 10,
    __on: {}, __scrolled: 0, __cls: cls, __clsAdded: added,
    classList: {
      // ★ 记「加过」而不是「现在还在」：页面 2.6 秒后自己会把 hit 摘掉，
      //   赌取样时刻只会得到一个忽红忽绿的探针。
      add: (c) => { cls.add(c); added.push(c); },
      remove: (c) => cls.delete(c),
      contains: (c) => cls.has(c),
    },
    scrollIntoView() { this.__scrolled += 1; },
    addEventListener(t, fn) { (this.__on[t] = this.__on[t] || []).push(fn); },
    getContext: () => new Proxy({}, { get: () => () => {}, set: () => true }),
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 1200, height: 224 }),
    querySelector: () => null,
    appendChild() {},
    focus() {},
  };
}

// ★ pre 里的 id 是「页面**应当**自己造出来的那些行」。
//   预注册它们在 REG 里，是为了能观察页面去碰它们；
//   至于页面有没有真的把 id 写进 HTML，另有一条判据直接读 innerHTML 来验。
//   两者缺一：只读 innerHTML 就证明不了页面真会去找那一行；
//   只预注册就变成了**假绿** —— 桩会对任何 id 都返回一个元素，
//   页面哪怕根本没发 id 也能「命中」。
function makeEnv({ search = "", hash = "", pre = [] } = {}) {
  const REG = new Map();
  for (const id of pre) REG.set(id, mkEl(id));
  const calls = [];
  // ★ 表单控件的初值必须照 HTML 写。浏览器给 <select> 的 value 是选中项
  //   （#win 上写着 selected 200），桩给空串的话页面会算出窗长 0 ——
  //   而「窗对准了没有」这条判据就会拿 0 当真，量出一个假红。
  const SEED = { win: "200", zex: "3", w0: "0", step: "20", lim: "50" };
  const get = (id) => {
    if (!REG.has(id)) { const e = mkEl(id); if (SEED[id] !== undefined) e.value = SEED[id]; REG.set(id, e); }
    return REG.get(id);
  };
  const document = {
    getElementById: get,
    querySelector: (sel) => get(sel),
    createElement: (t) => mkEl("new-" + t),
  };
  const sandbox = {
    document,
    window: { devicePixelRatio: 1, addEventListener() {} },
    location: { search, hash },
    URLSearchParams,
    fetch: (u, o) => { const url = u.startsWith("http") ? u : BASE + u; calls.push(url); return fetch(url, o); },
    setTimeout, clearTimeout, queueMicrotask,
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
    console,
  };
  return { ctx: vm.createContext(sandbox), sandbox, REG, calls };
}

async function waitFor(pred, ms = 8000, step = 25) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (pred()) return true; await new Promise((r) => setTimeout(r, step)); }
  return false;
}
const txt = (env, id) => (env.REG.has(id) ? env.REG.get(id).textContent : "<元素未被创建>");
const html = (env, id) => (env.REG.has(id) ? env.REG.get(id).innerHTML : "<元素未被创建>");

/* ------------------------------------------------------------ 接口侧（独立靶子） */
const api = async (p) => {
  const r = await fetch(BASE + p);
  if (!r.ok) throw new Error(`GET ${p} -> HTTP ${r.status}`);
  return r.json();
};
const arr = (d) => (Array.isArray(d) ? d : d.items || d.elements || d.sections || d.data || []);

/* ------------------------------------------------------------ 方向 ①：/geo?u= */
const GEO_EXPORT = `
;globalThis.__T = {
  get WIN() { return WIN; },
  get ROUTE() { return ROUTE; },
  get POP() { return POP; },
  get SECV() { return SEC.value; },
  get SURF() { return SURF; },
};`;

async function runGeo(src, search) {
  const env = makeEnv({ search });
  vm.runInContext(src + GEO_EXPORT, env.ctx, { filename: "geo.html<inline>" });
  const T = env.sandbox.__T;
  await waitFor(() => txt(env, "surfgen") !== "");
  await waitFor(() => T.SURF, 6000);
  return { env, T };
}

/* ------------------------------------------------------------ 方向 ②：/geometry?s=#el- */
const GEO2_EXPORT = `
;globalThis.__T = {
  get SECTIONS() { return SECTIONS; },
  focusHashRow, loadAlignment, loadSection,
};`;

async function runGeometry(src, { search = "", hash = "", pre = [], waitRows = true, waitCall = null }) {
  const env = makeEnv({ search, hash, pre });
  vm.runInContext(src + GEO2_EXPORT, env.ctx, { filename: "geometry.html<inline>" });
  const T = env.sandbox.__T;
  // ★ 空壳路段（element_count=0）本来就没有线元行，等「表里出现 el-」会白等到超时。
  //   那种情形该等的是「路段选好了」。
  if (waitRows) await waitFor(() => html(env, "eltbl").includes("el-"), 9000);
  else await waitFor(() => env.REG.has("pick") && String(env.REG.get("pick").value) !== "", 9000);
  // ★ 还要等取数**真的发出去**。只等 pick 填上的话，可能在 loadSection 还没发出请求
  //   之前就取样 —— 那样看到的「一次都没取」是探针早看了一步，不是页面没取。
  if (waitCall) await waitFor(() => env.calls.some((u) => u.includes(waitCall)), 9000);
  return { env, T };
}

/* ------------------------------------------------------------ 跑 */
const line = () => {};   // 分节标题。判据名自带 ①/② 前缀，留着只为读源码时顺眼。

const t0 = Date.now();
// ★ 措辞与退出码必须对上：这是**没跑成**（2），不是失败。
//   原先这里打的是 ✗，读起来像「跑了没过」—— 而这两件事要分清是本轮的题目。
try { await fetch(BASE + "/geo"); }
catch (e) { console.error(`○ 没跑成：console 不可达（${BASE}）${e.message}`); process.exit(2); }

const geoHtml = inlineScript("geo.html");
const geomHtml = inlineScript("geometry.html");

// 接口侧的独立靶子
const secs = arr(await api("/gw/v1/geometry/sections"));
// ★ 页面自己也是这么挑的（见 geo.html 的 loadSections）：挑**有几何数据**的那一条。
//   本机上 id=1 是空壳（element_count=0 且没配 EPSG），它的 /geo/route 按设计返 503 ——
//   那不是页面坏了，是「不猜默认带号」那条规矩在起作用。探针不该拿它当靶子。
const withGeom = secs.filter((s) => s.element_count || s.station_count || s.pi_count);
if (!withGeom.length) { console.error("✗ 没有一条路段带几何数据，无从验起"); process.exit(2); }
const secId = String(withGeom[0].id);
// 另一条 —— 用它验 ?s= 真的换了路段。拿默认那条验等于没验。
const other = secs.find((s) => String(s.id) !== secId);
const secId2 = other ? String(other.id) : secId;
const route = await api(`/gw/v1/geometry/sections/${secId}/geo/route?step_m=20`);
const rels = arr(route.elements || route);
const align = await api(`/gw/v1/geometry/sections/${secId}/alignment`);
const aels = arr(align);

const U = 5000.0;
// ★ 半开区间 [start, end)，与页面同一口径（也与此仓「段边界归后段」的既有裁定一致）。
//   用闭区间的话，在段交界处 find 会先撞见上一段 —— 判据就会跟着页面一起错。
const wantEl = rels.find((e) => U >= e.start_station_m && U < e.end_station_m) || rels[0];
const wantSeq = wantEl.element_seq;

/* ★ 判据块包成函数：**同一批**判据要能拿去审「被故意改坏的页面」。
   一个从没红过的探针证明不了什么 —— 定不出靶子的门，永远不会红。 */
async function runChecks(geoHtml, geomHtml) {
const R = [];
const chk = (name, ok, detail) => { R.push({ name, ok, detail }); };

/* ── 方向 ①：带 ?u= ─────────────────────────────────────────── */
const A = await runGeo(geoHtml, `?u=${U}`);
const popShown = A.T.POP.style.display === "block";
const popHtml = A.T.POP.innerHTML || "";

line(`  ── 方向 ①：/geo?u=${U}（从「GE 道路几何」的线元表点过来）──`);
chk("①-a 里程窗对准 5000 m：窗心 = 5000，且 5000 确实落在窗内",
    Math.abs((A.T.WIN.start + A.T.WIN.start + A.T.WIN.len) / 2 - U) < 1e-6
      && U >= A.T.WIN.start && U <= A.T.WIN.start + A.T.WIN.len,
    `窗 = ${A.T.WIN.start.toFixed(1)} .. ${(A.T.WIN.start + A.T.WIN.len).toFixed(1)}`);
chk("①-b 那一段的详情被弹出来了", popShown, `#pop.display = "${A.T.POP.style.display}"`);
chk("①-c 命中的正是接口里含 5000 的那一段", String(wantSeq).length > 0,
    `接口：u∈[${wantEl.start_station_m}, ${wantEl.end_station_m}] → 第 ${wantSeq} 段`);
const wantHref = `/geometry?s=${encodeURIComponent(A.T.SECV)}#el-${encodeURIComponent(wantSeq)}`;
chk("①-d 弹窗给出回「GE 道路几何」的链接，且指着刚**这一段**", popHtml.includes(wantHref),
    `想要 ${wantHref}；实得 ${(popHtml.match(/href="([^"]*)"/) || [])[1] || "（没有链接）"}`);
chk("①-e 链接的目标路段就是页面当前选中的那一段",
    popHtml.includes(`s=${encodeURIComponent(A.T.SECV)}`), `当前路段 = ${A.T.SECV}`);
chk("①-f 数据照常取到（深链没有把页面搞坏）", html(A.env, "meta").includes("取出时刻"),
    "#meta 有取出时刻");

// 交界处：线元表那一列写的是本段**起点**桩号，而它同时是上一段的终点。
// 从第 N 段那行点过来，必须弹第 N 段。闭区间会先撞见第 N-1 段 —— 这条判据就是钉它的。
const Ab = await runGeo(geoHtml, `?u=${wantEl.start_station_m}`);
const gotSeq = (Ab.T.POP.innerHTML.match(/#el-(\d+)/) || [])[1];
chk("①-i 桩号正好落在两段交界处时，弹的是**后**一段（与线形积分门的裁定一致）",
    gotSeq === String(wantSeq),
    `u=${wantEl.start_station_m}（第 ${wantSeq} 段起点）→ 弹出第 ${gotSeq} 段`);

/* ── 方向 ①：不带 ?u=（不该有弹窗） ─────────────────────────── */
const A0 = await runGeo(geoHtml, "");
chk("①-g 不带 ?u= 时**不弹**任何详情（便利不许自己冒出来）",
    A0.T.POP.style.display !== "block", `#pop.display = "${A0.T.POP.style.display}"`);

/* ── 方向 ①：?u 写坏了（不报错、不崩） ───────────────────────── */
const A1 = await runGeo(geoHtml, "?u=不是个数");
const A2 = await runGeo(geoHtml, "?u=99999999");
const badOk = (r) => html(r.env, "meta").includes("取出时刻")
                 && r.T.POP.style.display !== "block"
                 && (txt(r.env, "err") === "" || r.env.REG.get("err").style.display !== "block");
chk("①-h ?u 写坏（不是数 / 超出全长）→ 静默退回，页面照常", badOk(A1) && badOk(A2),
    `非数：meta✓=${html(A1.env, "meta").includes("取出时刻")} 弹=${A1.T.POP.style.display}；` +
    `超界：meta✓=${html(A2.env, "meta").includes("取出时刻")} 弹=${A2.T.POP.style.display}`);

/* ── 方向 ②：?s= 预选 + #el- 高亮 ───────────────────────────── */
// ★ 分两摊跑，因为两件事需要不同的路段：
//   行与高亮要在**有几何数据的那条**上验（空壳那条一张表都没有）；
//   预选则要拿**另一条**验才有意义 —— 拿默认那条验，页面就算把 ?s= 整个忽略也照样通过。
const last = aels[aels.length - 1] || { element_seq: 1, start_station_km: 0 };
const seqForRow = last.element_seq;
const B = await runGeometry(geomHtml, { hash: `#el-${seqForRow}`, pre: [`el-${seqForRow}`] });
const eltbl = html(B.env, "eltbl");
const wantU = (Number(last.start_station_km) * 1000).toFixed(3);
const row = B.env.REG.get(`el-${seqForRow}`);

line("");
line(`  ── 方向 ②：/geometry?s=${secId}#el-${seqForRow}（从「区域帧」的详情点过来）──`);
chk("②-a 不带 ?s= 时选的是**有几何数据**的那条（默认逻辑没被改坏）",
    String(B.env.REG.get("pick").value) === secId,
    `#pick.value = ${B.env.REG.get("pick").value}，想要 ${secId}`);
chk("②-b 线元表每行都带 id='el-<seq>'（否则深链没得指）",
    eltbl.includes(`id='el-${seqForRow}'`),
    `#eltbl 里 ${(eltbl.match(/id='el-\d+'/g) || []).length} 行有 id；接口说共 ${aels.length} 段`);
chk("②-c 每行给出「在区域帧里看」的链接，u 值 = 该行起点桩号×1000",
    eltbl.includes(`href='/geo?u=${wantU}'`),
    `接口：第 ${seqForRow} 段 start_station_km=${last.start_station_km} → 想要 u=${wantU}`);
chk("②-d #el-<seq> 命中：滚到那一行并加高亮 class",
    row && row.__scrolled > 0 && row.__clsAdded.includes("hit"),
    `scrollIntoView ${row ? row.__scrolled : "?"} 次；加过的 class = ${row ? row.__clsAdded.join(",") || "（没有）" : "?"}`);

/* ── 方向 ②：?s= 真的换了路段 ───────────────────────────────── */
const Bp = await runGeometry(geomHtml, {
  search: `?s=${secId2}`, waitRows: false, waitCall: `sections/${secId2}/alignment`,
});
chk("②-e ?s= 把路段换成指的那一条（不是默认那条）",
    String(Bp.env.REG.get("pick").value) === secId2,
    `#pick.value = ${Bp.env.REG.get("pick").value}，想要 ${secId2}（默认是 ${secId}）`);
const alignCalls = Bp.env.calls.filter((u) => /\/alignment/.test(u));
chk("②-f 为那一条真的去取了几何（不是只改了显示）",
    alignCalls.some((u) => u.includes(`/${secId2}/`)),
    alignCalls.map((u) => u.replace(BASE, "")).join(" ") || "（一次都没取）");

/* ── 方向 ②：指错了（不报错、退回默认） ─────────────────────── */
const B2 = await runGeometry(geomHtml, { search: "?s=999999", hash: "#el-999999" });
chk("②-g ?s / #el- 指错了 → 退回默认路段，**且不报错**",
    String(B2.env.REG.get("pick").value) === secId
      && B2.env.REG.get("err").style.display !== "block",
    `#pick = ${B2.env.REG.get("pick").value}；#err = "${(B2.env.REG.get("err").textContent || "").slice(0, 90)}"`);

return R;
}

/* ★ 自我证明：把**出货的那段代码**改坏，本探针必须变红。
   没有这一节，绿色只说明这脚本跑完了 —— 它证明不了判据定得出靶子。 */
const MUTANTS = [
  {
    what: "窗心从 u 改回「段中点」（乙 刚修掉的那个 bug）",
    geo: (s) => s.replace("aimAt(u);", "aimAt((el.start_station_m + el.end_station_m) / 2);"),
    must: ["①-a"],
  },
  {
    what: "区间改回闭区间 [start, end]（交界处会弹上一段）",
    geo: (s) => s.replace("u < e.end_station_m", "u <= e.end_station_m"),
    must: ["①-i"],
  },
  {
    // ★ 只要 ②-b。②-d 是**证明不了的**那半条：桩里预注册了 el-<seq>，
    //   页面就算一行 id 都不发，focusHashRow 也照样「命中」。
    //   两条合起来才成立：②-b 说浏览器里真会有那一行，②-d 说页面真会去找它。
    what: "线元行不再发 id（深链没得指）",
    geom: (s) => s.replace('], els, r => "el-" + r.element_seq);', "], els);"),
    must: ["②-b"],
  },
  {
    what: "「在区域帧里看」那一列的 u 少乘 1000（米当成了公里）",
    geom: (s) => s.replace("(Number(r.start_station_km) * 1000).toFixed(3)", "Number(r.start_station_km).toFixed(3)"),
    must: ["②-c"],
  },
  {
    what: "?s= 被整个忽略（永远用默认路段）",
    geom: (s) => s.replace(
      'const want = new URLSearchParams(location.search).get("s");',
      "const want = null;"),
    must: ["②-e"],
  },
];

/* ── 出结果 ─────────────────────────────────────────────────── */
const R = await runChecks(geoHtml, geomHtml);
console.log("  ── 判据 ──");
for (const r of R) console.log(`    ${r.ok ? "✓" : "✗"} ${r.name}  ${r.detail}`);

let bad = R.filter((r) => !r.ok).length;
console.log("");
console.log("  ── ★ 自我证明：改坏出货代码，本探针必须变红 ──");
for (const m of MUTANTS) {
  const g1 = m.geo ? m.geo(geoHtml) : geoHtml;
  const g2 = m.geom ? m.geom(geomHtml) : geomHtml;
  // 替换串对不上就说明页面改了而探针没跟着改 —— 那本身要算失败，不能默默跳过。
  if ((m.geo && g1 === geoHtml) || (m.geom && g2 === geomHtml)) {
    console.log(`    ✗ ${m.what} → 变异没生效（替换串对不上，请修探针）`);
    bad += 1;
    continue;
  }
  const R2 = await runChecks(g1, g2);
  const red = R2.filter((r) => !r.ok).map((r) => r.name);
  const hit = m.must.every((k) => red.some((n) => n.startsWith(k)));
  if (!hit) bad += 1;
  console.log(`    ${hit ? "✓" : "✗"} ${m.what} → 判红 ${red.length} 条` +
              `（指定 ${m.must.join("/")}${hit ? " 全中" : " **没全红**"}）`);
}

console.log("");
console.log(`  ${bad === 0 ? "全部通过 ✓" : `✗ ${bad} 条不合格`}`);
console.log(`  （${((Date.now() - t0) / 1000).toFixed(1)} s）`);
process.exit(bad === 0 ? 0 : 1);
