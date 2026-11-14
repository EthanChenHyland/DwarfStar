# Storage and the filesystem

DwarfStar keeps code in Git and everything else under five fixed roots. This page is the map: where things live, how much they take, what the 2026-11 consolidation reclaimed, and the two tools that report and clean disk safely.

## Canonical roots

| Root | Default | Holds | In Git |
|---|---|---|---|
| CODE | `~/Developer/DwarfStar` | Repository: catalog, service, agent, UI, tests, patches, docs | yes |
| ASSETS | `~/AI/DwarfStar` | `Models/`, `Runtimes/`, `Tools/`, `Sources/`, `Manifests/`, `Archive/` | no |
| DATA | `~/Library/Application Support/DwarfStar` | `chats/`, `config/`, `instances/`, `state/`, `telemetry/`, `runtime/splash-home`, `backups/` | no |
| CACHE | `~/Library/Caches/DwarfStar` | prefix/KV spill (`splash-kv`), `scratch/`, agent browser cache, `metal.json` | no |
| LOGS | `~/Library/Logs/DwarfStar` | `lifecycle.jsonl`, per-model server logs, `archive/` | no |

The installed app is `~/Applications/DwarfStar Workstation.app` (it records only the repository path; no data). Each root reads from its env var, then its `config/local.json` key, then the default (`src/dwarfstar/paths.py`).

## Current disk use

Measured with `bin/dwarfstar storage` (M1 Max, 2026-11-13):

| Category | Size |
|---|---:|
| Production models (3 models + shared draft/projector) | 195.06 GiB |
| Production runtime (Splash-m1 build) | 0.32 GiB |
| User data (chats, settings, state, telemetry) | 0.09 GiB |
| Cache (recreatable) | 0.66 GiB |
| Sources (Splash + model provenance) | 0.69 GiB |
| Manifests and evidence | 0.15 GiB |
| Tools (agent tool versions) | 0.40 GiB |
| Archive (retired installations, provenance) | 17.98 GiB |
| **Total** | **≈215.3 GiB** |
| Unreferenced large files | none |

The ten largest files are the six split-GGUF model parts (40.87, 35.04, 35.04, 26.82, 26.82, 26.82 GiB), Cyber's F32 vision projector (1.67 GiB), the DFlash2 draft (1.12 GiB), the shared BF16 projector (0.85 GiB) and one preserved-history event log in the archive (0.43 GiB).

## The 2026-11 consolidation

The product was moved off the old `~/local-ai-setup` tree onto the five roots above, duplicate and native-only artifacts were retired, and caches and scratch were cleaned — all with SHA verification and an inventory kept (`<assets>/Manifests/storage/`). **311,726,134,212 bytes (≈290.3 GiB) were reclaimed**; free space went from ≈308.9 GB to ≈619.6 GB. No model data was deleted carelessly: anything large that could not be classified with confidence was kept, and the migration receipts record every move. Provenance (pinned sources, preserved history, legacy installations) is retained under `Archive/`, which is why the archive is ~18 GiB.

## Reporting and cleaning

Two commands, both read-only or dry-run by default:

```bash
bin/dwarfstar storage                 # bytes per category, unreferenced large files, the largest items
bin/dwarfstar storage --empty-chat-trash [--apply]   # purge chats already deleted in the Workstation
bin/dwarfstar cache status            # what the cache root holds
bin/dwarfstar cache clean [--apply]   # remove recreatable cache contents (dry run without --apply)
bin/dwarfstar cache clean --browsers --apply         # also the agent's browser cache (re-downloads ~400 MB)
```

Nothing in these touches production models, the runtime, chats (except an explicit `--empty-chat-trash`), settings, manifests, sources, the archive or logs.

## Backup and restore

Chats, settings and provenance are small and irreplaceable; everything else can be rebuilt or downloaded again.

```bash
bin/dwarfstar backup                        # ~/AI/DwarfStar/Backups/DwarfStar-Backup-<UTC>.zip (readable by you only)
bin/dwarfstar restore <backup.zip>          # validate it and show what would change; nothing is written
bin/dwarfstar restore <backup.zip> --apply  # restore: the current state is backed up first, then merged, migrated and checked
```

A backup holds every chat record, the agent session files that are the canonical transcript of shared chats, files the agent wrote in a chat's workspace, the approval mode, the Workstation selection, the optional `config/local.json`, and the catalog's runtime and model hashes as provenance. It leaves out model weights, runtimes, caches, logs, telemetry, deleted chats (unless `--include-trash`), the agent's scratch and databases, and `permission-runtime.json`, which describes this machine's agent bundle (run `bin/dwarfstar agent-patch --apply` after restoring elsewhere). Restore checks the format, every path and every checksum, refuses a backup from a newer DwarfStar, never deletes anything the backup does not contain, never overwrites an existing `config/local.json`, and refuses while the Workstation is answering a chat. The pre-restore state goes to `<data>/backups/pre-restore/`.

Persistent records carry a schema version; `bin/dwarfstar migrate` (dry run) and `--apply` bring them current, backing up each original to `<data>/backups/migrations/` first. The service migrates at start.

## External Swift BF16 backup

Swift's BF16 rebuild source is an **external, off-SSD backup**. It is **not mounted** and is intentionally **not copied onto the internal disk** — production does not need it (Swift runs from its GSQ-RCO Q2_0 files like the others). It exists only to reconstruct Swift's weights from scratch if ever required. Do not mount, copy or delete it as part of routine work.

## The retired `~/local-ai-setup` tree

The old production tree is retired. After the consolidation it holds only a regenerable `.DS_Store`; it is kept empty as a convenience anchor and can be removed with `rmdir ~/local-ai-setup` once nothing is working in it. Nothing in the product points at it.
