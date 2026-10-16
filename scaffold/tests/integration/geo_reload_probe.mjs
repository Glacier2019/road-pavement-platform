// 区域帧页（/geo）「数据新鲜度」与「重新取数」的验收探针。
//
// 护四件事 —— 2026-10-16 之前，一件都没有：
//   ① 曲面面板写出**产物生成时间**（manifest.generated_at）
//   ② 平面图面板写出**取出时刻**
//   ③ 「重新取数」把曲面也带上，并且真的绕过浏览器缓存
//   ④ 按钮必须**回话**：否则「成功了」和「点了没反应」是同一幅画面
//
// 做法照抄 geo_surface_probe.mjs：把 geo.html 的真实内联脚本装进 Node 跑，
//   Canvas2D 用记录型桩替代，页面代码一个字不改。
// ★ 与它不同的是：内联脚本**本文件自己从 geo.html 抽**，
//   不去读 .scratch/ 里的暂存件 —— 那种依赖不入库，等于把探针绑在一个
//   别人机器上不存在的文件上（D-036 补记刚踩过这个坑）。
//
// ★ 它**不是**注册门：要 console 活在 :8024 上（清单与网格都走 HTTP），
//   所以不进 run_contract_tests.sh 的 TESTS= —— 一个环境不在就跳过的检查，
//   在"全绿"里的读数和"没跑"是一样的。
//
// 跑法：node scaffold/tests/integration/geo_reload_probe.mjs
//       （先起 console：docker compose -f docker-compose.skeleton.yml up -d console）
//
// ★ 它自带**变异体**：把页面源码里那几行防护逐个删掉，验证本探针确实会红。
//   一个定不出靶子的探针，永远不会红（宪法 §V）。
import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const HTML = path.resolve(HERE, "../../modules/M9-console/geo.html");
const BASE = "http://127.0.0.1:8024";
const realFetch = globalThis.fetch;

/* ------------------------------------------------------------ 取内联脚本 */
function inlineScript() {
  const s = fs.readFileSync(HTML, "utf8");
  const i = s.indexOf("<script>"), j = s.lastIndexOf("</script>");
  if (i < 0 || j < 0) throw new Error("geo.html 里找不到内联 <script>");
  return s.slice(i + "<script>".length, j);
}

