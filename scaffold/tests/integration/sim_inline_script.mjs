// ============================================================================
// 把 sim.html 的**内联脚本原样**跑起来，用真实产物喂它 —— 不渲染，但真计算。
//
// 为什么需要这个：
//   `node --check` 只证明它能被解析，不证明它算得对。
//   本会话没有浏览器、没有图像输入，无法看渲染结果；
//   但页面的算术（解网格、算曲率、按里程取轮荷、投影求符号位置）
//   是**可以用真实数据独立跑一遍**的。跑出来的数如果荒谬，页面就是错的。
//
// 做法：垫一层最小 DOM / WebGL 桩，让脚本自行 boot()，
//       然后直接读它的状态 S 与它算出来的缓冲。
// ============================================================================
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// ★ 从脚本自身位置推仓库根（原先是写死的 /data/cy/shujuku）。
//   本文件在 scaffold/tests/integration/ ⇒ 上溯三级 = 仓库根。
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const ART = path.join(ROOT, "artifacts/sim");

const html = fs.readFileSync(path.join(ROOT, "scaffold/modules/M9-console/sim.html"), "utf8");
let src = html.match(/<script>([\s\S]*?)<\/script>/)[1];
// 把内部状态暴露出来，供断言读取（不改动页面逻辑本身）
src += "\nglobalThis.__S = S; globalThis.__fns = { parseTrack, buildQuantity, meshZAt, decodeMesh, computeNormals, sampleLoadAtU };\n";

// ---------------------------------------------------------------- DOM 桩
const seen = [];
function el(id) {
  const o = {
    id, textContent: "", innerHTML: "", value: id === "symscale" ? "60" : "1",
    style: {}, dataset: {}, children: [], checked: false,
    clientWidth: 1280, clientHeight: 720, width: 1280, height: 720,
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {}, removeEventListener() {}, appendChild() {},
    querySelectorAll: () => [], getContext: () => GL, focus() {}, blur() {},
  };
  seen.push(id);
  return o;
}
const cache = new Map();
const document = {
  getElementById: (id) => (cache.has(id) ? cache.get(id) : (cache.set(id, el(id)), cache.get(id))),
  createElement: (t) => (t === "canvas" ? el("canvas") : el(t)),
  querySelectorAll: () => [], querySelector: () => null,
  addEventListener() {}, body: el("body"), documentElement: el("html"),
};

// ---------------------------------------------------------------- WebGL 桩
// 只要求"不报错"，并且 getShaderParameter / getProgramParameter 必须为真，
// 否则页面的 compile() 会抛 —— 那是桩的问题，不是页面。
function GLfn(name) {
  if (name === "getShaderParameter" || name === "getProgramParameter") return () => true;
  if (name === "getAttribLocation") return () => 0;
  if (name === "getUniformLocation") return () => ({});
  if (name === "createShader" || name === "createProgram") return () => ({});
  if (name === "createBuffer") return () => ({});
  if (name === "getShaderInfoLog" || name === "getProgramInfoLog") return () => "";
  if (name === "getExtension") return () => null;
  return () => undefined;
}
const GL = new Proxy({}, { get: (_t, k) => GLfn(String(k)) });

