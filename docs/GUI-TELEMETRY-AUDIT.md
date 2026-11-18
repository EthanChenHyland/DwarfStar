# GUI and telemetry dead-code audit (after 1.1.0)

A cleanup pass on the Workstation's telemetry plumbing and the Advanced visualizer after ds3 became the production runtime (1.1.0). Nothing new is measured. The engine and the ds3 patches are unchanged. Goal: one telemetry model, one visualizer, ds2 rollback that keeps working, explicit replay, and no leftover native-era machinery.

## Architecture after the audit

```
model server (Splash ds3 | ds2)              Workstation service (8038)                 Workstation web view
  GET /telemetry?since&level  ──────────────▶  /api/telemetry (level clamped 2–3) ──────▶  Advanced view: one single-flight
     schema 2: decode_cycle,                                                              5 Hz poll, only while it is open
     router_aggregate, router_sample,
     ple_aggregate
  GET /status (memory plan, receipts) ──────▶  /api/state ──────────────────────────────▶  Standard + Advanced (receipt)

controller, agent layer ── UDP ──▶ hub ──────▶  /api/events (one SSE stream) ───────────▶  app.js: one EventSource
                                    └─ records sessions (last 30) ─▶ /api/sessions ──────▶  Advanced: replay picker
```

* **Inference telemetry** comes only from the engine's `GET /telemetry`. It never passes through the hub, is never recorded, and is read only by the Advanced view.
* **The event stream** carries runtime, lifecycle, agent, tool, chat and host-sample events. There is one subscriber per window, with no levels.
* **Capabilities** (`catalog` → `backend.telemetry`) say exactly what `/telemetry` reports. The view branches on these, never on a runtime name:

| Runtime | `dflash` | `routing` | `routing_levels` | `ple` |
|---|---|---|---|---|
| ds3 (production) | `cycle` | `sampled` | `aggregate`, `sampled` | `aggregate` |
| ds2 (rollback) | `cycle` | `unavailable` | — | `none` |
| ds1 | `none` | `unavailable` | — | `none` |

## Signal inventory

| Signal | Producer | Consumer | Status |
|---|---|---|---|
| `decode_cycle` (width, output, drafted, accepted, wall/cycle ms) | engine, ds2 and ds3 | DFlash2 card, output stream, request history, export | keep |
| `router_aggregate` (per phase: layer × expert route counts) | engine, ds3 | overview strips, heatmap, layer focus, export | keep |
| `router_sample` (1 decode step in N, row 0, no weights) | engine, ds3, level 3 only | outlines, busiest-expert marks, export | keep |
| `ple_aggregate` (rows requested / hit / read, bytes, latency) | engine, ds3 | Storage PLE line, per-request PLE in export | keep |
| runtime / lifecycle / agent / tool / chat events | controller, agent layer | nav state, action timeline, agent actions, replay | keep |
| host samples | service `_system_loop`, only while someone subscribes | Standard and Advanced host meters | keep |
| `tick` (`r` router rows with weights, `dp`/`da` MTP, `ple`, `pos`, `k`) | retired native runtime | — | **removed** |
| `request`, `kv`, `vision` events | retired native runtime | — | **removed** (consumers) |
| hub router heat, `/api/heat` | hub, from ticks | old heatmap seed | **removed** |
| subscriber detail levels (`?level=` on `/api/events`) | hub | — | **removed** (the query is ignored) |

**Reachability proof.** Splash (ds1–ds3) has never emitted `tick`, `request`, `kv` or `vision`. Nothing in `src/` produces them. All 30 retained session recordings contain only `runtime`, `lifecycle`, `agent`, `tool`, `chat` and `session_end` events. No archive, manifest, backup or app-data directory holds a tick recording. The native renderers therefore had no input, live or replayed. Dynamic coverage of the visualizer in the real app executed 57 of 59 functions. The other two, `pickLayer` and `routerSample`, were then exercised by hand: a heatmap row click, and Sampled selections.

## What was removed

