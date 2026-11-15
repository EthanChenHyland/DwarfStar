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

`scripts/backends/build_splash.sh` builds Splash-m1 from the pinned `1.6.1-m1` source with DwarfStar's patches (`backends/splash-m1/patches/`) using Xcode 26.6's Metal toolchain: with `--telemetry` the production `splash-m1-1.6.1-ds2-telemetry`, without it the `splash-m1-1.6.1-ds1` rollback, into the output folder given (`<assets>/Runtimes/<runtime id>/`). `config/runtimes.json` pins the engine, shader and metadata-adapter SHA-256; the controller refuses anything that does not match. The native ds4 runtime is retired and not built; `docs/NATIVE-RETIREMENT.md` covers reconstructing it from git for comparison.

## Seal

```bash
bin/dwarfstar seal            # write the production baseline and seal (after committing)
bin/dwarfstar seal --verify   # seconds; add --hash-models to rehash every model file
```

A seal (schema 2) binds content, not history: the git tree hash and a hash of the product files (code, configuration, launchers, app, agent and runtime patches), the pinned runtime hashes, the model manifest (every file's size and SHA-256), the generated configuration, the persistent data schemas and the Workstation executable. Commit, branch and tag are metadata, so the same tree on another commit verifies. Documentation, tests, CI and research evidence are hashed in the baseline too, but a difference there is a **note**, not a failure, so a report committed after sealing does not break the seal. Verify names each changed, added or removed file. Schema 1 seals (before 1.0.0) are still read, with the same product/notes split taken from their baseline; a seal from a newer DwarfStar is refused.

## Releasing

```bash
bin/dwarfstar release-check   # no model is loaded; exit 1 if anything blocks a release
```

It checks a clean tree; the branch and an annotated `v<version>` tag at HEAD; that `pyproject.toml` and the package agree on the version; the production runtime's installed hashes against its pin; the model manifest; `doctor`; repository hygiene (no giant files, weights, secrets, home paths or chats); documentation links; that persistent data is at the current schemas; the seal (current schema, sealed at this tree, verifies); and the canonical roots. The order for a release: merge to `main`, `bin/dwarfstar seal` on the merge commit, `git tag -a v<version>`, `release-check` until it passes, push the tag, then a GitHub Release with the notes and known limitations (never model weights, backups or chats as assets).

**Branch protection (recommended for a solo repository).** On GitHub: Settings → Branches → Add branch ruleset (or classic rule) for `main`: require a pull request before merging (approvals: 0, so you can merge your own), require the three CI status checks to pass (`core (unit, catalog, generated config, migrations)`, `static and security (syntax, lint, JS, hygiene)`, `docs (links, known limitations)`; they appear in the picker after the workflow has run once) and the branch to be up to date, block force pushes and deletions. Leave "include administrators" off if you need an emergency path.
