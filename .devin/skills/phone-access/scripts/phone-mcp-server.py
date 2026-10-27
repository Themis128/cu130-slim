#!/usr/bin/env python3
"""phone MCP server — ADB + uiautomator2 control for the Galaxy S22+.

Tools: phone_tap, phone_swipe, phone_text, phone_click_text, phone_app_start,
phone_screenshot, phone_dump, phone_state. Device: 192.168.1.77:5555.
"""
import json, subprocess, sys, re

DEVICE = "192.168.1.77:5555"

def adb(*args):
    return subprocess.run(["adb", "-s", DEVICE, *args], capture_output=True, text=True, timeout=15)

def dump():
    adb("shell", "uiautomator", "dump", "/sdcard/window_dump.xml")
    return adb("shell", "cat", "/sdcard/window_dump.xml").stdout

def texts():
    return [t for t in re.findall(r'text="([^"]+)"', dump()) if t.strip()]

TOOLS = [
    {"name":"phone_tap","description":"Tap at x,y","inputSchema":{"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"}},"required":["x","y"]}},
    {"name":"phone_swipe","description":"Swipe x1,y1 → x2,y2","inputSchema":{"type":"object","properties":{"x1":{"type":"integer"},"y1":{"type":"integer"},"x2":{"type":"integer"},"y2":{"type":"integer"},"duration_ms":{"type":"integer","default":300}},"required":["x1","y1","x2","y2"]}},
    {"name":"phone_text","description":"Type text into focused field","inputSchema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}},
    {"name":"phone_click_text","description":"Accessibility-click an element by text (works where taps fail — React-native cards)","inputSchema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}},
    {"name":"phone_app_start","description":"Launch an app by package name","inputSchema":{"type":"object","properties":{"package":{"type":"string"}},"required":["package"]}},
    {"name":"phone_back","description":"Press back","inputSchema":{"type":"object","properties":{}}},
    {"name":"phone_home","description":"Press home","inputSchema":{"type":"object","properties":{}}},
    {"name":"phone_dump","description":"Return all visible text nodes","inputSchema":{"type":"object","properties":{}}},
    {"name":"phone_state","description":"Current foreground app + screen state","inputSchema":{"type":"object","properties":{}}},
    {"name":"phone_screenshot","description":"Save a screenshot to the given Windows path","inputSchema":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}},
]

def call(name, args):
    if name == "phone_tap":
        return adb("shell", "input", "tap", str(args["x"]), str(args["y"])).returncode == 0
    if name == "phone_swipe":
        a = args
        return adb("shell", "input", "swipe", str(a["x1"]), str(a["y1"]), str(a["x2"]), str(a["y2"]), str(a.get("duration_ms",300))).returncode == 0
    if name == "phone_text":
        return adb("shell", "input", "text", args["text"].replace(" ", "%s")).returncode == 0
    if name == "phone_click_text":
        try:
            import uiautomator2 as u2
            d = u2.connect(DEVICE)
            e = d(text=args["text"])
            if e.exists: e.click(); return "clicked"
            return "not found"
        except Exception as ex:
            return f"error: {ex}"
    if name == "phone_app_start":
        try:
            import uiautomator2 as u2
            u2.connect(DEVICE).app_start(args["package"]); return "started"
        except Exception as ex:
            return f"error: {ex}"
    if name == "phone_back":
        adb("shell", "input", "keyevent", "KEYCODE_BACK"); return "ok"
    if name == "phone_home":
        adb("shell", "input", "keyevent", "KEYCODE_HOME"); return "ok"
    if name == "phone_dump":
        return texts()
    if name == "phone_state":
        r = adb("shell", "dumpsys", "window").stdout
        cur = re.search(r"mCurrentFocus=\S+ (\S+)/(\S+)", r)
        return {"focus": f"{cur.group(1)}/{cur.group(2)}" if cur else "?", "texts": texts()[:15]}
    if name == "phone_screenshot":
        r = subprocess.run(["adb","-s",DEVICE,"exec-out","screencap","-p"], capture_output=True)
        with open(args["path"], "wb") as f:
            f.write(r.stdout)
        return f"saved {len(r.stdout)}b to {args['path']}"
    return f"unknown tool {name}"

for line in sys.stdin:
    try:
        req = json.loads(line)
        mid, method, params = req.get("id"), req.get("method"), req.get("params", {})
        if method == "initialize":
            out = {"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"phone","version":"1"}}
        elif method == "tools/list":
            out = {"tools": TOOLS}
        elif method == "tools/call":
            res = call(params.get("name"), params.get("arguments") or {})
            out = {"content":[{"type":"text","text":res if isinstance(res,str) else json.dumps(res)}]}
        elif method == "notifications/initialized":
            continue
        else:
            out = None
        if mid is not None:
            sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":mid,"result":out or {}}) + "\n"); sys.stdout.flush()
    except Exception as e:
        sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":req.get("id"),"error":{"code":-32000,"message":str(e)}}) + "\n"); sys.stdout.flush()
