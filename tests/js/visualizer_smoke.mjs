// Headless smoke test for app/workstation/web/visualizer.js: loads the real file against a stub DOM and canvas,
// feeds it the telemetry of each runtime (ds3 aggregate and sampled, ds2, ds1) and a replayed session, drives every
// mode, leaves and re-enters the view, and fails on any exception. Exercised paths are reported.
// Run: node tests/js/visualizer_smoke.mjs   (no dependencies)
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const source = readFileSync(process.env.VISUALIZER_JS || fileURLToPath(new URL("../../app/workstation/web/visualizer.js", import.meta.url)), "utf8");

// ---------------------------------------------------------------- stub DOM and canvas
const context2d = new Proxy({}, {
  get: (target, key) => key === "measureText" ? (text) => ({ width: String(text).length * 6 })
    : key === "createImageData" ? (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) })
    : key in target ? target[key] : () => {},
  set: (target, key, value) => { target[key] = value; return true; },
});
class Element {
  constructor(id) { this.id = id; this.style = {}; this.dataset = {}; this.children = new Map(); this.value = ""; this.max = 0; this.checked = false; this.innerHTML = ""; this.textContent = ""; this.title = ""; this.options = []; }
  querySelector(selector) {
    if (!this.children.has(selector)) {
      const element = new Element(selector);
      const mode = /data-mode="(\w+)"/.exec(selector); if (mode) element.dataset.mode = mode[1];
      this.children.set(selector, element);
    }
    return this.children.get(selector);
  }
  querySelectorAll(selector) {
    if (selector === "[data-mode]") return ["overview", "layer", "heatmap"].map((m) => this.querySelector(`[data-mode="${m}"]`));
    return [];
  }
  getContext() { return context2d; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 1200, height: 600 }; }
  get clientWidth() { return 1200; }
  get clientHeight() { return 600; }
  classList = { toggle() {}, add() {}, remove() {} };
  click() { this.onclick && this.onclick({ target: this }); }
  appendChild() {} remove() {}
}
const root = new Element("#view-visualizer");
const frames = [];
const sandbox = {
  console, Math, JSON, Date, Map, Set, Array, Number, String, Object, Promise, Uint32Array, Float32Array, Float64Array, Uint8ClampedArray, isFinite, Blob: class { constructor(parts) { this.parts = parts; } },
  URL: { createObjectURL: () => "blob:", revokeObjectURL() {} },
  performance: { now: () => clock },
  setTimeout: () => 0, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
  requestAnimationFrame: (cb) => { frames.push(cb); return frames.length; }, cancelAnimationFrame() {},
  ResizeObserver: class { observe() {} },
  AbortSignal: { timeout: () => undefined },
  document: { querySelector: (s) => s === "#view-visualizer" ? root : new Element(s), createElement: () => new Element("canvas"), body: new Element("body"), hidden: false },
};
let clock = 1000, telemetry = null, sessions = [];
sandbox.fetch = async () => ({ json: async () => ({ ok: true, telemetry }) });
sandbox.window = sandbox;
const model = (caps) => ({ id: "scope", display_name: "DwarfStar Scope", context_label: "128K", backend: { telemetry: caps, speculative: "DFlash2" },
  quantization: { ple: "IQ4_NL, disk" }, cache: { k_cache_dtype: "INT8", v_cache_dtype: "INT8", kv_bytes_per_token: 13000 },
  memory: { routed_experts_gib: 31.6, resident_model_gib: 35.6, kv_gib: 1.6, buffers_gib: 3.5, projector_gib: 0.8 },
  memory_plan: { metal_working_set_gib: 51.8, planned_gib: 41.6, metal_working_set_source: "measured", metal_headroom_gib: 10 } });
const S = { view: "visualizer", listeners: [], live: { tools: { timeline: [] } }, model: "scope", system: {},
  state: { active_profile: "scope-low", models: { scope: { last_request: { input_tokens: 9000, output_tokens: 900 }, splash: { scheduler: {}, prefix_cache: { reused_tokens: 32, hit_rate: 0.5 } } } } },
  catalog: { details: { scope: { assets: { ple: { bytes: 2 ** 34, file: "ple" }, main: { bytes: 2 ** 35, file: "m" }, draft: { bytes: 2 ** 30, file: "d" }, vision: { bytes: 2 ** 29, file: "v" } } } } } };
let current = model({});
sandbox.DwarfStar = { S, esc: String, toast() {}, runningModel: () => "scope", modelOf: () => current, profileLabel: () => "Scope LOW",
  runningLine: () => "", liveModeSwitch: () => "", bindLiveModes() {},
  api: async (path) => path === "/api/sessions" ? { ok: true, sessions } : path.startsWith("/api/sessions/") ? { ok: true, events: [{ kind: "runtime", model: "scope", phase: "running" }, { kind: "tool", phase: "end", name: "read", category: "workspace", call: "1", ok: true, ms: 3, t: 1 }] } : { ok: true, events: [] } };
vm.runInNewContext(source, sandbox);
const V = sandbox.Visualizer;

