#!/usr/bin/env python3
"""
TG Portal Bot — 交互式群组转发管理
用法: python tg_bot.py
通过 Bot API 轮询，无需 Pyrogram session

部署: docker exec -d tg-login python /sessions/tg_bot.py
"""
import json
import os
import re
import sys
import time
import urllib.request

SDIR = "/sessions" if os.path.isdir("/sessions") else "/app/sessions"
BOT_CFG = os.path.join(SDIR, "bot_config.json")
SRC_CFG = os.path.join(SDIR, "sources_config.json")
STATE_FILE = os.path.join(SDIR, "bot_state.json")
OFFSET_FILE = os.path.join(SDIR, "bot_offset.txt")

TG_API = "https://api.telegram.org/bot"


def log(msg):
    print(f"[Bot] {msg}", flush=True)


def load_json(path, default=None):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except:
            pass
    return default or {}


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# 使用 HTTP 代理，节点已切换为稳定的 nat1de-ws-tls
os.environ["HTTPS_PROXY"] = "http://mihomo:7890"


def api(method, data=None, timeout=20):
    """调用 Telegram Bot API"""
    cfg = load_json(BOT_CFG)
    token = cfg.get("token", "")
    if not token:
        return None
    try:
        url = f"{TG_API}{token}/{method}"
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        if "400" not in str(e) and "409" not in str(e):
            log(f"API error: {e}")
        return None


def send_chat_action(chat_id, action="typing"):
    return api("sendChatAction", {"chat_id": chat_id, "action": action})


