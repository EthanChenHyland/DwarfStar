# Development

## Requirements

* macOS on Apple Silicon (developed on an M1 Max, 64 GiB)
* Python 3.10+ (3.12 used), Node 20+, Bun (for the agent), Xcode command-line tools (`swiftc`)
* `uvx` for lint (optional)

## Setup

```bash
cp config/local.example.json config/local.json   # optional: override any of the five roots
(cd agent-tools && npm ci)
# install the Oh My Pi agent CLI (`omp`) globally with Bun; generate links acp/vendor/omp to its sources
bin/dwarfstar generate                            # write per-model instance config and local links
bin/dwarfstar assets                              # verify model files against the catalog
```

## Cheap validation (no model is loaded)

```bash
scripts/validate/check.sh
```

Runs the unit tests (catalog, lifecycle state machine, memory admission, telemetry hub, chats, shared Zed sessions, storage, crash recovery, approval and redaction policy), parses every Python file, lints, checks generated configuration against the catalog, checks the benchmark index is reproducible, and syntax-checks the JavaScript and TypeScript.

## Changing a model fact

Edit `config/models/<model>.json`, then:

```bash
bin/dwarfstar generate --model <model>
scripts/validate/check.sh
```

Never edit files under `<data>/instances/` by hand; they are generated.

## Runtime validation

`scripts/validate/lifecycle_matrix.py` drives the Workstation lifecycle (start, switch, same-family effort change, cross-model, stop) through the service. `scripts/validate/storage_smoke.py` cold-starts each model, runs a tiny generation and vision check, resumes a chat's own session, runs a tool turn and stops — the migration and post-cleanup validation. `scripts/backends/splash_equivalence.py` runs the deterministic prompt set and the native-vs-Splash A/B. These load a model, so they are not part of `check.sh`.

## Building the runtime

`scripts/backends/build_splash.sh` builds Splash-m1 from the pinned `1.6.1-m1` source with DwarfStar's two patches (`backends/splash-m1/patches/`) using Xcode 26.6's Metal toolchain, into `<assets>/Runtimes/splash-m1-1.6.1-ds1/`. `config/runtimes.json` pins the engine, shader and metadata-adapter SHA-256; the controller refuses anything that does not match. The native ds4 runtime is retired and not built; `docs/NATIVE-RETIREMENT.md` covers reconstructing it from git for comparison.

## Seal

```bash
bin/dwarfstar seal            # write the production baseline and seal (after committing)
bin/dwarfstar seal --verify   # seconds; add --hash-models to rehash every model file
```
