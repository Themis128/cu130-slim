#!/usr/bin/env python3
"""Map + fill LinkedIn Qualtrics forms via CDP on the user's Edge (:9222).

  qualtrics_fill.py --map <form-url|tid>        dump question->QID map
  qualtrics_fill.py --answers a.json <url|tid>  fill {QID: "text"|[radio idx]|[checkbox idxs]}
  qualtrics_fill.py --country Greece <url|tid>  pick the custom dropdown option
  qualtrics_fill.py --submit <url|tid>          click Next/Submit
"""
import json, sys, time, urllib.request
import websocket

CDP = "http://localhost:9222"

def open_tab(url_or_tid):
    tabs = json.loads(urllib.request.urlopen(CDP+"/json/list", timeout=10).read())
    t = next((x for x in tabs if x.get("id")==url_or_tid), None)
    if not t:
        ver = json.loads(urllib.request.urlopen(CDP+"/json/version", timeout=10).read())
        b = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=30, suppress_origin=True)
        b.send(json.dumps({"id":1,"method":"Target.createTarget",
              "params":{"url":url_or_tid,"newWindow":False,"background":True}}))
        tid = json.loads(b.recv())["result"]["targetId"]; time.sleep(8)
        tabs = json.loads(urllib.request.urlopen(CDP+"/json/list", timeout=10).read())
        t = next(x for x in tabs if x.get("id")==tid)
    return websocket.create_co