def send_message(chat_id, text, reply_markup=None, reply_to=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        data["reply_markup"] = reply_markup
    if reply_to:
        data["reply_to_message_id"] = reply_to
    return api("sendMessage", data)


def get_queue_info():
    """获取队列信息：总任务数、待处理数"""
    sources = load_json(SRC_CFG, [])
    total = len(sources)
    pending = len([s for s in sources if not s.get("complete")])
    return total, pending


def edit_message(chat_id, msg_id, text, reply_markup=None):
    data = {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return api("editMessageText", data)


def answer_callback(callback_id, text=""):
    return api("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


# ============================================================
# 链接解析
# ============================================================
def parse_link(text):
    """解析消息中的链接，返回 (type, identifier)"""
    # 消息链接: https://t.me/xxx/123 或 https://t.me/c/1234567/4205
    m = re.search(r"(?:https?://)?t(?:elegram)?\.me/(?:c/)?([^/\s]+)/(\d+)", text)
    if m:
        chat_raw = m.group(1)
        msg_id = int(m.group(2))
        # 私密群组链接格式: t.me/c/1234567890/123 → 转为完整 ID: -1001234567890
        if chat_raw.isdigit() and not chat_raw.startswith("-"):
            chat = f"-100{chat_raw}"
        else:
            chat = chat_raw
        return "message", (chat, msg_id)

    # 群组链接: https://t.me/xxx 或 @xxx
    m = re.search(r"(?:https?://)?t(?:elegram)?\.me/([^/\s]+)", text)
    if m:
        return "group", m.group(1)

    # 纯数字 ID
    m = re.search(r"(-?\d{10,})", text)
    if m:
        return "group", m.group(1)

    return None, None


# ============================================================
# 会话状态管理
# ============================================================
def get_state(chat_id):
    states = load_json(STATE_FILE, {})
    return states.get(str(chat_id), {})


def set_state(chat_id, key, value):
    states = load_json(STATE_FILE, {})
    cid = str(chat_id)
    if cid not in states:
        states[cid] = {}
    states[cid][key] = value
    save_json(STATE_FILE, states)


def clear_state(chat_id):
    states = load_json(STATE_FILE, {})
    states.pop(str(chat_id), None)
    save_json(STATE_FILE, states)


# ============================================================
# 键盘
# ============================================================
def keyboard(buttons):
    return {"inline_keyboard": buttons}


METHOD_KB = keyboard([
    [{"text": "🔄 可转存（批量）", "callback_data": "method:forward"},
     {"text": "⬇️ 需下载上传", "callback_data": "method:upload"}],
    [{"text": "❌ 取消", "callback_data": "cancel"}],
])

MODE_KB = keyboard([
    [{"text": "📦 一次性转存", "callback_data": "mode:once"},
     {"text": "👁️ 持续监控", "callback_data": "mode:watch"}],
    [{"text": "❌ 取消", "callback_data": "cancel"}],
])

INTERVAL_KB = keyboard([
    [{"text": "1小时", "callback_data": "interval:1"},
     {"text": "3小时", "callback_data": "interval:3"},
     {"text": "6小时", "callback_data": "interval:6"}],
    [{"text": "12小时", "callback_data": "interval:12"},
     {"text": "24小时", "callback_data": "interval:24"}],
    [{"text": "❌ 取消", "callback_data": "cancel"}],
])

MSG_METHOD_KB = keyboard([
    [{"text": "🔄 可转存", "callback_data": "msg_method:forward"},
     {"text": "⬇️ 需上传", "callback_data": "msg_method:upload"}],
    [{"text": "❌ 取消", "callback_data": "cancel"}],
])


# ============================================================
# 命令处理
# ============================================================
def handle_start(chat_id):
    send_message(chat_id,
        "<b>📡 TG Portal Bot</b>\n\n"
        "直接发送群组链接或消息链接即可开始。\n\n"
        "命令:\n"
        "/stats - 查看统计\n"
        "/list - 查看监控列表\n"
        "/add - 快捷添加（默认一次性+可转存）\n"
        "/help - 帮助")


def handle_stats(chat_id):
    from notify import collect_daily_stats
    s = collect_daily_stats()
    send_message(chat_id,
        f"<b>📊 TG Portal 统计</b>\n\n"
        f"📤 去重转发文件: <b>{s.get('unique_files', 0)}</b>\n"
        f"📥 累计下载: {s.get('downloaded', 0)}\n"
        f"👀 监控群组: {s.get('watch_groups', 0)}\n"
        f"⏳ 待上传: {s.get('pending_uploads', 0)}\n"
        f"📋 待转发: {s.get('pending_forwards', 0)}")


def handle_list(chat_id):
    sources = load_json(SRC_CFG, [])
    if not sources:
        send_message(chat_id, "暂无监控群组。发送群链接添加。")
        return
    lines = ["<b>📋 监控列表</b>\n"]
    for s in sources:
        icon = "🟢" if s.get("enabled") else "🔴"
        mode = "👁️监控" if s.get("mode") == "watch" else "📦一次"
        method = "批量" if s.get("method") == "forward" else "上传"
        interval = s.get("watch_interval_hours", "?")
        done = "✅" if s.get("complete") else ""
        lines.append(f"{icon} <b>{s['name']}</b> {mode} {method} {interval}h {done}")
    send_message(chat_id, "\n".join(lines))


def handle_add(chat_id, reply_to, text):
    """快捷添加：从文本中提取链接，添加为一次性+可转存"""
    link_type, info = parse_link(text)
    total, pending = get_queue_info()
    queue_info = f"\n📋 队列 #{total}: 共 {total} 个源群，{pending} 个待处理"

    if link_type == "group":
        group_id = info
        name = f"bot_{group_id.replace('/','_')[:20]}"
        sources = load_json(SRC_CFG, [])
        for s in sources:
            if s["name"] == name:
                send_message(chat_id, f"⚠️ <b>{name}</b> 已存在", reply_to=reply_to)
                return
        sources.append({
            "name": name, "source": group_id, "method": "forward",
            "enabled": True, "mode": "once", "complete": False,
            "watch_interval_hours": 6, "skip_photos": False,
            "min_video_mb": 5, "extra_skip_words": [],
        })
        save_json(SRC_CFG, sources)
        send_message(chat_id, f"✅ 已添加: <b>{name}</b>\n方式: 一次性转存 + 可转发{queue_info}", reply_to=reply_to)
    elif link_type == "message":
        send_message(chat_id, "消息链接请直接发送完整链接进行交互式添加", reply_to=reply_to)
    else:
        send_message(chat_id, "未识别到有效链接，请发送群组或消息链接", reply_to=reply_to)


# ============================================================
# 回调处理
# ============================================================
def handle_callback(callback_id, chat_id, msg_id, data):
    state = get_state(chat_id)
    answer_callback(callback_id)

    if data == "cancel":
        clear_state(chat_id)
        edit_message(chat_id, msg_id, "❌ 已取消")
        return

    # 消息链接流程
    if data.startswith("msg_method:"):
        method = data.split(":")[1]
        info = state.get("pending_info")
        if info:
            chat_name, message_id = info
            name = f"msg_{chat_name}_{message_id}"
            sources = load_json(SRC_CFG, [])
            sources.append({
                "name": name, "source": chat_name, "method": method,
                "enabled": True, "mode": "once", "complete": False,
                "watch_interval_hours": 6, "skip_photos": False,
                "min_video_mb": 5, "extra_skip_words": [],
            })
            save_json(SRC_CFG, sources)
            clear_state(chat_id)
            method_text = "🔄 转存" if method == "forward" else "⬇️ 下载上传"
            if method == "upload":
                time_note = "\n⏰ 将在每日 02:05-16:00 自动处理"
            else:
                time_note = ""
            edit_message(chat_id, msg_id,
                f"✅ 已添加单条消息 ({method_text})\n"
                f"📌 群组: {chat_name}\n"
                f"📨 消息ID: {message_id}{time_note}")
        return

    # 群组链接流程
    if data.startswith("method:"):
        method = data.split(":")[1]
        set_state(chat_id, "method", method)
        set_state(chat_id, "step", "mode")
        edit_message(chat_id, msg_id,
            f"已选择: {'🔄 可转存' if method == 'forward' else '⬇️ 需上传'}\n\n请选择转发模式:",
            reply_markup=MODE_KB)
        return

    if data.startswith("mode:"):
        mode = data.split(":")[1]
        set_state(chat_id, "mode", mode)
        if mode == "watch":
            set_state(chat_id, "step", "interval")
            edit_message(chat_id, msg_id, "请选择监控间隔:", reply_markup=INTERVAL_KB)
        else:
            # 一次性 → 直接完成
            finalize_source(chat_id, msg_id)
        return

    if data.startswith("interval:"):
        hours = int(data.split(":")[1])
        set_state(chat_id, "watch_interval_hours", hours)
        finalize_source(chat_id, msg_id)
        return


def finalize_source(chat_id, msg_id):
    """完成添加源群"""
    state = get_state(chat_id)
    group_id = state.get("pending_group", "")
    method = state.get("method", "forward")
    mode = state.get("mode", "once")
    interval = state.get("watch_interval_hours", 6)

    name = f"bot_{group_id.replace('/','_')[:20]}"
    sources = load_json(SRC_CFG, [])
    for s in sources:
        if s["name"] == name:
            clear_state(chat_id)
            edit_message(chat_id, msg_id, f"⚠️ 群组 <b>{name}</b> 已存在，请用 /list 查看")
            return

    sources.append({
        "name": name, "source": group_id, "method": method,
        "enabled": True, "mode": mode, "complete": False,
        "watch_interval_hours": interval,
        "skip_photos": method == "upload",
        "min_video_mb": 5, "extra_skip_words": [],
    })
    save_json(SRC_CFG, sources)
    clear_state(chat_id)

    total, pending = get_queue_info()
    queue_info = f"\n📋 队列: 共 {total} 个源群，{pending} 个待处理"
    mode_text = f"👁️ 持续监控 (每{interval}h)" if mode == "watch" else "📦 一次性转存"
    method_text = "🔄 可转存" if method == "forward" else "⬇️ 需下载上传"

    if mode == "watch":
        status_note = "👁️ 自动监控运行中 (16:00-02:00)"
    elif method == "upload":
        status_note = "⏰ 将在每日 02:05-16:00 自动下载上传"
    else:
        status_note = "📤 将在下一次转发周期自动处理"

    edit_message(chat_id, msg_id,
        f"<b>✅ 添加成功!</b> #{total}\n\n"
        f"群组: <code>{group_id}</code>\n"
        f"方式: {method_text}\n"
        f"模式: {mode_text}{queue_info}\n\n"
        f"{status_note}")


# ============================================================
# 主循环
# ============================================================
def process_message(msg):
    """处理收到的消息"""
    chat_id = msg["chat"]["id"]
    msg_id = msg.get("message_id")
    text = msg.get("text", "").strip()
    if not text:
        return

    # 立即显示"已看到，处理中"
    send_chat_action(chat_id, "typing")

    def reply(text, **kw):
        return send_message(chat_id, text, reply_to=msg_id, **kw)

    # 命令
    if text.startswith("/start") or text.startswith("/help"):
        handle_start(chat_id)
        return
    if text.startswith("/stats"):
        handle_stats(chat_id)
        return
    if text.startswith("/list"):
        handle_list(chat_id)
        return
    if text.startswith("/add"):
        handle_add(chat_id, msg_id, text[5:].strip() or text)
        return

    # 解析链接
    link_type, info = parse_link(text)
    total, pending = get_queue_info()
    queue_info = f"\n\n📋 队列: 共 {total} 个源群，{pending} 个待处理"

    if not link_type:
        reply("❌ 未识别到链接。请发送:\n• 群组链接 https://t.me/xxx\n• 消息链接 https://t.me/xxx/123\n• 或使用 /add 快捷添加" + queue_info)
        return

    if link_type == "group":
        set_state(chat_id, "pending_group", info)
        set_state(chat_id, "step", "method")
        reply(f"👀 已识别群组: <code>{info}</code>{queue_info}\n\n请选择转发方式:",
              reply_markup=METHOD_KB)

    elif link_type == "message":
        chat_name, message_id = info
        set_state(chat_id, "pending_info", (chat_name, message_id))
        set_state(chat_id, "step", "msg_method")
        reply(f"👀 已识别消息链接\n群组: <code>{chat_name}</code>\n消息: {message_id}{queue_info}\n\n请选择转发方式:",
              reply_markup=MSG_METHOD_KB)


def process_update(update):
    if "message" in update:
        msg = update["message"]
        if "text" in msg:
            process_message(msg)
    elif "callback_query" in update:
        cb = update["callback_query"]
        handle_callback(
            cb["id"],
            cb["message"]["chat"]["id"],
            cb["message"]["message_id"],
            cb["data"]
        )


def main():
    token = load_json(BOT_CFG).get("token", "")
    if not token:
        log("Bot token not configured. Set in web UI: Forward -> 通知")
        return

    # 加载上次 offset，避免重复处理旧消息
    try:
        with open(OFFSET_FILE) as f:
            offset = int(f.read().strip())
    except:
        offset = 0
    log(f"Bot started. Polling from offset={offset}...")

    while True:
        try:
            params = {"offset": offset, "limit": 10}
            result = api("getUpdates", params, timeout=20)
            if result and result.get("ok"):
                for update in result.get("result", []):
                    process_update(update)
                    offset = update["update_id"] + 1
                if result.get("result"):
                    with open(OFFSET_FILE, "w") as f:
                        f.write(str(offset))
            else:
                log(f"Poll returned: {result}")
            time.sleep(3)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log(f"Poll error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
