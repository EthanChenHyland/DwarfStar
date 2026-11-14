# Reproducibility

DwarfStar is reproducible in four layers: **code → runtime build → model assets → user data**. Each is pinned or regenerable; nothing large lives in Git.

## 1. Code

The repository (`~/Developer/DwarfStar`) holds the catalog, service, agent, UI, tests, patches and docs. The production baseline hashes every tracked file; `bin/dwarfstar seal --verify` checks the working tree against the seal and names any file that differs (product differences fail it; documentation and tests are notes).

## 2. Runtime build

One runtime for every model, pinned in `config/runtimes.json`. Production is the advanced-telemetry build **ds2** (`splash-m1-1.6.1-ds2-telemetry`); ds1 is kept as the rollback (docs/ADVANCED-TELEMETRY.md):

| Item | Pin |
|---|---|
| Source | [paperniuk/splash](https://github.com/paperniuk/splash) tag `1.6.1-m1`, commit `5312c54` |
| Patches | `0001-qwen2-tokenizer-profile`, `0002-exact-bf16-planes`, and for ds2 `telemetry/0003a`, `0003b`, `0004` |
| Engine (ds2) | SHA-256 `7c8d51c6…` (ds1 engine `75bc297f…`) |
| Shaders | `splash.metallib` `694abdf2…`, `host-step.metallib` `8e177c75…` |
| Metadata adapter | `install/gguf.py` `9fe87539…` |

Build it with `scripts/backends/build_splash.sh … --telemetry` for ds2 (omit `--telemetry` for ds1) (Xcode 26.6 + its Metal toolchain, `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer`); the result goes to `<assets>/Runtimes/splash-m1-1.6.1-ds1/`. The controller refuses to start a runtime whose engine, shader or adapter hashes do not match the pin. The retired native ds4 runtime is not built or installed; `docs/NATIVE-RETIREMENT.md` says how to reconstruct it from git for comparison.

## 3. Model assets

Weights are not in Git. They live under `<assets>/Models/` and are pinned by source revision and by bytes + SHA-256 per file:

| Model | Source | Revision |
|---|---|---|
| Scope | `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF` | `ed59f920…` (published GGUF, byte for byte) |
| Swift | `ukisai/Swift1.5-Qwen3.8-Flash-Next` | `0bd4fe22…` |
| Cyber | `dealignai/Qwen3.8-Flash-Next-CYBERSECURITY-NVFP4` | `15ef113c…` (converted — see `docs/CYBER-CONVERSION.md`) |
| Draft (shared) | `incoai/Qwen3.8-Flash-Next-DFlash2` | DFlash2 draft model for the whole family |

`bin/dwarfstar assets --hash` re-hashes every main, PLE, draft and vision file against the catalog. Cyber's conversion toolchain lives with its original installation, not in this repository; the external Swift BF16 rebuild source is an off-SSD backup, intentionally not copied onto the internal disk (`docs/STORAGE.md`).

## 4. User data

Chats, settings, generated instance config, runtime state and telemetry sessions live under `<data>` (`~/Library/Application Support/DwarfStar`); caches under `<cache>`; logs under `<logs>`. All of it is per-machine and either regenerable (`dwarfstar generate` rewrites instance config from the catalog) or recreatable (caches). None is in Git.

## Roots

The five roots default to standard macOS locations and are each overridable in `config/local.json`:

| Root | Env var / `local.json` key | Default |
|---|---|---|
| CODE | — | the repository checkout |
| ASSETS | `DWARFSTAR_ASSETS` / `assets_root` | `~/AI/DwarfStar` |
| DATA | `DWARFSTAR_DATA` / `data_root` | `~/Library/Application Support/DwarfStar` |
| CACHE | `DWARFSTAR_CACHE` / `cache_root` | `~/Library/Caches/DwarfStar` |
| LOGS | `DWARFSTAR_LOGS` / `logs_root` | `~/Library/Logs/DwarfStar` |

## Machine prerequisites

DwarfStar does not install these; `bin/dwarfstar doctor` checks each.

| Prerequisite | Version | Why |
|---|---|---|
| Apple Silicon Mac, macOS | 26.4+ (validated: M1 Max, 64 GiB, macOS 26.5.2) | Splash-m1's Metal kernels and the Metal working-set limit |
| Python | 3.10+ (Homebrew 3.12 used) | the CLI, service and tests; standard library only |
| Node | 22.15+ | agent tools (`agent-tools/`) |
| Bun | 1.x | runs the Oh My Pi agent |
| Oh My Pi agent | **18.4.5** (`bun add -g @oh-my-pi/pi-coding-agent@18.4.5`) | the ACP agent; `bin/dwarfstar agent-patch --apply` applies DwarfStar's approval scoping to exactly this version |
| Xcode command-line tools | — | `swiftc` for the Workstation app |
| Xcode 26.6 + Metal toolchain | — | only to rebuild the runtime |
| Zed, KimiCU | optional | editor integration; computer-use tool |

## The agent approval patch

The approval presets (Workspace Auto, Full Auto) are enforced partly inside the Oh My Pi agent bundle. The edits are four exact snippet replacements recorded in `acp/omp-patch.json`, applied by `bin/dwarfstar agent-patch --apply` (`--revert` restores the stock snippets). They act only under the environment `bin/dwarfstar-acp` sets; other uses of the agent behave as upstream. The command writes `permission-runtime.json` in the data root, and the launcher refuses an agent bundle that no longer matches it, so an agent update cannot silently drop the scoping. Before 2026-11 this patch existed only on the development machine, and its Full Auto path imported a file from the retired `~/local-ai-setup` tree; `agent-patch --apply` migrates such a bundle.

## A second checkout

A clean clone (for testing, or a second working copy) must not share the installed data: give it its own data, cache and logs roots, and a free service and telemetry port, while it may read the canonical assets root:

```bash
export DWARFSTAR_DATA=/tmp/ds/data DWARFSTAR_CACHE=/tmp/ds/cache DWARFSTAR_LOGS=/tmp/ds/logs DWARFSTAR_TELEMETRY_PORT=8049
bin/dwarfstar generate && bin/dwarfstar doctor && bin/dwarfstar serve --port 8048
```

Only one model runs at a time on a machine (ports 8030–8032), whichever checkout starts it.

## Rebuilding on another machine

1. Install the machine prerequisites (above) and clone the repository. `config/local.json` is optional: the roots default to the standard locations.
2. Place the model files under `<assets>/Models/<Model>/` with the catalog's names; `bin/dwarfstar assets --hash` verifies them.
3. Build the runtime (step 2 above) into `<assets>/Runtimes/splash-m1-1.6.1-ds1/`; the controller checks its hashes.
4. `bin/dwarfstar generate`, `bin/dwarfstar agent-patch --apply`, `scripts/validate/check.sh`, then build the app (`docs/WORKSTATION.md`) and `bin/dwarfstar zed --apply`.
5. `bin/dwarfstar doctor` should report every check ready; `bin/dwarfstar seal --verify` should pass.

## Evidence index

`research/benchmark-index.json` is generated by `dwarfstar bench-index` from the canonical reports and the evidence under `<assets>/Manifests/evidence`. `--check` verifies it is reproducible from those inputs; its paths are relative to the evidence root, so it carries no machine paths.
