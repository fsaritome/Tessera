---
name: stop
description: Stop the Tessera map server that runs in the background. Use when the user wants to stop, close or end the Tessera map, the map server, or Tessera's background task.
allowed-tools: Bash(uvx --quiet --from ${CLAUDE_PLUGIN_ROOT} tessera stop) PowerShell(uvx --quiet --from ${CLAUDE_PLUGIN_ROOT} tessera stop)
---

# Tessera stop

Run `uvx --quiet --from ${CLAUDE_PLUGIN_ROOT} tessera stop`. It stops only the map that
this computer's Tessera started, after checking it is really Tessera on that port.

Reply in one or two lines: what it printed, and that `/tessera:open` starts the map again
whenever the user wants, without reading anything. If it said no map is running, say that
nothing needed stopping. If the map was started in this session as a background task, it
is now finished too.
