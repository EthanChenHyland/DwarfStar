# Cyber conversion

DwarfStar Cyber is a format conversion of `dealignai/Qwen3.8-Flash-Next-CYBERSECURITY-NVFP4` @ `15ef113c7090844f97f355f400169791dc76c2e3` (ModelOpt NVFP4, W4A4 export, 206 shards, 135.25 GB). The pinned checkpoint is the only reference: no Scope, Swift or upstream Qwen tensor was substituted, even where names and shapes match.

## Pipeline

| Family | Source | Output | Fidelity against the source |
|---|---|---|---|
| Routed experts | 294,912 NVFP4 tensors (E2M1 codes, E4M3 block scales, FP32 tensor scale) | 144 Q2_0 tensors, 31.64 GiB | Relative RMS 0.356–0.373 per projection (mean 0.359), cosine 0.934 |
| N-gram (PLE) table | 128 FP8 shards × BF16 scalar `0x3951` (0.000199) | IQ4_NL sidecar, disk-backed | Relative RMS 0.076, cosine 0.997 |
| Dense trunk | 1,070 BF16 tensors | BF16 in the main file | Exact (a few norm / `ssm_a` elements at 1 ulp, identical to upstream) |
| MTP head | 31 BF16 tensors | 32-tensor `blk.48.*` sidecar, Q8_0 experts | Experts relative RMS 5.6e-3, rest exact |
| Vision | 333 BF16 tensors | F32 projector | Bit-exact |

**Expert encoding.** The 4-bit experts do not fit resident on a 64 GiB machine (63.3 GiB), so they are requantized to the 2.25-bit Q2_0 container. A per-block least-squares search picks the F16 scale and the codes over {−d, 0, d, 2d} that minimise squared error; it halves the weight error of the plain encoder (0.36 against 0.72 relative RMS) and is exact against brute force on hard blocks.

**N-gram scale.** The upstream converter upcasts the FP8 shards and drops the scalar, which would make the table about 5,000× too large. This conversion applies it and refuses to publish unless the decoded magnitude matches the scaled reference within 10 %.

**Structural audit.** An independent re-parse of all five files against the loader's rules passed with zero discrepancies.

## Runtime settings

* Context 131072 (128K), output ceiling 81920 (the shared policy), prefill chunk 1024 (its one override). With the default 2048 chunk the 128K plan (51.92 GiB with the vision projector) exceeded the 51.84 GiB Metal working-set limit and prefill failed at depth; the 1024 chunk reduces the plan to 50.81 GiB.
* K and V cache F16, the same as every model; the runtime's Qwen4Exp graph has no quantized KV path.
* Sampling from the checkpoint's `generation_config.json` (temperature 1.0, top-p 0.95, top-k 20).
* Reasoning efforts OFF, LOW, MEDIUM, XHIGH, each distinct on the wire.

## Known limitations

* The source is a W4A4 export; no Apple runtime reproduces 4-bit activations, so the build is weight-faithful with full-precision activations.
* The 2-bit expert encoding flattens the opening distribution: 4 of 80 sampled openings of one agent request stopped after a few junk tokens (Scope and Swift: 0 of 80); 2 of 21 historical agent sessions needed a retry. A calibrated re-encode from the same source is deferred.
* The model has no refusal behaviour. In Full Auto, text in a page, file or screenshot can steer its tools without an approval step.
* Tokenizer conformance 257 of 339 on the unpatched runtime (NFC normalization and 24 rarely-typed special tokens), the same as Scope with this tokenizer.

Full build report, manifests and toolchain: the original Cyber installation's `benchmarks/reviews/dwarfstar-cyber-complete-build-20261112T150844Z/` (not in this repository).
