# Shared sessions (Workstation and Zed)

A DwarfStar conversation can be used from the Workstation's **Chats** and from Zed's agent panel, sharing one record. This page states exactly what is shared and what is not, so nothing is claimed that is not implemented.

## The canonical store

Both clients drive the same ACP agent (`bin/dwarfstar-acp --profile <id>`). The agent keeps one **append-only session file** per conversation:

```
<workspace>/.omp-dwarfstar-<profile>-agent/sessions/<cwd>/<time>_<session id>.jsonl
```

That file is the one canonical transcript. The agent's own context (including its automatic compactions) lives there, so whichever client resumes the session continues from the same place. The ACP **session id** is what links a Workstation chat and a Zed thread to the same file.

## What each client does

* **Zed** lists a workspace's sessions with ACP `session/list` and reopens one with `session/load`; the agent (`@oh-my-pi/pi-coding-agent`) advertises `loadSession`, `session/list`, `session/resume` and `session/fork`. Zed 1.22 records its threads in `sidebar_threads` (thread id, session id, agent id, title, folder paths).
* **The Workstation** resumes a chat's session with `session/resume`, falling back to `session/load`. For a chat it created, the full visible transcript is also stored under `<data>/chats/`; for a chat continuing a Zed thread, the transcript is read from the agent session file itself.

## Moving a conversation between the clients

* **Chats → Zed tab** (read-only) enumerates the DwarfStar threads in Zed's database and shows, per thread, what the Workstation can do:
  * **Continue** — the thread's workspace is present and its profile is current: open it as a Workstation chat on the *same* agent session and workspace. Nothing is copied; one chat per session.
  * **Read** — the workspace was archived in the storage consolidation, or the thread used a retired profile: the transcript is reconstructed read-only from the session file.
  * **Missing / empty** — the session file is no longer on this Mac, or Zed recorded no session for the thread.
* **Open in Zed** (from a chat's menu) marks the chat's session as the shared transcript and opens its workspace in Zed, where the matching DwarfStar agent lists the session in its history. It is refused when the chat shows messages the session file does not hold (a fork's copied history), so Zed could never show a truncated conversation.

## One writer at a time

The agent holds a single-writer lock on a workspace. If one client has a conversation open, the other cannot write to it at the same time: the Workstation reports this plainly ("open in another DwarfStar agent") instead of starting a second session. A chat that genuinely lost its session is told so, and never silently forks. Back-to-back Workstation turns wait briefly (up to 6 s) for the previous turn's agent to release the lock, rather than racing it.

## Limits, stated honestly

* **No live cross-client sync.** The clients share a file and a lock, not a live stream. Changes appear when the other client next opens or resumes the conversation, not while it is open elsewhere.
* **Zed's own metadata is not shared or written.** Zed's thread titles and folder grouping stay in Zed's database (read-only to the Workstation); the Workstation's tags, stars and pins stay in the chat record.
* **Archived or deleted workspaces.** Threads whose temporary workspaces were removed in the 2026-11 storage consolidation cannot be continued; they read where the session file survives and otherwise show as missing.
* **Reasoning is never shown in the Workstation.** The agent session file holds the model's thinking blocks as part of its own context, and Zed displays them when it reopens a thread; the Workstation reads only user text, visible reply text and tool-call names, and stores no reasoning of its own.