// ---------------------------------------------------------------- helpers
const failures = [];
async function settle() { for (let i = 0; i < 5; i++) await Promise.resolve(); }
async function frame(label) {
  const pending = frames.splice(0);
  for (const cb of pending) { try { cb(clock); } catch (error) { failures.push(`${label}: ${error.stack.split("\n").slice(0, 2).join(" ")}`); } }
}
const poll = async () => { await sandbox.__pollOnce?.(); };
function layersOf(n, f) { return Array.from({ length: 48 }, (_, l) => f(l)).slice(0, n); }
let seq = 0;
function ds3Batch(withSample) {
  const t = clock;
  const events = [
    { seq: ++seq, t, kind: "decode_cycle", width: 1, output: 3, drafted: 4, accepted: 2, wall_ms: 70, cycle_ms: 72 },
    { seq: ++seq, t: t + 1, kind: "router_aggregate", phase: "decode", steps: 1, routes: 240, cells: 96, interval_ms: 180, payload: true, layers: layersOf(48, (l) => [l, 3, l + 1, 2]) },
    { seq: ++seq, t: t + 1, kind: "router_aggregate", phase: "prefill", steps: 1, routes: 96, cells: 48, interval_ms: 900, payload: true, layers: layersOf(48, (l) => [l + 2, 2]) },
    { seq: ++seq, t: t + 2, kind: "router_aggregate", phase: "decode", steps: 1, routes: 0, cells: 0, interval_ms: 180, payload: false },
    { seq: ++seq, t: t + 2, kind: "ple_aggregate", interval_ms: 500, payload: true, table: { requested_rows: 40, hit_rows: 30, read_rows: 10, read_bytes: 900, cached_rows: 10, cache_capacity_rows: 100, read_ms_p50: 0.5, read_ms_p95: 2 } },
  ];
  if (withSample) events.push({ seq: ++seq, t: t + 3, kind: "router_sample", step: 8, every: 8, top_k: 10, payload: true, layers: layersOf(48, (l) => [l, l + 1]) });
  return { schema: 2, next: seq, dropped: 0, router: { available: true, layers: 48, experts: 512, top_k: 10, mode: withSample ? "sampled" : "aggregate", sample_every: 8, samples_dropped: 0 }, events };
}
function ds2Batch() { return { schema: 1, next: ++seq, dropped: 0, events: [{ seq, t: clock, kind: "decode_cycle", width: 1, output: 2, drafted: 3, accepted: 1, wall_ms: 70, cycle_ms: 71 }] }; }

// The poll lives behind setInterval: capture its callback and run it on demand.
let intervalCallback = null;
sandbox.setInterval = (cb) => { intervalCallback = cb; return 7; };
async function tick(label) { clock += 200; if (intervalCallback) { await intervalCallback(); await settle(); } await frame(label); }
async function modes(label) {
  for (const mode of ["overview", "layer", "heatmap", "overview"]) { root.querySelector(`[data-mode="${mode}"]`).click(); await tick(`${label} ${mode}`); }
}

// ---------------------------------------------------------------- scenarios
const DS3 = { dflash: "cycle", routing: "sampled", routing_levels: ["aggregate", "sampled"], ple: "aggregate" };
const DS2 = { dflash: "cycle", routing: "unavailable", routing_levels: [], ple: "none" };
const DS1 = { dflash: "none", routing: "unavailable", routing_levels: [], ple: "none" };

current = model(DS3);
V.mount(); await settle();
await frame("ds3 first frame, before any poll");          // V.router is still null here
telemetry = ds3Batch(false);
await modes("ds3 aggregate");
root.querySelector("#viz-sampled").checked = true; root.querySelector("#viz-sampled").onchange({ target: { checked: true } });
telemetry = ds3Batch(true);
await modes("ds3 sampled");
root.querySelector("#viz-canvas").onclick({ clientX: 400, clientY: 300 });   // heatmap row pick
await tick("ds3 layer pick");
const history = root.querySelector("#viz-history");
history.onclick({ target: { closest: () => ({ dataset: { request: "1", action: "view" } }) } }); await modes("ds3 viewing a request");
history.onclick({ target: { closest: () => ({ dataset: { request: "1", action: "export" } }) } });
root.querySelector("#viz-pause").onclick(); await tick("ds3 back to live");
V.unmount(); await tick("left the view");
V.mount(); await settle(); await tick("re-entered the view");

sessions = [{ id: "20261118-000000-scope" }];
await root.querySelector("#viz-session").onchange({ target: { value: "20261118-000000-scope" } }); await settle();
await modes("replay");
root.querySelector("#viz-pause").onclick(); await tick("replay ended (router not yet re-polled)");

current = model(DS2); telemetry = ds2Batch();
await modes("ds2 fallback");
current = model(DS1); telemetry = { schema: 1, next: seq, dropped: 0, events: [] };
await modes("ds1 (no cycles)");

if (failures.length) { console.error("FAIL\n" + failures.join("\n")); process.exit(1); }
console.log("ok: ds3 aggregate, ds3 sampled, layer pick, request view/export, leave/re-enter, replay, ds2, ds1");
