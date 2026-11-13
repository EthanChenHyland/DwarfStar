# Models

All three are Qwen3.8-Flash-Next-family (`qwen4exp`) models: 48 layers mixing linear-attention and full-attention blocks, 512 routed experts with 10 active per token, a shared expert, an n-gram per-layer embedding table (PLE) read from disk, a one-layer multi-token-prediction (MTP) head and a vision projector. The catalog files in `config/models/` are the source of truth; the Workstation's **Models** view renders them with one schema.

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
| K / V cache | F16 / F16 | F16 / F16 | F16 / F16 |
| Prefill chunk | 2048 | 2048 | 1024 |
| Memory plan at 128K | 43.16 GiB + 0.57 vision | 43.16 GiB + 0.57 vision | 49.14 GiB + 1.67 vision |
| Metal headroom (51.84 GiB limit) | 8.11 GiB | 8.11 GiB | 1.03 GiB |
| Admission requirement | 45.2 GiB | 45.2 GiB | 52.3 GiB |
| Reasoning | LOW | LOW, MEDIUM | OFF, LOW, MEDIUM, XHIGH |
| Endpoint | 127.0.0.1:8030 | 127.0.0.1:8031 | 127.0.0.1:8032 |
| Default | yes | no | no |

## KV cache

Every model uses the same cache policy, fixed by the runtime's Qwen4Exp graph: K and V in F16 (34,176 bytes per token across the full-attention layers, 4.17 GiB at 128K including the F32 indexer keys), block keys F16. **A Q8 KV cache was requested for this release; the runtime has no quantized KV path for this architecture**, so F16 remains. Disk KV checkpoints (12 GiB budget) are enabled for all three.

## Measured performance

Short prompts at temperature 0 on the unified runtime unless noted (M1 Max, 64 GiB).

| | Scope | Swift | Cyber |
|---|---:|---:|---:|
| Load, cold / warm | 15.5 s / 1.6 s | 15.6 s / 1.6 s | 22 s / 2.1 s |
| Time to first token | 0.55 s | 0.54 s | 0.56 s |
| Prefill | 283 tok/s | 282 tok/s | 278–288 tok/s (59K–120K) |
| Decode | 40.9 tok/s | 40.8 tok/s | 35.7 tok/s |
| MTP acceptance | 86.8 % | 88.6 % | 78.8 % |

The production validation (docs/BENCHMARKS.md) adds the 128K depth results for Cyber.

## Quality highlights

* **Scope**: wins the quality-first matched comparison against Swift LOW; latency is ~95 % model compute; native MTP adds ~5 % decode.
* **Swift MEDIUM**: 8 of 8 frozen low-risk tasks on the first attempt (LOW: 6 of 8) at about the same wall time.
* **Cyber**: 8/8 coherence prompts, coding fix and a synthetic defensive source review (3/3 defects, 0 false positives); 4 of 80 sampled openings of one agent request fail early (the others: 0 of 80).
