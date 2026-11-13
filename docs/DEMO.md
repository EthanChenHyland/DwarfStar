# Demo guide

About ten minutes. Nothing here changes a model or its configuration.

## 1. Open the Workstation

Open **DwarfStar Workstation** from `~/Applications`. The Run view shows three models; Scope Vanilla is selected and marked **Default**. Each model card shows its context (128K for all three) as a property, not a choice.

## 2. Start Scope

Click **Launch Scope Vanilla LOW**. The switch steps appear under *Last switch*: memory check, start, load time (about 2 s warm, 15 s cold), ready. The sidebar turns green.

## 3. Use it from Zed

In Zed: Agent Panel → **+** → External Agents → **DwarfStar Scope Vanilla LOW**. Open a scratch folder and ask for something that needs tools, for example:

> Count the lines containing ERROR in data.txt with the shell, write the number to result.txt, and reply with the number.

Full Auto approves workspace actions without prompts.

## 4. Watch it think (without reading its thoughts)

Switch the Workstation to **Visualizer** while the agent works:

* the router lights 10 of 512 experts per layer for each token,
* MTP drafts are accepted (green) or rejected,
* PLE reads flash on the disk-backed n-gram table,
* context fills against 128K,
* tool calls appear on the action timeline.

**Live** shows the same run as numbers: prefill and decode rates, MTP acceptance, memory.

## 5. Chat in the Workstation

Open **Chats** → **New chat** and ask the same question. The reply streams in with its tool calls; the header shows the profile and how much of the 128K context the agent is using. Rename the chat by clicking its title; archive or search it from the sidebar.

## 6. Change reasoning without a restart

Select **Swift 1.5**, then **MEDIUM**, and click **Switch to Swift 1.5 MEDIUM**: Scope is stopped first, then Swift starts. Now select **LOW**: the model keeps running; only the Zed entry changes (**DwarfStar Swift 1.5 LOW**).

## 7. Cyber

Select **Cyber**: four reasoning levels (OFF, LOW, MEDIUM, XHIGH). Switch to it; memory admission shows its larger requirement (about 52 GiB available needed). Cyber's statistics page (**Models → Cyber**) shows how it was converted and its measured limitations.

## 8. Evidence

**Benchmarks** lists every result behind the decisions, including the production validation of this release. **Diagnostics** shows that generated configuration matches the catalog, the Zed entries match, and the seal is present.

## 9. Stop

Click **Stop model**. No model server remains running.
