# Post-release audit (2026-11-15)

**Headline: there is no release yet.** This audit was asked for after release, but `main` is still `57627e0` (the ds1 convergence merge). The hardening work and ds2 are in **PR #3** (`release/hardening-v1`), which is open, has green CI and is not merged. No `v1.0.0` tag and no GitHub Release exist.

The installed product runs from the PR branch. So the most important finding is a gap, not a defect: **GitHub's `main` lacks the Workspace Auto security fix that this Mac already runs.**

Beyond that, the audit found no correctness or security defect. It found and fixed:
- stale documentation (five docs called ds1 the production runtime);
- misleading Models-page entries;
- a dead API route;
- a lock file captured in backups;
- the app's bundle version;
- missing version surfacing;
- missing `main` protection.

## Actual state at the start

| Item | State |
|---|---|
| Checkout | `~/Developer/DwarfStar`, branch `release/hardening-v1` @ `76b3813`, clean, in sync with origin |
| `origin/main` | `57627e0`, unprotected (now protected; see below) |
| PRs | #1 merged (convergence). #2 open (ds2), fully contained in #3. #3 open (hardening + ds2), CI green. |
| Release | none; no `v1.0.0` tag |
| Seal | schema 1, sealed 2026-11-14; verify fails by design (lists the PR's product files; re-seal after merge) |
| Doctor | 27 ready, 0 optional missing, 0 blocking |
| Running | Workstation service only (`127.0.0.1:8038`, UDP `127.0.0.1:8039`); no model server; no stray Splash, conversion or benchmark processes |
| Installed app | `~/Applications/DwarfStar Workstation.app`, built 2026-11-13. It predates the Save-panel download fix and reported bundle version "3.0" (fixed in the build script; rebuild after merge). |

## GitHub

**Branches**

| Branch | Class | Evidence | Action |
|---|---|---|---|
| `main` | — | default branch | kept; **now protected** |
| `release/hardening-v1` | B (active) | PR #3; 18 commits not in `main` | keep. Auto-deletes on merge (setting enabled). |
| `experiment/splash-advanced-telemetry` | B | PR #2; 4 commits, all contained in `release/hardening-v1` | after #3 merges, close PR #2 and delete the branch |
| `perf/backend-and-workstation` | C | PR #1 merged; 0 commits outside `main` | **deleted** (remote and local) |

**Tags** (all annotated and pushed; none deleted)

| Tag | Target | Class | Purpose |
|---|---|---|---|
| `rollback/native-default-20261113` | `9b70ac6` | A | last state with native as default; recovery of native behaviour |
| `checkpoint/splash-default-20261113` | `e7ec543` | A | Splash default, before native removal |
| `checkpoint/pre-advanced-telemetry-20261113` | `57627e0` | A | the ds1 production state; becomes the rollback point once ds2 merges |

No temporary, duplicate or lightweight tags exist. `v1.0.0` is pending.

**PRs.** All three PRs were checked:
- #1 is merged as `57627e0`; its branch commits are reachable from `main`.
- #2's four commits are reachable from #3's branch, so #2 holds no unique work.
- #3 holds all the hardening work plus #2.
- No work exists only on an old branch. There are no closed-unmerged or draft PRs, and no issues.

**Actions**
- One workflow, `ci`, with jobs `core` (macOS), `static and security` and `docs`.
- The latest run passed in 16 s; on macOS it ran all 138 tests, including the live seatbelt test.
- It needs no weights and uses only temp roots, and it has read-only `GITHUB_TOKEN` permission.
- The earlier cancelled run was a retired runner image (`macos-14`), already fixed.
- Actions: `checkout@v4`, `setup-python@v5`, `setup-node@v4`. These are current majors; no change needed.
- Artifacts: 0. Caches: 2 dependency caches, 23 MB, which GitHub auto-evicts. Log retention is 90 days, which is fine.

**Branch protection (applied).** `main` now has:
- a pull request required, with 0 approvals (solo);
- the three CI checks required, with the branch up to date;
- force-push and deletion blocked;
- the same rules applied to admins (switch off in Settings for an emergency).

The repository also now auto-deletes branches on merge. Wiki and Projects are enabled but unused; that is cosmetic, so I left it.

**Content and history**
- **Tracked files:** a hygiene scan of all files found 0 problems.
- **History:** 205 paths have ever been committed. None of them are weights, logs, databases, chats, backups, keys or local config.
- **Secrets:** the only secret-like strings in all of history are the synthetic test key. The only private IP is the test's `10.0.0.5`.
- **Home paths:** none appear in history.
- **Identities:** commits use one identity (plus GitHub's own merge commit), with no AI attribution.

**Size:** `.git` is 14 MB; the largest blob ever committed is a 416 KB docs screenshot. The checkout is 68 MB, 50 MB of which is untracked `node_modules`. No history rewrite is warranted.

## Mac

**Canonical layout** (all present; no active dependence on anything else)

| Root | Size | Contents |
|---|---:|---|
| `~/Developer/DwarfStar` | 0.07 GiB | source (one checkout; no worktrees) |
| `~/AI/DwarfStar` | 215.85 GiB | Models 195.06, Archive 18.93, Runtimes 0.65, Sources 0.64, Tools 0.41, Manifests 0.16, Backups (11 KB) |
| `~/Library/Application Support/DwarfStar` | 0.12 GiB | one chat (59 MB of it recreatable agent scratch), config, state, instances, telemetry sessions (30 files, 213 KB) |
| `~/Library/Caches/DwarfStar` | 0.66 GiB | agent browser cache (active) and the Metal measurement |
| `~/Library/Logs/DwarfStar` | < 1 MB | rotated |
| `~/Applications/DwarfStar Workstation.app` | 0.2 MB | the only app bundle |

**Production models.**
- There is exactly one canonical copy per model: Scope, Swift and Cyber two split parts each; the shared DFlash2 draft; the shared BF16 projector (Scope and Swift); and Cyber's F32 projector.
- Sizes match the catalog (`doctor`), and the storage report shows 0 bytes unreferenced.
- No other representation of these models exists inside the DwarfStar roots.

**Runtimes**
- `splash-m1-1.6.1-ds2-telemetry` (322 MB, production) and `…-ds1` (341 MB, rollback).
- **Recommendation: keep ds1.** It is 0.17 % of model storage and gives an instant rollback while ds2 is new.
- The build inputs `Sources/splash-m1/{splash-src, 1.6.1-m1-release, splash-telemetry}` are kept for reproducibility.

**Native leftovers.** No native binaries, build trees, logs, launchers or plists remain. Native exists only:
- in the Git tags;
- in `config/runtimes.json`'s historical note;
- in Visualizer code that replays native sessions. No native session files remain on disk (all 30 recorded sessions are Splash), so that code is unreachable in practice; see improvements.

**Archive** (17.98 GiB, intentionally kept provenance)
- It holds byte-identical repeats inside the preserved history snapshot: a 0.43 GiB log 4 times and a 0.32 GiB log 5 times, about 2.6 GiB in all.
- They faithfully represent what each historical backup held. Left as is.

**Tools** (`Tools/v6`, `v7`, `document-tools`). These are active: the machine-local `config/local.json` adds them to the agent's tool and read paths. They are not part of a clean clone, which is by design (local extras).

**Other locations**
- `~/local-ai-setup`: gone (removed during the audit).
- LaunchAgents, login items and shell rc files: no DwarfStar entries.
- Zed: exactly the seven `bin/dwarfstar-acp --profile …` entries on the canonical path, with no old repo or native entries. 18 historical threads, 10 with session files, were left untouched.
- VS Code: one recent-workspace entry for `local-ai-setup`. This is editor history, left alone.
- `~/Downloads`: nothing DwarfStar-related.
- Trash: not readable from here (macOS privacy).
- `~/Documents/Codex/` (2.5 GiB): work folders from another coding agent, including the Swift 1.5 prebuild gate (2.1 GiB). One of its projector copies is byte-identical to the canonical shared projector (0.85 GiB). Classified D (your documents, outside DwarfStar), so not deleted.
- The external Swift BF16 backup volume is not mounted. As documented, it is off-SSD and intentionally not copied internally.

**Chats, trash, logs, telemetry, backups**
- **Chats:** one real chat (Cyber LOW, 2026-11-12), kept. The trash is empty. Trash is only emptied by an explicit `storage --empty-chat-trash`. There is no automatic purge, which is the right default.
- **Logs:** server logs keep the newest 10; the lifecycle journal rotates at 5 MB with 3 kept. There are no crash logs or native logs.
- **Telemetry:** sessions are capped at 30 files of 20,000 events. The ds2 ring is a fixed 4,096 records. Visualizer history is in-memory only, under 2 MiB. Every telemetry string is an enum, id or name.
- **Backups:** one real backup, 11 KB: 1 chat, 10 files, schema 2, checksummed. It also contained a transient session lock file (now excluded). There are no test backups.
- **Migration backups:** kept under `<data>/backups/`.

**Cleanup receipt**

| Item | Bytes | Why safe |
|---|---:|---|
| `~/Library/Caches/DwarfStar/telemetry-experiment/` | 40,429,594 | Splash home folders for the finished A/B experiment; the cache tool lists it as disposable and `telemetry_ab.py` recreates it |
| 10 × `.DS_Store` in `Archive/` | ~60 KB | Finder metadata |
| Test clones in the previous session's temporary scratch (two clean rooms, the GitHub clone, the first-run instance, test builds) | ~200 MB | clean, with no unpushed commits; temporary space |
| `perf/backend-and-workstation` branch | — | merged; 0 unique commits |

Free disk: 618.47 GB before, 618.36 GB after. The difference is noise from the smoke run and system activity; storage was not a goal.

## Product health

- **Catalog and runtime pin:** three models, seven profiles, 131,072 context, INT8 KV. The runtime pin matches the installed ds2 hashes, and the generated configuration has no drift.
- **Smoke test:** Scope LOW loaded in 26 s and wrote about 220 tokens of predictable code at about 83 tok/s with 88 % draft acceptance. The ds2 ring recorded 31 cycles with 0 dropped.
  - This is consistent with, and above, the documented 35–67 tok/s mixed-workload range, as expected for easy text.
  - The prefix-cache hit rate read 0 on a prompt likely too short to fill a cache block, so it neither confirms nor refutes the cache. The earlier backend A/B validated it.
- **Telemetry overhead:** not re-measured. There has been no runtime change since the measured −0.11 %.
- **Sessions:** discovery of shared Zed sessions works (the clean clone found the same 18 threads).
- **Security:**
  - all listeners are on loopback, and the LAN address does not answer;
  - foreign Host and Origin get 403;
  - Workspace Auto cannot reach the service;
  - the fresh-install default is Workspace Auto;
  - diagnostics redact `~`.

  All of this is in PR #3, not yet on `main`.
- **Clean clone:** verified again from GitHub, with the identical tree. Setup, `doctor` and 138 tests pass. No hidden dependency remains except the documented optional local tool paths.

## Fixes made during the audit

1. **Docs said ds1 was the production runtime.** README, ARCHITECTURE, BACKENDS, DEVELOPMENT and REPRODUCIBILITY (two places) now name ds2 as production with ds1 as the rollback. REPRODUCIBILITY no longer says a ds2 build lands in the ds1 folder.
2. **Models page.** Scope's three pre-Splash measurements are now labelled "(retired native runtime)". One read "Native adaptive MTP +5.34 % decode" next to current Splash numbers.
3. **Check count.** WORKSTATION.md said "26 ready checks"; it now says every check (27 on a complete installation).
4. **App bundle version.** The bundle reported "3.0". `build.py` now stamps the product version (1.0.0).
5. **Version surfacing.**
   - `/api/diagnostics` carries `version` (DwarfStar version, git revision, runtime).
   - The Diagnostics header shows it, so Copy diagnostics includes it.
   - `doctor` prints it first.
6. **Backups.** They no longer include transient agent session lock files (test added).
7. **Dead route.** `/api/benchmarks` had no caller since the Benchmarks tab was removed; it is deleted.
8. **Update procedure.** `docs/REPRODUCIBILITY.md` → "Updating an installation" covers backup, stop, checkout, generate, the patch check, migrate, app rebuild, `doctor`, re-seal and how to go back.
9. **GitHub settings.** `main` is protected and merged branches auto-delete.

## Improvements

**Finish the 1.0.0 release.** Not engineering; this is what keeps the first finding open.
- Problem: GitHub `main` is ds1 without the security fix; there is no tag or Release; the seal fails on this Mac until re-sealed.
- Evidence: PR #3 is open; `origin/main` is `57627e0`.
- Benefit: what GitHub calls the product matches what runs here.
- Complexity: Low. Risk: Low (CI is green, the clean clone passes).
- Priority: P0. Recommendation: **Do now** (after your review):
  1. merge #3;
  2. close #2;
  3. `bin/dwarfstar seal` on the merge commit;
  4. `git tag -a v1.0.0`;
  5. `release-check`;
  6. push the tag;
  7. publish the Release from `CHANGELOG.md`;
  8. rebuild the app.

**`dwarfstar update`**
- Problem: updating an installation is ten manual steps (now documented), where a missed `generate`, app rebuild or re-seal leaves the install inconsistent.
- Evidence: there was no update procedure at all before this audit; the app version had drifted.
- Benefit: one command runs the safe steps with a dry run; irreversible parts (checkout) need confirmation.
- Complexity: Medium. Risk: Low–Medium.
- Priority: P1. Recommendation: **Later.** Do it before the second release, when there is a real update to exercise it; the documented procedure suffices until then.

**Release script**
- Problem: tagging and publishing are manual.
- Evidence: the release order in DEVELOPMENT.md has seven steps.
- Benefit: fewer mistakes when releasing.
- Complexity: Low. Risk: Low (confirm before each irreversible step).
- Priority: P2. Recommendation: Later, together with `update`.

**Remove native-replay paths from the Visualizer**
- Problem: the router grid, routing heatmap, PLE read pulses and native request rows only draw data from native sessions, and none remain on disk.
- Evidence: 0 of 30 telemetry sessions carry router rows; Splash never emits them.
- Benefit: simpler code and less "not reported" chrome.
- Complexity: Medium (visualizer surgery). Risk: Low.
- Priority: P2. Recommendation: Later, only if the Visualizer is touched for another reason.

**Support bundle**
- Problem: none in practice. `doctor` and Copy diagnostics now carry the version, redact home paths and exclude chats.
- Priority: **Not worth it.**

**Doctor self-repair**
- Problem: the remedies are already exact commands. The only deterministic fixes are `generate` and `agent-patch --apply`.
- Priority: **Not worth it** now; a "fix everything" button is the wrong design.

**Deduplicate the Archive**
- About 2.6 GiB of identical logs in preserved history, against 618 GB free.
- Priority: **Not worth it.**

**Not worth doing without new evidence:** another backend, native restoration, MLX, another filesystem migration, router telemetry needing GPU sync, new quantizations or matched drafts, new model families, cloud sync, accounts, a mobile app, a framework rewrite. None of the audit's evidence points at any of them.

**Single best next step:** finish the 1.0.0 release (P0 above).

**Is another engineering phase justified? No.** After the release, use DwarfStar. Revisit `dwarfstar update` when a second release is actually being prepared.
