"""
DRY-RUN v2: bulk scan TGdown, report spam candidates (no deletion)
"""
import asyncio, json, os, re
from collections import Counter
from pyrogram import Client

SDIR = "/app/sessions" if os.path.isdir("/app/sessions") else "/sessions"
FWD_LOG = os.path.join(SDIR, "forwarded_log.json")
DEST = -1004420616732
URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)

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


def is_spam_text(text):
    for phrase in SKIP_PHRASES:
        if phrase in text:
            return True, f"BL:{phrase}"
    has_gamble = any(w in text for w in GAMBLING_WORDS)
    if not has_gamble:
        return False, ""
    has_url = bool(URL_RE.search(text))
    if has_url:
        return True, "gamble+url"
    ec = sum(1 for ch in text if ord(ch) > 0x2600)
    if len(text) > 30 and ec / len(text) > 0.3:
        return True, "gamble+emoji"
    return False, ""


async def main():
    client = Client(
        os.path.join(SDIR, "media_downloader"),
        api_id=30431350,
        api_hash="dd31870e60686ad7b7fd01b2ac544259",
        proxy={"scheme": "http", "hostname": "mihomo", "port": 7890},
    )
    await client.start()
    me = await client.get_me()
    print(f"Login: {me.first_name}")

    # Load known forwarded msg_ids
    with open(FWD_LOG) as f:
        fwd = json.load(f)
    fwd_msg_ids = {v["msg_id"] for v in fwd.values() if v.get("msg_id")}

    # Bulk scan TGdown (last 9000 messages)
    print(f"Scanning TGdown (bulk, up to 9000 msgs)...")
    spam = []
    checked = 0
    async for msg in client.get_chat_history(DEST, limit=9000):
        checked += 1
        if msg.id not in fwd_msg_ids:
            continue  # only check forwarded messages

        text = msg.caption or msg.text or ""
        is_sp, reason = is_spam_text(text)
        if is_sp:
            spam.append((msg.id, reason, text[:100]))
            print(f"  SPAM msg {msg.id} | {reason} | {text[:80]}")

        if checked % 1000 == 0:
            print(f"  scanned {checked}...")

    print(f"\n{'='*50}")
    print(f"DRY-RUN RESULT")
    print(f"{'='*50}")
    print(f"Forwarded msgs scanned: {len(fwd_msg_ids)}")
    print(f"Spam candidates: {len(spam)}")
    print()

    reasons = Counter(r for _, r, _ in spam)
    for r, c in reasons.most_common():
        print(f"  {r}: {c}")

    print(f"\n--- Details ---")
    for msg_id, reason, caption in spam:
        print(f"  msg {msg_id} | {reason} | {caption}")

    print(f"\nDRY-RUN COMPLETE - nothing deleted")
    await client.stop()


asyncio.run(main())
