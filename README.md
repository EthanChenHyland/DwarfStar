# DwarfStar

A local workstation for running large mixture-of-experts language models as coding agents on one Apple-Silicon Mac. DwarfStar runs three 48-layer Qwen3.8-Flash-Next-family models (512 experts, 10 active) at 2.25 bits per expert weight on a 64 GiB M1 Max, with native multi-token prediction, a disk-backed n-gram embedding table, vision, a 128K context, and a full agent tool stack in Zed.

![Workstation](docs/assets/workstation-run.png)

## Models

| Model | Reasoning | Context | K / V cache | Default |
|---|---|---:|---|---|
| **Scope Vanilla** — the published GSQ-RCO Q2_0 build of Qwen3.8-Flash-Next | LOW | 128K | F16 / F16 | yes |
| **Swift 1.5** — Swift 1.5 fine-tune, same recipe, its own routers, MTP head and tokenizer | LOW, MEDIUM | 128K | F16 / F16 | no |
| **Cyber** — format conversion of the pinned Dealign CYBERSECURITY NVFP4 checkpoint | OFF, LOW, MEDIUM, XHIGH | 128K | F16 / F16 | no |

Seven profiles, one per model × reasoning effort. Context is a model property; there are no context variants. All three run one shared runtime policy; Cyber's only override is a 1024-token prefill chunk (instead of 2048), required to keep its 128K plan under the GPU's Metal working set. Details and measured numbers: [docs/MODELS.md](docs/MODELS.md).

## What is in the box

* **One Workstation** (`DwarfStar Workstation.app`): pick a model and a reasoning level, start/switch/stop with memory admission, per-model statistics, live telemetry, a compute visualizer, the benchmark history and diagnostics.
* **One controller** that only ever runs one model, refuses unpinned binaries or model files, and checks each model's own memory plan before loading.
* **Chats** in the Workstation: durable, searchable conversations that run through the same agent (tools, Full Auto, automatic compaction) and stay on your Mac.
* **One agent launcher** for every profile: `bin/dwarfstar-acp --profile <id>` (ACP, Full Auto, workspace/shell/browser/computer-use/vision tools, compaction, recovery).
* **One runtime build** (`ds4-unified`): the pinned Paperniuk ds4 Metal runtime plus optional telemetry hooks, token-identical to the reference builds for all three models.
* **Real telemetry**: router selections per layer, MTP drafts, PLE disk reads, context occupancy, prefill/decode and tool actions — produced only while someone is watching.

![Visualizer](docs/assets/visualizer.png)

## Quick start

```bash
cp config/local.example.json config/local.json      # point data_root at your model data
(cd agent-tools && npm ci)
bin/dwarfstar generate && bin/dwarfstar assets
scripts/validate/check.sh                            # no model is loaded
python3 app/workstation/build.py --bundle "$HOME/Applications/DwarfStar Workstation.app"
bin/dwarfstar zed --apply                            # seven Zed agent entries
```

Model weights are not in this repository. See [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md).

## Documentation

| | |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | Layers, code and data roots, ports |
| [Models](docs/MODELS.md) | The three models, their pins, memory and measured performance |
| [Workstation](docs/WORKSTATION.md) | Views, profiles, memory admission, building the app |
| [Visualizer](docs/VISUALIZER.md) | What each region shows and where it comes from |
| [Benchmarks](docs/BENCHMARKS.md) | Results, how they were measured, the production validation |
| [Cyber conversion](docs/CYBER-CONVERSION.md) | NVFP4 → DwarfStar, fidelity and limitations |
| [Research history](docs/RESEARCH-HISTORY.md) | How it was built, with dates |
| [Reproducibility](docs/REPRODUCIBILITY.md) | Pins, data root, rebuilding |
| [Development](docs/DEVELOPMENT.md) | Setup, validation, changing the catalog |
| [Demo](docs/DEMO.md) | A ten-minute walkthrough |

## Status

Production: Scope Vanilla LOW is the default. Swift and Cyber are selectable alternatives. Known limitations are listed in [docs/known-limitations.json](docs/known-limitations.json); the most important are that the runtime has no quantized KV cache (Q8 KV was requested and is not available; K/V stay F16) and that Cyber's 2-bit expert encoding occasionally produces a broken first turn.

## Licence

MIT (`LICENSE`). Upstream components and model licences: `THIRD_PARTY_NOTICES.md`. No model weights are included.
