"""
统一转发引擎 — 支持多源群，支持监控模式

用法：
  docker exec tg-login python /sessions/forward_engine.py              # 处理所有启用的源群
  docker exec tg-login python /sessions/forward_engine.py --dry        # 仅扫描不转发
  docker exec tg-login python /sessions/forward_engine.py --watch     # 监控模式，定时检查新消息
  docker exec tg-login python /sessions/forward_engine.py --group xxx  # 指定群
"""
import asyncio
import json
import os
import re
import sys
import tempfile
import time as _time
from pyrogram import Client

# ============================================================
# 配置 — 通过环境变量传入，避免硬编码泄露
# ============================================================
import os as _os

API_ID = int(_os.environ.get("TG_API_ID", "0"))
API_HASH = _os.environ.get("TG_API_HASH", "")
DEST = int(_os.environ.get("TG_DEST_GROUP", "0"))
PROXY_HOST = _os.environ.get("TG_PROXY_HOST", "mihomo")
PROXY_PORT = int(_os.environ.get("TG_PROXY_PORT", "7890"))
PROXY = {"scheme": "http", "hostname": PROXY_HOST, "port": PROXY_PORT}

SDIR = "/app/sessions" if _os.path.isdir("/app/sessions") else "/sessions"
SESSION = _os.path.join(SDIR, "media_downloader")
DEDUP_FILE = _os.path.join(SDIR, "downloaded_ids.txt")
FWD_LOG_FILE = _os.path.join(SDIR, "forwarded_log.json")
SRC_CFG_FILE = _os.path.join(SDIR, "sources_config.json")   # 源群配置（网页可编辑）
DEFAULT_WATCH_INTERVAL_HOURS = 6  # 默认监控检查间隔（小时）

MIN_DURATION = 10      # 视频最少秒数
FW_BATCH = 10           # 批量转发每批条数
FW_DELAY = 10           # 批量间隔
SINGLE_DELAY = 8        # 下载上传间隔

# ============================================================
# 源群配置 — 从 sources_config.json 加载（网页可管理）
# 格式:
# [{"name":"xxx", "source":"https://t.me/xxx", "method":"forward",
#   "enabled":true, "watch":false, "watch_interval":30,
#   "skip_photos":false, "min_video_mb":5, "extra_skip_words":[]}]
# ============================================================
def load_sources():
    if _os.path.exists(SRC_CFG_FILE):
        try:
            with open(SRC_CFG_FILE) as f:
                return json.load(f)
        except:
            pass
    return []


def save_sources(sources):
    with open(SRC_CFG_FILE, "w") as f:
        json.dump(sources, f, ensure_ascii=False, indent=2)

SOURCES = load_sources()

# ============================================================
# 垃圾过滤
# ============================================================
SKIP_PHRASES = [
    "一键清理", "清理僵尸粉", "僵尸粉", "清除",
    "加群", "进群", "群号", "复制群", "打开群",
    "免费约", "同城约", "私聊", "一对一", "1v1", "1对1",
    "扫码", "关注公众号", "成人站",
]
URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def log(msg):
    print(msg, flush=True)


# ============================================================
# 去重：记录每个转发过的文件，防止不同群之间重复转发
# ============================================================
def load_forwarded_log():
    if _os.path.exists(FWD_LOG_FILE):
        try:
            with open(FWD_LOG_FILE) as f:
                return json.load(f)
        except:
            pass
    return {}