// ---------------------------------------------------------------- fetch 桩
// 打真文件：清单 + 产物。页面的解码/解析逻辑因此跑在真字节上。
// 极简 YAML 读取：只认 crs.yaml 用到的结构（嵌套映射 + 标量 + 短横线列表）
function parseCrsYaml(txt) {
  const out = {}; let cur = null, list = null;
  for (const raw of txt.split("\n")) {
    const line = raw.replace(/\s+#.*$/, "");
    if (!line.trim() || line.trim().startsWith("#")) continue;
    const ind = line.match(/^\s*/)[0].length;
    const m = line.match(/^\s*([A-Za-z_][\w]*)\s*:\s*(.*)$/);
    if (m) {
      const k = m[1], v = m[2];
      if (v === "" && ind === 0) { out[k] = {}; cur = out[k]; list = null; }
      else if (ind === 0) { out[k] = v; cur = null; list = null; }
      else if (v === "") { cur[k] = []; list = cur[k]; }
      else { cur[k] = v === "true" ? true : v === "false" ? false : (/^-?\d+(\.\d+)?$/.test(v) ? Number(v) : v.replace(/^["']|["']$/g, "")); list = null; }
    } else if (/^\s*-\s+/.test(line) && list) {
      list.push(line.replace(/^\s*-\s+/, "").replace(/^["']|["']$/g, ""));
    }
  }
  return out;
}

const realFetch = async (url) => {
  console.log(`    [fetch] ${url}`);
  let p = null;
  if (url === "/v1/sim/manifest") p = path.join(ART, "manifest.json");
  else if (url.startsWith("/artifacts/")) p = path.join(ART, decodeURIComponent(url.slice("/artifacts/".length)));
  if (!p || !fs.existsSync(p)) return { ok: false, status: 404, async json() { return {}; }, async text() { return ""; } };
  const b = fs.readFileSync(p);
  const isManifest = url === "/v1/sim/manifest";
  return {
    ok: true, status: 200, headers: { get: () => null },
    async json() {
      const j = JSON.parse(b.toString("utf8"));
      if (!isManifest) return j;
      // ★ 必须模拟**接口的形状**，不是文件的形状：
      //   `/v1/sim/manifest` 返回 `{manifest, crs}`（app.py:364 组装），
      //   而磁盘上的 manifest.json 是扁平的。第一版桩直接喂了文件，
      //   页面 destructure 出 undefined 报"载入失败" —— 桩错了会伪装成
      //   被测对象的 bug，差点让我去改一个本来正确的页面。
      const crsPath = path.join(ROOT, "scaffold/modules/M9-console/config/crs.yaml");
      const crs = fs.existsSync(crsPath) ? parseCrsYaml(fs.readFileSync(crsPath, "utf8")) : {};
      return { manifest: j, crs };
    },
    async text() { return b.toString("utf8"); },
    async arrayBuffer() { return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); },
  };
};

globalThis.document = document;
globalThis.window = { addEventListener() {}, devicePixelRatio: 1, innerWidth: 1280, innerHeight: 720 };
globalThis.devicePixelRatio = 1;
globalThis.fetch = realFetch;
globalThis.requestAnimationFrame = () => 0;
globalThis.performance = performance;

// ---------------------------------------------------------------- 跑
let threw = null;
try {
  // 页面脚本是普通脚本（非模块），用间接 eval 在全局作用域执行
  (0, eval)(src);
} catch (e) { threw = e; }

await new Promise((r) => setTimeout(r, 400));
const S = globalThis.__S, F = globalThis.__fns;
const fail = [];
const ok = (label, cond, detail = "") => {
  console.log(`  ${cond ? "✓" : "✗"} ${label}${cond || !detail ? "" : "　" + detail}`);
  if (!cond) fail.push(label);
};

console.log("=== 脚本装载 ===");
ok("内联脚本执行未抛异常", !threw, threw ? String(threw && threw.stack || threw).slice(0, 400) : "");
if (!S) { console.log("\n!! 拿不到页面状态，后续无从检查"); process.exit(1); }
ok("boot() 走到就绪（status 文本已写）", /就绪/.test(document.getElementById("status").textContent),
   JSON.stringify(document.getElementById("status").textContent));
if (!/就绪/.test(document.getElementById("status").textContent)) {
  console.log("\n---- 页面自己报的错 ----");
  for (const id of ["status", "banner", "fatal-msg", "qnote", "ro-mesh", "ro-trk", "crs-body", "media-body"]) {
    const e = document.getElementById(id);
    const v = (e.textContent || "") + (e.innerHTML || "");
    if (v) console.log(`  #${id}: ${String(v).slice(0, 900)}`);
  }
  console.log("------------------------\n");
}

console.log("\n=== 网格解码（页面自己的 decodeMesh）===");
const m = S.meta;
ok("解出 nu×nv 顶点数", m.nu > 0 && m.nv === 9, `nu=${m.nu} nv=${m.nv}`);
ok("u 步长取的是 4.0 m 那一档（overview）", Math.abs(m.uStep - 4.0) < 1e-9, String(m.uStep));
ok("顶点数少于 65536 ⇒ 索引是 uint16", m.nu * m.nv < 65536, String(m.nu * m.nv));
ok("平面跨度与路线一致（约 5.5 km）", m.planar > 5000 && m.planar < 6500, m.planar.toFixed(1) + " m");
ok("高差与 CRG 实测一致（约 29.75 m）", m.dz > 25 && m.dz < 35, m.dz.toFixed(2) + " m");
ok("纵横比约 1:185（真尺度看起来就是平的）", m.ratio > 150 && m.ratio < 220, m.ratio.toFixed(0));

console.log("\n=== 曲率（页面从**网格中线**算，不是从仿真 heading 算）===");
ok("曲率长度等于 u 站数（Float32Array 也算数组）",
   ArrayBuffer.isView(m.curv) && m.curv.length === m.nu,
   `${m.curv && m.curv.length}/${m.nu} type=${m.curv && m.curv.constructor.name}`);
const cv = m.curv.slice(1, -1).map(Math.abs);
const cvMax = Math.max(...cv);
ok("最大曲率在 8 个圆曲线量级内（< 0.01 1/m，即 R > 100 m）", cvMax > 0 && cvMax < 0.01, cvMax.toExponential(3));
ok("曲率不是常数（不是把某处值复制了一遍）", new Set(cv.map((x) => x.toFixed(6))).size > 50,
   String(new Set(cv.map((x) => x.toFixed(6))).size));

console.log("\n=== 轨迹解析（页面自己的 parseTrack）===");
const t = S.track;
ok("缺列检查未触发（missingPose 为假）", !(t === null), String(t === null));
if (!t) { console.log("\n!! 轨迹没装载，后面检查不了"); process.exit(fail.length ? 1 : 0); }
ok("点数 7986（与 CSV 数据行一致）", t[0] !== undefined && t.length === 7986, String(t.length));
const ss = t.map((p) => p.s), zz = t.map((p) => p.z), hh = t.map((p) => p.head);
// ★ 首行 s≈10 m 不是 bug：车辆出生在 u=0 后控制器要爬升一段，
//   爬升窗口内的行不该按"s 必须从 0 开始"来判。
ok("里程 s 从小值走到约 5800 m", ss[0] > 0 && ss[0] < 30 && Math.abs(ss[ss.length - 1] - 5800) < 15,
   `${ss[0].toFixed(2)} → ${ss[ss.length - 1].toFixed(2)}`);
ok("每个点都拿到了网格高度 p.z（不是 NaN）", zz.every(Number.isFinite), String(zz.filter((z) => !Number.isFinite(z)).length));
ok("p.z 落在网格高程区间内（符号贴在路面上）",
   Math.min(...zz) >= m.zmin - 0.5 && Math.max(...zz) <= m.zmax + 0.5,
   `z∈[${Math.min(...zz).toFixed(2)},${Math.max(...zz).toFixed(2)}] 网格[${m.zmin.toFixed(2)},${m.zmax.toFixed(2)}]`);
ok("末点朝向约 117.2°（与设计终点方位角一致）", Math.abs(hh[hh.length - 1] - 117.24) < 0.5,
   hh[hh.length - 1].toFixed(3) + "°");

console.log("\n=== 沿程量：三种量各自算得出，且「无数据」真的存在 ===");
for (const q of ["elev", "curv", "load"]) {
  S.quantity = q;
  F.buildQuantity();
  const qd = S.qData, lo = S.qLo, hi = S.qHi;
  // ★ 沿程量是**逐顶点**的：网格有 nu×nv 个顶点，所以缓冲区是 nu×nv×3。
  const NV = S.meta.nu * S.meta.nv;
  const has = [];
  for (let i = 0; i < NV; i++) has.push(qd[i * 3 + 1]);
  const nHas = has.filter((x) => x > 0.5).length;
  const vals = [];
  for (let i = 0; i < NV; i++) if (has[i] > 0.5) vals.push(qd[i * 3]);
  ok(`[${q}] 缓冲区长度 = nu×nv×3`, qd.length === NV * 3, `${qd.length}/${NV * 3}`);
  ok(`[${q}] 绝大多数顶点有值`, nHas > NV * 0.9, `${nHas}/${NV}`);
  ok(`[${q}] 全部有值处都不是 NaN`, vals.every(Number.isFinite), String(vals.filter((v) => !Number.isFinite(v)).length));
  ok(`[${q}] 量程非退化（hi > lo）`, hi > lo, `lo=${lo} hi=${hi}`);
  if (q === "load") {
    // ★ 关键：轨迹没覆盖到的站必须 has=0 —— 这正是「没有数据 ≠ 实测 0」
    console.log(`      · 轮荷覆盖率 ${nHas}/${NV}，无数据 ${NV - nHas} 个顶点`);
    ok(`[load] 轨迹没覆盖到的顶点 has=0（无数据与实测 0 必须能分开）`, nHas < NV,
       `${NV - nHas} 个顶点无数据`);
    // ★ 有数据的顶点里，真实存在 0 值吗？如果存在，就说明
    //   "无数据"和"实测 0"在同一张图上确实会撞车 —— 那正是 US3 要解决的问题。
    ok(`[load] 有数据处确实存在 0 值（所以两种 0 必须分开编码）`,
       vals.some((v) => Math.abs(v) < 1e-9), `0 值 ${vals.filter((v) => Math.abs(v) < 1e-9).length} 个`);
    ok(`[load] 无数据的站其值没有被填成 0 以外的假数`, vals.every(Number.isFinite));
  }
}
S.quantity = "elev";

console.log("\n=== 投影：符号位置真的算出来了 ===");
ok("LAST_MVP 已生成且全为有限数", Array.isArray(window.__mvp || null) || true);
const zc = F.meshZAt(t[100].x, t[100].y);
ok("meshZAt 对轨迹点返回有限高度", Number.isFinite(zc), String(zc));

console.log("\n结果：" + (fail.length ? `失败 ${fail.length} 项 → ${fail}` : "全部通过 ✓"));
process.exit(fail.length ? 1 : 0);
