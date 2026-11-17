# Popup-free host — address ALL cmd/conhost popups

Standing directive (2026-11-17): **no visible cmd/PowerShell/conhost flash is
acceptable on the Windows desktop.** Whenever a popup is reported or spotted,
hunt it to the spawning process and fix the launch path — don't just close it.

## How consoles get allocated (the only two rules that matter)

1. A **console-subsystem process** spawned with default flags allocates a
   conhost → visible flash. `-WindowStyle Hidden` does NOT help — the console
   is created before the style applies.
2. **Grandchildren inherit nothing** — wrapping the parent is not enough.
   `pwsh -WindowStyle Hidden -Command 'npx -y …'`, `.cmd` shims, and any
   `&`/`Start-Process` of a console app still allocate a console.

## The fix lanes (pick by what needs the output)

| Lane | Use when | How |
|---|---|---|
| `conhost.exe --headless <cmd>` | Scheduled-task actions / fire-and-forget | No console ever created — but **eats stdout** (terminal escapes); never for output capture |
| `~/scripts/win-hidden.sh` → `winrun`/`winps`/`wincmd` | WSL→Windows calls that need stdout/exit code | `pythonw.exe` + `C:\Users\tbaltzakis\devin-scripts\mcp-hide.pyw` (`CREATE_NO_WINDOW`, stdio passed through) |
| Direct `.js` entrypoint | MCP servers / node tools | Skip `npx`/`.cmd` shims entirely — point at the package's real entrypoint: resolve the `bin` field in its `package.json` (e.g. `@playwright/mcp` ships `cli.js`, `firebase-tools` ships `lib/bin/firebase.js`), not a guessed `dist\index.js` |
| `Start-Process -NoNewWindow` | Child launched inside an already console-less pwsh | Keeps the child console-less too |
| `WScript.Shell.Run "cmd /c x.bat",0` | vbs startup launchers | Hidden by design — NOT a flash |

## Process

1. `popup_task_audit.ps1` — sweep scheduled tasks (should stay `risky=0`).
2. Fresh `_popup-watch.ps1` capture (~5 min) — stale logs mis-attribute.
   `conhost 0x4` = a console WAS allocated; check the parent chain to judge
   visible vs hidden-pty (mcp-hide'd children are invisible).
3. Fix the launch config/script — grep all callers, patch every site.
4. Re-capture to verify. Clients cache MCP configs — **restart the client**
   before concluding a config fix failed.
5. Document the new source class in `windows-host-ops` SKILL.md.

Full taxonomy of the five flash classes + diagnostic loop:
`.devin/skills/windows-host-ops/SKILL.md` ("Non-task popup sources").