/* ------------------------------------------------------------ DOM / fetch 桩 */
function makeEnv(state) {
  const REG = new Map();
  const CTXS = new Map();
  const calls = [];

  const ctxOf = (id) => {
    if (!CTXS.has(id)) {
      const rec = { pts: [], fills: 0, strokes: 0, texts: [], styles: new Set() };
      CTXS.set(id, new Proxy({}, {
        get(t, k) {
          switch (k) {
            case "moveTo": case "lineTo": return (x, y) => rec.pts.push([x, y]);
            case "fill": return () => { rec.fills++; };
            case "stroke": return () => { rec.strokes++; };
            case "fillText": return (s) => rec.texts.push(String(s));
            default: return () => {};
          }
        },
        set(t, k, v) { if (k === "fillStyle" || k === "strokeStyle") rec.styles.add(v); t[k] = v; return true; },
      }));
    }
    return CTXS.get(id);
  };

  const el = (id) => ({
    id, style: {}, innerHTML: "", textContent: "", className: "", value: "",
    disabled: false, __on: {},
    clientWidth: 1200, clientHeight: 224, width: 0, height: 0,
    offsetWidth: 10, offsetHeight: 10,
    getContext: () => ctxOf(id),
    // ★ 要能把监听器**记下来**：本探针的靶子之一就是 #reload 的点击，
    //   桩要是一律 no-op，那个靶子就永远打不中。
    addEventListener(t, fn) { (this.__on[t] = this.__on[t] || []).push(fn); },
    appendChild() {}, setPointerCapture() {},
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 1200, height: 224 }),
    querySelector: () => el(id + "-q"),
  });

  // ★ 表单控件的初始值照 HTML 写（#win 上写着 selected，range 用 value 属性）。
  //   桩给空串的话页面会读到 0，量出来的就不是真实页面。
  const SEED = { win: "200", zex: "3", w0: "0", step: "20" };

  const document = {
    getElementById: (id) => {
      if (!REG.has(id)) { const e = el(id); if (SEED[id] !== undefined) e.value = SEED[id]; REG.set(id, e); }
      return REG.get(id);
    },
    querySelector: (s) => REG.get(s) || (REG.set(s, el(s)), REG.get(s)),
    createElement: (t) => el("new-" + t),
  };

  const fetchStub = async (u, o) => {
    const url = u.startsWith("http") ? u : BASE + u;
    calls.push(url);
    // 只让清单取不到，别的照常 —— 用来验「取不到时不能留旧值」。
    if (state.failManifest && /\/v1\/sim\/manifest/.test(url)) {
      return { ok: false, status: 500, json: async () => ({}) };
    }
    return realFetch(url, o);
  };

  const sandbox = {
    document,
    window: { devicePixelRatio: 1, addEventListener() {} },
    // ★ 页面会读 location.search（跨页深链 /geo?u=…）。vm 的上下文里**既没有**
    //   location 也没有 URLSearchParams —— 不补桩，页面读它的那一段就会抛
    //   ReferenceError，而探针会把「桩没给」读成「页面坏了」。
    //   （这不是假设：加了深链之后，探针正是这样偶发变红的。）
    //   state.search / state.hash 让判据能把一条真深链喂进去。
    location: {
      get search() { return state.search || ""; },
      get hash() { return state.hash || ""; },
    },
    URLSearchParams,
    fetch: fetchStub,
    setTimeout: (...a) => setTimeout(...a),
    clearTimeout: (...a) => clearTimeout(...a),
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
    console,
  };
  const ctx = vm.createContext(sandbox);
  return { ctx, sandbox, REG, calls };
}

const TEXPORT = `
;globalThis.__T = { loadSection, loadSurface, get SURF() { return SURF; } };`;

async function waitFor(pred, ms = 8000, step = 50) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, step));
  }
  return false;
}

// ★ 读一个可能**根本没被创建**的元素。
//   页面一旦没走到那段代码（比如曲面压根没被取），getElementById 从没被调过，
//   桩里就没有这个 id。这时要**照实说**，而不是让探针自己抛异常 ——
//   探针崩了等于把一个"没验到"混成了"验过了"。
function txt(env, id) {
  const e = env.REG.get(id);
  return e ? e.textContent : "<元素未被创建>";
}
function html(env, id) {
  const e = env.REG.get(id);
  return e ? e.innerHTML : "<元素未被创建>";
}

const manifestUrl = (us) => us.filter((u) => /\/v1\/sim\/manifest/.test(u));
const meshUrl = (us) => us.filter((u) => /\/artifacts\/.*\.bin/.test(u));
const routeUrl = (us) => us.filter((u) => /\/geo\/route\?step_m=/.test(u));

/* ------------------------------------------------------------ 跑一次页面 */
async function scenario(src, { failManifestAtReload = false, search = "" } = {}) {
  const state = { failManifest: false, search };
  const env = makeEnv(state);
  vm.runInContext(src + TEXPORT, env.ctx, { filename: "geo.html<inline>" });
  const T = env.sandbox.__T;

  // 等开机那支 IIFE 把曲面取回来
  await waitFor(() => txt(env, "surfgen") !== "");
  await waitFor(() => T.SURF, 3000);

  const bootUrls = [...env.calls];
  const boot = {
    gen: txt(env, "surfgen"),
    note: txt(env, "surfnote"),
    meta: html(env, "meta"),
    urls: bootUrls,
  };

  // 点「重新取数」
  env.calls.length = 0;
  if (failManifestAtReload) state.failManifest = true;
  const rl = env.REG.get("reload");
  await Promise.all((rl.__on.click || []).map((fn) => fn({})));
  const reload = {
    note: txt(env, "rlnote"),
    gen: txt(env, "surfgen"),
    surfnote: txt(env, "surfnote"),
    urls: [...env.calls],
  };

  // 切路段（不带 fresh）应当**照常吃缓存**：只验它没有多挂缓存串
  env.calls.length = 0;
  await T.loadSection();
  const section = { urls: [...env.calls] };

  return { T, boot, reload, section, failManifestAtReload };
}