* **Hub** (`src/dwarfstar/telemetry/hub.py`, 359 → 267 lines):
  * tick fan-out and throttling (`TICK_FANOUT_HZ`, `_tick`, `_flush_tick`);
  * router heat and `heat()`;
  * per-subscriber levels and `wanted_level()`, replaced by `watched()`;
  * native fields in the live snapshot.
* **Service:**
  * the `/api/heat` route;
  * the level argument to `/api/events`;
  * `detail_level` in diagnostics.
* **`app.js`:**
  * the tick branch and tick, event and tool series;
  * the `sourceLevel` reconnect on every view change (now one EventSource for the window's life);
  * `live.request` in the nav phase;
  * Standard's native fallbacks (it reads the request receipt only).
* **`visualizer.js`** (903 → 833 lines, with the new features):
  * the native tick, request, KV and vision handling;
  * `seedHeat`, `animateTick`, `drawLayer`, `drawHeat`, `renderRequests` and the Requests panel;
  * `backendName`;
  * MTP naming (`COLORS.mtp` → `COLORS.draft`) and `COLORS.cyber`;
  * native expert counts;
  * the KV-checkpoint branch;
  * the replay paths that drew native router rows.
* **CSS:** `.req-bar`, `.cols-4` (and its media query), `.notice.info`, `.tabs`.
* **Capabilities:** the stale native flags (`router`, `ple` boolean, `mtp`, `requests`, `memory_plan`). `ple_rows` was renamed to `ple`.

## What was fixed along the way

| Problem | Fix |
|---|---|
| The drawing loop kept running after leaving Advanced (`requestAnimationFrame` never cancelled) | Unmount cancels the frame and the poll; re-entering restarts both once |
| `V.reads` grew without bound on ds3 (one entry per PLE aggregate) | Replaced by one timestamp (`pleReadAt`) |
| First frame before the first poll, or right after a replay, threw (`V.router` null) | Null guard; covered by the headless test |
| During prefill the Advanced status read "idle · last run" (the scheduler snapshot is not refreshed while Advanced is open) | Fresh prefill-phase routing drives "prefilling a prompt"; the scheduler stays the fallback |
| On ds2, ds1 and in replay, Layer focus showed a layer slider with nothing to pick | The picker follows the routing capability, like the Sampled toggle |
| Replay could look like live data | Replay says "not in this recording" for router, PLE and draft cycles; the clock reads "replay · no decode cycles recorded" |

## Polling: one owner per loop

| Loop | Owner | Runs when |
|---|---|---|
| `/api/events` SSE | `app.js` `connect()` | the window is open (one connection) |
| `/api/state` refresh | `app.js` | on runtime and lifecycle events, plus the app's own refresh |
| `/api/telemetry` 5 Hz, single-flight | `visualizer.js` | Advanced is mounted and the model reports something |
| drawing (`requestAnimationFrame`) | `visualizer.js` | Advanced is mounted |
| host samples | service `_system_loop` | someone is subscribed (`hub.watched()`) |

Standard Live never polls `/telemetry`. The engine drops back to aggregate 3 s after the last level-3 poll, and turns off 3 s after the last poll at any level.

## Export

The request export moved to **format 2**. It adds the engine's `telemetry_schema`, the exact `capabilities`, `cycles_semantics`, and a `ple` section with semantics. The `router` section is unchanged. Format 1 was never read back by anything; it is a download only. Verified in the browser: a 161-cycle request exported 161 rows × 480 = 77,280 routes, and PLE was attributed to the request.

## ds2 rollback and replay

* **ds2** has routing `unavailable` and PLE `none`. The processor region says "Router activity not reported by this runtime", the Storage PLE line says "read rate: not reported", and the DFlash2 card and output stream work from `decode_cycle`. The Sampled toggle and layer picker are hidden, and the console is clean. This was checked in a real browser with the branch's assets and a ds2-capability catalog, and headlessly with ds2 telemetry.
* **ds1** has no cycles. The draft card says "per-cycle drafts not reported by this runtime".
* **Replay** draws tool actions and the runtime from the recording and fabricates nothing. Native recordings are not kept and are not drawn.

## Measurements

The WebContent process was sampled with `top`, 7 × 3 s per state, on the same machine with Scope LOW loaded.

| State | Before (1.1.0) | After |
|---|---|---|
| Run page, idle, after visiting Advanced | 4.2 % CPU | 0.4 % |
| Standard Live | 4.3 % | 0.8 % |
| Advanced Live, decoding | 17.3 % | 16.2 % |
| WebView resident memory | 72–80 MB | 66–68 MB |

The idle and Standard drops come from stopping the drawing loop when you leave Advanced. Advanced itself costs the same, as expected: it draws the same picture.

## Storage

The ds3 development byproducts in `~/AI/DwarfStar/Sources/splash-m1/` (`splash-ds3`, `splash-ds3-repro`, `ds3-builds`) were deleted, reclaiming 931 MiB. They are reproducible: `scripts/backends/build_splash.sh SOURCE RELEASE_DIST OUT --ds3` rebuilds the runtime from `splash-src` and the committed patches, and the installed ds3 runtime was itself built that way, from a fresh clone, before the byproducts were deleted. The exported patches matched the committed ones (`cmp`). The installed ds3 and ds2 runtimes, patches, pinned hashes, source revisions, manifests, model storage and external backups were not touched.

## Kept on purpose

* `scripts/validate/production_validation.py` still counts `tick` and `request` events. Its evidence format is read by `telemetry_ab.py`, `backend_ab.py` and `benchindex`, and old evidence files carry those fields. Against Splash the counters are zero; its `?level=` query is now ignored.
* `runtime_control.py` keeps the `ds4-server` foreign-process guard and the `DS4_` environment scrub. These are safety checks against a stray native server, not telemetry.
* `acp/benchmark-snapshot.ts` keeps the `ds4_server.c` renderer identity string, which describes historical benchmark snapshots.
* Historical docs (`DS3-GPU-TELEMETRY*.md`, `NATIVE-RETIREMENT.md`, the reports) describe native ticks as they were and were not rewritten.

## Tests

* `tests/js/visualizer_smoke.mjs`, run by `tests/test_visualizer_js.py`, loads the real `visualizer.js` against a stub DOM and canvas. It drives every scenario and fails on any exception:
  * ds3 first frame, aggregate and sampled;
  * a layer pick;
  * request view and export;
  * leaving and re-entering the view;
  * replay;
  * ds2, including the hidden controls;
  * ds1.

  It caught both the first-frame null bug and the ds2 layer picker before they were fixed.
* `test_capabilities_are_exactly_the_reported_semantics` pins the capability key set and the ds3, ds2 and ds1 mappings.
* `test_hub.py` checks three things:
  * the snapshot carries only what live producers send;
  * the hub is watched while anyone subscribes;
  * every subscriber gets every event (multi-consumer).
* The existing ds3 unit tests are unchanged. They cover the checker's schema, sequence, bounds, per-phase routes, samples, PLE and expired-detail rules, and the pins.
* The live checker `scripts/backends/ds3_check.py` is unchanged. It covers both phases, levels 2 and 3, and a second consumer that must get every event's detail.
* The view's poll is single-flight by construction: a guard flag means a slow response never overlaps the next tick.
* In this audit the real app showed the following on Scope LOW:
  * PREFILL→DECODE switching automatically;
  * Sampled 1 in 8 with outlines;
  * a heatmap layer pick;
  * request history matching the server's token counts (40 and 120);
  * request view, replay, and export format 2.

## Remaining debt

* Request history is a decode run, not a request id. The engine reports no ids, so two clients decoding at once share one entry. This is documented in `VISUALIZER.md`.
* `production_validation.py`'s native counters could become a versioned evidence field. That needs `benchindex` to accept both formats, so it was left alone.
* No engine-level dead path was found, so the engine freeze held.
