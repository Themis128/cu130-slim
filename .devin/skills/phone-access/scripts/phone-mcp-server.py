#!/usr/bin/env python3
"""phone MCP server — ADB + uiautomator2 for Galaxy S22+ (192.168.1.77:5555)."""
import json, subprocess, sys, re
D="192.168.1.77:5555"
def adb(*a): return subprocess.run(["adb","-s",D,*a],capture_output=True,text=True,timeout=15)
def dump():
    adb("shell","uiautomator","dump","/sdcard/window_dump.xml")
    return adb("shell","cat","/sdcard/window_dump.xml").stdout
TOOLS=[
 {"name":"phone_tap","inputSchema":{"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"}},"required":["x","y"]},"description":"Tap x,y"},
 {"name":"phone_click_text","inputSchema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]},"description":"Accessibility-click text (works where taps fail)"},
 {"name":"phone_app_start","inputSchema":{"type":"object","properties":{"package":{"type":"string"}},"required":["package"]},"description":"Launch app"},
 {"name":"phone_dump","inputSchema":{"type":"object","properties":{}},"description":"Visible texts"},
 {"name":"phone_back","inputSchema":{"type":"object","properties":{}},"description":"Press back"},
 {"name":"phone_screenshot","inputSchema":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]},"description":"Save screenshot"}]
def call(n,a):
    if n=="phone_tap": return adb("shell","input","tap",str(a["x"]),str(a["y"])).returncode==0
    if n=="phone_click_text":
        try:
            import uiautomator2 as u2; e=u2.connect(D)(text=a["text"])
            if e.exists: e.click(); return "clicked"
            return "not found"
        except Exception as x: return str(x)
    if n=="phone_app_start":
        try:
            import uiautomator2 as u2; u2.connect(D).app_start(a["package"]); return "started"
        except Exception as x: return str(x)
    if n=="phone_dump": return [t for t in re.findall(r'text="([^"]+)"',dump()) if t.strip()]
    if n=="phone_back": adb("shell","input","keyevent","KEYCODE_BACK"); return "ok"
    if n=="phone_screenshot":
        r=subprocess.run(["adb","-s",D,"exec-out","screencap","-p"],capture_output=True)
        open(a["path"],"wb").write(r.stdout); return f"saved {len(r.stdout)}b"
    return "unknown"
for line in sys.stdin:
    try:
        q=json.loads(line); i,m,p=q.get("id"),q.get("method"),q.get("params",{})
        if m=="initialize": r={"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"phone","version":"1"}}
        elif m=="tools/list": r={"tools":TOOLS}
        elif m=="tools/call":
            v=call(p.get("name"),p.get("arguments") or {})
            r={"content":[{"type":"text","text":v if isinstance(v,str) else json.dumps(v)}]}
        else: r=None
        if i is not None: sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":i,"result":r or {}})+"\n");sys.stdout.flush()
    except Exception as e:
        sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":q.get("id"),"error":{"code":-32000,"message":str(e)}})+"\n");sys.stdout.flush()
