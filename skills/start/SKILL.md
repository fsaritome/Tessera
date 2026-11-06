---
name: start
description: Start here for Tessera - explains in plain words what Tessera does, how it protects the user's data and what it costs, shows the Tessera commands as a menu, checks where the user stands and suggests the next step. Use when the user asks what Tessera is, how to use it, what the Tessera commands are, or says help, start or menu in connection with Tessera.
allowed-tools: Bash(uvx --quiet --from ${CLAUDE_PLUGIN_ROOT} tessera status *) PowerShell(uvx --quiet --from ${CLAUDE_PLUGIN_ROOT} tessera status *) Read AskUserQuestion Skill
---

# Tessera: start here

Give the user a short, friendly welcome with a menu. Run nothing except the two reads in
step 1, until the user picks something in step 3. Write in the user's language.

## 1. Find out where the user stands

- Read `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json` for the version.
- Run `uvx --quiet --from ${CLAUDE_PLUGIN_ROOT} tessera status --json`. It reads only
  local files. Fields: `events` (sessions found per source), `units`, `extracted`,
  `pending`, `batches_open`, `batches_answered`, `merge_current`, `accepted` (terms agreed), `map_running`. If `uv` is missing, say in one sentence that
  Tessera needs uv (https://docs.astral.sh/uv/) and still show the explanation and menu.

## 2. Reply with exactly these parts, kept short

**Heading:** "Tessera <version>: a map of what you do with Claude".

**What it does** (2-3 sentences): Tessera finds your Claude Code, Claude desktop (Cowork)
and claude.ai sessions on this computer, removes credentials and personal details, and lets
your own Claude read them in batches. From that it builds a zoomable map in your browser:
a few headlines about your life and work, the projects inside them, every activity, and the
methods that connect them.

**Your data** (bullets):
- Everything stays on this computer; the map runs at http://127.0.0.1:18765 and only you
  can open it.
- Before Claude reads anything, passwords and keys drop the whole session, and hosts, IPs,
  emails and paths are replaced with placeholders.
- Nothing is posted or shared anywhere.

**Cost** (one sentence): Claude reads your sessions on your own Claude plan, so it uses
your plan's limits; `/tessera:estimate` shows how much before anything is read, for free.

**Your risk** (one sentence): Tessera is free software provided as is; you use it at your
own risk, and before its first run it shows the terms and asks you to agree. If `accepted`
is true, say instead: "You agreed to the terms (`tessera terms` shows them again)."

**Menu** as a table:

| Command | What it does | Uses your plan? |
|---|---|---|
| `/tessera:start` | This overview | No |
| `/tessera:estimate [days]` | Count sessions, batches and tokens, read nothing | No |
| `/tessera:map [days]` | Read new sessions and build or update the map (default 30 days). Never reads a session twice, and continues a run that stopped halfway | Yes |
| `/tessera:open [share]` | Open the existing map again; `share` hides your job projects and private activities | No |
| `/tessera:stop` | Stop the map running in the background | No |
| `/tessera:update` | Check for a newer Tessera and install it | No |

**Adding more** (one line): run `/tessera:map` again for new sessions, `/tessera:map 365`
for older ones; a claude.ai data export zip in Downloads is added on the next run.

**Where you stand** (one or two lines, from step 1):
- No sessions found yet (`events` empty): "Nothing scanned yet."
- Sessions found, map not built (`merge_current` false): how many sessions and how many
  are not read yet.
- `batches_answered` above 0: "A previous run stopped halfway; `/tessera:map` continues it without paying twice."
- Map built (`merge_current` true): "Your map is ready."
- `map_running` true: add "The map is running in the background; `/tessera:stop` stops it."

**Next step** (one line, pick one):
- Nothing scanned or map not built: "Start with `/tessera:estimate` to see the cost, then
  `/tessera:map`."
- Map ready: "`/tessera:open` shows it; `/tessera:map` adds sessions since the last run."

## 3. Let the user pick

Ask with the AskUserQuestion tool, question "What would you like to do?", and these
options (at most four):
- No map yet: "Estimate the cost (free)", "Build my map", "Nothing for now".
- Map ready: "Open my map", "Add new sessions", "Estimate the cost (free)", "Nothing for now".
- Map ready and running: "Stop the map", "Add new sessions", "Nothing for now".

Then run the chosen command with the Skill tool: `tessera:estimate`, `tessera:map` (also for
"Add new sessions"), `tessera:open` or `tessera:stop`. On "Nothing for now" or anything
else, end with one line: the commands from the menu work any time.

If AskUserQuestion is not available, end after the menu with the "Next step" line and run
nothing yourself.
