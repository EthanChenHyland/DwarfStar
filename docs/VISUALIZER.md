# Live — Advanced view (the Visualizer)

The Advanced view of **Live** (`app/workstation/web/visualizer.js`) draws a data map of the running model: where its data lives, the processor grid, the draft, context, memory and the agent's actions. It draws only what the runtime reports; where Splash does not report a signal, the region says so rather than animate an estimate. Toggle it from the **Standard** / **Advanced** switch at the top of Live.

## What it shows

| Region | Source | Notes |
|---|---|---|
| Storage | catalog + `/telemetry` `ple_aggregate` (ds3) | The model, draft and projector are resident; only the PLE (n-gram) table is read from disk during inference. On ds3 the card shows the row store's read rate, cache-hit share and SSD reads per poll (latency and cache occupancy on hover); on ds2 it says "not reported". |
| Unified memory | controller memory plan + system sampler | Resident model, INT8 K/V cache, buffers, projector, the Metal limit and live wired memory; context of the latest request. |
| Processor: Overview | `/telemetry` `router_aggregate` (ds3) | Each layer's routes over the last ~1.5 s, scaled to the layer's busiest expert; with **Sampled selections** on, the latest sampled step's experts in white. A badge says AGGREGATE or SAMPLED 1 IN N. Native replays draw their recorded per-token rows; ds2 says "not reported". |
| Processor: Layer focus | same | One layer's routes per expert (log shade), the ten busiest experts with shares, the top-10 share, effective experts and experts used; the latest sample outlined. |
| Routing heatmap | same | Routes executed per layer and expert for the session or a viewed request, in the phase the model is in (prefill while it processes a prompt, decode while it generates; the badge names it); busiest experts across layers and the median effective experts. Counts include speculative rows a later check rejects. |
| Speculative draft (DFlash2) | `/telemetry` `decode_cycle` (ds2+) | Proposals, acceptances and rejections **per verify cycle**: rolling acceptance, the current cycle, and a bar per recent cycle (green accepted, red rejected). |
| Output stream | `decode_cycle` + request history | Committed tokens per cycle (accepted drafts green, rejections marked red) and the decode rate of the run being generated or viewed, else the last run. |
| Context | agent receipt and `/status` | Context occupancy of the latest request against the model's 128K. |
| Prefix cache | `/status` cache | Tokens reused and the hit rate. |
| Action timeline | agent tool events | Workspace, shell, browser, computer use (KimiCU) and vision actions with duration and result. Names and categories only. |
| Recent requests | `decode_cycle` + router aggregates | The last 20 decode runs (Splash reports cycles, not request ids) with cycles, acceptance, decode time and rate; View replays one, Export writes its cycles and, on ds3, its routing (`router_mode: aggregate|sampled`, approximate at the run's edges). |

**Backend capabilities.** The catalog tells the page what the backend reports. The production ds3 runtime reports requests, rates, latency, draft acceptance (aggregate and per verify cycle), prefix-cache use, its memory plan, router aggregates (sampled selections on request) and row-store aggregates. Router weights and per-token PLE reads are not reported and are not drawn; on ds2 the router and PLE regions are labelled "not reported" instead of estimated.

Never shown: prompt text, reasoning, tool arguments, file contents or credentials. This is a compute and action view, not a transcript.

## Request history and replay

On the advanced-telemetry runtime the view keeps the **last 20 requests** in the page's memory, under **Recent requests**, named by profile and time (for example "Swift 1.5 MEDIUM · 14:32"; never by content). Each row shows tokens generated, verify cycles, drafts accepted, decode time and rate.

* **View** draws that request on the canvas: the scrubber walks its cycles, the draft panel shows acceptance up to the cycle under the playhead and the output stream the tokens committed so far. Live recording continues meanwhile; **Back to live** returns. **Pause** and the scrubber work on the live stream as before.
* **Export JSON** saves one request's cycles to a file chosen in a Save panel (a format-1 document: totals, capabilities, and per cycle `t_ms, output, drafted, accepted, cycle_ms, width`). On ds3 it adds `router`: `router_mode` (`aggregate` or `sampled`), the decode routes as `[layer, expert, routes]` cells and up to 128 sampled steps; router intervals (~180 ms) are assigned to the run they overlap by engine time, so a request's routing is exact in its middle and approximate at its first and last interval. Nothing is exported otherwise, and never any text.
* **What a request is.** The runtime reports decode cycles, not request ids, so an entry is one uninterrupted decode run: a new entry starts after 400 ms with no cycle. Between agent turns there is always tool time and a prefill, which produce no cycles, so turns separate; two clients decoding at once would share one entry (the `width` field shows batching).
* **Bounds.** Each request keeps its first 4,096 cycles in one fixed 96 KiB buffer (under 2 MiB for all 20); totals keep counting past the cap and the row says how many were kept. Cycles the engine ring overwrote before they were read are counted as missed. History holds numbers only, lives in the window, and is gone on reload; nothing is written to disk. Measured with 30 synthetic requests including one of 10,000 cycles: 20 rows kept, the long one capped at 4,096 with exact totals, page heap 4.6 MiB.

## Detail levels

The hub records at two levels, chosen by whether the Advanced view is open:

* **1 — Standard / Live**: request receipts, system samples and tool events.
* **2 — Advanced**: the same, kept with any router rows a replayed native session carries.

## Overhead

Inference: the view's polling and the ds3 router counters are within run-to-run noise of ds2 (docs/DS3-GPU-TELEMETRY.md §5). Polling runs only while the Advanced view is open (one request at a time, 5 Hz); with nobody polling, the engine's router counters switch off. The page: in the installed app the Advanced view costs about 17–18 % of one core while visible (16.7 % on ds2: the canvas is redrawn every frame) and nothing while hidden or on another view; Standard about 4 %. Its memory levels off once history and histograms are full (117 → 147 MB over two minutes of decoding, then flat).

## Recorded sessions

Each session's events are written to `<data>/telemetry/sessions/`, and the view can replay one from the session picker. Live Splash sessions carry requests, rates and actions; native sessions recorded earlier also carry router and draft detail.
