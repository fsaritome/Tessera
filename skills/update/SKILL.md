---
name: update
description: Check whether a newer version of the Tessera plugin exists and update it. Use when the user asks to update Tessera, which Tessera version they have, or whether Tessera is up to date.
allowed-tools: Read Bash(git ls-remote --tags --refs *) Bash(claude plugin marketplace update tessera) Bash(claude plugin update tessera@tessera) PowerShell(git ls-remote --tags --refs *) PowerShell(claude plugin marketplace update tessera) PowerShell(claude plugin update tessera@tessera)
---

# Tessera update

1. **Installed version.** Read `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json` and note
   `version` and `repository`.
2. **Newest version.** Run `git ls-remote --tags --refs <repository>.git` (the repository
   URL from step 1 plus `.git`). The newest version is the highest `vX.Y.Z` tag, compared
   as numbers. If git is missing or offline, say you could not check and give the manual
   steps from step 4. Stop.
3. **Compare.**
   - Installed is the newest or newer: say "Tessera <version> is up to date." Stop.
   - A newer version exists: say which (for example "0.2.2 → 0.3.0") and continue.
4. **Update.** Run `claude plugin marketplace update tessera`, then
   `claude plugin update tessera@tessera`.
   - If both succeed: say the new version is installed and takes effect in a new session
     (start a new session, or type `/reload-plugins` in a terminal session).
   - If `claude` is not found (common on Windows, where the installer's folder is often not
     on the PATH) or a command fails, do not try other ways. Tell the user to update by
     hand, either in a terminal session (`/plugin`, then the **Marketplaces** tab, select
     **tessera**, then **Update marketplace**) or in a terminal with the two commands above.
     Mention once that adding `%USERPROFILE%\.local\bin` (Windows) or `~/.local/bin` (Mac,
     Linux) to the PATH makes `claude` available.
5. If the user installed a pinned version (a marketplace added as `...#vX.Y.Z`), say that
   a pinned marketplace stays on its version: to move to a newer one, remove the marketplace
   and add it again with the new tag, ideally after checking that version.

Use one or two short lines per step; no paragraphs.
