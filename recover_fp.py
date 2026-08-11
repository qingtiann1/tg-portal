"""
Recover falsely deleted messages from source group.
Strategy: scan source group, re-forward messages that are NOT in forwarded_log
and NOT flagged as spam by our structural rules.
"""
import asyncio, json, os, re, sys
from pyrogram import Client

SDIR = "/app/sessions" if os.path.isdir("/app/sessions") else "/sessions"
FWD_LOG = os.path.join(SDIR, "forwarded_log.json")
SRC = -1001956237835  # zuoai_caobi (反差丨人妻丨学生妹丨内射)
DEST = -1004420616732  # TGdown

GAMBLING_WORDS = {
    "HPAY", "hpay", "金运国际", "金运", "百家乐", "BBIN",
    "PG电子", "赏金女王", "爆奖", "爆分", "汇旺", "首充", "首存",
    "彩金", "体验金", "打码", "返利", "注册即享", "官方网址",
    "官方客服", "官网注册", "赞助", "频道赞助商",
    "下注", "赌场", "USDT", "提款", "喜提",
    "会员投稿", "会员爆奖", "hpay77",
    "救援金", "回归彩金", "生日礼金", "帕拉梅拉",
}
SKIP_PHRASES = [
    "男娘", "变性", "gay", "Gay", "GAY", "VR",
    "人妖", "伪娘", "Shemale", "shemale", "ladyboy",
]
URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)

def is_spam(msg):
    text = msg.caption or msg.text or ""
    if not text:
        return False
    for phrase in SKIP_PHRASES:
        if phrase in text:
            return True
    has_gamble = any(w in text for w in GAMBLING_WORDS)
    if not has_gamble:
        return False
    has_url = bool(URL_RE.search(text))
    emoji_ratio = sum(1 for ch in text if ord(ch) > 0x2600) / max(len(text), 1)
    return has_url or emoji_ratio > 0.3


async def main():
    client = Client(
        os.path.join(SDIR, "fwd_engine"),
        api_id=30431350,
        api_hash="dd31870e60686ad7b7fd01b2ac544259",
        proxy={"scheme": "http", "hostname": "mihomo", "port": 7890},
    )
    await client.start()
    me = await client.get_me()
    print(f"Login: {me.first_name}")

    # Load forwarded_log to know what's already forwarded
    if os.path.exists(FWD_LOG):
        with open(FWD_LOG) as f:
            fwd = json.load(f)
    else:
        fwd = {}

    existing_fids = set(fwd.keys())
    print(f"Existing forwarded: {len(existing_fids)}")

    # Scan source group
    recovered = 0
    skipped_spam = 0
    skipped_existing = 0
    new_entries = {}

    print("Scanning source group...")
    async for msg in client.get_chat_history(SRC, limit=300):
        if not msg.video and not msg.photo and not msg.document:
            continue

        fid = None
        if msg.video:
            fid = getattr(msg.video, "file_unique_id", None)
        if not fid and msg.document:
            fid = getattr(msg.document, "file_unique_id", None)

        if not fid:
            continue

        # Skip if already in forwarded_log
        if fid in existing_fids:
            skipped_existing += 1
            continue

        # Skip if spam
        if is_spam(msg):
            skipped_spam += 1
            continue

        # Forward this message
        try:
            fwd_msg = await client.forward_messages(DEST, SRC, msg.id)
            caption = (msg.caption or "")[:60]
            print(f"  [{recovered+1}] src msg {msg.id} -> TGdown {fwd_msg.id} | {caption}")

            # Record in new entries
            tgdown_link = f"https://t.me/c/4420616732/{fwd_msg.id}"
            new_entries[fid] = {
                "source": "zuoai_caobi",
                "tgdown_link": tgdown_link,
                "msg_id": fwd_msg.id,
                "src_msg_id": msg.id,
                "time": str(msg.date),
            }
            recovered += 1
        except Exception as e:
            err = str(e)[:100]
            print(f"  FAIL src msg {msg.id}: {err}")
            if "FLOOD" in err.upper():
                wait = 30
                print(f"  flood wait {wait}s...")
                await asyncio.sleep(wait)

        await asyncio.sleep(2)

    # Merge into forwarded_log
    fwd.update(new_entries)
    with open(FWD_LOG, "w") as f:
        json.dump(fwd, f, ensure_ascii=False, indent=2)

    print(f"\nDone! Recovered: {recovered}, Spam skipped: {skipped_spam}, Existing: {skipped_existing}")
    print(f"forwarded_log: {len(fwd)} entries")
    await client.stop()

asyncio.run(main())
