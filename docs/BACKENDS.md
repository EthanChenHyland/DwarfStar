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
| **Splash-m1** | **Direct**: loads the existing local files | **Not supported**: Splash accepts only `gpt2/qwen35` and `gpt2/flint`; a lossless split repack loads metadata but the tokenizer profile is refused. Relabelling would mis-tokenize | **Not supported**: after a lossless split repack, Splash refuses the BF16 shared-expert projection (`tensor is not tile aligned`). Fixing it means re-encoding Cyber's exact BF16 trunk, which is out of scope |
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

## Recommendation

| Model | Native | Splash-m1 | Native oMLX | Recommended |
|---|---|---|---|---|
| Scope | works (control) | **faster, leaner, equal answers** | not practical | **Splash-m1**, once integrated behind DwarfStar's lifecycle and validated |
| Swift 1.5 | works | tokenizer profile unsupported | not practical | **Native** |
| Cyber | works at 128K | BF16 trunk unsupported | not practical | **Native** |

Native remains production for every model until the Splash adapter passes the lifecycle matrix and the user approves moving the default model's backend.

## Reproducing

```bash
scripts/validate/backend_ab.py native-scope 8030 qwen3.8-flash-next --out <data>/evidence/backend-ab
scripts/validate/backend_ab.py splash-scope 8040 qwen3.8-flash-next --out <data>/evidence/backend-ab
```

`scripts/backends/repack_split_gguf.py` writes the published two-part split layout from a main GGUF and a standalone PLE GGUF, losslessly (tensor payloads and metadata verified identical); it is how the Swift and Cyber compatibility checks were made.
