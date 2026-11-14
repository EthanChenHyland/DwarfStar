# Live — Advanced view (the Visualizer)

The Advanced view of **Live** (`app/workstation/web/visualizer.js`) draws a data map of the running model: where its data lives, the processor grid, the draft, context, memory and the agent's actions. It draws only what the runtime reports; where Splash does not report a signal, the region says so rather than animate an estimate. Toggle it from the **Standard** / **Advanced** switch at the top of Live.

## What it shows

| Region | Source | Notes |
|---|---|---|
| Storage | catalog + PLE read events | The model, draft and projector are resident; only the PLE (n-gram) table is read from disk during inference. Splash does not report the read rate, so that line says "not reported". |
| Unified memory | controller memory plan + system sampler | Resident model, INT8 K/V cache, buffers, projector, the Metal limit and live wired memory. |
| Processor / layers | runtime `tick` events (native replays only) | 48 layers; per-token router selections (10 of 512 experts per layer). **Splash does not report router selections**, so the grid stays empty with a label for live sessions; sessions recorded with the earlier native runtime replay the selections they captured. |
| Routing heatmap | hub router heat | Cumulative expert selection counts per layer — native replays only, for the same reason. |
| Speculative draft (DFlash2) | `/telemetry` ring (ds2) | A DFlash2 draft model proposes tokens; the target verifies them. The production ds2 runtime reports proposals, acceptances and rejections **per verify cycle** from a bounded in-engine ring, which this panel draws (rolling acceptance, the current cycle, animated accepted/rejected draft tokens). The Standard view keeps the running total. See docs/ADVANCED-TELEMETRY.md. |
| Context | request and `/status` | Context occupancy against the model's 128K. |
| Prefix cache | `/status` cache | Tokens reused and the hit rate. |
| Action timeline | agent tool events | Workspace, shell, browser, computer use (KimiCU) and vision actions with duration and result. Names and categories only. |
| Requests | request events / replay | Splash does not send per-request events, so for a live session the latest request (from the agent's receipt) and Splash's running totals are in the Standard view; replays of native sessions show one row per request. |

**Backend capabilities.** The catalog tells the page what the backend reports. The production ds2 runtime reports requests, rates, latency, draft acceptance (aggregate and **per verify cycle**), prefix-cache use and its memory plan — not per-token router, expert or PLE activity — so those regions are labelled "not reported by the Splash-m1 backend"; nothing is drawn from estimates. The advanced-telemetry channel is documented in docs/ADVANCED-TELEMETRY.md.

Never shown: prompt text, reasoning, tool arguments, file contents or credentials. This is a compute and action view, not a transcript.

## Detail levels

The hub records at two levels, chosen by whether the Advanced view is open:

* **1 — Standard / Live**: request receipts, system samples and tool events.
* **2 — Advanced**: the same, kept with any router rows a replayed native session carries.

## Overhead

Measured in the production validation (`docs/BENCHMARKS.md`, step `overhead`): the same temperature-0 workload with the view closed and open, two runs each. Output was identical across modes. See the validation record for the decode difference.

## Recorded sessions

Each session's events are written to `<data>/telemetry/sessions/`, and the view can replay one from the session picker. Live Splash sessions carry requests, rates and actions; native sessions recorded earlier also carry router and draft detail.
