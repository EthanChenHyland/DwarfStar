# Native runtime retirement and recovery

DwarfStar ran on the Paperniuk **native ds4** Metal runtime until 2026-11-13, then moved every model to **Splash-m1** (`splash-m1-1.6.1-ds1`), which is faster and leaner on all three with the same answers ([BACKENDS.md](BACKENDS.md)). The native runtime is **retired**: it is not installed, there is no backend selection, and the product carries one inference stack. Its behaviour stays reproducible from Git for comparison or audit. This page is how.

## What was removed from the working product

The retirement commit (`122e384`, `refactor(runtime): retire the native ds4 backend`) removed the native server command and installer, native log and memory-plan parsing, the native telemetry source and detail heartbeats, the hub's native log reducer, native preflight and diagnostics checks, the MTP sidecars, the `runtime_overrides`/backend blocks, the `native/` build tree and the scripts that only drove or measured native (`scripts/calibrate/`, `prefill_sweep.py`, `runtime_parity.py`, `tokenizer_parity.py`, `native_metrics.py`). `config/runtimes.json` now pins only Splash and records the retirement.

## Recovery points

| Ref | What it is |
|---|---|
| `rollback/native-default-20261113` | The whole product with native as the default runtime, installed and pinned. Proven by starting Scope and Cyber on `ds4-server` and getting the same answers before retirement. |
| `checkpoint/splash-default-20261113` | The Splash-default checkpoint, immediately after promotion. |
| commit `122e384^` | The last commit before native was removed: `native/`, the native scripts and the backend-selection code are all present. |

## Rebuilding native for comparison

To run the native runtime again (for an A/B, not for production):

1. Check out the rollback tag into a worktree: `git worktree add ../DwarfStar-native rollback/native-default-20261113`.
2. In that tree, `native/apply_hooks.py` with `native/patches/` turns the pinned ds4 checkout (`m1-flash-next` @ `6ea59815…`) into the `ds4-unified` build; `config/runtimes.json` there pins each build's binary SHA-256 and the controller refuses a mismatch.
3. Place the model files; native loads each model's **native main** (not the Splash split layout) plus its PLE, MTP sidecar and projector.
4. `bin/dwarfstar generate`, then start a model. Use `scripts/backends/splash_equivalence.py` (present on the current branch) to compare native and Splash answers on the same prompts.

## Reconstructing the native model representations

The current catalog carries only Splash's production assets. The native ones are reconstructable:

* **Native main GGUF** — the Splash part-1 file is the native main with the split-GGUF metadata keys added; `scripts/backends/repack_split_gguf.py` performs the lossless repack, so removing the split keys (or rebuilding from the pinned source GGUF) returns the native main. Tensor payloads are identical.
* **MTP sidecars** — regenerated from the pinned model sources with the native build tooling; they are not needed by Splash (which uses the DFlash2 draft instead) and are not kept resident.
* **Cyber** — its conversion toolchain lives with the original Cyber installation; `docs/CYBER-CONVERSION.md` covers it.

## Why keep it recoverable rather than installed

A second active runtime doubles the surface that must be validated, memory-planned and kept pinned, for a backend that lost the comparison on every model. Keeping native in Git gives the audit trail and the ability to re-run the A/B without carrying a second production stack. The Visualizer still **replays** telemetry recorded on the native runtime (router, MTP and PLE rows), so historical sessions keep their full detail even though live sessions run on Splash.
