# Final product audit and 1.0.0 finish (2026-11-15)

This follows `docs/POST-RELEASE-AUDIT.md`. That audit's headline gap was that there was no release, and `main` lacked the security fix. It is closed here:
- PR #2 was closed as superseded (0 unique commits).
- PR #3 was merged into `main` (`5091b93`, a normal merge commit).
- This work was done in one PR from `feat/finish-1.0.0`; `v1.0.0` is tagged on its merge.

## GitHub

| Item | State |
|---|---|
| Branches | `main` only. The merged `release/hardening-v1`, `experiment/splash-advanced-telemetry` and `perf/backend-and-workstation` were deleted; each had 0 commits outside `main`. Merged branches auto-delete. |
| Tags | `rollback/native-default-20261113`, `checkpoint/splash-default-20261113`, `checkpoint/pre-advanced-telemetry-20261113` (the ds1 production state; now the ds2 rollback point), and `v1.0.0` |
| Protection | A PR is required (0 approvals), the three CI checks are required with the branch up to date, and force-push and deletion are blocked. Admins are included. |
| CI | `core` (macOS), `static and security`, `docs`: green, about 15 s. No artifacts; dependency caches about 23 MB, auto-evicted. |

## This Mac

- **Layout:** the canonical layout is unchanged (see `docs/STORAGE.md`). There is one installed app, `~/Applications/DwarfStar Workstation.app`.
- **Old app:** the previous bundle (version "3.0", built 2026-11-13, no icon) was replaced through `setup`'s safe install. The new app was verified, launched from LaunchServices, quit and relaunched, and then the rollback copy was removed.
- **Found and fixed:** while testing, a throwaway instance's `generate` had re-pointed this checkout's `agent-tools/state` and `cache` links at its temporary roots. The links were restored, and environment-overridden roots can no longer re-point them (test added).
- **Removed:** throwaway instances, test clones and build scratch, all under temporary space. No production asset, real chat, provenance or rollback runtime was touched.
- **Your one real chat** is unchanged.

## App

- **Bundle:** `local.dwarfstar.Workstation`, version 1.0.0 (CFBundleShortVersionString and CFBundleVersion), arm64, macOS 14+, category Developer Tools. It records `DwarfStarHome` and `DwarfStarRevision`, the release commit.
- **Version surfacing:** the same version appears in the bundle, Diagnostics' header, Settings → About, `doctor`'s first line and `setup`'s summary.
- **Build:** `bin/dwarfstar setup` (or `build.py --install`) builds, verifies (version, icon, executable, signature), moves the old bundle to `<cache>/build/app-rollback/`, installs and re-registers it with LaunchServices.
- **Smoke test** in the installed app: Chats with folders, the Zed tab, Run, Live Standard and Advanced (DFlash2 panel and Recent requests during a real turn), Diagnostics, Settings (About, trash), the command palette, an Export JSON Save panel (cancelled), quit and relaunch.

## Icon

- **Concepts considered:**
  - a compact stellar core;
  - a core with routing geometry (rejected: it reads as a network diagram);
  - a technical observatory (chosen).
- **First draft:** a shaded ball with a ring read as Saturn. It was replaced by a small, flat, white-hot core in a corona, a wider inclined orbit carrying a body, and measurement ticks.
- **Source:** `app/workstation/icon/dwarfstar.svg`. It is rendered by AppKit (`render.swift`) at every `.icns` size. At 64 px and below, the ticks are dropped and the orbit is drawn heavier.
- **Checked:** at 1024, 128, 64, 32 and 16 px, in Finder and in the Dock next to other apps. At 16 px the core and tile carry it; the orbit is faint but present.

## Setup

- **Before:** about nine documented commands (generate, agent patch, assets, doctor, app build, Zed).
- **Now:** `bin/dwarfstar setup`, which reports what it did and a status:
  - runtime and each model;
  - Workstation version and revision;
  - Zed;
  - chats and folders;
  - doctor (27/27 here).
- **Idempotent:** a second run reported "already done" for every step. It wrote no new Zed backup and built no second bundle.
- **Never downloads:** a missing model or runtime is reported with where it belongs.
- **Update:** back up, `stop-all`, pull, then `setup`. Documented in the README's *Update DwarfStar* section and in `docs/REPRODUCIBILITY.md`.

## Chats

| Capability | Checked |
|---|---|
| Rename, archive, delete (to trash), pins, stars, tags, search, fork, new chat with same settings, Markdown/JSON export, bulk archive/delete | existing unit tests plus UI use on a throwaway instance |
| Folders: create, nest (2 levels), rename, delete (chats become Unfiled; subfolders need an explicit choice), Move to folder… (menu, bulk, drag), counts, scoped list, search across folders | `tests/test_folders.py` plus UI on a throwaway instance |
| Archive keeps the folder; fork inherits it; folder neutral to sessions; backup carries folders and restore merges them by id; schema 3 migration and lossless rollback | `tests/test_folders.py` |
| Trash: listed in Settings, emptied only on "empty trash", no timer | `tests/test_folders.py` plus UI |

## Workstation and Zed

These were checked with Scope, using a synthetic chat that was deleted afterwards:
- **Three Workstation turns** ("alpha", "beta", then a third) used one agent session id and one session file throughout.
- **Moving the chat** into a folder changed neither.
- **Sharing with Zed** kept the same session, which then resumed over ACP (`session/resume`, the call Zed makes) without falling back to a new one.
- **A second concurrent turn** was refused ("This chat is already answering").
- **The folder** was kept throughout.

## Remaining improvements

- **P0:** none.
- **P1:** none that justify a new phase.
- **`dwarfstar update`:** wait for 1.0.1. There has not yet been a real update between releases to design it against, and `setup` now covers the hard part. *Update DwarfStar* is four commands.
- **P2:**
  - remove the Visualizer's native-replay drawing paths (unreachable: no native sessions remain);
  - a release script for seal, tag and Release.
- **Not worth it** (no evidence calls for any of them):
  - another backend or native restoration;
  - MLX;
  - router GPU readback;
  - new conversions or drafts;
  - storage clean-up beyond this;
  - cloud sync, accounts, mobile, a framework rewrite;
  - a support bundle (Copy diagnostics covers it);
  - doctor auto-repair.

**Recommendation: stop engineering DwarfStar and use it.**
