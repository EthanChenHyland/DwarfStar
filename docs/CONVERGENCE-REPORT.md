# DwarfStar convergence — final report (2026-11-13)

The closing phase of the DwarfStar convergence: one polished Workstation, one Splash runtime, three production models, seven profiles, one clean filesystem, one shared Workstation/Zed session system, truthful telemetry, no active legacy backend. This report records the state at the end of that phase.

## Git and release

1. **Branch**: `perf/backend-and-workstation`.
2. **Remote**: https://github.com/EthanChenHyland/DwarfStar (public, MIT).
3. **Sync**: this phase added six commits (`d8987a4` telemetry plumbing, `e1ff7f9` unslop pass, `44421a9` UI tests + screenshots, `0533c23` draft study, `60116f2` docs, and this report) on top of the previously pushed `1f10469` (Splash telemetry) and `b9ea551` (shared Zed sessions), and pushed them to the branch.
4. **Seal**: `bin/dwarfstar seal --verify` → ok, 0 problems, 160 tracked files, editor entries match; `bin/dwarfstar doctor` → 26 ready, 0 optional missing, 0 blocking. The seal lives at `<assets>/Manifests/seal/` (not in Git). All 87 unit tests pass; `scripts/validate/check.sh` passes.

## Workstation design (the unslop pass)

5. **Problems before the pass.** A card around every number; accent-filled chips everywhere; a mixed set of glyph nav icons; native-era panels (router/MTP/PLE) shown as if live under Splash; Diagnostics a wall of 26 equally-weighted green checks; Settings folded into Diagnostics; chat rows carrying every metadata field; reply text shown raw; "Launch"/"statistics" copy; a Visualizer that competed with Live.
6. **Pages changed.** Run, Chats, Models, Live (Standard + Advanced), Diagnostics, a new Settings page, the command palette, the nav and the shared stylesheet.
7. **Hierarchy.** One primary action per page; a running-state line on Run and Live; "All passed" standing in for 26 checks on Diagnostics with detail behind disclosure; measured headline first on Models.
8. **Spacing and type.** One spacing scale (`--sp-1…6`), three radii, obvious type steps; quiet metric rows replace cards-around-numbers.
9. **Navigation.** Chats first and the default landing when a model runs; Live and its Advanced view under one entry; Diagnostics (read-only state) and Settings (approval, Zed, data roots) split; SVG icons; no Benchmarks tab.
10. **Chat sidebar.** Rows are title + one meta line; secondary actions in one **More** menu; segmented filters; pin/star as quiet marks.
11. **Chat content.** The conversation is the priority; reply text renders code blocks and inline code; many tool calls collapse; approval cards still stand out; reasoning never shown.
12. **Composer.** One input with the profile and the approval mode shown in its footer; a link to change approval in Settings.
13. **Run.** Model guidance with measured speed/memory; one consequence sentence per action; Start/Stop obvious; the stage row informative without dominating when idle.
14. **Models.** A measured headline, then identity/quantization/memory/storage; policy, capabilities and hashes behind disclosure.
15. **Diagnostics.** A status strip; one memory composition; performance and system side by side; a Needs-attention panel only when something is wrong.
16. **Live / Visualizer.** Standard view carries Splash's own measurements and the latest request; Advanced is the data map with unreported regions labelled; prefix-cache and draft copy corrected for Splash.
17. **Settings.** Tool approval, Zed entries, shared conversations, the five data roots.
18. **Zed UX.** A compact "Shared with Zed" marker, Open in Zed and Continue here where they work; a plain "open elsewhere" message instead of a fork.
19. **Command palette.** Chats first, then models/reasoning, then actions and views; obsolete commands removed.
20. **Empty states.** Short text plus one action; no marketing blocks.
21. **Copy.** "Start", "Runtime", "Healthy", "No active requests" — direct labels, explanations only where there is real ambiguity.
22. **Responsive.** No horizontal overflow and nothing escaping a panel at 1440×900, 1600×1000 or 1100×700 (automated element-bounds audit).
23. **Screenshot validation.** Every page captured at the three sizes and from a real agent turn; home paths masked to `~`; test chats removed afterwards.

## Filesystem

24. **CODE** — `~/Developer/DwarfStar` (this repository).
25. **ASSETS** — `~/AI/DwarfStar` (`Models/`, `Runtimes/`, `Tools/`, `Sources/`, `Manifests/`, `Archive/`).
26. **RUNTIME** — `~/AI/DwarfStar/Runtimes/splash-m1-1.6.1-ds1`.
27. **DATA** — `~/Library/Application Support/DwarfStar`.
28. **CACHE** — `~/Library/Caches/DwarfStar`.
29. **LOGS** — `~/Library/Logs/DwarfStar`.
30. **TOOLS** — `~/AI/DwarfStar/Tools/{v6,v7,document-tools}`.
31. **Old `~/local-ai-setup`** — retired; held only a regenerable `.DS_Store`, now removed. The empty directory is kept as this session's shell anchor and can be `rmdir`'d; nothing in the product points at it.

## Storage

32. **Bytes purged in the 2026-11 consolidation**: 311,726,134,212.
33. **Reclaimed**: ≈290.3 GiB; free space ≈308.9 GB → ≈619.6 GB.
34. **Current DwarfStar disk use**: ≈215.3 GiB — production models 195.06, archive 17.98, sources 0.69, cache 0.66, tools 0.40, runtime 0.32, manifests 0.15, user data 0.09.
35. **Unreferenced large files**: none (`bin/dwarfstar storage`).
36. **External Swift BF16 backup**: off-SSD, not mounted, intentionally not copied onto the internal disk; production does not need it.