/* ------------------------------------------------------------ 独立复算 */
const p2 = (n) => String(n).padStart(2, "0");
// ★ 这里**故意不调用页面里的 localStamp**：拿被测代码去核对被测代码，
//   两边一起错就永远绿。这个格式化是独立写的一份。
function fmtLocal(iso) {
  const d = new Date(String(iso).replace(/(\.\d{3})\d+/, "$1"));
  if (isNaN(d)) return null;
  return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())} ` +
         `${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`;
}

/* ------------------------------------------------------------ 判据 */
const STAMP = String.raw`\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}`;

function judge(runs, wantGen) {
  const ok = runs.ok, bad = runs.bad;
  const c = (name, pass, detail) => ({ name, ok: !!pass, detail: detail || "" });

  return [
    c("① 曲面面板写出产物生成时间",
      ok.boot.gen === wantGen,
      `面板 "${ok.boot.gen}" ／ 清单里算出来 "${wantGen}"`),

    c("① 生成时间来自 manifest，值对得上（不是占位符）",
      !!wantGen && wantGen.includes("20") && ok.boot.gen === wantGen,
      `wantGen=${wantGen}`),

    c("② 平面图面板写出取出时刻",
      new RegExp(`取出时刻</dt><dd>${STAMP}</dd>`).test(ok.boot.meta),
      ok.boot.meta.match(/取出时刻<\/dt><dd>[^<]*/) || "meta 里没有这一行"),

    c("③ 点「重新取数」后清单被重新请求，且带缓存串",
      manifestUrl(ok.reload.urls).some((u) => u.includes("?t=")),
      `manifest 请求：${JSON.stringify(manifestUrl(ok.reload.urls))}`),

    c("③ 网格也被重新请求，且带缓存串",
      meshUrl(ok.reload.urls).some((u) => u.includes("?t=")),
      `网格请求：${JSON.stringify(meshUrl(ok.reload.urls))}`),

    c("③ 「重新取数」把平面图也再取一遍",
      routeUrl(ok.reload.urls).length > 0,
      `route 请求 ${routeUrl(ok.reload.urls).length} 次`),

    c("④ 按钮回话（写明这次动作发生在几点）",
      new RegExp(`^已重新取数 ${STAMP}$`).test(ok.reload.note),
      `rlnote="${ok.reload.note}"`),

    c("★ 开机第一次取**不**挂缓存串（不许把缓存白白废掉）",
      manifestUrl(ok.boot.urls).length > 0 &&
      manifestUrl(ok.boot.urls).every((u) => !u.includes("?t=")),
      `开机 manifest：${JSON.stringify(manifestUrl(ok.boot.urls))}`),

    c("★ 切路段**不**挂缓存串（只有手动重取才绕缓存）",
      manifestUrl(ok.section.urls).length > 0 &&
      manifestUrl(ok.section.urls).every((u) => !u.includes("?t=")),
      `切路段 manifest：${JSON.stringify(manifestUrl(ok.section.urls))}`),

    // ★ 这一条是"能红"的核心：防护一旦删掉，旧时刻会留着假装新时刻。
    c("★★★★ 清单取不到时，生成时间必须**空着**（不能留旧值假装新值）",
      ok.boot.gen !== "" && bad.reload.gen === "",
      `开机 "${ok.boot.gen}" ／ 清单 500 之后 "${bad.reload.gen}"`),
  ];
}

/* ------------------------------------------------------------ 变异体 */
// 每个变异体：在源码上做一处改动 → 声明**必须变红**的判据。
const MUTANTS = [
  {
    why: "删掉「先清空生成时间」",
    from: '  gen.textContent = "";\n',
    to: "",
    mustFail: ["★★★★ 清单取不到时，生成时间必须**空着**（不能留旧值假装新值）"],
  },
  {
    why: "手动重取不挂缓存串",
    from: 'const bust = fresh ? `?t=${Date.now()}` : "";',
    to: 'const bust = "";',
    mustFail: [
      "③ 点「重新取数」后清单被重新请求，且带缓存串",
      "③ 网格也被重新请求，且带缓存串",
    ],
  },
  {
    why: "loadSection 不带上曲面",
    from: "  await loadSurface(fresh);\n",
    to: "",
    mustFail: [
      "③ 点「重新取数」后清单被重新请求，且带缓存串",
      "★★★★ 清单取不到时，生成时间必须**空着**（不能留旧值假装新值）",
    ],
  },
  {
    why: "按钮不回话",
    from: 'RLNOTE.textContent = `已重新取数 ${localStamp(new Date())}`;',
    to: 'RLNOTE.textContent = "";',
    mustFail: ["④ 按钮回话（写明这次动作发生在几点）"],
  },
];

/* ------------------------------------------------------------ 主 */
const out = [];
let failed = 0;
const line = (s) => out.push(s);

const src = inlineScript();
line(`  内联脚本 ${src.length} 字符（自己从 geo.html 抽，不读 .scratch/）`);

const mf = await realFetch(`${BASE}/v1/sim/manifest`).then((r) => r.json())
  .catch((e) => { throw new Error(`取不到清单（console 起了吗？${BASE}）：${e.message}`); });
const wantGen = "产物生成于 " + fmtLocal((mf.manifest || mf).generated_at);
line(`  manifest.generated_at = ${(mf.manifest || mf).generated_at}`);
line(`  期望面板写：${wantGen}`);
line("");

const ok = await scenario(src);
const bad = await scenario(src, { failManifestAtReload: true });
const checks = judge({ ok, bad }, wantGen);

line("  ── 判据 ──");
for (const k of checks) {
  if (!k.ok) failed++;
  line(`    ${k.ok ? "✓" : "✗"} ${k.name}${k.detail ? "  " + k.detail : ""}`);
}

line("");
line("  ── ★ 自我证明：删掉防护，本探针必须变红 ──");
for (const m of MUTANTS) {
  if (!src.includes(m.from)) {
    failed++;
    line(`    ✗ 变异体「${m.why}」没能在源码里找到锚点 —— 锚点漂了，这一条等于没验`);
    continue;
  }
  const mutated = src.replace(m.from, m.to);
  if (mutated === src) {
    failed++;
    line(`    ✗ 变异体「${m.why}」替换后源码没变 —— 等于没验`);
    continue;
  }
  let got;
  try {
    const mok = await scenario(mutated);
    const mbad = await scenario(mutated, { failManifestAtReload: true });
    got = judge({ ok: mok, bad: mbad }, wantGen);
  } catch (e) {
    got = null;
    line(`    ✗ 变异体「${m.why}」跑不起来：${e.message}`);
    failed++;
    continue;
  }
  const red = new Set(got.filter((k) => !k.ok).map((k) => k.name));
  const missed = m.mustFail.filter((n) => !red.has(n));
  if (missed.length) {
    failed++;
    line(`    ✗ ${m.why} —— 该红没红：${JSON.stringify(missed)}`);
  } else {
    line(`    ✓ ${m.why} → 判红 ${red.size} 条（含全部 ${m.mustFail.length} 条指定的）`);
  }
}

console.log(out.join("\n"));
console.log("");
console.log(failed === 0 ? "  全部通过 ✓" : `  ✗ ${failed} 条不合格`);
process.exit(failed === 0 ? 0 : 1);
