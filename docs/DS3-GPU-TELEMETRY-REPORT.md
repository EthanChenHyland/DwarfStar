# ds3 GPU telemetry — experiment report (2026-11-17)

Goal: recover the original rich telemetry (router activity, expert selection and utilisation, layer × expert heatmaps,
PLE / expert-storage activity) on Splash, without the native backend and without slowing inference. Design and full
measurements: [DS3-GPU-TELEMETRY.md](DS3-GPU-TELEMETRY.md). Outcome: **router aggregates, sampled selections, heatmaps and PLE aggregates recovered within noise of ds2, verified in the
installed app and production service; ds3 is the production candidate on this branch, ds2 the rollback.**

## Baseline

1. `main` / `v1.0.0` at `fdd417b`. Checkpoint tag `checkpoint/pre-ds3-gpu-telemetry-20261117` on it.
2. Work on branch `experiment/splash-ds3-gpu-telemetry` in a separate worktree: the production service runs its code
   from the main checkout, which stayed on `main`, unmodified, throughout.
3. Production runtime `splash-m1-1.6.1-ds2-telemetry`: engine `7c8d51c6…`, shaders `694abdf2…` / `8e177c75…` — verified
   unchanged at the end; no ds2 binary, hash, patch or manifest was modified.
4. ds2 on Scope: TTFT 0.51 s (short) / 26.5 s (8.8K prefill); prefill 150.8 / 332.5 tok/s; per-verify-cycle decode
   72–74 ms, 40–43 tok/s; DFlash acceptance 46–58 % by test; peak 40.72 GiB; thermal nominal.
5. Noise: the same ds2 binary differs by ~1.5 % per cycle between server sessions; within a session, configurations
   repeat to ±0.1 % (draft-length-adjusted); single runs spread ±1 %.
6. Per-cycle cost depends on the draft rows a cycle verifies, so all overheads are draft-length-adjusted
   (`scripts/backends/ds3_overhead.py`) and compared with ds2 in the same session.

## Old telemetry audit

7. Contract reconstructed from `native/telemetry/ds4_telemetry.h` and `docs/VISUALIZER.md` at
   `rollback/native-default-20261113` (read only): table in DS3-GPU-TELEMETRY.md §1.
8. Router selections: native read back row 0–3 selections with weights each cycle at level 2. Splash keeps them in
   GPU-private scratch that every layer overwrites; the grouping kernel already counts them per expert.
9. Heatmaps: native heat was row 0 of observed ticks only; ds3's aggregate counts every executed route, per phase.
10. PLE: native counted disk reads per cycle; Splash's row store already keeps requests, cache hits, SSD reads, bytes and
    latency — ds3 reports their deltas. Expert storage: all three models keep routed experts resident.
11. MTP panels map to DFlash2 (ds2, unchanged). Not recovered: router weights, speculative rows' selections,
    per-token PLE reads, the native per-request log stream.

## GPU design

12. Hook: the MoE grouping kernel (one threadgroup, thread *e* owns expert *e*, `counts[e]` already final). No routing
    recomputed, no logits or index tensors copied, routing outputs unchanged.
13. One Shared-storage page, 525,120 bytes, allocated once: header, `[2 phases][64][512]` uint32 histogram, 64-slot sample
    ring (step, layers written, period, ids). Bounds 64 layers × 512 experts × top-16, read from the model's shape.
14. Modes are one word: off / aggregate / sampled (1 in N, default 8) / exact (research). A poll sets it; each level
    lapses 3 s after its last poll; with no poll the kernels read one word and stop.
15. Reader: `memcpy` of the page on a poll (histograms only below level 3), differences against the previous copy,
    rate-limited emission (decode ≥ 180 ms, prefill ≥ 900 ms, PLE ≥ 450 ms) into ds2's bounded ring.
16. Schema 2: `router_aggregate`, `router_sample`, `ple_aggregate` beside ds2's `decode_cycle`; `capabilities` and a
    `router` object (live mode, period, drops, page size, `counts: routes_executed`). Numbers and ids only.
17. No synchronization: no command-buffer wait, fence, event, blit, new dispatch or encoder; no lock or store on the
    graph-build path once the page exists; no atomics contention (one owner per cell); serial encoders already order the
    groupings (DS3-GPU-TELEMETRY.md §3).
18. Memory: 0.5 MB GPU; engine reader < 1 MB plus ≤ 4 MiB of recent detail shared by consumers; ring unchanged;
    Workstation < 7 MB with full history.

## Router results

19. Kernel only (counters forced on, nothing exposed), against ds2 in session: aggregate −0.91 % short / +0.65 % decode,
    exact −0.42 % / −0.08 %.
20. AGGREGATE with an 8 Hz consumer (router + PLE): +0.51 % short / +0.11 % decode. Gate < 2 %: pass.
21. OFF, interleaved ds2/ds3 ×3: −0.01 % short / +0.36 % decode (ds3 slower in each pair: likely real, ~0.25 ms per
    cycle). Gate < 0.5 %: pass.
