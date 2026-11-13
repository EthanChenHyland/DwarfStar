# Architecture

DwarfStar is one product with three models. Everything that is not a model fact is shared.

```
                     ┌──────────────────────────────────────────────┐
  DwarfStar          │  dwarfstar serve  (127.0.0.1:8038)           │
  Workstation.app ──▶│  · profile selection   · lifecycle (switch)  │◀── browser / tests
  (WebKit shell)     │  · telemetry hub (SSE) · statistics, history │
                     └───────┬──────────────────────────┬───────────┘
                             │ one controller per model │ UDP 8039 events
                             ▼                          │ UDP 8037 detail heartbeats
          dwarfstar runtime --model {scope|swift|cyber} │
                             │ start/stop/health/status │
                             ▼                          │
   ds4-server (ds4-unified build) ──── port 8030 / 8031 / 8032 ──┘
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
| Runtime | `native/` → `<data>/runtime/ds4-unified/` | The pinned Paperniuk ds4 build plus the Swift tokenizer adapter and optional telemetry hooks (inactive unless enabled by environment). Token-identical to the reference builds for all three models. |
| Telemetry | `src/dwarfstar/telemetry/`, `native/telemetry/` | Fire-and-forget UDP events from runtime, controller and agent; the hub aggregates and streams them. Detail (router rows) is only produced while a subscriber asks for it. |
| UI | `app/workstation/web/` | Run, Models, Live, Visualizer, Benchmarks, Research, Diagnostics. Plain JS, served only on loopback. |
| Editor | `src/dwarfstar/zed.py` | Seven generated Zed entries, all `bin/dwarfstar-acp --profile <id>`. |
| Seal | `src/dwarfstar/seal.py` | Production baseline (every tracked file hashed) and the compact seal checked by `dwarfstar seal --verify`. |

## One runtime policy

`config/runtime-policy.json` holds every setting the three models share; a model file may override a shared value only with a written reason, and the catalog rejects anything else.

| Common to every model | Value |
|---|---|
| Runtime build | `ds4-unified` (pinned ds4 + Swift tokenizer adapter + optional telemetry) |
| Context / output ceiling | 131072 / 81920 tokens |
| K / V cache | F16 / F16 (the Qwen4Exp graph has no quantized KV; Q8 KV is future runtime work) |
| Prefill chunk | 2048 tokens |
| Sampling | temperature 1.0, top-p 0.95, top-k 20 |
| MTP, vision, PLE | MTP on (adaptive); projector resident; n-gram table read from disk |
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

The repository holds code, configuration, tests and summaries. Everything large or machine-specific lives under the data root named in `config/local.json` (`dwarfstar-data/` on the reference machine): model files (symlinks to the original installation directories, nothing was moved), runtime builds, generated instance config, state, logs, telemetry sessions and raw evidence. See `docs/REPRODUCIBILITY.md`.

## Ports

| Port | Use |
|---|---|
| 8030 / 8031 / 8032 | Scope / Swift / Cyber OpenAI-compatible endpoints (one running at a time) |
| 8038 | Workstation service (HTTP + SSE), loopback only, Origin-checked |
| 8039 | Telemetry events (UDP, loopback) |
| 8037 | Detail-level heartbeats to the runtime (UDP, loopback) |
