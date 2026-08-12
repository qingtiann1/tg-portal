"""Status report script"""
import json, os, time, subprocess

print("Time:", time.strftime("%Y-%m-%d %H:%M:%S"))
print()

# 1. Scheduler log
log_file = "/app/log/scheduler.log"
if os.path.exists(log_file):
    with open(log_file) as f:
        lines = [l.strip() for l in f if l.strip()]
    seen = set()
    deduped = []
    for l in lines:
        if l not in seen:
            seen.add(l)
            deduped.append(l)
    print("=== Scheduler (last 20) ===")
    for l in deduped[-20:]:
        print(l)
print()

# 2. forwarded_log
fwd_log = "/app/sessions/forwarded_log.json"
if os.path.exists(fwd_log):
    with open(fwd_log) as f:
        d = json.load(f)
    print("forwarded_log entries:", len(d))
print()

# 3. Processes
result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
for line in result.stdout.split("\n"):
    if "python" in line and "grep" not in line:
        print(line[:200])
print()

# 4. Sources
src_cfg = "/app/sessions/sources_config.json"
with open(src_cfg) as f:
    sources = json.load(f)
enabled = [s for s in sources if s.get("enabled")]
completed = [s for s in sources if s.get("complete")]
upload_pending = [s for s in sources if s.get("method") == "upload" and s.get("enabled") and not s.get("complete")]
watch = [s for s in sources if s.get("mode") == "watch" and s.get("enabled")]
print("=== Sources ===")
print("Total: {}, Enabled: {}, Completed: {}".format(len(sources), len(enabled), len(completed)))
print("Watch: {}".format([s["name"] for s in watch]))
print("Upload pending: {}".format([s["name"] for s in upload_pending]))
print()

# 5. Progress
pf = "/app/sessions/fwd_zuoai_caobi.json"
if os.path.exists(pf):
    with open(pf) as f:
        p = json.load(f)
    print("Progress: last_id={} fwd={} skip={}".format(p.get("last_id",0), p.get("forwarded",0), p.get("skipped",0)))
else:
    print("Progress file: not yet (watch scan running)")
print()

# 6. Upload queue
uq = "/app/sessions/upload_queue.json"
if os.path.exists(uq):
    with open(uq) as f:
        q = json.load(f)
    print("Upload queue:", json.dumps(q, ensure_ascii=False))
else:
    print("Upload queue: empty (upload window at 02:05)")

print()
print("Done.")
