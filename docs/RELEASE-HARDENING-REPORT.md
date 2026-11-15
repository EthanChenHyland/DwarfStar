# Release hardening and final reliability — report (1.0.0)

Branch `release/hardening-v1`, based on the ds2 experiment tip `09a6d91` (PR #2, not yet merged into `main`), so this branch carries the ds2 runtime commits as well. Inference was not changed: no runtime rebuild, no model change, no new backend, no engine instrumentation, no performance suite rerun. Commit identity Ethan Chen; no AI attribution.

## Repository

1. **Branch.** All work is on `release/hardening-v1`; nothing was developed on `main`. Twelve commits on top of the ds2 tip, each one concern: CI; reproducible setup; data versioning; backup/restore; security; app downloads; Visualizer history; setup guide; seal and release-check; release notes; this report.
2. **Base.** `origin/main` is still `57627e0` (ds1). PR #2 (ds2) is open and unmerged. This branch includes PR #2's four commits, so merging it into `main` also merges ds2.
3. **Not pushed.** Pushing the branch is refused because the GitHub CLI token lacks the `workflow` scope that `.github/workflows/ci.yml` requires, and there is no SSH key. One command fixes it: `gh auth refresh -h github.com -s workflow`.
4. **Hygiene.** `scripts/validate/repo_hygiene.py` scans every tracked file. It finds 0 problems across 188 files: no files over 2 MiB, no weights, archives or databases, no secrets, no home paths, no chats or backups. The single synthetic private key in a redaction test carries an explicit `hygiene: allow` marker.
5. **History.** No public history was rewritten. Every earlier tag is kept: `rollback/native-default-20261113`, `checkpoint/splash-default-20261113` and `checkpoint/pre-advanced-telemetry-20261113`.

## CI and reproducibility

6. **CI.** `.github/workflows/ci.yml` (GitHub Actions, no weights) runs three parallel jobs:
   - **core** (macOS, `macos-latest`; the pinned `macos-14` image is no longer offered): unit tests on temporary roots, catalog and generated configuration, and a benchmark-index structure check.
   - **static and security** (Ubuntu): AST parse, ruff 0.6.9, `node --check`, and repository hygiene.
   - **docs** (Ubuntu): links, anchors, and known-limitations validity.

   The workflow has not yet run on GitHub, because of the push block in item 3.
7. **Local equivalent.** `scripts/validate/check.sh` runs the same checks: 138 unit tests, plus hygiene and documentation-link steps that now fail the script instead of being swallowed by a pipe.
8. **Branch protection.** Recommended setup for a solo repository: require a pull request with 0 approvals, require the three CI checks to pass with the branch up to date, and block force-push and deletion. Exact UI steps are in `docs/DEVELOPMENT.md` → Releasing. These rules cannot be set until the workflow has run once.
9. **First clean-room run.** A fresh clone with temporary roots and canonical assets exposed six hidden dependencies:
   - `generate` did not create the logs root;
   - the agent approval patch existed only on this Mac;
   - that patch's Full Auto path imported a file from the retired `~/local-ai-setup` tree, a latent bug;
   - `permission-runtime.json` was never generated;
   - `config/local.example.json` pinned the production data root;
   - a second instance collided with production on UDP 8039.

   All six are fixed. The agent browser remains a documented, explicit step.
10. **Agent patch as data.** `acp/omp-patch.json` records four exact snippet edits for Oh My Pi 18.4.5. `bin/dwarfstar agent-patch [--apply|--revert]` applies or reverts them:
    - it classifies a bundle as stock, patched, legacy or unknown;
    - it backs up before writing;
    - it refuses a bundle it does not recognise;
    - it tolerates unrelated edits elsewhere in the file.

    Full Auto now imports this checkout's policy module through `DWARFSTAR_PERMISSION_MODULE`. Nine tests cover it.
11. **Second checkout.** A second checkout can run beside production using `DWARFSTAR_ASSETS/DATA/CACHE/LOGS`, `DWARFSTAR_TELEMETRY_PORT` and `DWARFSTAR_HTTP_PORT` (documented in `docs/REPRODUCIBILITY.md`).
12. **Second clean-room run.** A fresh clone of the final branch was set up by following the README Quick start exactly:
    - `npm ci`, `generate`, `agent-patch --apply` (it found the bundle already patched and made no write), `assets` (all files verified), `doctor` (0 blocking);
    - `check.sh` (138 tests), app build, and a service on port 8048;
    - the setup guide was offered; the fresh approval mode was Workspace Auto; a foreign Host and a foreign Origin POST both got 403; path traversal got 404;
    - migrate (nothing to migrate), backup, and `release-check`. The only release blocker was the seal, as expected, since it is re-sealed after the merge.

    - **Tiny Scope smoke** from the clean clone's own service and temp data roots: Scope LOW loaded in 55 s and answered "17 + 25" with `42`. The ds2 ring returned 10 decode cycles (52 drafted, 31 accepted, 0 dropped), with numeric fields only. Shared-session discovery found 18 Zed threads, 10 of them with session files. The model was then stopped and its port freed.

    I did not run `zed --apply`, so your Zed settings were left untouched. The run used a local clone rather than a GitHub clone, because of the push block in item 3.

## Data

13. **Schema versioning.** Every persistent record type now carries a `schema` field: chats (2), Workstation state (1), approval (1) and permission record (1). An ordered registry of pure, idempotent migrations (`src/dwarfstar/migrations.py`) brings older records forward.
14. **Migration safety.**
    - `bin/dwarfstar migrate` is a dry run by default. `--apply` copies every original to `<data>/backups/migrations/<UTC>/` first, writes atomically and reads each file back.
    - Malformed records, and records from a newer DwarfStar, are reported and left untouched.
    - The service migrates at start, so no code ever reads an old shape.
15. **What migrations keep.** Messages, roles, text, times, tool titles, agent sessions, pins, stars, tags, archive state, forks and Zed links all survive. The only thing removed is reasoning text that an earlier build may have left in a message.
16. **Fixtures.** Five synthetic fixtures (no real chats) cover every earlier shape: no schema, pre-organize, pre-Zed with reasoning, fork, and a Zed thread with an old workspace path. Twelve tests cover them.
17. **Real data.** Your real data was migrated (chat 1→2, state 0→1) with identical logical content. The originals are backed up.
18. **Rollback to an older build.** Builds before 1.0.0 accept only chat schema 1. `migrate --rollback` is lossless and was tested as a round trip. The stale-service case is documented: a service started before the migration kept old code and reported "Unsupported chat schema 2" until I restarted it during this phase.
19. **Backup.** `bin/dwarfstar backup` writes a mode-0600 zip containing:
    - a manifest with format, versions and schemas, plus a SHA-256 for every file;
    - chats, workspace files and agent sessions;
    - approval, state, `config/local.json` and the catalog.

    Recreatable caches, agent databases and the permission record are excluded. Default location: `~/AI/DwarfStar/Backups`.
20. **Restore.** `bin/dwarfstar restore` is a plan by default. The `--apply` path:
    - refuses to run while a chat is answering;
    - refuses damaged checksums, unsafe paths and newer schemas;
    - writes a safety backup of the current state;
    - stages the files and swaps them in atomically, merging so that nothing is deleted;
    - never overwrites `local.json`;
    - migrates, then checks that every restored chat loads.

    Five tests cover it. A plan against your latest real backup shows 0 chats to change and 1 state file that differs.

## Security

21. **Listeners.** The service (TCP 8038), the telemetry socket (UDP 8039) and every model server (`--host 127.0.0.1`) bind loopback only. A request to the LAN address on port 8038 fails to connect.
22. **Request guard.** Every request method (GET and POST; no others are defined) first checks that Host is `127.0.0.1` or `localhost`, and that any Origin is an http(s) loopback origin. That blocks DNS rebinding and other web pages; opaque `null` origins are refused. A structural test fails if a new request method is added without the guard.
23. **Static files.** Static files are resolved inside the web folder only. Traversal attempts, symlinks pointing out, folders and NUL bytes all get 404 (tested).
24. **Endpoint review.** I reviewed every endpoint:
    - diagnostics, chats, export, Zed threads, sessions (session ids are regex-validated), telemetry (proxied only to the catalog's own endpoint), reconcile, switch/stop/launch and permissions;
    - all are loopback-guarded;
    - mutations that can lose data (bulk delete, chat delete) need typed confirmations;
    - Full Auto needs `confirm: "full_auto"`.
25. **Self-promotion (found and fixed).** A Workspace Auto agent's sandbox allowed all outbound network, so a sandboxed tool could `curl` `POST /api/permissions` and switch the installation to Full Auto; I confirmed this live (HTTP 200 from inside the sandbox). The sandbox now denies the service port; the internet and model ports still work. A live seatbelt test, with a control run, proves the deny works.
26. **Approval setting.** The approval setting (`approval.json`) cannot be written from the sandbox, because writes are confined to the workspace (tested). Ask mode shows every tool call to you. Full Auto is already the maximum, so there is nothing to escalate to.
27. **Fresh-install default.** A fresh installation previously defaulted to Full Auto when `approval.json` was missing. It now starts in Workspace Auto. Full Auto is entered only through its confirmation, and an invalid setting fails closed. Your installation's explicit Full Auto choice is unchanged.
28. **Visibility and Zed.** Full Auto is visible: the composer, the Run page, Settings and the setup guide all show the current mode. Zed uses the same launcher, `bin/dwarfstar-acp`, so the same mode and sandbox apply to it.
29. **Telemetry payload.** I scanned the recorded telemetry sessions: the longest string field is 35 characters, and every string is an enum, id or name (no text). The ds2 ring is numeric. Tool names are regex-limited to 64 characters.
30. **Path redaction.** `/api/diagnostics` and `/api/setup` contain no `/Users/` paths; home is shown as `~`. Exported request JSON contains no paths. Fourteen security tests cover items 21–30.

## Visualizer

31. **Request history.** Advanced Live keeps the last 20 requests, named by profile and time (for example "Swift 1.5 MEDIUM · 11:22 PM"), never by content. Each row shows tokens generated, cycles, drafts accepted, decode time and tokens per second, and a running request is marked "generating".
32. **Replay.**
    - **View** draws one request on the canvas.
    - The scrubber walks its cycles. Acceptance shown is the cumulative value up to the playhead (cycle 65 showed 129/260 drafts), and the output stream advances with it.
    - Live recording continues in the background, and **Back to live** returns. **Pause** still works on the live stream.
33. **Export.** **Export JSON** writes format 1: totals, capabilities, a statement of how requests are segmented, and per-cycle numbers. It runs only on an explicit click. The filename uses local time and model, never content.
34. **Truthful segmentation.** The runtime reports cycles, not request ids, so a request here is one uninterrupted decode run, split after 400 ms without a cycle. The UI, the export and the known limitations all say so.
35. **Memory bounds.** Each request keeps its first 4,096 cycles in a fixed 96 KiB Float32Array, so 20 requests stay under 2 MiB. Totals keep counting past the cap.

    Stress test: 30 synthetic requests, one of them 10,000 cycles long. 20 rows were kept; the long request was capped at 4,096 cycles with exact totals; page heap was 4.6 MiB. Nothing is written to disk.
36. **App downloads (found and fixed).** The app had no download handling, so the existing chat Export links did nothing inside the app. Downloads now go through a Save panel. It compiles, and the app build and all view snapshots pass; I did not exercise the Save panel itself by hand.

## Setup

37. **Setup guide.** A new installation (setup never finished and no chats yet) opens the Setup guide once. It walks the `doctor` checks by area in green, yellow and red; Check again re-runs them. It then offers the approval presets (Full Auto keeps its confirmation), and Finish records `setup_completed`.
38. **Never downloads.** Remedies are commands with Copy buttons. For a missing model, the remedy now lists every expected file path and byte size. Nothing is installed or downloaded.
39. **Existing installations.** An installation already in use is never interrupted; your production install is not auto-opened, because it has chats. The guide remains reachable from Diagnostics → Setup guide and from the setup banner. Three tests cover it, and I checked it end to end on a throwaway instance (8 blockers shown, Ask set, Finish recorded, banner shown afterwards).

## Seal

40. **Schema 2.** The seal now binds content rather than history:
    - the git tree hash and a product-files hash;
    - runtime hashes, the model manifest and generated configuration;
    - the persistent data schemas and the Workstation executable.

    Commit, branch and tag are recorded as metadata only.
41. **Verify output.** Verify now names every changed, added and removed file. Product differences fail it. Differences in documentation, tests, CI or evidence are reported as notes, so a report committed after sealing no longer breaks the seal; that was the failure seen at the start of this phase.
42. **Compatibility.** Schema 1 seals are still read, with the same product/notes split taken from their baseline. Seals from a newer DwarfStar are refused. Eight tests cover this.
43. **Release check.** `bin/dwarfstar release-check` loads no model. It checks:
    - a clean tree and an annotated `v<version>` tag;
    - version consistency;
    - the runtime pin and the model manifest;
    - `doctor`, hygiene and documentation links;
    - data schemas, the seal at this tree, and canonical roots.

    On this branch today, everything passes except the tag (a warning) and the seal (expected: re-seal on the merged commit).

## Release

44. **Version.** The version is 1.0.0 in both `pyproject.toml` and the package. `CHANGELOG.md` has the release notes and serves as the GitHub Release body.
45. **Known limitations.** Five entries were added, for 17 in total:
    - request-history segmentation;
    - pre-1.0 chat schema;
    - the pinned agent-patch version;
    - the sandbox network scope;
    - the setup guide never installs anything.
46. **Not yet done (by design).** The `v1.0.0` tag and the GitHub Release come after review and merge. The documented order is:
    - merge into `main`;
    - `bin/dwarfstar seal` on the merge commit;
    - `git tag -a v1.0.0`;
    - `release-check` until it passes;
    - push the tag;
    - create the Release.

    No weights, backups or chats are ever release assets.

## Final state

47. **Validation.**
    - Tests: 138 pass, and `check.sh` passes.
    - `doctor`: 27/27 ready.
    - Migrate dry run: nothing pending. The backup plan restores cleanly.
    - Loopback probes: 403.
    - UI: all eight views snapshot through a freshly built app, and the Visualizer and Setup flows were exercised in the browser.
    - Seal verify fails, as expected before re-sealing: it lists exactly the hardening's product files.
48. **Production state.**
    - The Workstation service runs this branch's code on 8038. I restarted it twice during the phase, with no chat answering and no model running.
    - No model is running.
    - Your approval mode is Full Auto, unchanged.
    - The installed app predates the download fix; rebuild it after merge.
49. **Protected items.** Scope, Swift, Cyber, the runtimes and the model files are untouched. The external Swift BF16 backup was not touched. No real chat was altered except by the documented, backed-up migration.
50. **Next steps for you.**
    1. Run `gh auth refresh -h github.com -s workflow`. I then push `release/hardening-v1` and open a PR into `main` that supersedes PR #2.
    2. Watch CI run and set up branch protection.
    3. Review and merge.
    4. Re-seal, tag `v1.0.0`, run `release-check`, and publish the Release.
