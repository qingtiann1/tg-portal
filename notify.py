"""TG Bot 通知模块 — 发送每日转发统计"""
import json
import os
import time
import urllib.request

SDIR = "/sessions"
BOT_CFG = os.path.join(SDIR, "bot_config.json")


def load_bot_config():
    if os.path.exists(BOT_CFG):
        try:
            with open(BOT_CFG) as f:
                return json.load(f)
        except:
            pass
    return {"token": "", "chat_id": "", "enabled": False, "daily_report_hour": 20}


def save_bot_config(cfg):
    with open(BOT_CFG, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def send_message(token, chat_id, text):
    """通过 Telegram Bot API 发送消息"""
    if not token or not chat_id:
        return False
    try:
        data = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"[Notify] Failed: {e}")
        return False


def collect_daily_stats():
    """统计当前数据（基于持久化文件，准确可靠）"""
    stats = {
        "unique_files": 0,
        "downloaded": 0,
        "pending_uploads": 0,
        "pending_forwards": 0,
        "watch_groups": 0,
    }

    # 去重文件数（forwarded_log 记录的是 file_unique_id → 转发信息）
    fwd_log = os.path.join(SDIR, "forwarded_log.json")
    if os.path.exists(fwd_log):
        try:
            with open(fwd_log) as f:
                log = json.load(f)
            stats["unique_files"] = len(log)
        except:
            pass

    # 下载计数
    dedup_file = os.path.join(SDIR, "downloaded_ids.txt")
    if os.path.exists(dedup_file):
        try:
            with open(dedup_file) as f:
                stats["downloaded"] = sum(1 for _ in f)
        except:
            pass

    # 待处理任务
    src_cfg = os.path.join(SDIR, "sources_config.json")
    if os.path.exists(src_cfg):
        try:
            with open(src_cfg) as f:
                sources = json.load(f)
            seen = set()
            for s in sources:
                if not s.get("enabled"):
                    continue
                if s.get("mode") == "watch":
                    stats["watch_groups"] += 1
                elif s.get("method") == "upload" and not s.get("complete"):
                    key = (s.get("source"), s.get("name"))
                    if key not in seen:
                        seen.add(key)
                        stats["pending_uploads"] += 1
                elif s.get("method") == "forward" and s.get("mode") == "once" and not s.get("complete"):
                    stats["pending_forwards"] += 1
        except:
            pass

    return stats


def send_daily_report():
    """发送每日报告"""
    cfg = load_bot_config()
    if not cfg.get("enabled") or not cfg.get("token") or not cfg.get("chat_id"):
        return False

    stats = collect_daily_stats()
    msg = (
        f"<b>📊 TG Portal 每日报告</b>\n"
        f"📅 {time.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"📤 已转发去重文件: <b>{stats.get('unique_files', 0)}</b>\n"
        f"📥 累计下载: {stats.get('downloaded', 0)}\n"
        f"👀 监控群组: {stats.get('watch_groups', 0)}\n"
        f"⏳ 待上传: {stats.get('pending_uploads', 0)}\n"
        f"📋 待转发: {stats.get('pending_forwards', 0)}"
    )
    return send_message(cfg["token"], cfg["chat_id"], msg)


def send_test_message(token, chat_id):
    """发送测试消息"""
    return send_message(token, chat_id,
        "<b>✅ TG Portal 通知测试成功</b>\n\n如果你收到这条消息，说明 Bot 配置正确。")
