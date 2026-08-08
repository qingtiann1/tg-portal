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
    """统计今日转发数据"""
    today = time.strftime("%Y-%m-%d")
    stats = {"forwarded": 0, "skipped": 0, "errors": 0, "dup_hard": 0, "dup_soft": 0}

    # 从各群进度文件汇总
    if os.path.exists(SDIR):
        for fname in os.listdir(SDIR):
            if fname.startswith("fwd_") and fname.endswith(".json"):
                try:
                    with open(os.path.join(SDIR, fname)) as f:
                        p = json.load(f)
                    stats["forwarded"] += p.get("forwarded", 0)
                    stats["skipped"] += p.get("skipped", 0)
                    stats["errors"] += p.get("errors", 0)
                except:
                    pass

    # 去重统计
    fwd_log = os.path.join(SDIR, "forwarded_log.json")
    if os.path.exists(fwd_log):
        try:
            with open(fwd_log) as f:
                log = json.load(f)
            stats["unique_files"] = len(log)
        except:
            pass

    # 下载统计
    dedup_file = os.path.join(SDIR, "downloaded_ids.txt")
    if os.path.exists(dedup_file):
        try:
            with open(dedup_file) as f:
                stats["downloaded"] = sum(1 for _ in f)
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
        f"📤 今日转发: <b>{stats['forwarded']}</b>\n"
        f"⏭️ 跳过: {stats['skipped']}\n"
        f"❌ 错误: {stats['errors']}\n"
        f"🔗 去重文件: {stats.get('unique_files', 0)}\n"
        f"📥 累计下载: {stats.get('downloaded', 0)}"
    )
    return send_message(cfg["token"], cfg["chat_id"], msg)


def send_test_message(token, chat_id):
    """发送测试消息"""
    return send_message(token, chat_id,
        "<b>✅ TG Portal 通知测试成功</b>\n\n如果你收到这条消息，说明 Bot 配置正确。")
