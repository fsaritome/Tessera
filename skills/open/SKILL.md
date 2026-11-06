---
name: open
description: Open the user's existing Tessera map in the browser again, without reading any sessions. With "share", open the share view that hides job projects, private activities and summaries. Use when the user wants to see, show or reopen their Tessera map.
argument-hint: "[share]"
allowed-tools: Bash(uvx --quiet --from ${CLAUDE_PLUGIN_ROOT} tessera *) PowerShell(uvx --quiet --from ${CLAUDE_PLUGIN_ROOT} tessera *) AskUserQuestion Skill
---

# Tessera open

Open the map that already exists. Read no sessions. `T` means exactly:

    uvx --quiet --from ${CLAUDE_PLUGIN_ROOT} tessera

1. Run `T status --json`.
   - If `accepted` is false: if `$ARGUMENTS` contains the word `agree`, run `T accept --yes` and continue. Otherwise run `T terms` and show the terms exactly as printed except its last line (the terminal hint). Then ask with the AskUserQuestion tool (question "Do you agree to these terms?", options "Agree and continue" and "Don't agree"). On "Agree and continue", run `T accept --yes` and continue; on anything else, do nothing more. If AskUserQuestion is not available, say "To agree and continue, type `/tessera:open agree`." and stop.
   - If `merge_current` is false and `extracted` is 0, there is no map yet: say so in one
     line and suggest `/tessera:estimate`, then `/tessera:map`. Stop.
   - If `map_running` is true, run `T stop` first, so the view you start now is the one
     the user sees (normal or share).
2. Start the map in the background: `T map --no-browser`, or `T map --no-browser --share`
   if `$ARGUMENTS` contains "share". If it says the port is already in use by something
   else, tell the user which program to close, or that `T stop` did not find a Tessera map.
3. Tell the user in two short lines: open http://127.0.0.1:18765; it keeps running in the
   background and `/tessera:stop` stops it. In share mode add: job projects, private
   activities and summaries are hidden, but read every label before posting a screenshot.
