# Demo guide

About ten minutes. Nothing here changes a model or its configuration.

## 1. Open the Workstation

Open **DwarfStar Workstation** from `~/Applications`. The Run view shows three models; Scope Vanilla is selected and marked **Default**. Each model card shows its context (128K for all three) as a property, not a choice.

## 2. Start Scope

Click **Start Scope Vanilla**. The stage row appears: memory check, load weights (with a timer against the measured ~25 s start), health check, ready. The sidebar turns green and the header line reads "Scope Vanilla LOW running".

## 3. Use it from Zed

In Zed: Agent Panel → **+** → External Agents → **DwarfStar Scope Vanilla LOW**. Open a scratch folder and ask for something that needs tools, for example:

> Count the lines containing ERROR in data.txt with the shell, write the number to result.txt, and reply with the number.

Full Auto approves workspace actions without prompts.

## 4. Watch it work (without reading its thoughts)

Open **Live** while the agent works. The **Standard** view shows Splash's own measurements: the latest request, then running time to first token, inter-token latency, prefill and decode rates, draft acceptance, prefix-cache hits, context, memory and the tool activity.

Toggle to **Advanced** (the data map). It shows where the model's data lives, the draft, context and memory, and the agent's actions on a timeline. Splash does not report per-token router selections or PLE reads, so those regions are labelled "not reported" rather than animated — nothing is estimated. (A replayed session recorded on the earlier native runtime does show its router and PLE detail.)

## 5. Chat in the Workstation

Open **Chats** → **New chat** and ask the same question. The reply streams in with its tool calls; the header shows the profile and how much of the 128K context the agent is using. Rename the chat by clicking its title; star, tag, fork or export it from the **More** menu; search or filter from the sidebar. The **Zed** filter lists DwarfStar threads from Zed, and a chat's **Open in Zed** moves the conversation there on the same agent session (`docs/SESSIONS.md`).

## 6. Change reasoning without a restart

Select **Swift 1.5**, then **MEDIUM**, and click **Switch to Swift 1.5 MEDIUM**: Scope is stopped first, then Swift starts. Now select **LOW**: the model keeps running; only the Zed entry changes (**DwarfStar Swift 1.5 LOW**).

## 7. Cyber

Select **Cyber**: four reasoning levels (OFF, LOW, MEDIUM, XHIGH). Switch to it; memory admission shows its larger requirement (about 52 GiB available needed). Cyber's statistics page (**Models → Cyber**) shows how it was converted and its measured limitations.

## 8. Evidence

Measured speed, memory and quality for each model are on its **Models** page; the full benchmark record is in `docs/BENCHMARKS.md`. **Diagnostics** shows that generated configuration matches the catalog, the Zed entries match, and the seal is present.

## 9. Stop

Click **Stop model**. No model server remains running.
