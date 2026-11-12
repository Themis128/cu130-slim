#!/usr/bin/env python3
"""host_gates_check.py — read-only probe of every remaining host-level gate.

    host_gates_check.py            # status table
    host_gates_check.py --json     # machine-readable
    host_gates_check.py --alert    # TG #ops digest when degraded (12h dedup)

Checks: MemAvailable, docker info probe, docker-desktop WSL distro state,
ChromeDebug scheduled task, Docker Desktop process, DMR SSH tunnel
(omv:12436 <- this PC's Docker Model Runner), CDP lane (:9229 relay ->
headless chrome :9330). All probes are read-only except dmr_tunnel and
cdp_lane, which relaunch their Startup .bat when the service is down;
other remediation stays with the existing healers/watchdogs.
"""
import json
import os
import re
import subprocess
import sys
import time

STATE_DIR = os.path.expanduser("~/.local/state/host-gates-ops")
STATE = os.path.join(STATE_DIR, "state.json")
LOG = os.path.expanduser("~/logs/host-gates-check.log")
TG_ALERT = ("/home/tbaltzakis/cu130-slim/.devin/skills/"
            "telegram-alert-ops/scripts/tg_alert.py")
RAM_WARN_MB = 6144          # ~5GB free was the observed flap threshold
DEDUP_SECS = 12 * 3600


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def sh(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return f"ERR:{e}"


def mem_available_mb():
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return -1


def docker_ok():
    out = sh("docker info --format '{{.ServerVersion}}'", timeout=25)
    return not out.startswith("ERR:") and bool(out.strip())


def win(cmd, timeout=25):
    """powershell.exe interop — may be dead during vsock flaps."""
    return sh(f"powershell.exe -NoProfile -Command \"{cmd}\"",
              timeout=timeout)


def check_ram():
    mb = mem_available_mb()
    # Win32_OperatingSystem.FreePhysicalMemory excludes standby cache and
    # reads ~0 under normal pressure; AvailableMBytes is the number Task
    # Manager calls "available" — the real flap predictor.
    host = win("(Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory)"
               ".AvailableMBytes", timeout=15)
    m = re.search(r"(\d+)", host)
    host_gb = round(int(m.group(1)) / 1024, 1) if m else None
    wsl_gb = round(mb / 1024, 1) if mb >= 0 else None
    degraded = ((mb >= 0 and mb < RAM_WARN_MB) or
                (host_gb is not None and host_gb < 1.0))
    return {
        "ok": not degraded,
        "wsl_memavail_gb": wsl_gb,
        "host_free_gb": host_gb,
        "detail": (f"WSL avail {wsl_gb}GB / host free {host_gb}GB"
                   + (" — RAM pressure (vsock flap risk)" if degraded else "")),
    }


def check_docker():
    ok = docker_ok()
    distro = sh("wsl.exe -l -v 2>/dev/null | tr -d '\\0'", timeout=15)
    dd_dead = "docker-desktop" in distro and not ok
    return {
        "ok": ok and not dd_dead,
        "docker_info": ok,
        "distro_state": distro.replace("\n", " | ")[:200] or "interop-dead",
        "detail": ("docker engine unreachable" if not ok else
                   "docker-desktop distro dead (GUI flap)" if dd_dead
                   else "engine ok"),
    }


def check_chromedebug_task():
    out = win("schtasks /query /tn ChromeDebug /fo list /v | "
              "Select-String 'Status|Last Run|Next Run'", timeout=20)
    if out.startswith("ERR:") or "Cannot find" in out or "ERROR" in out:
        return {"ok": None, "detail": f"task query failed: {out[:120]}",
                "raw": out[:200]}
    status_ok = "Ready" in out or "Running" in out
    return {"ok": status_ok, "detail": out.replace("\n", " | ")[:200],
            "raw": out[:300]}


def check_docker_desktop_proc():
    # Process name varies across DD versions/locales — com.docker.backend
    # is the stable backend; 'Docker Desktop' is the GUI. Either counts.
    out = win("Get-Process -ErrorAction SilentlyContinue | Where-Object { "
              "$_.Name -match 'docker' } | Select-Object -First 1 Name",
              timeout=15)
    if out.startswith("ERR:"):
        return {"ok": None, "detail": f"probe failed: {out[:100]}"}
    m = re.search(r"(com\.docker\.\S+|Docker Desktop|docker(?:-\S+)?)",
                  out, re.I)
    alive = bool(m)
    return {"ok": alive, "detail": "Docker process "
            + (m.group(1) if alive else "NONE found")}


DMR_TUNNEL_BAT = ("C:\\Users\\tbaltzakis\\dmr-tunnel.bat")


def check_dmr_tunnel():
    """omv:12436 reverse tunnel to this PC's DMR (Nextcloud assistant).

    Health signal is omv actually listening on 12436 (the -R bind
    lives or dies with the ssh process). Port is firewalled to LAN,
    so the probe goes over ssh (omv_id_rsa), not a TCP connect.
    """
    listen = sh("ssh -i ~/.ssh/omv_id_rsa -o ConnectTimeout=8 -o "
                "BatchMode=yes tbaltzakis@192.168.1.128 "
                "\"ss -tln | grep -c ':12436 '\"", timeout=20)
    m = re.findall(r"\d+", listen.strip().splitlines()[-1]
                   if listen.strip() else "")
    listen_up = bool(m) and int(m[-1]) > 0
    if listen_up:
        return {"ok": True, "detail": "omv listening on 12436"}
    detail = "omv NOT listening on 12436"
    # Self-heal: the bat loops internally but nothing restarts a killed
    # cmd.exe until next logon — relaunch it hidden, but only when no
    # dmr-tunnel loop exists at all (an existing loop recovers on its
    # own; spawning another would just pile up during an omv outage).
    loops = win("Get-CimInstance Win32_Process | Where-Object { "
                "$_.Name -eq 'cmd.exe' -and "
                "$_.CommandLine -match 'dmr-tunnel' } | "
                "Measure-Object | Select-Object -ExpandProperty Count",
                timeout=60)
    lm = re.search(r"\d+", loops)
    if lm and int(lm.group()) == 0:
        rel = win(f"Start-Process -FilePath '{DMR_TUNNEL_BAT}' "
                  "-WindowStyle Hidden; 'launched'", timeout=15)
        detail += f"; no loop found, relaunched={rel.strip()[:40]}"
    elif lm:
        detail += f"; {lm.group()} loop(s) alive, waiting"
    else:
        detail += "; process probe failed, not relaunching"
    return {"ok": False, "detail": detail}


CDP_LAUNCH_BAT = ("C:\\Users\\tbaltzakis\\AppData\\Roaming\\Microsoft\\Windows"
                  "\\Start Menu\\Programs\\Startup\\cdp-launch.bat")


def check_cdp_lane():
    """LinkedIn/FB portal CDP lane: relay 0.0.0.0:9229 -> headless chrome
    :9330. cdp-launch.bat is Startup-only, so a crashed chrome/relay stays
    dead until next logon — li/fb session healers then report no-cdp.
    Probe via the Windows host IP (localhost:9229 in WSL is NOT the
    Windows listener)."""
    import urllib.request
    host_ip = sh("ip route | awk '/default/ {print $3; exit}'",
                 timeout=5).strip() or "172.22.144.1"
    try:
        with urllib.request.urlopen(
                f"http://{host_ip}:9229/json/version", timeout=6) as r:
            body = r.read().decode("utf-8", "replace")
        if '"Browser"' in body:
            return {"ok": True, "detail": "CDP :9229 responding"}
        return {"ok": False, "detail": f"unexpected /json/version: {body[:80]}"}
    except Exception as e:
        rel = win(f"Start-Process -FilePath '{CDP_LAUNCH_BAT}' "
                  "-WindowStyle Hidden; 'launched'", timeout=15)
        return {"ok": False,
                "detail": f"CDP :9229 down ({str(e)[:60]}); "
                          f"relaunched cdp-launch={rel.strip()[:40]}"}


def tg(text):
    try:
        subprocess.run(["python3", TG_ALERT, "ops", text], timeout=30)
    except Exception as e:
        log(f"tg failed: {e}")


def main():
    alert = "--alert" in sys.argv
    checks = {
        "ram": check_ram(),
        "docker": check_docker(),
        "chromedebug_task": check_chromedebug_task(),
        "docker_desktop_proc": check_docker_desktop_proc(),
        "dmr_tunnel": check_dmr_tunnel(),
        "cdp_lane": check_cdp_lane(),
    }
    # IDE-authority loop is undetectable — always surface as a note.
    degraded = [k for k, v in checks.items() if v.get("ok") is False]
    result = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "degraded": degraded, "checks": checks}
    if "--json" in sys.argv:
        print(json.dumps(result, indent=1))
        return 0

    print(f"{'gate':22} {'state':8} detail")
    for k, v in checks.items():
        state = "ok" if v.get("ok") else ("warn" if v.get("ok") is False
                                        else "?")
        print(f"{k:22} {state:8} {v.get('detail','')}")
    print("\nIDE note: if devin-server install-spam returns, close the IDE's "
          "'wsl+docker-desktop' remote authority — use 'wsl+Ubuntu-24.04'.")

    if alert and degraded:
        st = {}
        try:
            st = json.load(open(STATE))
        except Exception:
            pass
        sig = ",".join(sorted(degraded))
        if time.time() - st.get("last_alert", 0) > DEDUP_SECS or \
                st.get("last_sig") != sig:
            lines = "\n".join(f"• {k}: {checks[k].get('detail','')}"
                              for k in degraded)
            tg(f"🖥️ host-gates degraded ({sig}):\n{lines}\n"
               "Remediation stays with the existing healers — this is the "
               "human-visible digest.")
            st = {"last_alert": time.time(), "last_sig": sig}
            os.makedirs(STATE_DIR, exist_ok=True)
            json.dump(st, open(STATE, "w"))
    log(f"checks done degraded={degraded or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
