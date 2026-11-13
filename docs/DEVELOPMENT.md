# Development

## Requirements

* macOS on Apple Silicon (developed on an M1 Max, 64 GiB)
* Python 3.10+ (3.12 used), Node 20+, Bun (for the agent), Xcode command-line tools (`swiftc`)
* `uvx` for lint (optional)

## Setup

```bash
cp config/local.example.json config/local.json   # set data_root
(cd agent-tools && npm ci)
# install the Oh My Pi agent CLI (`omp`) globally with Bun; generate links acp/vendor/omp to its sources
bin/dwarfstar generate                            # write per-model instance config and local links
bin/dwarfstar assets                              # verify model files against the catalog
```

## Cheap validation (no model is loaded)

```bash
scripts/validate/check.sh
```

Runs the unit tests (catalog and backends, lifecycle state machine, memory admission, telemetry hub, chats, crash recovery, approval and redaction policy), parses every Python file, lints, checks generated configuration against the catalog, checks the benchmark index is reproducible, and syntax-checks the JavaScript and TypeScript.

## Changing a model fact

Edit `config/models/<model>.json`, then:

```bash
bin/dwarfstar generate --model <model>
scripts/validate/check.sh
```

Never edit files under `<data>/instances/` by hand; they are generated.

## Runtime validation

`scripts/validate/production_validation.py` drives the product the way a user does (Workstation switch, `dwarfstar-acp`, telemetry stream). Each step writes one JSON record under `<data>/evidence/production-validation/steps/`; `summarize` produces `validation.json`, which `dwarfstar bench-index` picks up.

`scripts/validate/runtime_parity.py` compares the unified runtime with the reference build at temperature 0.

## Native runtime

`native/apply_hooks.py` and `native/patches/` turn the pinned ds4 checkout into the `ds4-unified` build. `config/runtimes.json` pins each build's binary SHA-256; the controller refuses a binary that does not match.

## Deferred research tooling

`scripts/calibrate/` (activation capture and a GPTQ-style Q2_0 encoder for a possible Cyber expert re-encode) is not used by the product and is not referenced by any runtime path. The runtime's calibration capture is compiled in but inactive unless `DS4_QWEN4_CALIB_DIR` is set.

## Seal

```bash
bin/dwarfstar seal            # write the production baseline and seal (after committing)
bin/dwarfstar seal --verify   # seconds; add --hash-models to rehash every model file
```
