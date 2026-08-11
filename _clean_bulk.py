"""Bulk delete ads from TGdown - matches forward_engine.py rules"""
import asyncio, json, os, re
from pyrogram import Client

SDIR = "/app/sessions" if os.path.isdir("/app/sessions") else "/sessions"
FWD_LOG = os.path.join(SDIR, "forwarded_log.json")
DEST = -1004420616732

URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
MENTION_RE = re.compile(r"@\w+|t\.me/\w+", re.IGNORECASE)
BARE_DOMAIN_RE = re.compile(r'\b[\w-]+\.(?:cc|com|net|xyz|top|vip)\b', re.IGNORECASE)

SKIP_PHRASES = [
    "男娘", "变性", "gay", "Gay", "GAY", "VR",
    "人妖", "伪娘", "Shemale", "shemale", "ladyboy",
]
GAMBLING_WORDS = {
    "HPAY", "HPAY娱乐", "hpay", "金运国际", "金运娱乐", "金运", "BBIN",
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
ABSOLUTE_PAIRS = [
    ("HPAY", "赞助直播"),
    ("帕拉梅拉", "金运"),
    ("BBIN", "百家乐"),
    ("PG电子", "爆分"),
    ("官方网址", "hpay77"),
    ("官方客服号", "汇旺"),
]


def is_spam(text):
    """Same rules as forward_engine.py"""
    tl = text.lower()
    has_url = bool(URL_RE.search(tl))
    has_mention = bool(MENTION_RE.search(tl))
    has_bare_domain = bool(BARE_DOMAIN_RE.search(tl))
    gamble_count = sum(1 for w in GAMBLING_WORDS if w in text)

    # 0. Hard blacklist
    for phrase in SKIP_PHRASES:
        if phrase in text:
            return True, "BL:" + phrase

    # 1. Absolute blocks
    if has_bare_domain and gamble_count >= 1:
        return True, "domain+{}kw".format(gamble_count)
    for a, b in ABSOLUTE_PAIRS:
        if a in text and b in text:
            return True, "{}+{}".format(a, b)
    if gamble_count >= 3 and has_mention:
        return True, "{}kw+mention".format(gamble_count)
    if gamble_count >= 5:
        return True, "dense({}kw)".format(gamble_count)

    # 2. Structural weak signals
    if gamble_count >= 1:
        if has_url or has_mention or has_bare_domain:
            return True, "gamble+link"
        ec = sum(1 for ch in text if ord(ch) > 0x2600)
        if len(text) > 30 and ec / len(text) > 0.3:
            return True, "gamble+emoji"

    return False, ""


async def main():
    client = Client(
        os.path.join(SDIR, "_scan"),
        api_id=30431350,
        api_hash="dd31870e60686ad7b7fd01b2ac544259",
        proxy={"scheme": "http", "hostname": "mihomo", "port": 7890},
    )
    await client.start()
    me = await client.get_me()
    print("Login: {}".format(me.first_name))

    # Load forwarded_log for reference
    with open(FWD_LOG) as f:
        fwd = json.load(f)
    fwd_ids = {v["msg_id"] for v in fwd.values() if v.get("msg_id")}

    # Scan ALL recent TGdown messages (not just forwarded)
    print("Scanning TGdown (all messages)...")
    spam = []
    checked = 0
    async for msg in client.get_chat_history(DEST, limit=9500):
        checked += 1
        try:
            text = msg.caption or msg.text or ""
            if not text:
                continue
            is_sp, reason = is_spam(text)
            if is_sp:
                spam.append(msg.id)
                preview = (text[:80] if len(text) >= 80 else text)
                print("  AD msg {} | {} | {}".format(msg.id, reason, preview))
        except (UnicodeDecodeError, UnicodeError, Exception):
            # skip messages with broken text (corrupted emoji etc)
            pass
        if checked % 1000 == 0:
            print("  scanned {}...".format(checked))

    print("\nTotal scanned: {}".format(checked))
    print("Ads found: {}".format(len(spam)))

    if not spam:
        print("Nothing to delete")
        await client.stop()
        return

    print("Deleting {} ad messages...".format(len(spam)))
    deleted = 0
    for i in range(0, len(spam), 100):
        batch = spam[i:i+100]
        try:
            await client.delete_messages(DEST, batch)
            deleted += len(batch)
            print("  {}/{}".format(deleted, len(spam)))
        except Exception as e:
            err = str(e)
            if "FLOOD" in err.upper():
                m = re.search(r"wait of (\d+) seconds", err)
                wait = int(m.group(1)) + 5 if m else 65
                print("  flood {}s...".format(wait))
                await asyncio.sleep(wait)
                try:
                    await client.delete_messages(DEST, batch)
                    deleted += len(batch)
                except Exception as e2:
                    print("  fail: {}".format(str(e2)[:80]))
            else:
                print("  fail: {}".format(err[:100]))
        await asyncio.sleep(3)

    # Clean forwarded_log of deleted entries
    spam_set = set(spam)
    cleaned = {k: v for k, v in fwd.items() if v.get("msg_id") not in spam_set}
    with open(FWD_LOG, "w") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)
    print("forwarded_log: {} -> {}".format(len(fwd), len(cleaned)))
    print("DONE! Deleted {} ads".format(deleted))
    await client.stop()


asyncio.run(main())
