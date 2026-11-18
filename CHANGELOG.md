# Changelog

## 1.1.1 — 2026-11-18

A patch release: bug fixes and maintenance cleanup in the Workstation's telemetry internals. **ds3 stays the production runtime; the engine, its patches and inference behaviour are unchanged.** No persistent-data schema changed; chats, folders, settings and Zed entries carry over as they are.

### Fixed

* The Advanced view kept drawing after you left it; leaving now stops its drawing loop and telemetry poll (idle CPU after visiting Advanced: 4.2 % → 0.4 %; Standard: 4.3 % → 0.8 %).
* PLE read state grew without bound on ds3 while the Advanced view was open.
* The first frame before any telemetry, and the first frame after a replay, could throw and leave the processor blank.
* The Advanced status read "idle · last run" while a prompt was prefilling; it now says "prefilling a prompt".
* On ds1 and ds2 (and in a replay) Layer focus showed a layer picker with nothing to pick; it now follows the routing capability like the Sampled toggle.

### Cleaned up

* The retired native runtime's telemetry path: per-token `tick` events, hub router heat, `/api/heat` and per-subscriber detail levels. Splash never emitted them and no kept recording contains them.
* The native router and PLE renderers, the dead Requests panel, old MTP naming and state, unused capability fields (`backend.telemetry` is now exactly `dflash`, `routing`, `routing_levels`, `ple`), and CSS nothing used.
* The Workstation keeps one event stream for the window's life instead of reconnecting it at a different detail level on every view change.
* Replays draw what a recording holds (tool actions and runtime events) and say "not in this recording" for the rest.

### Telemetry export

* Request export is **format 2**: it names the engine's telemetry schema and the runtime's capabilities, states the cycle semantics (DFlash2 verify cycles, exact) and adds the request's PLE totals. Every format-1 field is kept; nothing in DwarfStar reads export files back, so no migration is needed.

See docs/GUI-TELEMETRY-AUDIT.md for the audit behind this release.

## 1.1.0 — 2026-11-18

A backwards-compatible feature release: Splash ds3 restores the rich Advanced visualisation with telemetry aggregated on the GPU. No persistent-data schema changed; chats, folders, settings and Zed entries carry over as they are.

### Splash ds3

* **Production runtime is now `splash-m1-1.6.1-ds3-gpu-telemetry`** (engine `ae07f09a…`, shaders `1d9ae6f5…`), built from the same pinned Splash-m1 1.6.1-m1 source with `scripts/backends/build_splash.sh --ds3`, which reproduces it byte for byte. **ds2 (`splash-m1-1.6.1-ds2-telemetry`) stays installed and pinned as the rollback**: set `config/runtime-policy.json` back to it, `dwarfstar generate`, restart.
* Router telemetry is **aggregated on the GPU**: the MoE grouping kernel already counts each step's routes per expert and now adds those counts to a 0.5 MB host-visible page. The engine reads it when a telemetry consumer polls, with a memory copy — no GPU command, no fence and no GPU→CPU synchronisation was added to the decode path. With nobody polling, the counters switch off.
* Optional **sampled selections** copy the experts row 0 of one decode step in N chose (N = 8 by default). Exact router weights are not available, and aggregates count routes executed (including speculative rows a later check rejects); nothing is presented as exact per-token routing.
* **PLE telemetry** from the row store's own counters: rows requested, cache hits, SSD reads, bytes and read latency.
* `GET /telemetry` schema 2: every consumer receives every event's detail (kept by sequence, bounded at 4 MiB), and each detail level lapses on its own 3 s after its last poll. ds2's exact per-verify-cycle DFlash2 records are unchanged.

### Restored Advanced visualisation

* Router activity per layer, a **layer × expert heatmap**, **Layer focus** (busiest experts with shares), **expert utilisation** and **routing concentration** (top-10 share, effective experts), **sampled expert selections**, and PLE activity and cache hits — alongside the existing exact DFlash2 verify-cycle telemetry.
* About **90 % of the useful native-era visualisation** is back (docs/DS3-GPU-TELEMETRY.md §9).

### Workstation

* The phase **follows the engine automatically**: prefill while a prompt is processed, decode while the model generates, on one display. There is no Decode/Prefill toggle any more.
* A badge reports what is drawn and the phase: `AGGREGATE · DECODE`, `SAMPLED 1 IN 8 · PREFILL`.
* The current **tok/s** displays correctly (the run being generated, else the last run), and the memory panel shows the latest request's context.
* A bar per recent verify cycle (accepted / rejected drafts) replaces the old flying MTP-style draft pills; the PLE dots that crossed the panels and stale native labels ("Trunk + MTP") are gone.
* Request export adds a `router` section with `router_mode: aggregate|sampled`.
* Fixed: returning to the Advanced view after another view left its telemetry poll stopped; a slow poll could be overtaken and replay events out of order; Standard Live's footer said router and PLE activity were unreported on a runtime that reports them.

### Performance

Measured on Scope (M1 Max), per verify cycle and adjusted for draft length, against ds2 in the same session; ordinary run-to-run variation is about ±1 %:

* ds3 with nobody watching (OFF): about **+0.36 %** decode (interleaved A/B), −0.01 % on short requests.
* Aggregate router + PLE, with the view polling: about **+0.11 %** decode.
* Sampled modes (1 in 16, 8 or 4): roughly **+0.12 to +0.36 %** decode.
* Memory: about **+0.001 GiB** at peak. Time to first token, prefill rate and draft acceptance are unchanged.

