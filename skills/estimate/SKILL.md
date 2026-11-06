---
name: estimate
description: Count how many of the user's Claude sessions Tessera would read and how many tokens that would use, without reading anything. Free and local. Use when the user asks what a Tessera map would cost, how many tokens or batches it needs, or wants a preflight before running /tessera:map.
argument-hint: "[days, default 30]"
allowed-tools: Bash(uvx --quiet --from ${CLAUDE_PLUGIN_ROOT} tessera *) PowerShell(uvx --quiet --from ${CLAUDE_PLUGIN_ROOT} tessera *) AskUserQuestion Skill
---

# Tessera estimate

A free preflight: count, do not read. In every command below, `T` means exactly:

    uvx --quiet --from ${CLAUDE_PLUGIN_ROOT} tessera

If `uv` is missing, stop and tell the user to install it (https://docs.astral.sh/uv/), in
one sentence. Do not install anything yourself.

Time window: the number in `$ARGUMENTS` (days) if there is one, otherwise 30. Call it DAYS.

0. Run `T status --json`. If `accepted` is false: if `$ARGUMENTS` contains the word `agree`, run `T accept --yes` and continue. Otherwise run `T terms` and show the terms exactly as printed except its last line (the terminal hint). Then ask with the AskUserQuestion tool (question "Do you agree to these terms?", options "Agree and continue" and "Don't agree"). On "Agree and continue", run `T accept --yes` and continue; on anything else, do nothing more. If AskUserQuestion is not available, say "To agree and continue, type `/tessera:estimate agree`." and stop.
1. Run `T scan --days DAYS`. It reads only local files and prints sessions per source.
2. Run `T prepare --days DAYS`. It writes redacted batch files on this computer and prints
   the number of units and batches and the estimated tokens. Do not open the batch files.
3. Reply in at most five lines, in the user's language:
   - sessions found per source,
   - how many are new (not read before), in how many batches,
   - the estimated tokens, and that reading them would use their own Claude plan,
   - that nothing has been read and nothing was sent anywhere,
   - the next step: `/tessera:map DAYS` to build the map, or a smaller DAYS to make it
     cheaper. If the output said "Nothing to do", say that everything in this window is
     already read and `/tessera:open` shows the map.

4. Unless the output said "Nothing to do", ask with the AskUserQuestion tool, question
   "Build the map now?", options "Build it now (about N tokens)" with N from the estimate,
   "Fewer days", "Not now". On "Build it now", run the `tessera:map` skill with the Skill
   tool and DAYS as its argument. On "Fewer days", ask with AskUserQuestion for 7, 14 or 30
   days (whichever are below DAYS) and start again at step 2 with that number. On
   anything else, stop. If AskUserQuestion is not available, end with the next-step line.

The estimate is counted locally with a ratio measured on Anthropic's token counter; say
"about", not an exact number.
