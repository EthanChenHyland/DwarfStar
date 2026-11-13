# Inference backends

DwarfStar is the product: Workstation, catalog, lifecycle, chats, compaction, ACP tools, memory admission, telemetry and diagnostics. The inference backend under each model is an implementation detail. This page records which backends were evaluated on the reference machine (M1 Max, 24-core GPU, 64 GiB, macOS 26.5.2) and why each model runs where it does.

## Candidates

| Backend | What it is | Status on this Mac |
|---|---|---|
| **Native** (`ds4-unified`) | The pinned Paperniuk ds4 Metal runtime with DwarfStar's tokenizer adapter and telemetry hooks | Production for all three models |
| **Splash-m1** 1.6.1-m1 | [paperniuk/splash](https://github.com/paperniuk/splash), the M1/M2 port of [incoai/splash](https://github.com/incoai/splash): Apple7 Metal kernels, DFlash2 speculative decoding, INT8 KV, prefix cache, PLE row streaming. Needs macOS 26.4+ | Installed isolated under `<data>/backends/splash-m1/` (release tarball SHA-256 `fde8cc8f…`); not on PATH |
| **Native oMLX / MLX** | [jundot/omlx](https://github.com/jundot/omlx) on MLX | Not installed: not practical for these models (below) |

## Compatibility per model

| | Scope | Swift 1.5 | Cyber |
|---|---|---|---|
| Artifact | published 2-part split GGUF (`qwen4exp`, PLE in part 2) | main + standalone PLE GGUF | main + standalone PLE GGUF, BF16 dense trunk |
| Tokenizer profile | `gpt2/qwen35` | `gpt2/qwen2` (Swift's own tokenizer) | `gpt2/qwen35` |
| **Splash-m1** | **Direct**: loads the existing local files | **Works** with the `qwen2` tokenizer-profile patch (below) | **Works** with the exact-BF16 planner patch (below); weights unchanged |
| **Native oMLX / MLX** | Not practical | Not practical | Not practical |
| Vision on Splash | Yes, with the original BF16 projector from the pinned source repo (`mmproj-Qwen3.8-Flash-Next-BF16.gguf`, 0.91 GB, SHA-256 `b1a82259…`). Splash refuses the Q8_0 projector: it converts vision weights to BF16 and refuses values that are not exactly representable | – | – |
| Speculative decoding | Splash: DFlash2 draft `incoai/Qwen3.8-Flash-Next-DFlash2` @ `a22a1744` (1.21 GB, 0.49 GiB resident). Native MTP sidecars are not used by Splash | – | – |

**Why not MLX.** Stock mlx-lm has no `qwen4_exp` (PR #1788 is open; mlx-vlm merged it). Published MLX builds are 4-bit or wider: routed experts alone are about 63 GiB at 4.5 bits, and oMLX has no per-layer-embedding SSD offload (its SSD tier holds KV blocks), so the 27 GB PLE table would also be resident. Even a 2-bit MLX build would hold about 32 GiB of experts plus the PLE table, above the 51.84 GiB Metal working set. No MLX conversion was made.

## Scope A/B: native vs Splash-m1

Same machine, same GGUF, same prompts, temperature 0, reasoning LOW, one request at a time (`scripts/validate/backend_ab.py`; evidence `<data>/evidence/backend-ab/`).

| | Native | Splash-m1 | Change |
|---|---:|---:|---:|
| Load, page cache warm | 12.8 s | 22 s | slower |
| TTFT, short prompt | 0.67 s | 0.54 s | −19 % |
| Prefill, 8K tokens | 291 tok/s | 333 tok/s | +14 % |
| Prefill, 32K tokens | 288 tok/s | 310 tok/s | +8 % |
| Generation while answering (MTP vs DFlash2) | 37–39.5 tok/s | 44–69 tok/s | +15–75 % |
| Wall, 8K needle question | 39.0 s | 32.6 s | −16 % |
| Wall, first turn of the repeated-prefix test | 36.3 s | 28.8 s | −21 % |
| Second turn, repeated prefix | 8,539 tokens reused, TTFT 0.35 s | 8,224 reused, TTFT 0.62 s | both reuse the prefix |
| Peak wired memory | 45.9 GiB | 41.8 GiB | −4.1 GiB (INT8 KV) |
| KV cache | F16 K/V + F32 indexer (34 KiB/token) | INT8 (13 KiB/token) | −61 % |
| Answers | correct (needle at 8K and 32K) | same answers | equal |
| Tool calls with DwarfStar's provider request | yes | yes (OpenAI `tool_calls`, separate `reasoning_content`) | equal |
| Vision | Q8_0 projector | BF16 projector | equal (correct description of the test image) |
| Telemetry | router, MTP, PLE, KV events | `/status` memory plan, request metrics; no router or PLE events | less detail |

Splash clears the 15 % bar on effective generation and end-to-end time and saves 4 GiB, with the same answers. It costs ~9 s more load and the visualizer's router/PLE/MTP detail.

## Decision (2026-11-13): one runtime, Splash-m1, for every model

| Model | Native ds4 | Splash-m1 1.6.1-m1-ds1 | MLX / oMLX | Production |
|---|---|---|---|---|
| Scope | works | faster, leaner, equal answers | not practical | **Splash-m1** |
| Swift 1.5 | works | faster, leaner, equal answers (tokenizer patch) | not practical | **Splash-m1** |
| Cyber | works at 128K | faster, 4.5 GiB leaner, equal answers (exact-BF16 patch) | not practical | **Splash-m1** |

`config/runtime-policy.json` (version 2026.11.3) makes `splash-m1-1.6.1-ds1` the runtime of every model. Validation on the promoted default: lifecycle matrix 14/14, same-family 14/14, cross-family 9/9, regression 4/4; 120K needle passed on Swift (428.8 s) and Cyber (431.6 s vs native's 453.8 s; decode 37.8 vs 20.0 tok/s; peak 49.4 vs 52.9 GiB); vision on all three; agent shell-tool turns through the Workstation on Swift MEDIUM and Cyber XHIGH.

**Rollback.** The native runtime stays installed and pinned. Per model: `"runtime_overrides": {"scope": "ds4-unified"}` in `config/local.json` (or `DWARFSTAR_RUNTIME_OVERRIDE=scope=ds4-unified`), then `bin/dwarfstar generate`; verified (Scope started on ds4-server and answered). Whole product: the tag `rollback/native-default-20261113`.

## Reproducing

```bash
scripts/validate/backend_ab.py native-scope 8030 qwen3.8-flash-next --out <data>/evidence/backend-ab
scripts/validate/backend_ab.py splash-scope 8040 qwen3.8-flash-next --out <data>/evidence/backend-ab
```

`scripts/backends/repack_split_gguf.py` writes the published two-part split layout from a main GGUF and a standalone PLE GGUF, losslessly (tensor payloads and metadata verified identical); it is how the Swift and Cyber compatibility checks were made.

## Prefill chunk and prefix reuse (native)

**Question.** Should DwarfStar pick the prefill chunk per start from the Metal headroom (larger chunks when memory allows) instead of the fixed shared 2048 with Cyber's 1024 override?

**Measurement.** `scripts/validate/prefill_sweep.py` starts Scope's own verified server command on a spare port with only `--prefill-chunk` changed, sends ~8K and ~32K-token prompts with one output token, and records the runtime's memory plan and peak wired memory. Every prompt starts with a unique line: without it the runtime restores the deterministic test document from its disk KV checkpoints and reports meaningless rates (4,700 tok/s at 32K). Evidence: `<data>/evidence/prefill-sweep/prefill-sweep-scope.json`.

| Chunk | Graph buffers | Plan | Peak wired | Prefill 8K | Prefill 32K |
|---:|---:|---:|---:|---:|---:|
| 1024 | 1.44 GiB | 42.04 GiB | 44.8 GiB | 284 tok/s | 275 tok/s |
| **2048** (shared default) | 2.56 GiB | 43.16 GiB | 45.7 GiB | **297 tok/s** | **282 tok/s** |
| 4096 | 4.79 GiB | 45.39 GiB | 47.3 GiB | 292 tok/s | 277 tok/s |

**Decision: no adaptive selection.** 2048 is the fastest chunk; 4096 is not faster and costs 2.2 GiB more; 1024 costs 3–4 % prefill and saves 1.1 GiB. The whole range is far inside the 15 % bar for a change, so the fixed shared default stays and Cyber keeps 1024 (its plan needs the headroom; the measured cost is about 4 % of prefill).

**Prefix reuse already exists.** The native runtime reuses a repeated prompt prefix in memory (second turn of the A/B test: 8,539 of ~8.6K tokens reused, TTFT 0.35 s) and also restores long prefixes from its disk KV checkpoints across restarts (12 GiB budget, a checkpoint every 10,000 tokens of a continued conversation). Agent turns, which resend the same system prompt and transcript, benefit from both. The Visualizer's Requests table shows the reused share for every request.

## Swift on Splash-m1 (2026-11-13)

**Tokenizer fix.** Splash derives a Hugging Face tokenizer from GGUF metadata and knew only the `qwen35` and `flint` profiles. Swift's GGUF declares `tokenizer.ggml.pre = qwen2`, its real profile: Swift's shipped `tokenizer.json` applies NFC, then Qwen2's regex *without* combining marks (its `tokenizer_config.json` also lists a `pretokenize_regex` with marks, which Hugging Face ignores; verified by loading Swift with `AutoTokenizer`). `backends/splash-m1/patches/0001-qwen2-tokenizer-profile.patch` adds that profile to `install/gguf.py`: the regex is byte-identical to Swift's `tokenizer.json`, plus a unit test in Splash's own suite. Nothing in Swift's GGUF was changed.

**Parity.** `scripts/backends/tokenizer_parity.py` compares, for the same GGUF, the native runtime's token IDs (`ds4 --dump-tokens --raw`, built from the same objects as the pinned server; metadata only, 0.3 s), Splash's production path (`tokenizer_files` → `AutoTokenizer` → `tokenizable`, no special tokens added), and optionally the model's own shipped tokenizer. Swift: **62/62 cases, 2,116 tokens, identical across all three** (plain text, code, punctuation, whitespace, Unicode incl. NFD, Devanagari, RTL and emoji, JSON tool calls, special tokens, and prompts rendered from Swift's chat template with tools and every reasoning setting). Real requests agree too: native and Splash report the same prompt token counts (70, 8,157, 32,466). Special-token config matches (no BOS, EOS `<|im_end|>`, pad `<|endoftext|>`, 33 added tokens); the six FIM tokens are marked control in the GGUF, so Splash would hide them when decoding with special tokens skipped (IDs unaffected).

The same harness on Scope shows three existing native-vs-Splash differences, unchanged by this work: native does not NFC-normalize `qwen35` input (decomposed accents tokenize differently), and native treats a literal `<|vision_start|>` typed in text as plain text.

**Layout.** Splash loads a two-part split GGUF. Its engine keeps only part 1's metadata and checks nothing in later parts but an optional `split.no` (`GgufFile::parse`), so `repack_split_gguf.py --link-part2` writes part 1 only (37.6 GB, tensor payloads and metadata verified identical) and symlinks Swift's unchanged PLE file as part 2.

| Swift | Native | Splash-m1 | Change |
|---|---:|---:|---:|
| TTFT, short | 0.69 s | 0.52 s | −25 % |
| Prefill, 8K / 32K | 295 / 283 tok/s | 333 / 310 tok/s | +13 % / +10 % |
| Generation while answering | 37–40 tok/s | 45–78 tok/s (DFlash2 draft) | +15–96 % |
| Wall, 8K needle / 32K needle | 37.0 / 124.0 s | 32.0 / 113.0 s | −13 % / −9 % |
| Repeated prefix, turn 2 | 8,523 reused, TTFT 0.34 s | 8,224 reused, TTFT 0.60 s | both reuse |
| Peak wired | 45.85 GiB | 43.04 GiB | −2.8 GiB |
| Answers | needle correct at 8K and 32K | same | equal |

Smoke tests on Splash: LOW and MEDIUM reasoning answer correctly, reasoning arrives separately, and a tool request returns a proper `shell` call. Not yet run: 128K depth, vision, DwarfStar lifecycle integration.

## Cyber on Splash-m1: diagnosis (2026-11-13)

Splash's planner repacks projection weights into "planes" whose width must be a multiple of the format's column unit: 256 for formats with native blocks over 64 elements (BF16 and F16 use 256-element blocks), else 64 (`quant_column_unit`, `tiledRepack`). Cyber keeps its dense trunk in BF16, exactly as the authoritative source stores it; Scope and Swift store the same tensors in 32/64-element quantized types. A full audit of Cyber's 1,223 tensors finds two gaps:

1. `blk.*.ffn_down_shexp.weight`, BF16 [2560 × **640**] (48 tensors): 640 = 2·256 + 128. The shared expert runs inside the grouped routed-expert pass as a slice exactly as wide as a routed expert (640), so neither column padding nor the float "as stored" projection (used only for the indexer) can serve it without changing the computation.
2. Hyper-connection projections `hc_*_down` [10240 → 320] and `hc_*_up` [320 → 10240] (+ `output_hc_*`), BF16: Splash deliberately converts any BF16/F16 hyper-connection projection to **Q8_0** at load (`quantizedOrQ8_0`, for bandwidth). That would silently change Cyber's learned values. Native multiplies them in BF16.

Every other Cyber BF16 tensor already fits (widths 2560/6144/10240) or uses an exact as-stored path (router, embeddings, alpha/beta, indexer, PLE conv).

Options considered, in order: (1) no other Splash type holds BF16 values exactly at a 64 unit (Q8_0/Q4_x/IQ4_NL/MXFP4/Q2_0 are all lossy); (2) no tail kernel exists for a partial 256 block; (3) loader-side zero padding cannot work because the shared expert's width is tied to the routed experts'; (4) **minimal engine change, lossless**: add a BF16 plane format with 64-element blocks (`meta_groups` 2; its scale `g` is always 1). The kernels are generic over the format table (`FmtFloat16<F, V>` reads `kQuantFormats[F].meta_groups`; instantiations come from `QUANT_FORMATS(X)`). The planner would pick it for BF16 tensors whose width is not a multiple of 256, and use BF16 planes instead of the Q8_0 conversion for BF16 hyper-connection projections. (5) Rebuilding or re-encoding Cyber is **not** needed. Option 4 is implemented (next section).

## Cyber on Splash-m1: exact BF16 planes (2026-11-13)

`backends/splash-m1/patches/0002-exact-bf16-planes.patch`, built from the 1.6.1-m1 source with Xcode 26.6's Metal toolchain:

* adds **BF16S**: BF16 in 64-value native blocks, the same values and planes as BF16 (its scale `g` is always 1); the kernels are generic over the format table, so it is one table entry, one kernel-list entry and one repack case;
* the planner picks BF16S for a BF16 tensor whose width is a multiple of 64 but not of 256 (Cyber's 640-wide shared-expert down projection, the 320-wide hyper-connection up projections);
* BF16 hyper-connection projections stay exact instead of being re-quantized to Q8_0 at load (F16 keeps upstream's Q8_0 path). This also applies to Scope and Swift: **their** GGUFs store these projections in BF16 too, so upstream Splash had been running them approximated; native runs them exactly. The cost is negligible (Scope wall 32.6 → 32.4 s at 8K, 113.7 → 114.3 s at 32K; decode within noise; up to ~1.5 GiB more peak wired).

Verification: a stock build from source is behaviourally identical to the pinned release (6/6 deterministic replies, including a 16K-token lookup). With the patch, Splash's own suites pass with BF16S in every format loop under Metal shader validation: planner (new 320-wide BF16 shared-expert case; exact BF16/BF16S hyper-connection planes), preparation (repack of every format against the reference), dequant (BF16S: 0 of 262,144 weights differ), projection (45 formats within fp64 bounds) and MoE numerics. Cyber's split part 1 was rewritten with tensor payloads and metadata verified identical; its PLE file is linked unchanged.

| Cyber | Native | Splash-m1 (patched) | Change |
|---|---:|---:|---:|
| TTFT, short | 0.85 s | 0.52 s | −39 % |
| Prefill, 8K / 32K | 297 / 292 tok/s | 327 / 307 tok/s | +10 % / +5 % |
| Generation while answering | 23–30 tok/s | 33–62 tok/s (DFlash2 draft) | +11–130 % |
| Wall, 8K needle / 32K needle | 46.0 / 126.9 s | 32.2 / 115.7 s | −30 % / −9 % |
| Repeated prefix, turn 2 | 8,497 reused, TTFT 0.44 s | 8,224 reused, TTFT 0.59 s | both reuse |
| Peak wired | 52.91 GiB | 48.37 GiB | −4.5 GiB |
| Answers | needle correct at 8K and 32K | same | equal |

Deterministic prompt set on Splash (`splash_equivalence.py`): all six correct, including the 16,404-token lookup. Prompt token counts equal native's for all three models. Tokenizer parity against each model's shipped `tokenizer.json`: Swift 62/62 for native and Splash; Cyber 62/62 for Splash, 59/62 for native (native skips NFC for `qwen35` and reads a typed `<|vision_start|>` as text), so Splash is the more faithful tokenizer for Cyber and Scope.

Not yet run on Splash: 128K depth for Swift and Cyber, vision for Swift and Cyber, Cyber OFF/MEDIUM/XHIGH, DwarfStar lifecycle integration.
