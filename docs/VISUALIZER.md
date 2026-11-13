# Visualizer

One implementation (`app/workstation/web/visualizer.js`) for every model. It draws only what telemetry reports; when a signal is absent the panel says so instead of animating.

## What it shows

| Region | Source | Notes |
|---|---|---|
| Storage | catalog + PLE read events | Main file, MTP block and projector are resident; only the PLE (n-gram) table is read from disk during inference. Reads light up as they happen. |
| Unified memory | controller memory plan + system sampler | Resident model, F16 K/V cache, buffers, projector; live wired memory and swap. |
| Processor / layers | runtime `tick` events | 48 layers; per-token router selections (10 of 512 experts per layer) with weights. Router rows are produced only while a full-detail subscriber is attached. |
| Routing heatmap | hub router heat | Cumulative expert selection counts per layer for the session. |
| MTP | `tick` `dp`/`da` counters | Drafts proposed, accepted and rejected per verify cycle. |
| Context | request and tick events | Context occupancy against the model's 128K. |
| Prefill / decode | request phases from the server log stream | Prompt start, prefill progress, decode rate, finish. |
| Action timeline | agent tool events | Workspace, shell, browser, computer use (KimiCU) and vision actions with duration and result. Names and categories only. |

Never shown: prompt text, reasoning, tool arguments, file contents or credentials. This is a compute and action view, not a transcript.

## Detail levels

The hub tells the runtime, by heartbeat on UDP 8037, how much detail anyone is watching:

* **0 — nobody watching**: the runtime sends no tick events.
* **1 — Live view**: counters (tokens, MTP, PLE, context).
* **2 — Visualizer**: counters plus router rows.

Heartbeats expire after 3 s, so a closed window returns the runtime to level 0 by itself.

## Overhead

Measured in the production validation (`docs/BENCHMARKS.md`, step `overhead`): the same temperature-0 workload with the visualizer closed and open, two runs each. Output was identical across modes. See the validation record for the decode difference.

## Recorded sessions

Each model session's events are written to `<data>/telemetry/sessions/` (router rows sampled), and the visualizer can replay one from the session picker.
