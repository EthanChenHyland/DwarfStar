# Models

All three are Qwen3.8-Flash-Next-family (`qwen4exp`) models: 48 layers mixing linear-attention and full-attention blocks, 512 routed experts with 10 active per token, a shared expert, an n-gram per-layer embedding table (PLE) read from disk, a one-layer multi-token-prediction (MTP) head and a vision projector. In production, Splash-m1 drafts with a separate DFlash2 draft model (not the models' own MTP head) and verifies every token against the target. The catalog files in `config/models/` are the source of truth; the Workstation's **Models** view renders them with one schema.

## Summary

| | Scope Vanilla | Swift 1.5 | Cyber |
|---|---|---|---|
| Source | `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF` @ `ed59f920` | `ukisai/Swift1.5-Qwen3.8-Flash-Next` @ `0bd4fe22` | `dealignai/Qwen3.8-Flash-Next-CYBERSECURITY-NVFP4` @ `15ef113c` |
| Derivation | published GGUF, byte for byte | GSQ-RCO Q2_0 experts, Swift BF16 routers, Swift MTP and tokenizer | converted from NVFP4 (see CYBER-CONVERSION.md) |
| Experts | Q2_0 (2.25 bpw) | Q2_0 (2.25 bpw) | searched Q2_0 (2.25 bpw) |
| Dense trunk | mixed Q3_K–Q6_K, IQ4_XS, BF16 routers | same | BF16 (exact) |
| PLE | IQ4_NL, disk-backed | IQ4_NL, disk-backed | IQ4_NL, disk-backed, source scalar applied |
| MTP | Q4_K + MXFP4 | Q4_K + MXFP4 | Q8_0 |
| Vision | Q8_0 + F16 | shared with Scope | F32 (bit-exact) |
| On disk | 63.9 GiB | 63.9 GiB | 72.0 GiB |
| Context | 128K (131072) | 128K (131072) | 128K (131072) |
| Output ceiling | 80K | 80K | 80K |
| K / V cache | INT8 / INT8 | INT8 / INT8 | INT8 / INT8 |
| Prefill chunk | 2048 | 2048 | 1024 |
| Memory plan at 128K | 43.16 GiB + 0.57 vision | 43.16 GiB + 0.57 vision | 49.14 GiB + 1.67 vision |
| Metal headroom (51.84 GiB limit) | 8.11 GiB | 8.11 GiB | 1.03 GiB |
| Admission requirement | 45.2 GiB | 45.2 GiB | 52.3 GiB |
| Reasoning | LOW | LOW, MEDIUM | OFF, LOW, MEDIUM, XHIGH |
| Endpoint | 127.0.0.1:8030 | 127.0.0.1:8031 | 127.0.0.1:8032 |
| Default | yes | no | no |

## KV cache

Every model uses the same cache policy, Splash-m1's: symmetric INT8 K and V with per-token-head F32 scales in 32-token pages (13,248 bytes per token, 1.62 GiB at 128K), a prefix cache in memory, and long requests' KV spilled to the SSD cache (`<cache>/splash-kv`). This is a genuine 8-bit KV cache; the retired native runtime could not do it (its Qwen4Exp graph allocated F16 K/V only: 34,176 bytes per token, 4.17 GiB at 128K). Backend evaluation and measurements: [BACKENDS.md](BACKENDS.md).

## Measured performance

Measured on Splash-m1 (`splash-m1-1.6.1-ds1`), M1 Max 64 GiB, the backend A/B of 2026-11-13 (short, 8K and 32K prompts, temperature 0). These are the values the catalog carries and the Models view shows.

| | Scope | Swift | Cyber |
|---|---:|---:|---:|
| Start, idle to ready | 25.2 s | 24.3 s | 27.0 s |
| Time to first token | 0.51 s | 0.51 s | 0.52 s |
| Prefill, 8K / 32K | 331 / 307 tok/s | 333 / 310 tok/s | 327 / 307 tok/s |
| Decode (draft-accelerated) | 35–67 tok/s | 41–78 tok/s | 33–62 tok/s |
| Draft acceptance (one coding prompt) | 73 % | 61 % (MEDIUM) | 70 % |
| Peak wired, whole machine | 43.35 GiB | 43.24 GiB | 48.37 GiB |

Decode depends on how many drafted tokens the target accepts, which varies by task and reasoning level; the range is the measured span. Draft acceptance is the share of DFlash2 draft tokens the target kept, measured live on one coding prompt per model (`dwarfstar runtime … status` → Splash `/status`). The production validation (docs/BENCHMARKS.md) adds the 120K depth results. The shared draft and whether a per-model draft is worth building: [DRAFT-MODELS.md](DRAFT-MODELS.md).

## Quality highlights

* **Scope**: wins the quality-first matched comparison against Swift LOW; latency is almost entirely model compute.
* **Swift MEDIUM**: 8 of 8 frozen low-risk tasks on the first attempt (LOW: 6 of 8) at about the same wall time.
* **Cyber**: 8/8 coherence prompts, coding fix and a synthetic defensive source review (3/3 defects, 0 false positives); 4 of 80 sampled openings of one agent request fail early (the others: 0 of 80).