## Runtime

37. **Splash-m1** `splash-m1-1.6.1-ds1` for every model — the one production runtime; no backend selection.
38. **Reproducible build** from `paperniuk/splash` `1.6.1-m1` (`5312c54`) + two patches (`0001-qwen2-tokenizer-profile`, `0002-exact-bf16-planes`) via `scripts/backends/build_splash.sh`.
39. **Pins**: engine `75bc297f…`, `splash.metallib` `694abdf2…`, `host-step.metallib` `8e177c75…`, adapter `install/gguf.py` `9fe87539…`; the controller refuses a mismatch.
40. **Native retirement**: native ds4 removed from the product, recoverable from Git (`docs/NATIVE-RETIREMENT.md`, tags `rollback/native-default-20261113` and `checkpoint/splash-default-20261113`).

## Models

41. **Scope Vanilla** — published GSQ-RCO Q2_0 of Qwen3.8-Flash-Next; LOW; default. Decode 35–67 tok/s, TTFT 0.51 s, prefill 331 tok/s, peak 43.35 GiB, draft acceptance 73%.
42. **Swift 1.5** — fine-tune with its own routers, MTP head and tokenizer; LOW, MEDIUM. Decode 41–78 tok/s, TTFT 0.51 s, prefill 333 tok/s, peak 43.24 GiB, acceptance 61% (MEDIUM).
43. **Cyber** — Dealign CYBERSECURITY conversion, BF16 trunk run exactly through BF16/BF16S planes; OFF, LOW, MEDIUM, XHIGH. Decode 33–62 tok/s, TTFT 0.52 s, prefill 327 tok/s, peak 48.37 GiB, acceptance 70%.
44. **Seven profiles**: scope-low; swift-low, swift-medium; cyber-off, cyber-low, cyber-medium, cyber-xhigh.
45. **128K** context (131072) for all three; output ceiling 80K.
46. **Vision** on all three (Scope/Swift share the BF16 projector; Cyber uses its F32 projector).
47. **Tools**: workspace, shell, browser, computer use (KimiCU), vision, through one ACP launcher.
48. **Memory**: Scope/Swift plan 41.56 GiB (start needs 43.1), Cyber 47.44 GiB (start needs 48.9), against a measured 51.84 GiB Metal working set.

## Sessions

49. **Canonical store**: the agent's append-only session file per conversation.
50. **Workstation → Zed**: Open in Zed opens the workspace; the matching agent lists the session (ACP `session/list`).
51. **Zed → Workstation**: the Chats → Zed tab enumerates DwarfStar threads (read-only) and continues a live-workspace, current-profile thread on the same session; others read-only.
52. **One writer**: a workspace lock; the Workstation reports "open elsewhere" instead of forking; back-to-back turns wait up to 6 s for release.
53. **Historical limits**: threads whose temporary workspaces were archived in the consolidation cannot be continued; they read where the file survives, else show missing/empty.
54. **Reasoning privacy**: the Workstation reads only user text, reply text and tool names; it stores and shows no reasoning, even where Zed keeps it in the file.

## Telemetry

55. **Exposed Splash metrics**: request counts, TTFT, inter-token latency, prefill and decode rates, draft acceptance (running total), prefix-cache hits, context, memory, thermal, scheduler state; plus the agent's tool actions and the latest request from its receipt. One `/status` read per poll (~2 ms, ~17 KB).
56. **Unavailable**: per-token router selections, expert activity, PLE reads — labelled "not reported", never estimated.
57. **Visualizer behaviour**: Advanced view draws the data map and labels the unreported regions; it replays native-recorded sessions with their router/PLE detail.

## Draft study (research only)

58. **Scope**: the shared DFlash2 draft is matched by construction (Scope is the base model); 73% acceptance. Keep.
59. **Swift**: same architecture and tokenizer; 61% at MEDIUM. A Swift-specific draft is not worth building at current acceptance. Keep.
60. **Cyber**: same; 70%. A Cyber draft is the only candidate with any case, and a weak one. Keep.
61. **Verdict**: no matched-draft phase is warranted now. Build a per-model DFlash2 only if a workload shows acceptance falling well below the base (~<50%) at the same effort. Nothing downloaded or built.

## Quality

62. **Tests**: 87 unit tests pass; `check.sh` passes (config matches, benchmark index reproducible, JS/TS syntax).
63. **UI checks**: profile matrix (7 profiles, no overflow at two sizes), lifecycle regression (Cyber MEDIUM → Stop → LOW through the UI matches the controller), chat organization, visualizer bounds, and the element-bounds audit at three sizes — all pass.
64. **Known limitations**: `docs/known-limitations.json` — Splash's unreported low-level signals; the shared (not per-model) draft, now measured and healthy; Cyber's occasional broken opening; Cyber no refusal; Cyber's tight Metal headroom; historical Zed threads with archived workspaces.
65. **Biggest remaining improvement**: Cyber's opening-robustness from a calibrated 2-bit expert re-encode (deferred; the source toolchain lives with the original Cyber installation).
66. **Recommended next phase**: none required for production. Optional, measured first: a Cyber-specific DFlash2 draft if a real workload shows depressed acceptance, and the Cyber calibrated re-encode for opening robustness.
