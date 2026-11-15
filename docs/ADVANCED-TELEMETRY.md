# Advanced telemetry (the ds2 runtime)

DwarfStar's production runtime is **`splash-m1-1.6.1-ds2-telemetry`** (ds2): the ds1 build plus a bounded in-engine telemetry channel that restores a per-verify-cycle view of speculative decoding, without changing inference. This page documents what it reports, how it is built, and the overhead that was measured before it was promoted. The earlier ds1 build remains pinned as the rollback.

## What it adds

Splash's `/status` already reports requests, time to first token, inter-token latency, prefill and decode rates, **aggregate** draft acceptance, prefix-cache hits, memory and thermal state. DwarfStar derives the Standard Live view and most of the Advanced view from `/status` deltas. The one thing `/status` cannot give is **per-verify-cycle** speculative-decode activity — proposals, acceptances and rejections for each decode step. ds2 adds exactly that:

* a bounded ring in the engine that records one record per decode batch, from values the engine already computed;
* a `GET /telemetry?since=<seq>&level=<0..2>` endpoint (localhost, like `/status`) that drains the ring since a sequence, with capability metadata and a dropped counter;
* DwarfStar proxies it at `/api/telemetry`; the Advanced Live view polls it and draws the **DFlash2 DRAFT** panel (rolling acceptance, proposals/accepted/rejected, the current cycle) and animates accepted/rejected draft tokens in the output stream.

## Capabilities

The runtime declares what it can report; the Visualizer renders only these and labels the rest, never estimating.

| Signal | ds2 | Why |
|---|---|---|
| DFlash speculative decode | **per cycle** | proposals/accepted/rejected per verify step, from the engine's own counters |
| Requests, TTFT, latency, prefill/decode, prefix cache, memory, thermal, KV, PLE throughput | via `/status` | already reported; the UI takes deltas |
| Router selections, expert utilization | **not reported** | Splash's per-step MoE scratch is reused across layers and GPU-private; capturing it needs host copies on the decode hot path, which the zero-overhead bar rules out (see below) |

## Why not router/expert

This was the primary target of the restoration experiment, and it was investigated at the source level and declined. In Splash, a decode step's `selectedExperts` buffer is scratch that every one of the 48 layers overwrites in turn, and it is GPU-private. By the time the engine's CPU-side completion handler runs, the buffer holds only the last layer's selection. Capturing all layers would require adding per-layer GPU→host copy commands to every decode step — work on the critical path, on a small GPU — which is what the retired native runtime did and what this experiment's hard rule forbids: telemetry must never slow inference. A sampled variant would need the same hot-path machinery, invoked occasionally. The honest outcome is that router/expert telemetry is not available on Splash within the zero-overhead constraint; the panel says so.

## Measured overhead

M1 Max, Scope, five runs each, Splash `/status` metric deltas around each request. The content-independent metric is **per-verify-cycle decode cost** (decode ms ÷ verify cycles); the run-to-run noise floor (ds1 vs ds1, fresh restart) is about ±0.7 %.

| Build | Per-cycle decode cost vs ds1 | Prefill | TTFT | Peak memory |
|---|---:|---:|---:|---:|
| ds2, telemetry **off** (nobody polling) | −0.17 % | −0.25 % | unchanged | +0.1 GiB |
| ds2, telemetry **advanced** (polled 8 Hz throughout) | −0.11 % | — | unchanged | +0.2 GiB |

Both are inside the noise floor and far under the 2 %/3 % bar. When nobody polls, the engine does one relaxed atomic load per decode batch and nothing else; the recording window decays after 3 s with no poll. The `/telemetry` endpoint served 863 polls during the advanced run at a 1.0 ms median, dropping 0 of 1550 events. The result generalizes across models: on Swift and Cyber the ring's summed drafted/accepted match `/status` exactly.

Output is unaffected: telemetry only reads values after a command has retired and touches no buffer, routing, cache or sampling decision. (Long greedy generations are inherently non-deterministic across runs on this runtime from float-reduction order — ds1 is not self-identical either — but short deterministic answers are byte-identical ds1 vs ds2.)

## Ring and safety

* Fixed capacity (4096 records, ≈200 KB, several minutes of history); overwrites oldest when full; never grows, never writes disk, never blocks the engine.
* Monotonic sequence ids; a poll passes `since` to continue without a gap; a `dropped` counter reports records overwritten before a slow reader saw them.
* Thread-safe: records are written on the engine thread under a brief lock that copies a handful of integers; drained on the status-response thread.
* Non-critical: if the endpoint fails, the buffer is empty, or the consumer disappears, inference continues normally. The DwarfStar proxy returns an empty batch on any error.
* Localhost only, like `/status`. The ring holds numeric runtime metadata only — never prompt text, reasoning, tokens or tool arguments.

## Build and identity

ds2 is built reproducibly from the pinned 1.6.1-m1 source with the ds1 patches plus the telemetry patches:

```
scripts/backends/build_splash.sh <splash-src> <release-dist> <out-dir> --telemetry
```

| Item | Value |
|---|---|
| Runtime id | `splash-m1-1.6.1-ds2-telemetry` |
| Engine SHA-256 | `7c8d51c6…` |
| Shaders, metadata adapter | **identical to ds1** (telemetry is engine C++ and server Python only) |
| Patches | ds1 `0001`, `0002` + `telemetry/0003a` (ring header), `0003b` (engine: frame, ring hook, endpoint provider), `0004` (server: frame, route) |

A clean rebuild reproduces the engine hash exactly. The ring header and endpoint are kept generic (runtime events, not DwarfStar UI assumptions), so the change could plausibly go upstream.

## Rollback

ds1 is unchanged and still pinned in `config/runtimes.json`. To revert: set `config/runtime-policy.json` `model_defaults.runtime` back to `splash-m1-1.6.1-ds1`, `bin/dwarfstar generate`, restart the service, and verify a tiny Scope generation. The Advanced view then shows the aggregate draft acceptance and labels per-cycle drafts "not reported", exactly as before. Checkpoint tag: `checkpoint/pre-advanced-telemetry-20261113`.
