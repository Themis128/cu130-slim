# win-bridge-ops — Windows-side agent for the physical gates

WSL↔Windows interop (`.exe` invocation, `powershell.exe`, vsock) dies
sporadically. This skill removes the dependency: a ~90-line stdlib-only
HTTP agent that runs on Windows and is reachable from WSL over LAN at
`http://<host-ip>:8765`.

## One-time setup (Windows, ~30 seconds)

    copy D:\cu130-slim\.devin\skills\win-bridge-ops\scripts\win_agent.py %USERPROFILE%\
    python %USERPROFILE%\win_agent.py          # leave the window open

(Set `WIN_AGENT_TOKEN` env on both sides if you want auth; without it the
agent still only listens for POST/GET on the LAN.)

To survive reboots: Task Scheduler → "win-agent" → trigger: at logon →
action: `python %USERPROFILE%\win_agent.py` → hidden.

## Agent endpoints

| Call | Does |
|---|---|
| `GET /health` | `{"ok":true}` — connectivity probe |
| `POST /exec` `{"cmd":"..."}` | runs cmd on Windows, returns stdout (60s cap) |
| `POST /launch-chrome` | starts the debug Chrome (`:9222`, profile dir `C:\tmp\chrome-debug`) |
| `POST /adb` `{"args":["devices"]}` | runs adb via PATH/SDK lookup |
| `GET /screenshot` | PNG of the Windows desktop (PIL optional) |

## gate_runner.py — executes the gate sequence from WSL

    python3 .devin/skills/win-bridge-ops/scripts/gate_runner.py

1. Probes the agent (auto-discovers host IP via `ip route`).
2. `/launch-chrome` → polls `localhost:9222` until the debug port answers.
3. Runs `gates-ops`'s `cdp_click.py` against WhatsApp Manager for the
   Cloud-API-terms banner (#786) — reports what it clicked/found.
4. `POST /adb devices` — if the S22+ shows up, hands off to
   `tiktok_profile_edit.py` (#789) — which is next-door tooling.
5. Prints a per-gate verdict line — what ran, what still needs the human.

After the agent is running, the *only* manual parts left are the Meta
login state inside the debug Chrome and whatever the platforms refuse to
expose — everything orchestrated.