### Limitations

Still unavailable: router gate weights; the exact expert selections of every speculative row (counted in the aggregates, not sampled); per-token PLE reads (reported per interval); per-request prompt rows where the runtime records none. These reduce granularity, not the main operational views.

## 1.0.0 — 2026-11-13

The first tagged release: the Splash-m1 workstation as validated on an M1 Max (64 GiB), hardened to be reproducible, recoverable and checkable. No inference behaviour changed in the hardening work; the runtime, models, profiles and benchmarks are those of the ds2 promotion.

### Runtime

* **ds2 advanced-telemetry runtime is production** (`splash-m1-1.6.1-ds2-telemetry`): the ds1 build plus a bounded in-engine ring and `GET /telemetry` with per-verify-cycle speculative-decode records. Measured cost is within the run-to-run noise floor (−0.11 % to −0.17 % per-cycle decode cost against ds1; ±0.7 % noise), output unaffected. ds1 stays pinned as the rollback. See docs/ADVANCED-TELEMETRY.md.
* Router, expert and PLE activity remain **not reported**: capturing them on Splash needs GPU→host copies on the decode hot path. The UI labels those regions instead of estimating.

### Workstation

* **Visualizer:** per-cycle DFlash2 draft panel (rolling acceptance, current cycle, accepted/rejected tokens), and a bounded **request history** (last 20 requests, named by profile and time) with View/scrub/Back to live and an explicit per-request JSON export.
* **Setup guide:** a new installation opens a first-run guide over the `doctor` checks (green/yellow/red with the command or file path for each fix). It never downloads anything.
* **Downloads** (chat export, request export) now go through a Save panel in the app.
* **Chat folders:** two levels (*Projects / DwarfStar*), Move to folder… from the chat menu, the bulk bar or drag and drop, counts, search across folders. Folders are organization only: a chat's agent session, workspace and Zed link are never touched. Deleting a folder never deletes a chat (its chats become Unfiled). Tags stay independent.
* **Chat trash:** Settings shows deleted chats and empties the trash on typed confirmation; nothing is purged on a timer.
* **App:** a DwarfStar icon rendered from one SVG at build time, an About panel, and the product version in the bundle, Diagnostics and `doctor`.

### Data

* **Versioned storage:** chats (schema 2), Workstation state, approval and permission records carry a `schema` and migrate forward at service start (`bin/dwarfstar migrate`, dry run by default, originals backed up). Migrations keep every message, tool title, agent session, organisation, fork and Zed link, and remove any reasoning an earlier build left in a record. `migrate --rollback` writes what pre-1.0 builds read.
* **Backup and restore:** `bin/dwarfstar backup` writes a checksummed zip (chats, workspaces' visible files and agent sessions, approval, Workstation state, local configuration, catalog); `restore` validates, refuses damaged or unsafe archives and newer schemas, saves a safety backup of the current state, merges, migrates and checks every restored chat. It never overwrites `config/local.json`.

### Security

* **Workspace Auto can no longer promote itself to Full Auto.** Its sandbox allowed all outbound network, so a sandboxed tool could call the Workstation service's approval endpoint; the service port is now denied in the sandbox.
* **A fresh installation starts in Workspace Auto**, not Full Auto. Full Auto is entered only by explicit confirmation and persists across restarts.
* Loopback-only API checks (Host and Origin; opaque origins refused), static files confined to the web folder, and a test that every request method is guarded. All listeners bind `127.0.0.1`. Telemetry carries numbers, ids and short enums only; diagnostics write home paths as `~`.

### Setup

* **`bin/dwarfstar setup`**: one idempotent command for configuration, the agent patch, Zed entries and the Workstation app (installed safely, the previous bundle kept in the cache), ending with a status summary. The README has one *Set up* and one *Update* section.

### Reproducibility and release

* The agent approval patch is now repository data (`acp/omp-patch.json`, exact snippet edits for Oh My Pi 18.4.5) applied by `bin/dwarfstar agent-patch --apply`, replacing a machine-only edit that imported a retired path. `generate` creates every root; `config/local.example.json` no longer pins roots; a second checkout can run beside production on its own ports.
* **CI** (GitHub Actions, no weights): unit tests, catalog and generated configuration, benchmark index structure, lint, JS/TS syntax, repository hygiene (giant files, weights, secrets, home paths, chats), documentation links and known limitations.
* **Seal schema 2** binds content (git tree, product files, runtime hashes, model manifest, data schemas); verify names each differing file, and documentation differences are notes. Schema 1 seals stay readable. **`bin/dwarfstar release-check`** checks a checkout before tagging.
* Clean-room installs from a fresh clone were run before and after the hardening.

### Known limitations

See docs/known-limitations.json (18 entries), including: Splash does not report router/expert/PLE activity; request history is segmented by decode pauses; pre-1.0 builds need `migrate --rollback`; the approval presets depend on the pinned agent version; Workspace Auto allows outbound network.

### Not in this release

Model weights, source checkpoints, backups and chats are never release assets. Models are placed by hand under `~/AI/DwarfStar/Models` (docs/REPRODUCIBILITY.md).
