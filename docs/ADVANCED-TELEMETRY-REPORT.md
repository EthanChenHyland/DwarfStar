# Advanced visualization restoration — experiment report (2026-11-13)

Goal: restore as much of DwarfStar's rich technical visualization as truthfully possible while keeping Splash as the single production inference architecture, and **never** slowing inference. Outcome: **per-cycle DFlash speculative-decode telemetry restored at no measurable cost and promoted (runtime ds2); router/expert telemetry investigated and declined as requiring hot-path GPU work.** The result meets the experiment's hard bars; ds1 remains a trivial rollback.

## Baseline

1. main HEAD before the experiment: `57627e0`.
2. Checkpoint tag: `checkpoint/pre-advanced-telemetry-20261113` (pushed).
3. ds1 hashes: engine `75bc297f…`, shaders `694abdf2…` / `8e177c75…`, adapter `9fe87539…`.
4. Baseline TTFT (Scope, short): 0.51 s.
5. Baseline prefill (8K): 331–332 tok/s.
6. Baseline decode: per-verify-cycle cost 72.7 ms; aggregate 40–42 tok/s.
7. Baseline peak memory: 41.8 GiB wired.
   Run-to-run noise floor (ds1 vs ds1, fresh restart): per-cycle cost ±0.7 %, aggregate decode ±3 %.

## Instrumentation

8. Architecture: a bounded in-engine ring fed on the engine thread from values `batchCompleted` already computed, read by the status-response thread; a `TelemetryRequest` frame (0x0007) and a `GET /telemetry?since&level` endpoint (localhost), answered with the status-event machinery; DwarfStar proxies it at `/api/telemetry`.
9. Buffer size: 4096 records (~200 KB, several minutes of history); overwrites oldest; sequence ids; dropped counter.
10. Polling: the Advanced view polls ~4 Hz; the overhead harness polled 8 Hz. UI render is decoupled from the generation rate.
11. Routing hook: none shipped (see 27).
12. Exact vs sampled routing: neither — routing is **not reported**.
13. PLE hook: none; PLE throughput is derived from `/status` deltas.
14. DFlash hook: `batchCompleted` records one `decode_cycle` per decode batch (width, output, drafted, accepted, wall ms, cycle ms) when a recent poll keeps capture alive; one relaxed atomic load when nobody polls.
15. KV/cache integration: from `/status` (no new hook).
16. Memory integration: from `/status` (no new hook).

## Performance (Scope, 5 runs each; per-verify-cycle cost is the content-independent metric)

17. ds2 OFF overhead: **−0.17 %** per cycle (prefill −0.25 %, TTFT unchanged, memory +0.1 GiB).
18. ds2 STANDARD: the Standard view uses `/status` only; no advanced collection runs.
19. ds2 ADVANCED (polled 8 Hz throughout): **−0.11 %** per cycle; consumer 1.0 ms median polls, 0 of 1550 events dropped, 0 errors.
20–23. TTFT, prefill, decode, memory changes: all within the noise floor; none attributable to telemetry.
24. Thermal: nominal throughout, both builds.

Both OFF and ADVANCED are far under the 2 %/3 % bar, and OFF is statistically indistinguishable from ds1 — the stricter "no regression when disabled" bar.

## Correctness

25. ds1 vs ds2 OFF output: equivalent. Long greedy generations are inherently non-deterministic across runs on this runtime (float-reduction order — ds1 is not self-identical either); short deterministic answers are byte-identical ds1 vs ds2 (5/5 on the prefill probe).
26. ds1 vs ds2 ADVANCED output: equivalent, by the same evidence; telemetry reads values only after a command retires.
27. Router semantic safety: not applicable — no routing instrumentation. Investigated: Splash's per-step `selectedExperts` scratch is GPU-private and reused across all 48 layers, so capturing per-token routing needs per-layer host-copy commands on the decode hot path, which the zero-overhead rule forbids. Declined (a sampled variant needs the same machinery). The panel stays "not reported".
28. Cache semantic safety: no cache hook; unaffected.
29. DFlash semantic safety: the ring reads `draftedTokens`/`acceptedDraftTokens` already computed; it changes no draft decision. Cross-validated: on all three models the ring's summed drafted/accepted match `/status` exactly.

## Visualization

30. Expert/router view: unchanged — "not reported" (declined, see 27).
31. PLE view: unchanged (from `/status`).
32. DFlash view: **restored** — the Advanced "DFlash2 DRAFT" panel shows rolling acceptance, proposals/accepted/rejected, the current cycle, and the output stream animates accepted/rejected draft tokens.
33. KV/context view: unchanged (from `/status`).
34. Memory view: unchanged (from `/status`).
35. Request/tool timeline: unchanged.
36. Standard vs Advanced: Standard stays calm (aggregate acceptance from `/status`); the per-cycle detail is Advanced-only.
37. UI performance: the poller keeps a bounded accumulator and capped synthetic ticks; 1.0 ms polls; no unbounded DOM/canvas growth.
38. Multi-size layout: no overflow and nothing outside panels at 1440×900, 1600×1000, 1100×700 (automated bounds check).

## Models

39. Scope: baseline and full overhead/equivalence measured; promoted.
40. Swift (LOW/MEDIUM): `/telemetry` validated; ring sums match `/status` (253/133).
41. Cyber (LOW/XHIGH): `/telemetry` validated; ring sums match `/status` (250/135).

## Promotion

42. Accepted (DFlash); router/expert rejected.
43. Reasons: DFlash passes every gate (overhead, equivalence, reproducibility, generality, safety) and restores a real signal; router/expert cannot be captured without hot-path GPU work that violates the zero-overhead rule.
44. Runtime selected: **`splash-m1-1.6.1-ds2-telemetry`** (engine `7c8d51c6…`; shaders and adapter identical to ds1). Built reproducibly by `build_splash.sh … --telemetry`; a clean rebuild reproduces the engine hash.
45. Rollback verified: flipping `runtime-policy` to `splash-m1-1.6.1-ds1` + `generate` resolves ds1 and `doctor` confirms its pins; flipping back restores ds2. ds1 is byte-unchanged.
46. Tests: 87 unit tests pass; `check.sh` passes; UI profile/bounds and the Cyber MEDIUM→Stop→LOW lifecycle pass on ds2. No 120K/depth tests were run: the change is observational and output is equivalent.
47. Seal: `bin/dwarfstar seal --verify` ok, 0 problems; `doctor` 26/26.
48. Branch/HEAD: `experiment/splash-advanced-telemetry`.
49. Recommended next action: merge to `main`. No further runtime work required. If a future phase wants router/expert, it must accept sampled, hot-path capture and re-measure against this bar; the capability metadata and the Visualizer already gate on it cleanly.
