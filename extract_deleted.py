"""Extract deleted spam msg_ids with source links"""
import json, re

SDIR = "/vol1/1000/docker/tg-downloader/sessions"

# Read cleanup log
with open("/tmp/clean_spam.log") as f:
    log = f.read()

# Extract SPAM msg_ids
spam_ids = set()
for line in log.split('\n'):
    m = re.search(r'SPAM.*?msg (\d+)', line)
    if m:
        spam_ids.add(int(m.group(1)))

print(f"Found {len(spam_ids)} unique spam msg_ids")

# Read forwarded_log
with open(f"{SDIR}/forwarded_log.json") as f:
    fwd = json.load(f)

# Build map: msg_id -> (source, link)
fwd_map = {}
for fid, info in fwd.items():
    msg_id = info.get("msg_id")
    if msg_id:
        fwd_map[msg_id] = (info.get("source", "?"), info.get("tgdown_link", "?"))

# Match
matched = []
unmatched = []
for sid in sorted(spam_ids):
    if sid in fwd_map:
        matched.append((sid,) + fwd_map[sid])
    else:
        unmatched.append(sid)

print(f"Matched: {len(matched)}, Unmatched: {len(unmatched)}")

# Output
lines = ["Deleted spam messages - source group links\n", "=" * 60 + "\n\n"]
lines.append(f"Total deleted: {len(spam_ids)}\n\n")

for sid, source, link in matched:
    lines.append(f"msg {sid}: {link}\n")
    lines.append(f"  source: {source}\n\n")

if unmatched:
    lines.append(f"\nUnmatched ({len(unmatched)}): {unmatched}\n")

# Write to sessions so it's accessible
with open(f"{SDIR}/deleted_list.txt", "w") as f:
    f.writelines(lines)
print("Written to sessions/deleted_list.txt")

# Print first 30 for preview
for sid, source, link in matched[:30]:
    print(f"msg {sid}: {link}  [{source}]")
