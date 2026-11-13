# Workstation

`DwarfStar Workstation.app` is a thin native window (Swift + WebKit, `app/workstation/Sources/main.swift`) around the local service. On launch it starts `bin/dwarfstar serve` if nothing answers on 127.0.0.1:8038, then shows the service's interface. All logic lives in the service, so the app, the command line and the tests behave identically.

## Views

| View | Contents |
|---|---|
| **Chats** | New chat (uses the model and reasoning chosen on Run), Recent / Starred / Archived, tag filters and search (titles, tags, messages), pins (kept on top), stars, tags, inline rename (never overwritten by the automatic title), fork the whole chat or from any reply, new chat with the same settings, export as Markdown or JSON, select mode for bulk archive/restore/delete (deleting needs the count typed out; chats are moved aside, not erased), context used and left, compactions. Turns run through the same ACP agent as Zed and resume its session, so tools, the approval mode and the agent's automatic compaction apply; the full transcript is kept separately under `<data>/chats/` (never in Git). In Ask mode a tool call shows an approval card in the chat. Reasoning is not shown. The last chat reopens after a restart. |
| **Run** | Model selector (Scope Vanilla, Swift 1.5, Cyber), a reasoning selector that changes per model (the last level used with each model is remembered), the model's context as a fixed capability (128K for all three), launch/switch/stop with a stage row (stop current, memory check, load with a timer against the measured load time, health check, ready), a pre-switch summary when a running model or an answering chat would be interrupted, memory admission, key measured numbers, the matching Zed entry and the tool approval presets. |
| **Models** | One statistics page per model with the same schema: identity, source and revision, quantization, storage, memory plan, context and KV cache dtypes, speed, MTP, capabilities, provenance, and quality/benchmark history. Anything not measured says **Not measured**. |
| **Live** | Prompt/output tokens, prefill and decode rates, MTP acceptance, context occupancy, PLE disk reads, unified memory, GPU and the tool activity of the running session. |
| **Visualizer** | See `docs/VISUALIZER.md`. |
| **Diagnostics** | Running model (reasoning, backend, PID, port, health, context used and left, prefill chunk, KV types); memory as a composition bar plus four separate figures (system RAM, DwarfStar's admission requirement, the Metal working-set limit, the measured peak); chats, agent session, compactions and approval mode; telemetry and the last recovery report; 23 health checks with remedies (machine, software, runtime pins, model files, generated config, Zed, agent browser, KimiCU, app); Refresh, Copy diagnostics (home paths as `~`) and Reconcile state. `bin/dwarfstar doctor` prints the same checks. |

## Profiles

| Model | Reasoning | Context | K / V cache | Default |
|---|---|---:|---|---|
| Scope Vanilla | LOW | 128K | F16 / F16 | yes |
| Swift 1.5 | LOW, MEDIUM | 128K | F16 / F16 | no |
| Cyber | OFF, LOW, MEDIUM, XHIGH | 128K | F16 / F16 | no |

Changing reasoning on a running model never restarts it; the effort is carried per request by the agent's provider file. Swift LOW and MEDIUM keep separate session namespaces.

## Tool approval

| Preset | Behaviour |
|---|---|
| **Ask** | Every tool call waits for approval: in Zed's panel, or as an approval card in the Workstation chat (declined after 10 minutes). |
| **Workspace Auto** | Tools run without asking inside the session's workspace folder, in a macOS sandbox; anything outside it is refused. |
| **Full Auto** | Every tool call runs without asking, outside any sandbox. Turning it on always needs an explicit confirmation. |

The preset is installation-wide (`<data>/config/approval.json`), applies to new agent sessions and the next chat turn, and is the same for every profile. macOS's own permission prompts (screen recording, accessibility) are separate and always apply.

## Recovery

When the service starts, and on **Reconcile state** in Diagnostics, saved state is compared with the real processes; nothing is ever signalled. A ledger whose process has exited is retired; a ledger whose PID now belongs to another program is retired without touching that program; a running server whose command line is exactly this model's generated one but has no ledger (the service or controller died during a start) is health-checked and adopted, so the Workstation reconnects instead of starting a second copy; any other model server is reported as foreign. A chat whose transcript ends with an unanswered message is marked interrupted (its agent session is intact; send again to continue), and telemetry sessions left open by a crash are closed.

## Memory admission

Before a start the controller applies one shared formula (`config/runtime-policy.json`): planned = resident model + KV + graph buffers + vision projector; required = planned + a 1.5 GiB system reserve. Requirements today: Scope 45.2 GiB, Swift 45.2 GiB, Cyber 52.3 GiB. Separately, a plan larger than the GPU's Metal working set (measured live, 51.84 GiB on an M1 Max 64 GiB) is refused, because it would fail at depth even with free memory. A refusal names the model, both numbers and what to do, and nothing is started. When another model is running the UI accounts for the memory the switch will free.

## Building and installing

```bash
python3 app/workstation/build.py --bundle "$HOME/Applications/DwarfStar Workstation.app"
```

The bundle records the repository path; it contains no model data or user settings. The build refuses to overwrite an existing bundle.

## Command line equivalents

```bash
bin/dwarfstar switch swift-medium     # what the Launch button does
bin/dwarfstar state                   # selection and per-model status
bin/dwarfstar stop-all
bin/dwarfstar runtime --model cyber requirement
```

## Screenshots

| | |
|---|---|
| ![Run](assets/workstation-run.png) Run: Scope selected and running | ![Model selector](assets/model-selector.png) Swift 1.5 with LOW / MEDIUM |
| ![Chats](assets/chats.png) A chat with Cyber LOW using the shell tool | |
| ![Statistics](assets/models-stats.png) Cyber statistics page | ![Live](assets/live.png) Live telemetry during a Cyber agent session |
| ![Visualizer](assets/visualizer.png) Visualizer: router, MTP, PLE, shell action | |
