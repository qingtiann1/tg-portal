"""Final fix: find ALL broken albums, delete broken ones, re-forward properly"""
import asyncio, json, re
from collections import defaultdict
from pyrogram import Client

SDIR = "/sessions"
SRC = -1001956237835
DEST = -1004420616732


async def main():
    c = Client(
        SDIR + "/_scan",
        api_id=30431350, api_hash="dd31870e60686ad7b7fd01b2ac544259",
        proxy={"scheme": "http", "hostname": "mihomo", "port": 7890},
    )
    await c.start()
    print("Login:", (await c.get_me()).first_name)

    with open(SDIR + "/forwarded_log.json") as f:
        fwd = json.load(f)
    existing_fids = set(fwd.keys())

    # Find ALL media groups
    print("Scanning source...")
    groups = defaultdict(list)
    async for msg in c.get_chat_history(SRC, limit=9000):
        gid = getattr(msg, "media_group_id", None)
        if gid:
            groups[gid].append(msg)

    # Find broken groups (partial forward) and unforwarded groups
    to_delete = []
    to_refwd = []

    for gid, msgs in groups.items():
        if len(msgs) <= 1:
            continue
        fwd_count = 0
        for msg in msgs:
            fid = (
                msg.video.file_unique_id if msg.video else
                msg.photo.file_unique_id if msg.photo else
                msg.document.file_unique_id if msg.document else None
            )
            if fid and fid in existing_fids:
                fwd_count += 1

        if 0 < fwd_count < len(msgs):
            # Broken: some forwarded, some not
            for msg in msgs:
                fid = (
                    msg.video.file_unique_id if msg.video else
                    msg.photo.file_unique_id if msg.photo else
                    msg.document.file_unique_id if msg.document else None
                )
                if fid and fid in fwd:
                    to_delete.append(fwd[fid]["msg_id"])
            to_refwd.append(msgs)
        elif fwd_count == 0:
            to_refwd.append(msgs)

    print("Broken groups: {}".format(len(to_refwd)))
    print("Msgs to delete: {}".format(len(to_delete)))

    # Step 1: Delete broken forwards
    if to_delete:
        print("Deleting broken forwards...")
        delete_set = set(to_delete)
        deleted = 0
        for i in range(0, len(to_delete), 100):
            batch = to_delete[i:i+100]
            try:
                await c.delete_messages(DEST, batch)
                deleted += len(batch)
                print("  del {}/{}".format(deleted, len(to_delete)))
            except Exception as e:
                err = str(e)
                if "FLOOD" in err.upper():
                    m = re.search(r"wait of (\d+) seconds", err)
                    wait = int(m.group(1)) + 5 if m else 65
                    print("  flood {}s...".format(wait))
                    await asyncio.sleep(wait)
                    try:
                        await c.delete_messages(DEST, batch)
                        deleted += len(batch)
                    except Exception:
                        pass
                else:
                    print("  fail: {}".format(err[:80]))
            await asyncio.sleep(3)

    # Step 2: Re-forward ALL groups as albums
    print("Re-forwarding {} groups...".format(len(to_refwd)))
    refwd_count = 0
    new_fwd = {}

    for msgs in to_refwd:
        msg_ids = sorted([m.id for m in msgs])
        caption_msg = next((m for m in msgs if m.caption), msgs[0])
        cap_preview = (caption_msg.caption or "")[:50]

        try:
            sent = await c.forward_messages(DEST, SRC, msg_ids)
            refwd_count += len(msg_ids)

            sent_list = sent if isinstance(sent, list) else [sent]
            for sm in sent_list:
                src_mid = getattr(sm, "forward_from_message_id", 0)
                src_msg = next((m for m in msgs if m.id == src_mid), None)
                if src_msg:
                    fid = (
                        src_msg.video.file_unique_id if src_msg.video else
                        src_msg.photo.file_unique_id if src_msg.photo else
                        src_msg.document.file_unique_id if src_msg.document else None
                    )
                    if fid:
                        meta = {}
                        if src_msg.video:
                            meta = {
                                "duration": getattr(src_msg.video, "duration", 0) or 0,
                                "width": getattr(src_msg.video, "width", 0) or 0,
                                "height": getattr(src_msg.video, "height", 0) or 0,
                                "size": getattr(src_msg.video, "file_size", 0) or 0,
                            }
                        new_fwd[fid] = {
                            "source": "zuoai_caobi",
                            "tgdown_link": "https://t.me/c/4420616732/{}".format(sm.id),
                            "msg_id": sm.id,
                            "time": str(src_msg.date),
                            "meta": meta,
                        }

            print("  [{}/{}] {} msgs | {}".format(
                refwd_count, sum(len(m) for m in to_refwd),
                len(msg_ids), cap_preview))
        except Exception as e:
            err = str(e)
            print("  FAIL: {}".format(err[:100]))
            if "FLOOD" in err.upper():
                await asyncio.sleep(30)

        await asyncio.sleep(2)

    # Save
    cleaned = {k: v for k, v in fwd.items() if v.get("msg_id") not in set(to_delete)}
    cleaned.update(new_fwd)
    with open(SDIR + "/forwarded_log.json", "w") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)

    print("forwarded_log: {} -> {}".format(len(fwd), len(cleaned)))
    print("DONE! Deleted {}, Re-fwd {} groups ({} msgs)".format(
        len(to_delete), len(to_refwd), refwd_count))
    await c.stop()


asyncio.run(main())
