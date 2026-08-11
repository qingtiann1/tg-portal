"""
Clean spam from TGdown — scan forwarded messages, delete ads
Uses structural rules: gambling keyword + content weakness (short video, few imgs)
"""
import asyncio, json, os, re, sys
from pyrogram import Client

SDIR = "/app/sessions" if os.path.isdir("/app/sessions") else "/sessions"
FWD_LOG = os.path.join(SDIR, "forwarded_log.json")
DEST = -1004420616732  # TGdown

URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)

# Same as forward_engine.py
SKIP_PHRASES = [
    "男娘", "变性", "gay", "Gay", "GAY", "VR",
    "人妖", "伪娘", "Shemale", "shemale", "ladyboy",
]

GAMBLING_WORDS = {
    "HPAY", "hpay", "金运国际", "金运娱乐", "金运", "BBIN",
    "PG电子", "赏金女王", "百家乐",
    "爆奖", "爆分", "汇旺", "彩金", "体验金", "首充", "首存",
    "救援金", "回归彩金", "生日礼金", "提款", "喜提", "USDT",
    "充值赠送", "注册即享",
    "下注", "赌场", "单注金额", "单注倍数", "爆分金额", "视频奖励",
    "一点配三边", "四点配两边", "电子大水中",
    "官方网址", "官方客服号", "官网注册",
    "频道赞助商", "频道专属代码", "赞助直播",
    "会员爆奖", "hpay77", "帕拉梅拉",
}


def is_spam(msg):
    """Structural spam detection - mirrors forward_engine.py"""
    text = msg.caption or msg.text or ""

    # Hard blacklist
    for phrase in SKIP_PHRASES:
        if phrase in text:
            return True, f"blacklist:{phrase}"

    has_gamble = any(w in text for w in GAMBLING_WORDS)
    if not has_gamble:
        return False, ""

    has_video = bool(msg.video or (msg.document and "video" in (getattr(msg.document, "mime_type", "") or "")))
    has_photo = bool(msg.photo)
    has_url = bool(URL_RE.search(text))

    video_dur = 0
    if msg.video:
        video_dur = getattr(msg.video, "duration", 0) or 0
    if msg.document:
        mime = getattr(msg.document, "mime_type", "") or ""
        if "video" in mime:
            video_dur = getattr(msg.document, "duration", 0) or 0

    # Structural rules
    if video_dur > 0 and video_dur <= 25:
        return True, f"gamble+short_video({video_dur}s)"
    if has_photo and not has_video and has_url:
        return True, "gamble+photo+url"
    if not has_video and not has_photo and has_url:
        return True, "gamble+text+url"
    if len(text) > 30:
        emoji_count = sum(1 for ch in text if ord(ch) > 0x2600)
        if emoji_count / len(text) > 0.3:
            return True, "gamble+emoji"

    return False, ""


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

    with open(FWD_LOG) as f:
        fwd = json.load(f)

    spam_ids = []
    checked = 0
    total = len(fwd)

    for fid, info in fwd.items():
        checked += 1
        msg_id = info.get("msg_id")
        if not msg_id:
            continue
        try:
            msg = await client.get_messages(DEST, msg_id)
            if msg:
                is_sp, reason = is_spam(msg)
                if is_sp:
                    spam_ids.append(msg_id)
                    caption = (msg.caption or msg.text or "")[:80]
                    print(f"  SPAM [{checked}/{total}] msg {msg_id} | {reason} | {caption}")
        except Exception as e:
            print(f"  SKIP [{checked}/{total}] msg {msg_id}: {e}")

        if checked % 200 == 0:
            print(f"  ...checked {checked}/{total}")

    print(f"\nFound {len(spam_ids)} spam / {total} total")

    if not spam_ids:
        print("Nothing to delete")
        await client.stop()
        return

    print(f"Deleting {len(spam_ids)} messages...")
    deleted = 0
    for i in range(0, len(spam_ids), 100):
        batch = spam_ids[i:i+100]
        try:
            await client.delete_messages(DEST, batch)
            deleted += len(batch)
            print(f"  {deleted}/{len(spam_ids)}")
        except Exception as e:
            err = str(e)
            if "FLOOD" in err.upper():
                m = re.search(r"wait of (\d+) seconds", err)
                wait = int(m.group(1)) + 5 if m else 65
                print(f"  flood wait {wait}s...")
                await asyncio.sleep(wait)
                try:
                    await client.delete_messages(DEST, batch)
                    deleted += len(batch)
                except Exception as e2:
                    print(f"  fail: {str(e2)[:80]}")
            else:
                print(f"  fail: {err[:100]}")
        await asyncio.sleep(3)

    spam_set = set(spam_ids)
    cleaned = {k: v for k, v in fwd.items() if v.get("msg_id") not in spam_set}
    with open(FWD_LOG, "w") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)
    print(f"forwarded_log: {len(fwd)} -> {len(cleaned)}")
    print(f"DONE! Deleted {deleted} spam")
    await client.stop()

asyncio.run(main())
