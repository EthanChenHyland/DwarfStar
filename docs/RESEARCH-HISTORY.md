# Research history

Dates come from the canonical reports, their run directories and file timestamps on the development machine. Raw evidence stays outside the repository; summaries are in `research/benchmark-index.json`.

## Before DwarfStar (30 Sep – 18 Oct 2026)

A series of local-workstation iterations (v6 to v15, a model lab and comparison harnesses) explored running large models and agents locally. Preserved snapshots show daily work from 30 September. Two pieces carried forward into DwarfStar: workspace secret redaction (`agent-tools/policy/redact_stdin.py`, 9 Oct) and the workspace guard extension (`acp/workspace-guard.ts`, 9 Oct). A Flash-Next drift study on 18 October led to choosing the Qwen3.8-Flash-Next family.

## Runtime and Scope (19 – 29 Oct)

| Date | Work |
|---|---|
| 19 Oct | Paperniuk ds4 Metal runtime (`m1-flash-next`, commit `6ea59815`) checked out with native MTP; Scope Vanilla (published GSQ-RCO Q2_0 GGUF) installed at 128K; Scope-first agent prompt; first Workstation. |
| 19–21 Oct | Agent tool layer: capability probe, browser plan runner, Node helpers; ACP extensions for transport, latency, efficiency, result hygiene, finish nudge, task summaries and infrastructure checks; benchmark runner, independent evaluator and live monitor; Zed navigation. |
| 25 Oct | Full Auto approval policy with a six-run compaction comparison (current settings kept); permission journal; task infrastructure (process ownership, ports, recovery). |
| 29 Oct | Vision input and KimiCU computer use for the agent; scalar ACP telemetry. |

## Swift 1.5 (31 Oct – 11 Nov)

| Date | Work |
|---|---|
| 31 Oct – 1 Nov | Swift 1.5 built with the GSQ-RCO recipe, its own routers, MTP head and tokenizer (tokenizer adapter patch for the runtime); BF16 sources archived to an external disk (50/50 files verified). |
| 1 Nov | Matched comparison: Scope wins on quality; Swift LOW faster but missed critical Ledger behaviour. |
| 1–2 Nov | Failure forensics, targeted verification, deterministic validation and Ledger reruns; Scope-fallback routing study (not deployed); LOW low-risk fast lane: 6 of 8. Kapitsa runtime port audited and parked. |
| 11 Nov | Swift MEDIUM rematch on the frozen suite: **8 of 8** (LOW 6 of 8) at near-equal cost. MEDIUM retained as a non-default profile. |

## Scope optimization (3 – 11 Nov)

Latency decomposition (≈95 % of warm agent time is native inference, 3 Nov); stable-prefix restore (first-token −21 s, task −5.65 %, not adopted, 3 Nov); bootstrap metadata cache (no benefit, 10 Nov); ACP start-up decomposition (3.3 s, branch closed, 11 Nov); native adaptive MTP audit (+5.34 % decode, 11 Nov). Scope optimization frozen.

## Cyber (11 – 12 Nov)

| Date | Work |
|---|---|
| 11 Nov | Read-only feasibility audit of `dealignai/Qwen3.8-Flash-Next-CYBERSECURITY-NVFP4` @ `15ef113c`; NVFP4 conversion path traced through llama.cpp; synthetic conversion proof (exact NVFP4 repack, n-gram scale bug found upstream). Full acquisition and conversion authorized. |
| 11–12 Nov | Real-layer pilot (searched Q2_0 chosen), 135 GB source acquired and verified, conversion of experts, n-gram table, trunk, MTP and vision; independent structural audit; runtime bring-up at 64K (128K failed at depth); agent and tool validation; durable Swift MEDIUM profile. |
| 12 Nov | Complete build report: functional with limitations. |

## Unified product (12 Nov)

| Time (CST) | Work |
|---|---|
| 13:18 | Pre-migration snapshot of all installations. |
| 13:30–14:00 | One repository: canonical catalog (model × reasoning profiles, context as a model property), generated per-model configuration, one controller, one ACP launcher; telemetry-capable runtime build `ds4-unified`; Cyber parity with the reference build. |
| 18:00–18:30 | Scope and Swift parity (token-identical); opening-token probe (Cyber 4/80 early stops, Scope and Swift 0/80); Workstation service, telemetry hub, web UI and visualizer; seal tooling. |
| 18:40–19:10 | Cyber at 128K by prefilling in 1024-token chunks (120,399-token test passed); production validation 15/15; docs; app installed; Zed entries regenerated. |
| 19:30–20:00 | Release pass: shared runtime policy with justified overrides only; one memory formula and a measured Metal limit; lifecycle state machine rebuilt after a reported Cyber MEDIUM → Stop → LOW failure (seven-profile matrix, same-family and cross-family sequences pass); Workstation chats on the agent's own sessions and compaction; visualizer layout fixes; licence. |
