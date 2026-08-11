"""
Clean spam from TGdown — scan forwarded messages and delete ads
Uses structural rules: gambling keyword + short video/low image count/promo links
"""
import asyncio, json, os, re, sys
from pyrogram import Client

SDIR = "/app/sessions" if os.path.isdir("/app/sessions") else "/sessions"
FWD_LOG = os.path.join(SDIR, "forwarded_log.json")
DEST = -1004420616732  # TGdown

URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
MENTION_RE = re.compile(r"@\w+|t\.me/\w+", re.IGNORECASE)

GAMBLING_WORDS = {
    "HPAY", "hpay", "金运国际", "金运", "百家乐", "BBIN",
    "PG电子", "赏金女王", "爆奖", "爆分", "汇旺", "首充", "首存",
    "彩金", "体验金", "打码", "返利", "注册即享", "官方网址",
    "官方客服", "官网注册", "赞助", "频道赞助商",
    "下注", "赌场", "USDT", "提款", "喜提",
    "会员投稿", "会员爆奖", "hpay77",
    "救援金", "回归彩金", "生日礼金", "帕拉梅拉",
}

# Hard blacklist: must skip regardless
SKIP_PHRASES = [
    "男娘", "变性", "gay", "Gay", "GAY", "VR",
    "人妖", "伪娘", "Shemale", "shemale", "ladyboy",
]

def is_spam_text(text):
    """Structural spam detection"""
    if not text:
        return False

    # Hard blacklist (user-requested content filter)
    for phrase in SKIP_PHRASES:
        if phrase in text:
            return True

    has_gamble = any(w in text for w in GAMBLING_WORDS)
    if not has_gamble:
        return False

    # Gambling keyword detected — check structural weakness
    has_url = bool(URL_RE.search(text))
    has_mention = bool(MENTION_RE.search(text))

    # Emoji density
    emoji_ratio = 0
    if len(text) > 30:
        emoji_count = sum(1 for ch in text if ord(ch) > 0x2600)
        emoji_ratio = emoji_count / len(text)

    # Structural rules (same as forward_engine.py):
    # - Gambling + URL → ad
    if has_url:
        return True
    # - Gambling + @mention → ad
    if has_mention:
        return True
    # - Gambling + high emoji density → ad
    if emoji_ratio > 0.3:
        return True

    return False


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
    total = len(fwd)
    checked = 0

    for fid, info in fwd.items():
        checked += 1
        msg_id = info.get("msg_id")
        if not msg_id:
            continue

        try:
            msg = await client.get_messages(DEST, msg_id)
            if msg:
                text = msg.caption or msg.text or ""
                if is_spam_text(text):
                    spam_ids.append(msg_id)
                    print(f"  SPAM [{checked}/{total}] msg {msg_id} | {text[:80]}")
        except Exception as e:
            print(f"  SKIP [{checked}/{total}] msg {msg_id}: {e}")

        if checked % 100 == 0:
            print(f"  ...checked {checked}/{total}")

    print(f"\nTotal: {len(spam_ids)} spam / {total} forwarded")

    if not spam_ids:
        print("Nothing to delete")
        await client.stop()
        return

    # Delete
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

    # Clean forwarded_log
    spam_set = set(spam_ids)
    cleaned = {k: v for k, v in fwd.items() if v.get("msg_id") not in spam_set}
    with open(FWD_LOG, "w") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)
    print(f"forwarded_log: {len(fwd)} -> {len(cleaned)}")
    print(f"DONE! Deleted {deleted} spam messages")
    await client.stop()

asyncio.run(main())