def save_forwarded_log(data):
    with open(FWD_LOG_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_msg_fid(msg):
    """获取消息的文件唯一ID"""
    if msg.video and msg.video.file_unique_id:
        return msg.video.file_unique_id
    if msg.photo and msg.photo.file_unique_id:
        return msg.photo.file_unique_id
    if msg.document and msg.document.file_unique_id:
        return msg.document.file_unique_id
    return None


def get_msg_meta(msg):
    """获取视频元数据（时长/分辨率/大小），用于相似度比较"""
    if msg.video:
        return {"duration": getattr(msg.video, "duration", 0) or 0,
                "width": getattr(msg.video, "width", 0) or 0,
                "height": getattr(msg.video, "height", 0) or 0,
                "size": getattr(msg.video, "file_size", 0) or 0}
    if msg.document and "video" in (getattr(msg.document, "mime_type", "") or ""):
        return {"duration": getattr(msg.document, "duration", 0) or 0,
                "width": 0, "height": 0,
                "size": getattr(msg.document, "file_size", 0) or 0}
    return None


def tgdown_msg_link(msg_id):
    dest_str = str(abs(DEST))
    if dest_str.startswith("100"):
        dest_str = dest_str[3:]
    return f"https://t.me/c/{dest_str}/{msg_id}"


def check_dup(fid, meta=None):
    """
    双层去重：
    - hard: file_unique_id 完全相同 → 直接跳过
    - soft: 时长相同(±3s)+分辨率相同 → 返回疑似链接，继续转发但标题加注
    返回 (level, info)  level="" 表示不重复
    """
    if not fid:
        return "", ""
    fwd_log = load_forwarded_log()

    if fid in fwd_log:
        orig = fwd_log[fid]
        return "hard", orig.get("tgdown_link", "")

    if meta and meta.get("duration", 0) > 0:
        dur = meta["duration"]
        w = meta.get("width", 0)
        h = meta.get("height", 0)
        for efid, entry in fwd_log.items():
            ed = entry.get("meta", {})
            ed_dur = ed.get("duration", 0)
            # 时长差 ≤1 秒 + 分辨率一致才算
            if ed_dur > 0 and abs(ed_dur - dur) <= 1 and ed.get("width") == w and ed.get("height") == h and w > 0:
                return "soft", entry.get("tgdown_link", "")
    return "", ""


def soft_dup_note(link):
    """生成软去重的标题标注"""
    return f"\n\n⚠️ 疑似重复: {link}"


def record_forward(fid, msg_id, source_name, meta=None):
    """记录转发到 TGdown，含元数据"""
    if not fid:
        return
    fwd_log = load_forwarded_log()
    fwd_log[fid] = {
        "source": source_name,
        "tgdown_link": tgdown_msg_link(msg_id),
        "msg_id": msg_id,
        "time": _os.popen("date -Iseconds").read().strip() if _os.name != "nt" else "",
        "meta": meta or {},
    }
    save_forwarded_log(fwd_log)


def is_spam(msg):
    """判断是否垃圾消息（直接跳过）"""
    text = (msg.text or msg.caption or "")
    tl = text.lower()
    has_video = bool(msg.video or (msg.document and "video" in (getattr(msg.document, "mime_type", "") or "")))
    has_photo = bool(msg.photo)
    has_url = bool(URL_RE.search(tl))

    for phrase in SKIP_PHRASES:
        if phrase in text:
            return True, f"skip:{phrase}"
    if msg.sticker or msg.animation:
        return True, "sticker/gif"
    if has_photo and not has_video and has_url:
        return True, "photo+url"
    if not has_video and not has_photo and has_url and len(text) < 300:
        return True, "text+url"
    if msg.forward_from_chat and not has_video and not has_photo:
        return True, "forward no media"
    if msg.video:
        dur = getattr(msg.video, "duration", 0) or 0
        if dur < MIN_DURATION:
            return True, f"short {dur}s"
    if msg.document:
        mime = getattr(msg.document, "mime_type", "") or ""
        if "video" in mime:
            dur = getattr(msg.document, "duration", 0) or 0
            if dur < MIN_DURATION:
                return True, f"short doc {dur}s"
    return False, ""


def is_media(msg):
    """是否含有效媒体"""
    if msg.video and (getattr(msg.video, "duration", 0) or 0) >= MIN_DURATION:
        return True
    if msg.photo:
        return True
    if msg.document:
        mime = getattr(msg.document, "mime_type", "") or ""
        if "video" in mime and (getattr(msg.document, "duration", 0) or 0) >= MIN_DURATION:
            return True
        if "image" in mime:
            return True
    return False


def has_url(text):
    return bool(URL_RE.search(text)) if text else False


def clean_caption(text):
    if not text:
        return ""
    return URL_RE.sub("", text).strip()


def progress_file(name):
    return _os.path.join(SDIR, f"fwd_{name}.json")


# ============================================================
# 策略1: forward_messages 批量转发
# ============================================================
async def forward_batch(client, src, dst, cfg):
    name = cfg["name"]
    pf = progress_file(name)
    prog = {"last_id": 0, "forwarded": 0, "skipped": 0, "errors": 0}
    if os.path.exists(pf):
        with open(pf) as f:
            prog = json.load(f)
    last_id = prog["last_id"]
    fwd = prog["forwarded"]
    skp = prog["skipped"]
    err = prog["errors"]

    def save():
        with open(pf, "w") as f:
            json.dump({"last_id": last_id, "forwarded": fwd, "skipped": skp, "errors": err}, f)

    # 扫描
    log(f"[{name}] Scanning...")
    all_msgs = []
    async for msg in client.get_chat_history(src.id):
        all_msgs.append(msg)
    all_msgs.reverse()

    valid_ids = []
    soft_dupes = []      # [(msg, caption, dup_link)] 软去重→单独发送加标注
    msg_fid_map = {}     # msg_id -> file_unique_id
    msg_meta_map = {}    # msg_id -> video metadata
    spam_stats = {}
    dup_count = 0
    soft_count = 0
    src_link = f"https://t.me/{cfg['source']}" if isinstance(cfg.get("source"), str) and not str(cfg["source"]).startswith("-") else f"tg://group?id={cfg['source']}"

    for msg in all_msgs:
        s, reason = is_spam(msg, cfg)
        if s:
            spam_stats[reason] = spam_stats.get(reason, 0) + 1
            continue
        if is_media(msg):
            caption = msg.caption or ""
            if has_url(caption):
                continue
            # 去重检查
            fid = get_msg_fid(msg)
            meta = get_msg_meta(msg)
            if fid:
                level, dup_info = check_dup(fid, meta)
                if level == "hard":
                    dup_count += 1
                    log(f"  [DUP] msg {msg.id}: {dup_info}")
                    continue
                if level == "soft":
                    soft_count += 1
                    log(f"  [SIMILAR] msg {msg.id}: {dup_info}")
                    soft_dupes.append((msg, caption, dup_info))
                    msg_fid_map[msg.id] = fid
                    if meta:
                        msg_meta_map[msg.id] = meta
                    continue  # 不进批量，稍后用 file_id 单发加标注
                msg_fid_map[msg.id] = fid
                if meta:
                    msg_meta_map[msg.id] = meta
            valid_ids.append(msg.id)

    pending = [mid for mid in valid_ids if mid > last_id]
    log(f"[{name}] Total:{len(all_msgs)} Spam:{sum(spam_stats.values())} Dup:{dup_count} Soft:{soft_count} Valid:{len(valid_ids)} Pending:{len(pending)}")
    for r, c in sorted(spam_stats.items(), key=lambda x: -x[1])[:8]:
        log(f"  {r}: {c}")

    if not pending:
        log(f"[{name}] All done!")
        if os.path.exists(pf):
            os.remove(pf)
        return

    for i in range(0, len(pending), FW_BATCH):
        batch = pending[i : i + FW_BATCH]
        log(f"[{name}] [{i+1}-{min(i+FW_BATCH, len(pending))}/{len(pending)}] {batch[:5]}...")
        try:
            sent_msgs = await client.forward_messages(dst.id, src.id, batch)
            fwd += len(batch)
            last_id = batch[-1]
            # 记录去重（写入 TGdown 消息链接）
            if sent_msgs:
                for sm in sent_msgs if isinstance(sent_msgs, list) else [sent_msgs]:
                    src_mid = getattr(sm, "forward_from_message_id", 0)
                    if src_mid and src_mid in msg_fid_map:
                        record_forward(msg_fid_map[src_mid], sm.id, name, msg_meta_map.get(src_mid))
            save()
            log(f"  OK ({fwd} total)")
        except Exception as e:
            err_str = str(e)
            if "FLOOD" in err_str.upper():
                m = re.search(r"wait of (\d+) seconds", err_str)
                wait = int(m.group(1)) + 10 if m else 65
                save()
                log(f"  FLOOD {wait}s (saved)...")
                await asyncio.sleep(wait)
                try:
                    sent_msgs = await client.forward_messages(dst.id, src.id, batch)
                    fwd += len(batch)
                    last_id = batch[-1]
                    if sent_msgs:
                        for sm in sent_msgs if isinstance(sent_msgs, list) else [sent_msgs]:
                            src_mid = getattr(sm, "forward_from_message_id", 0)
                            if src_mid and src_mid in msg_fid_map:
                                record_forward(msg_fid_map[src_mid], sm.id, name, msg_meta_map.get(src_mid))
                    save()
                    log(f"  Retry OK ({fwd} total)")
                except Exception as e2:
                    err += len(batch)
                    log(f"  FAIL: {str(e2)[:120]}")
            else:
                err += len(batch)
                log(f"  FAIL: {err_str[:120]}")
        await asyncio.sleep(FW_DELAY)

    # Phase 2: 清洗含链接标题后发送
    dirty = [m for m in all_msgs if m.id > last_id and is_media(m) and has_url(m.caption or "") and not is_spam(m)[0]]
    if dirty:
        log(f"[{name}] Phase 2: cleaning {len(dirty)} messages with URLs...")
        for i, msg in enumerate(dirty):
            cap = clean_caption(msg.caption or "")
            log(f"  [{i+1}/{len(dirty)}] msg {msg.id}: {cap[:60]}")
            try:
                fresh = await client.get_messages(src.id, msg.id)
                if not fresh:
                    skp += 1
                    continue
                if fresh.video:
                    v = fresh.video
                    await client.send_video(dst.id, v.file_id, caption=cap,
                                            width=v.width, height=v.height, duration=v.duration)
                elif fresh.photo:
                    await client.send_photo(dst.id, fresh.photo.file_id, caption=cap)
                elif fresh.document:
                    await client.send_document(dst.id, fresh.document.file_id, caption=cap)
                fwd += 1
                last_id = msg.id
                save()
            except Exception as e:
                err_str = str(e)
                if "FLOOD" in err_str.upper():
                    m = re.search(r"wait of (\d+) seconds", err_str)
                    wait = int(m.group(1)) + 10 if m else 65
                    save()
                    await asyncio.sleep(wait)
                    try:
                        fresh = await client.get_messages(src.id, msg.id)
                        if fresh and fresh.video:
                            await client.send_video(dst.id, fresh.video.file_id, caption=cap,
                                                    width=fresh.video.width, height=fresh.video.height,
                                                    duration=fresh.video.duration)
                            fwd += 1
                            last_id = msg.id
                            save()
                    except Exception as e2:
                        err += 1
                else:
                    err += 1
            await asyncio.sleep(SINGLE_DELAY)

    # Phase 3: 软去重 — 用 file_id 发送 + 标题加疑似重复链接
    if soft_dupes:
        pending_soft = [(m, c, l) for m, c, l in soft_dupes if m.id > last_id]
        if pending_soft:
            log(f"[{name}] Phase 3: soft-dupes {len(pending_soft)} (send with warning)...")
            for i, (msg, cap, link) in enumerate(pending_soft):
                warn_cap = (cap or msg.caption or "") + f"\n\n⚠️ 疑似重复: {link}"
                log(f"  [{i+1}/{len(pending_soft)}] msg {msg.id}: {warn_cap[:80]}")
                try:
                    fresh = await client.get_messages(src.id, msg.id)
                    if not fresh:
                        skp += 1
                        continue
                    if fresh.video:
                        v = fresh.video
                        sent = await client.send_video(dst.id, v.file_id, caption=warn_cap,
                                                        width=v.width, height=v.height, duration=v.duration)
                    elif fresh.photo:
                        sent = await client.send_photo(dst.id, fresh.photo.file_id, caption=warn_cap)
                    elif fresh.document:
                        sent = await client.send_document(dst.id, fresh.document.file_id, caption=warn_cap)
                    else:
                        skp += 1
                        continue
                    if sent:
                        fid = get_msg_fid(fresh) or (sent.video.file_unique_id if sent.video else (sent.photo.file_unique_id if sent.photo else None))
                        if fid:
                            record_forward(fid, sent.id, name, get_msg_meta(fresh))
                    fwd += 1
                    last_id = msg.id
                    save()
                    log(f"  OK")
                except Exception as e:
                    err_str = str(e)
                    if "FLOOD" in err_str.upper():
                        m = re.search(r"wait of (\d+) seconds", err_str)
                        wait = int(m.group(1)) + 5 if m else 65
                        save()
                        await asyncio.sleep(wait)
                    else:
                        err += 1
                await asyncio.sleep(SINGLE_DELAY)

    if os.path.exists(pf):
        os.remove(pf)
    log(f"[{name}] DONE: fwd={fwd} skip={skp} err={err}")


# ============================================================
# 策略2: 下载→上传（禁止转发的群）
# ============================================================
async def upload_group(client, src, dst, cfg):
    name = cfg["name"]
    skip_photos = cfg.get("skip_photos", True)
    pf = progress_file(name)
    prog = {"last_id": 0, "forwarded": 0, "skipped": 0, "errors": 0, "last_error": ""}
    if os.path.exists(pf):
        with open(pf) as f:
            prog = json.load(f)
    last_id = prog["last_id"]
    fwd = prog["forwarded"]
    skp = prog["skipped"]
    err = prog["errors"]
    ler = prog.get("last_error", "")

    def save():
        with open(pf, "w") as f:
            json.dump({"last_id": last_id, "forwarded": fwd, "skipped": skp, "errors": err, "last_error": str(ler)[:200]}, f)

    log(f"[{name}] Scanning...")
    all_msgs = []
    async for msg in client.get_chat_history(src.id):
        all_msgs.append(msg)
    all_msgs.reverse()

    items = []
    spam_stats = {}
    dup_count = 0
    soft_count = 0
    src_link = f"https://t.me/{cfg['source']}" if isinstance(cfg.get("source"), str) and not str(cfg["source"]).startswith("-") else f"tg://group?id={cfg['source']}"
    last_cap_src = 0
    cap_seq = 0

    for i, msg in enumerate(all_msgs):
        s, reason = is_spam(msg, cfg)
        if s:
            spam_stats[reason] = spam_stats.get(reason, 0) + 1
            continue
        if not is_media(msg):
            continue
        if skip_photos and msg.photo and not msg.video:
            continue

        # 去重检查
        fid = get_msg_fid(msg)
        meta = get_msg_meta(msg)
        dup_note = ""
        if fid:
            level, link = check_dup(fid, meta)
            if level == "hard":
                dup_count += 1
                log(f"  [DUP] msg {msg.id}: {link}")
                continue
            if level == "soft":
                soft_count += 1
                log(f"  [SIMILAR] msg {msg.id}: {link}")
                dup_note = f"\n\n⚠️ 疑似重复: {link}"

        caption = msg.caption or ""
        if not caption and msg.video:
            for j in range(i - 1, max(i - 6, -1), -1):
                prev = all_msgs[j]
                pt = (prev.text or prev.caption or "").strip()
                if not pt:
                    continue
                s1 = prev.from_user.id if prev.from_user else None
                s2 = msg.from_user.id if msg.from_user else None
                if not s1 or not s2 or s1 != s2:
                    continue
                td = abs((msg.date - prev.date).total_seconds()) if msg.date and prev.date else 999
                if td <= 60:
                    caption = pt
                    src_id = prev.id
                    if src_id == last_cap_src:
                        cap_seq += 1
                    else:
                        cap_seq = 0
                        last_cap_src = src_id
                    if cap_seq > 0:
                        caption = f"{caption} {cap_seq + 1}"
                    break

        items.append((msg, clean_caption(caption + dup_note)))

    pending = [(m, c) for m, c in items if m.id > last_id]
    log(f"[{name}] Total:{len(all_msgs)} Spam:{sum(spam_stats.values())} Dup:{dup_count} Soft:{soft_count} Items:{len(items)} Pending:{len(pending)}")

    if not pending:
        log(f"[{name}] All done!")
        if os.path.exists(pf):
            os.remove(pf)
        return

    for idx, (msg, cap) in enumerate(pending):
        log(f"[{name}] [{idx+1}/{len(pending)}] msg {msg.id} | {cap[:60]}")
        tmp = tempfile.mktemp(suffix=".mp4")
        try:
            fresh = await client.get_messages(src.id, msg.id)
            if not fresh:
                skp += 1
                last_id = msg.id
                save()
                continue

            await client.download_media(fresh, file_name=tmp)
            fsize = os.path.getsize(tmp)

            if fresh.video:
                v = fresh.video
                sent = await client.send_video(dst.id, tmp, caption=cap,
                                               width=v.width, height=v.height,
                                               duration=int(v.duration or 0))
            elif fresh.photo:
                sent = await client.send_photo(dst.id, tmp, caption=cap)
            else:
                sent = await client.send_document(dst.id, tmp, caption=cap)

            if sent and (sent.video or sent.photo):
                fid = sent.video.file_unique_id if sent.video else sent.photo.file_unique_id
                with open(DEDUP_FILE, "a") as f:
                    f.write(f"{fid}\n")
                # 记录去重（TGdown 消息链接）
                record_forward(fid, sent.id, name, get_msg_meta(msg))

            fwd += 1
            last_id = msg.id
            save()
            log(f"  OK {fsize}B")
        except Exception as e:
            err_str = str(e)
            if "FLOOD" in err_str.upper():
                m = re.search(r"wait of (\d+) seconds", err_str)
                wait = int(m.group(1)) + 5 if m else 65
                save()
                log(f"  FLOOD {wait}s...")
                await asyncio.sleep(wait)
                try:
                    fresh = await client.get_messages(src.id, msg.id)
                    if fresh:
                        await client.download_media(fresh, file_name=tmp)
                        if fresh.video:
                            sent = await client.send_video(dst.id, tmp, caption=cap,
                                                           width=fresh.video.width,
                                                           height=fresh.video.height,
                                                           duration=int(fresh.video.duration or 0))
                            if sent and sent.video:
                                with open(DEDUP_FILE, "a") as f:
                                    f.write(f"{sent.video.file_unique_id}\n")
                                record_forward(sent.video.file_unique_id, sent.id, name, get_msg_meta(fresh))
                            fwd += 1
                            last_id = msg.id
                            save()
                            await asyncio.sleep(SINGLE_DELAY)
                            continue
                except Exception as e2:
                    err += 1
                    ler = str(e2)
            else:
                err += 1
                ler = err_str
            save()
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        await asyncio.sleep(SINGLE_DELAY)

    if os.path.exists(pf):
        os.remove(pf)
    log(f"[{name}] DONE: fwd={fwd} skip={skp} err={err}")


# ============================================================
# 主入口
# ============================================================
async def run_one_source(client, dst, cfg, dry=False):
    """执行单个源群转发，完成后标记 complete"""
    log(f"
{'='*60}")
    log(f"[{cfg['name']}] START method={cfg['method']} mode={cfg.get('mode','once')}")
    log(f"{'='*60}")
    try:
        src = await client.get_chat(cfg["source"])
        log(f"Source: {src.title} (id={src.id})")
    except Exception as e:
        log(f"Failed: {e}")
        return
    if dry:
        log("[DRY RUN]")
        return
    success = True
    error_msg = ""
    try:
        if cfg["method"] == "forward":
            await forward_batch(client, src, dst, cfg)
        elif cfg["method"] == "upload":
            await upload_group(client, src, dst, cfg)
    except Exception as e:
        success = False
        error_msg = str(e)[:300]
        log(f"[{cfg['name']}] FAILED: {error_msg}")

    # Bot 通知
    try:
        from notify import load_bot_config, send_message
        bcfg = load_bot_config()
        if bcfg.get("enabled") and bcfg.get("token") and bcfg.get("chat_id"):
            srcs = load_sources()
            total = len(srcs)
            pending = len([s for s in srcs if not s.get("complete")])
            if success:
                send_message(bcfg["token"], bcfg["chat_id"],
                    f\"✅ 任务完成: <b>{cfg['name']}</b>\\n群组: {cfg['source']}\\n方式: {cfg['method']}\\n\\n📋 剩余 {pending}/{total}\")
            else:
                send_message(bcfg["token"], bcfg["chat_id"],
                    f\"❌ 任务失败: <b>{cfg['name']}</b>\\n群组: {cfg['source']}\\n原因: {error_msg}\\n\\n📋 剩余 {pending}/{total}\")
    except Exception:
        pass

    if cfg.get("mode", "once") == "once":
        srcs = load_sources()
        for s in srcs:
            if s["name"] == cfg["name"]:
                s["complete"] = True
        save_sources(srcs)
        log(f"[{cfg['name']}] Marked complete.")


async def main():
    dry_run = "--dry" in sys.argv
    once_mode = "--once" in sys.argv
    watch_mode = "--watch" in sys.argv
    target = None
    for a in sys.argv:
        if a.startswith("--group="):
            target = a.split("=", 1)[1]

    client = Client(SESSION, api_id=API_ID, api_hash=API_HASH, proxy=PROXY)
    await client.start()
    me = await client.get_me()
    log(f"Login: {me.first_name}")
    dst = await client.get_chat(DEST)
    log(f"Dest: {dst.title}")

    sources = load_sources()
    if not sources:
        log("No sources. Add via web UI: http://nas:5000 -> Forward tab")
        await client.stop()
        return

    active = [s for s in sources if s.get("enabled") and (not target or s["name"] == target)]
    if not active:
        log("No enabled sources.")
        await client.stop()
        return

    if watch_mode:
        watching = [s for s in active if s.get("mode") == "watch"]
        if not watching:
            log("No sources with mode='watch'.")
            await client.stop()
            return
        for s in watching:
            h = s.get("watch_interval_hours", DEFAULT_WATCH_INTERVAL_HOURS)
            log(f"  Watching: {s['name']} every {h}h")
        while True:
            log(f"
[Watch] Checking {len(watching)} groups...")
            for cfg in watching:
                await run_one_source(client, dst, cfg)
            h = min(s.get("watch_interval_hours", DEFAULT_WATCH_INTERVAL_HOURS) for s in watching)
            log(f"[Watch] Sleeping {h}h...")
            await asyncio.sleep(h * 3600)

    elif once_mode or dry_run:
        for cfg in active:
            if cfg.get("complete") and not dry_run:
                log(f"[{cfg['name']}] Already complete, skip.")
                continue
            await run_one_source(client, dst, cfg, dry=dry_run)
        log("
===== ONCE DONE =====")

    else:
        for cfg in active:
            await run_one_source(client, dst, cfg)

    await client.stop()


if __name__ == "__main__":
    asyncio.run(main())