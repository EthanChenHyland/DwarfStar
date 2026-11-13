# Draft models (speculative decoding)

Splash-m1 decodes speculatively: a small **draft** proposes several tokens, the full model verifies them in one pass, accepted tokens are kept and the rest discarded. Decoding is lossless — outputs are exactly the target model's — so a draft only ever affects **speed**, through how many of its proposed tokens the target accepts. This page records the current draft, its measured acceptance per model, and whether a per-model draft is worth building. It is a study; nothing here downloads or builds weights.

## The current draft

All three models share one draft: **`incoai/Qwen3.8-Flash-Next-DFlash2`** (`<assets>/Models/Shared/Qwen3.8-Flash-Next-DFlash2`, 1.2 GB BF16, ≈0.49 GiB resident in Splash's plan).

DFlash2 is not a standalone language model. It is a **feature-conditioned block-diffusion drafter**: it reads the *target model's own hidden states* at a fixed set of layers — `target_layer_ids = [5, 15, 25, 35, 45]` — and predicts a whole block of tokens in one pass, with a lightweight selector tracing one path through the candidates. Because it conditions on the target's features rather than running an independent model, what it needs from a target is:

* **the same architecture and layer layout** (so layers 5/15/25/35/45 mean the same thing), and
* **the same tokenizer and vocabulary** (248,320 tokens; `tie_word_embeddings` false), and
* **a hidden-state distribution at those layers close to what it was trained on** (the base `Qwen/Qwen3.8-Flash-Next`).

The first two are exact for all three DwarfStar models. The third is where a fine-tune can drift.

## Measured acceptance

One coding prompt per model, measured live from Splash `/status` (`drafted` vs `accepted` draft tokens) on 2026-11-13:

| Model | Reasoning | Draft acceptance | Decode | Notes |
|---|---|---:|---:|---|
| Scope | LOW | **73 %** (312 / 427) | 58.9 tok/s | base model — matched by construction |
| Cyber | LOW | **70 %** (287 / 408) | 54.5 tok/s | CYBERSECURITY fine-tune of the base |
| Swift | MEDIUM | **61 %** (288 / 474) | 48.1 tok/s | Swift 1.5 fine-tune; MEDIUM drafts more and reasons more |

Acceptance varies with task and reasoning level (the Models page shows a decode *range* for this reason), but the picture is consistent: every model accepts the majority of drafted tokens, and the two fine-tunes are close to the base. There is no evidence of a mismatched draft.

## Per model

### Scope — already the matched draft
Scope *is* the published base model (quantized to GSQ-RCO Q2_0). DFlash2 was trained against this exact family, so it is the matched draft by construction. 73 % acceptance, the highest of the three. **Recommendation: keep. Nothing to build.**

### Swift — matched enough; no per-model draft warranted
Swift is a fine-tune of the same base: identical architecture, identical tokenizer (after the `qwen2` tokenizer-profile patch), and the exact layers DFlash2 taps are unchanged. Fine-tuning shifts the hidden-state distribution somewhat, which is why MEDIUM acceptance (61 %) sits below Scope's — though MEDIUM reasoning, which drafts more aggressively and produces more varied tokens, accounts for much of that gap. A **Swift-specific DFlash2** (trained on Swift's own features) would recover some acceptance, but it would cost another ~1.2 GB on disk and ~0.49 GiB resident, require the DFlash training pipeline and a GPU, and only pays back if acceptance is materially depressed. At 61 % it is not. **Recommendation: keep the shared draft. Revisit only if a measured LOW-reasoning acceptance falls well below Scope's.**

### Cyber — matched enough; a Cyber draft is the only candidate with any case, and a weak one
Cyber is also a same-architecture, same-tokenizer fine-tune, and it accepts 70 % — essentially Scope's level — despite its 2-bit searched-Q2_0 experts and BF16 trunk. The draft reads BF16 captured features, not the quantized weights, so the aggressive expert quantization does not break it. A **Cyber-specific draft** could in principle lift acceptance a few points while preserving Cyber's identity (the draft never contributes tokens the target rejects, so it cannot change behaviour). But at 70 % the expected wall-time gain is small, and building it needs Cyber's own feature capture plus the training pipeline. **Recommendation: keep the shared draft. A Cyber draft is the only one worth even considering later, and only if a future workload shows acceptance dropping.**

## Is a matched-draft phase worth it?

**No, not at current acceptance.** The shared DFlash2 draft already delivers 61–73 % acceptance across all three models and a 1.3–1.8× decode speed-up over autoregressive (the upstream DFlash2 numbers, consistent with what is measured here). A per-model draft would add disk and resident memory per model and a training pipeline, for a few points of acceptance at best. The decision rule for a future phase: build a per-model DFlash2 **only if** a representative workload shows that model's acceptance falling well below Scope's base-model level (say, under ~50 %) at the same reasoning effort. Until then, one shared draft is the right trade.

No model artifact is recommended for download now. Nothing on this page was built, converted, or substituted — a draft is never swapped tensor-for-tensor, since DFlash2 is trained as a whole against a target's features.
