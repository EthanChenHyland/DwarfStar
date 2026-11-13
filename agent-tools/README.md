# DwarfStar development tools

Enhanced tooling is enabled for every DwarfStar profile on the next fresh ACP connection. The original Scope-First behavioral prompt is unchanged; retired conversations are accessible through Workstation Chat History. No server restart or new model is needed. An already-running ACP process keeps its old environment until reopened; existing saved chats are preserved.

## Commands in the agent's bash tool

- `dwarf-capabilities`: detect current versions/paths, read dated connectivity receipts.
- `dwarf-capabilities --network`: refresh shell + Node public HTTPS tests (no model request).
- `dwarf-browser --help`: thin Playwright browser plan runner.
- `dwarf-browser http://127.0.0.1:5173 page.png`: URL + screenshot + console/error/response metadata on stdout.
- `dwarf-browser browser-plan.json`: deterministic interaction plan.
- `dwarf-node test.mjs`: run a workspace ESM utility importing the installed `playwright` or `puppeteer` without installing duplicate harnesses. Ordinary CommonJS `require` also resolves them via this profile's scoped `NODE_PATH`.

No global PATH changes. Use the project's own package manager/lockfile. `npm install`, `npm install -D ...`, `npx`, existing pnpm/bun, Python virtual environments and public Git clones work within the workspace. Do not use global installs. Shared utilities can be stored under `agent-tools/utilities`; shared packages can be installed with `npm install --prefix <DwarfStar>/agent-tools <package>`. The install manifest, lockfile, dependencies, utility directory, state and dedicated caches are writable; security/launcher files are not.

## Example plan

Save in the current workspace:

```json
{
  "url": "http://127.0.0.1:5173",
  "server": {"command": "npm run dev -- --host 127.0.0.1 --port 5173", "url": "http://127.0.0.1:5173"},
  "viewport": {"width": 1280, "height": 800},
  "screenshot": "validation.png",
  "dom": "validation.html",
  "output": "browser-receipt.json",
  "steps": [
    {"op": "wait", "selector": "h1"},
    {"op": "exists", "selector": "canvas"},
    {"op": "eval", "expression": "document.title"}
  ]
}
```

Only include `server` when this invocation should own that server. It waits for the explicit localhost URL and stops the owned process group afterward, including errors. Background children from a bash call are cleaned up when the call completes: start, inspect and stop a dev server within one bounded invocation, or use `plan.server`. `withServer` and `launchBrowser` are also exported from `lib/browser.mjs` for custom scripts. Artifacts/file inputs must be workspace-local. Browser steps support click (selector or exact text), type/fill, key press, wait, exists, eval, title, reload, DOM, screenshot, viewport.

Playwright is the default. `launchBrowser({engine:'puppeteer'})` is the fallback. Both use the installed Chrome at the dynamically detected path and fresh temporary unauthenticated profiles. Headed mode: `"headed": true`. `"file": "index.html"` opens a local page; use localhost for fetches/ES modules because standard browser CORS still applies. No cookie, saved-password or real browser profile import is provided. Network receipts include URL/status/method/failure metadata; no headers, cookie jars or response bodies are dumped automatically.

## macOS browser boundary

`--use-mock-keychain` is supplied for automated Chrome. The original real-home browser and Keychain paths remain denied. The native Chrome sandbox cannot initialize inside the inherited macOS Seatbelt process-tree sandbox (observed sandbox-init failures and SIGSEGV). Only these test browser processes use `--no-sandbox` when `DWARFSTAR_TOOLING_ACTIVE=1`; their enclosing Seatbelt file/network/credential boundary remains active and inherited. Chrome launched outside that tooling boundary keeps its native sandbox. No global macOS/Chrome security setting was changed.

Use explicit `127.0.0.1` server bindings. Seatbelt accepts only `*` or `localhost` in host filters, and `localhost` also includes machine-local addresses. It cannot guarantee that arbitrary project code binds exclusively to loopback. Obvious public-bind commands are approval-required/refused by this shell; this command check is advisory, not a security parser. The shipped dev-server examples bind loopback, readiness URLs are restricted to localhost, and validation opens no public service.

## Permissions and logs

1. Automatic: workspace reads/writes, builds/tests, read-only Git and existing permitted local commits, public docs/HTTPS, browser automation, bounded local servers.
2. Automatic + logged: workspace dependency installs/downloads, npx, virtualenvs, agent-tools package installs/utilities, browser-cache downloads. Commands, exit codes, elapsed times and redacted output are recorded in `<workspace>/.dwarfstar-tooling-logs/`. Tool output remains subject to the existing fail-closed redactor.
3. Explicit operator approval: global installs, Homebrew, persistent services, shell/global PATH/config changes and writes outside approved roots. These are blocked by the sandbox; the model must ask the operator for a separately authorized action. An ACP approval does **not** grant an unsandboxed retry.
4. Always explicit operator action: sudo/security/real Keychain/SSH/private credentials/authenticated-account/destructive unrelated-file operations. Credentials are not exposed. No automatic escalation tool exists.

The native OMP tool surface remains `read,write,edit,bash`. Bash keeps its existing JSON argument schema. Its approval policy allows the deliberately confined development operations and prompts for obvious restricted commands; user-level deny/prompt policies remain authoritative. Native workspace/symlink/hardlink protection, private-home reads, outside writes and Apple-event restrictions are retained. This is not a guarantee against every attack in arbitrary untrusted code; it is the existing process sandbox extended for requested development access.

## Capability context and telemetry

`state/capabilities.json` is refreshed for local versions/paths when this ACP profile starts. Connectivity is an explicit dated test receipt, marked stale after 24 hours. No network or browser test runs automatically on every turn. `extension.ts` supplies a compact (~2 KB) environmental custom context message once per ACP session, independently of the unchanged original Scope-First system prompt. `dwarf-capabilities --network` refreshes public connectivity when requested. The DwarfStar status app exposes the same cached information in its collapsed **Environment tools · Scope-First** section. Existing status polling remains 3 seconds; it reads a JSON file and issues no new tooling probes.

## Persistent storage

Browser cache: `agent-tools/cache/browsers` (currently empty: both harnesses share installed Chrome). npm cache: `agent-tools/cache/npm`. No global npm install, extra model, model request, model/server restart or Drift run was used to install/validate this environment.

Validation sources under `tests/` are intentional reusable infrastructure checks. Run them only in a disposable workspace through `boundary.py`; they start ordinary browser/localhost test processes and do not contact a model. The installation report and evidence live outside user projects.
