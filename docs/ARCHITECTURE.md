# Architecture

DwarfStar is one product with three models. Everything that is not a model fact is shared.

```
                     ┌──────────────────────────────────────────────┐
  DwarfStar          │  dwarfstar serve  (127.0.0.1:8038)           │
  Workstation.app ──▶│  · profile selection   · lifecycle (switch)  │◀── browser / tests
  (WebKit shell)     │  · telemetry hub (SSE) · statistics, history │
                     └───────┬──────────────────────────┬───────────┘
                             │ one controller per model │ UDP 8039 events
                             ▼                          │ (runtime, controller, agent)
          dwarfstar runtime --model {scope|swift|cyber} │
                             │ start/stop/health/status │
                             ▼                          │
   Splash-m1 (1.6.1-m1-ds1) ───────── port 8030 / 8031 / 8032 ──┘
                             ▲
   Zed ── bin/dwarfstar-acp --profile <id> ── Oh My Pi ACP agent + DwarfStar tools
```

## Layers

| Layer | Where | What it does |
|---|---|---|
| Catalog | `config/models/*.json`, `config/profiles.json`, `config/runtimes.json` | The only source of model facts, profiles (model × reasoning effort) and pinned runtime builds. Context, cache dtype and memory plan are model properties. |
| Generated instance config | `<data>/instances/<model>/` | `dwarfstar generate` writes `runtime.json`, ACP provider files and the agent registry from the catalog. `scripts/validate/check.sh` fails if they drift. |
| Controller | `src/dwarfstar/runtime_control.py` | Starts, stops and health-checks one model's server. Refuses unpinned binaries or assets, enforces per-model memory admission and never loads a second model. |
| Lifecycle | `src/dwarfstar/service.py` (`Lifecycle`) | Switches profiles: stops the other model, waits for its port, checks memory, starts, health-checks. Same-model effort changes do not restart. |
| Agent | `src/dwarfstar/agent.py`, `acp/`, `agent-tools/` | One ACP launcher for every profile. Full Auto, workspace/shell/browser/KimiCU/vision tools, compaction and recovery are identical across profiles; only the provider file (model + effort) differs. |
| Runtime | `backends/splash-m1/` → `<assets>/Runtimes/splash-m1-1.6.1-ds1/` | Splash-m1 1.6.1-m1 built from source with DwarfStar's two patches (`scripts/backends/build_splash.sh`), pinned by engine, shader and metadata-adapter hashes. It is the one runtime for every model. The retired native ds4 build is not installed; its source and behaviour are recoverable from git (`docs/BACKENDS.md`). |
| Telemetry | `src/dwarfstar/telemetry/` | Fire-and-forget UDP events (port 8039) from runtime, controller and agent; the hub aggregates and streams them over SSE. Per-request Splash metrics are read from the server's `/status` on demand. Reasoning text is never recorded. |
| UI | `app/workstation/web/` | Chats, Run, Models, Live (with an Advanced view, the former Visualizer), Diagnostics and Settings. Benchmark data is engineering infrastructure (`research/`, `scripts/`), never a Workstation page. Plain JS, served only on loopback. |
| Editor | `src/dwarfstar/zed.py` | Seven generated Zed entries, all `bin/dwarfstar-acp --profile <id>`. |
| Seal | `src/dwarfstar/seal.py` | Production baseline (every tracked file hashed) and the compact seal checked by `dwarfstar seal --verify`. |

## One runtime policy

`config/runtime-policy.json` holds every setting the three models share; a model file may override a shared value only with a written reason, and the catalog rejects anything else.

| Common to every model | Value |
|---|---|
| Runtime build | `splash-m1-1.6.1-ds1` for every model |
| Context / output ceiling | 131072 / 81920 tokens |
| K / V cache | INT8 / INT8 (Splash-m1) |
| Prefill chunk | 2048 tokens |
| Sampling | temperature 1.0, top-p 0.95, top-k 20 |
| Speculation, vision, PLE | DFlash2 draft model, every token verified by the target; projector resident; n-gram table read from disk |
| Weights / GPU | memory-mapped, Metal residency, full GPU offload |
| Disk KV checkpoints | 12 GiB budget, every 10,000 tokens |
| Lifecycle | one model at a time; start 600 s, stop 60 s, port close 90 s; 3 health attempts |
| Memory | planned = model + KV + buffers + projector; required = planned + 1.5 GiB reserve; plan must fit the Metal working set |
| Agent | same ACP launcher, Scope-first prompt, Full Auto, tools, compaction, recovery, telemetry, chats |

| Model-specific | Scope | Swift | Cyber |
|---|---|---|---|
| Weights, quantization, MTP head, projector | own | own (shared projector) | own |
| Reasoning levels | LOW | LOW, MEDIUM | OFF, LOW, MEDIUM, XHIGH |
| Port | 8030 | 8031 | 8032 |
| **Override** | none | none | prefill chunk 1024: with 2048 its 128K plan (51.92 GiB) exceeds the 51.84 GiB Metal working set and prefill failed at depth |

## Code and data roots

The repository holds only code, configuration, tests and summaries. Everything else lives under five fixed roots, each overridable in `config/local.json` but defaulting to a standard macOS location. The 2026-11 consolidation moved the product off the old `local-ai-setup` tree onto these roots; nothing points back to it.

| Root | Default | Holds |
|---|---|---|
| CODE | `~/Developer/DwarfStar` | This repository |
| ASSETS | `~/AI/DwarfStar` | Models, the runtime build, agent tools, manifests, sources, archive |
| DATA | `~/Library/Application Support/DwarfStar` | Chats, settings, generated instance config, runtime state, telemetry sessions |
| CACHE | `~/Library/Caches/DwarfStar` | Prefix/KV spill, scratch, agent browser cache — all recreatable |
| LOGS | `~/Library/Logs/DwarfStar` | Lifecycle and per-model server logs |

The installed app is `~/Applications/DwarfStar Workstation.app`. See `docs/REPRODUCIBILITY.md` and `docs/STORAGE.md`.

## Ports

| Port | Use |
|---|---|
| 8030 / 8031 / 8032 | Scope / Swift / Cyber OpenAI-compatible endpoints (one running at a time) |
| 8038 | Workstation service (HTTP + SSE), loopback only, Origin-checked |
| 8039 | Telemetry events (UDP, loopback) |
