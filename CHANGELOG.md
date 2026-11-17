# Changelog

## Unreleased — ds3 GPU telemetry

### Runtime

* **ds3 is the production runtime** (`splash-m1-1.6.1-ds3-gpu-telemetry`, engine `ae07f09a…`, shaders `1d9ae6f5…`): ds2 plus GPU-side router telemetry. The MoE grouping kernel adds the routes it already counts per layer and expert into a 0.5 MB host-visible page that the engine reads on a `/telemetry` poll with no GPU command and no wait; optional sampled selections (row 0 of one decode step in N); PLE row-store and expert-store aggregates. Overhead within noise of ds2 (OFF +0.36 % decode in an interleaved A/B). ds2 is the rollback (`config/runtime-policy.json`). `build_splash.sh --ds3` reproduces it byte for byte.
* `/telemetry` schema 2: every consumer gets every event's detail (kept by sequence, 4 MiB); each telemetry level lapses on its own 3 s after its last poll.

### Workstation

* **Advanced Live** draws router aggregates: per-layer recent routing, Layer focus (busiest experts, concentration), a layers × experts heatmap (session or request, decode or prefill), an AGGREGATE / SAMPLED 1 IN N badge and a *Sampled selections* switch; the PLE card shows read rate, cache hits and latency. Request export adds `router` with `router_mode`.
* The DFlash2 card's draft-token animation is replaced by a bar per recent verify cycle (accepted / rejected); the output stream shows the decode rate of the current or last run; the memory panel shows the latest request's context. MTP-era labels and the flying PLE dots are gone.
* Fixed: returning to the Advanced view after another view no longer leaves its telemetry poll stopped; a slow poll can no longer be overtaken and replay events out of order; Standard Live's footer no longer says router and PLE activity are unreported on a runtime that reports them.

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
