#!/usr/bin/env python3
"""Standalone TG notification script - called from web UI"""
import json, urllib.request, sys, time, traceback

LOG_FILE = "/app/log/notify.log"

def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

try:
    text = sys.argv[1] if len(sys.argv) > 1 else ""
    if not text:
        log("ERROR: no text argument")
        sys.exit(1)

    with open("/app/sessions/bot_config.json") as f:
        cfg = json.load(f)

    if not cfg.get("enabled") or not cfg.get("token") or not cfg.get("chat_id"):
        log("ERROR: bot not enabled or missing config")
        sys.exit(1)

    data = json.dumps({"chat_id": cfg["chat_id"], "text": text, "parse_mode": "HTML"}).encode()
    proxy = urllib.request.ProxyHandler({"https": "http://mihomo:7890"})
    opener = urllib.request.build_opener(proxy)
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{cfg['token']}/sendMessage",
        data=data, headers={"Content-Type": "application/json"})
    resp = opener.open(req, timeout=10)
    result = json.loads(resp.read())
    if result.get("ok"):
        log(f"OK: msg_id={result['result']['message_id']} text={text[:60]}")
    else:
        log(f"API_ERROR: {result}")
except Exception as e:
    log(f"EXCEPTION: {e}\n{traceback.format_exc()}")
    sys.exit(1)