22. Exact cross-checks on every run: router steps = DFlash cycles; decode routes = Σ(drafted + 1) × 10 × 48; prefill
    routes = uncached prompt tokens × 480; all 48 layer totals equal; sampled experts ⊂ aggregate; 0 drops.
23. SAMPLED at 1 in 16 / 8 / 4: +0.26 / +0.41 / +0.17 % short, +0.22 / +0.36 / +0.12 % decode — all noise; **1 in 8**
    chosen (one step ~0.6 s; the ring rides out ~40 s without a poll). Exact is research only, never proxied.

## PLE

24. Reads the row store's and expert store's existing counters on a poll; no extra reads, nothing on the decode path.
25. Router-only vs router + PLE: PLE only (page disabled, polled) −0.11 % / +0.29 %; router + PLE +0.51 % / +0.11 % —
    indistinguishable.
26. PLE read rows and bytes equal the `/status` `streamed_table` deltas exactly on every model and profile.
27. Expert storage: all three models resident (`expert_storage: resident`); the streamed-store deltas are implemented
    but not exercised, since no current model streams experts.

## Models

28. Scope LOW: levels 2, 3, 4 pass.
29. Swift LOW and MEDIUM: levels 2, 3 pass.
30. Cyber LOW and XHIGH: levels 2, 3 pass (searched-Q2_0 experts, BF16 dense: same kernel, no dimension assumptions).

## Workstation

31. Advanced Live view: Overview (per-layer routes over ~1.5 s, sampled step overlay), Layer focus (expert grid, busiest
    experts, top-10 share, effective experts), Routing heatmap (session or request); all follow the live phase
    (prefill while a prompt is processed, decode while generating), named in the badge.
32. Labels: AGGREGATE / SAMPLED 1 IN N badge with a tooltip on what is counted; samples carry their own period; intervals
    taken by another consumer are counted and named; facts panel rewritten for ds3; nothing drawn as exact.
33. Standard Live is unchanged; native session replays keep their own router rows (ds3 routing is off in replays).
34. Cost: 0.4–0.8 ms per frame in every mode; data rebuilds 3–7 ms at most 5 Hz; nothing while hidden.
35. Export adds `router` (`router_mode: aggregate|sampled`, `[layer, expert, routes]` cells, samples).
36. Proxy: level 2 by default, 3 with *Sampled selections*, clamped to 2–3.
37. Verified in the installed Workstation against the production service (DS3-GPU-TELEMETRY.md §10), after a harness
    run; the defects that review found are fixed there.

## Safety

38. Outputs: where ds2 is deterministic ds3 is identical (8.8K prefill test: one hash per run position over 4 ds2 and 11 ds3
    sessions; short first run 3/4 vs 10/11); long decodes vary within ds2 as much as between builds (Splash's
    verify-rows nondeterminism).
39. Failure safety: a schema mismatch in the first build returned API errors to the poll while serving continued; page
    allocation failure disables router telemetry and nothing else; telemetry exceptions empty one poll.
40. Defects found and fixed during the experiment: ds2's schema-1 gate; sample drops inferred from step gaps (now counted
    by the kernel); the highest level never lapsing while lower levels polled (now per-level deadlines); a sample's
    period read at poll time (now stored per slot); a lock per layer on graph build (now lock-free).

## Reproducibility

41. `scripts/backends/build_splash.sh SOURCE DIST OUT --ds3` from a clean clone reproduces the working build byte for
    byte: engine `ae07f09a…`, `splash.metallib` `1d9ae6f5…`, `host-step.metallib` `8e177c75…` (= ds2), adapter `9fe87539…`.
42. Patches `backends/splash-m1/patches/ds3/`: 0005a page, 0005b kernel, 0006 reader, 0007 server (999 lines).
43. `config/runtimes.json` pins `splash-m1-1.6.1-ds3-gpu-telemetry`; tests: 181 pass, including installed-runtime hash
    checks for ds1, ds2 and ds3.

## Decision

44. Success level: **FULL** for the requested signals (router activity, expert selection/utilisation, heatmaps, PLE),
    with documented limits: no router weights, samples are row 0 only, expert-cache activity only for streamed models.
45. Promotion gate: every item passes (DS3-GPU-TELEMETRY.md §11).
46. Not covered: 120K contexts (excluded), long agent sessions.
47. Decision: **PROMOTE.** The installed app and the production service pass, the useful native visualisation is about
    90 % restored, several viewers are served correctly, rollback works through the real path, and no regression was
    found.
48. Rollback: set `config/runtime-policy.json` back to `splash-m1-1.6.1-ds2-telemetry`, generate, restart; ds2's
    installation is byte-identical to its pin.
49. This branch sets the production pin to ds3; merging it promotes ds3, with ds2 and ds1 still pinned.
50. Not merged: pull request for review.
