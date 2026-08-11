"""
TG Portal Scheduler — 24h orchestrator for forward/watch + upload tasks.
Runs inside tg-downloader container.

Time windows (Beijing time):
  16:00 - 02:00  Watch mode: scan source groups every 2h, forward new content
  02:00 - 02:05  Daily restart window
  02:05 - 16:00  Upload mode: process pending upload tasks one by one

Health checks every 30min. Bot notification on failures.
"""
import json, os, re, subprocess, sys, time, traceback
import urllib.request as _ur

SDIR = "/app/sessions" if os.path.isdir("/app/sessions") else "/sessions"
BOT_CFG = os.path.join(SDIR, "bot_config.json")
SRC_CFG = os.path.join(SDIR, "sources_config.json")
UPLOAD_Q = os.path.join(SDIR, "upload_queue.json")  # Pending uploads across restarts
ENGINE = os.path.join(SDIR, "forward_engine.py")
LOG_DIR = "/app/log"

# Time windows (Beijing time = UTC+8, container may use UTC)
# We use localtime via time.localtime()
WATCH_START = (16, 0)   # 16:00
WATCH_END = (2, 0)      # 02:00 next day
RESTART_TIME = (2, 0)   # 02:00-02:05 daily restart
UPLOAD_START = (2, 5)   # 02:05
UPLOAD_END = (16, 0)    # 16:00

WATCH_INTERVAL_HOURS = 2   # Check every 2h during watch window
HEALTH_CHECK_MINUTES = 30  # Health check interval


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = "[{}] {}".format(ts, msg)
    print(line, flush=True)
    try:
        with open(os.path.join(LOG_DIR, "scheduler.log"), "a") as f:
            f.write(line + "\n")
    except:
        pass


def load_bot_cfg():
    if os.path.exists(BOT_CFG):
        try:
            with open(BOT_CFG) as f:
                return json.load(f)
        except:
            pass
    return {"token": "", "chat_id": "", "enabled": False}


def send_tg(msg):
    """Send TG notification via Bot API"""
    cfg = load_bot_cfg()
    if not cfg.get("enabled") or not cfg.get("token") or not cfg.get("chat_id"):
        return False
    try:
        data = json.dumps({
            "chat_id": cfg["chat_id"],
            "text": msg,
            "parse_mode": "HTML"
        }).encode()
        proxy = _ur.ProxyHandler({"https": "http://mihomo:7890"})
        opener = _ur.build_opener(proxy)
        req = _ur.Request(
            "https://api.telegram.org/bot{}/sendMessage".format(cfg["token"]),
            data=data, headers={"Content-Type": "application/json"})
        opener.open(req, timeout=10)
        return True
    except Exception as e:
        log("TG notify failed: {}".format(e))
        return False


def is_watch_time():
    """Check if current time is in watch window (16:00-02:00)"""
    now = time.localtime()
    h, m = now.tm_hour, now.tm_min
    current = h * 60 + m
    watch_start = WATCH_START[0] * 60 + WATCH_START[1]
    watch_end = WATCH_END[0] * 60 + WATCH_END[1]
    if watch_start <= watch_end:
        return watch_start <= current < watch_end
    else:
        # Crosses midnight: 16:00-23:59 or 00:00-02:00
        return current >= watch_start or current < watch_end


def is_upload_time():
    """Check if current time is in upload window (02:05-16:00)"""
    now = time.localtime()
    h, m = now.tm_hour, now.tm_min
    current = h * 60 + m
    upload_start = UPLOAD_START[0] * 60 + UPLOAD_START[1]
    upload_end = UPLOAD_END[0] * 60 + UPLOAD_END[1]
    return upload_start <= current < upload_end


def is_restart_time():
    """Check if it's the daily restart window (02:00-02:05)"""
    now = time.localtime()
    h, m = now.tm_hour, now.tm_min
    current = h * 60 + m
    restart_start = RESTART_TIME[0] * 60 + RESTART_TIME[1]
    return restart_start <= current < restart_start + 5


def kill_engines():
    """Kill all forward engine processes in this container"""
    try:
        subprocess.run(["pkill", "-f", "forward_engine.py"], capture_output=True, timeout=5)
    except:
        pass


def is_engine_running():
    """Check if any forward engine is running"""
    try:
        result = subprocess.run(["pgrep", "-f", "forward_engine.py"], capture_output=True, text=True, timeout=5)
        return bool(result.stdout.strip())
    except:
        return False


def graceful_switch():
    """Wait for current task to finish before allowing mode switch"""
    if is_engine_running():
        log("Waiting for current task to finish before switching...")
        for _ in range(60):  # max 5 minutes wait
            time.sleep(5)
            if not is_engine_running():
                log("Current task finished, switching now")
                return True
        log("Timeout waiting for task, force killing")
        kill_engines()
        time.sleep(3)
    return True


def start_watch():
    """Start forward engine in watch mode for all watch-enabled sources"""
    kill_engines()
    sources = load_sources()
    watching = [s for s in sources if s.get("enabled") and s.get("mode") == "watch"]
    if not watching:
        log("No watch sources configured")
        return
    for s in watching:
        name = s["name"]
        log_file = os.path.join(LOG_DIR, "fwd_{}.log".format(name))
        cmd = "python3 {} --watch {} >> {} 2>&1".format(ENGINE, name, log_file)
        subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log("Started watch: {}".format(name))


def process_uploads():
    """Process pending upload tasks one by one from sources_config"""
    kill_engines()
    sources = load_sources()

    # Load upload queue (persists across scheduler restarts)
    queue_state = {}
    if os.path.exists(UPLOAD_Q):
        try:
            with open(UPLOAD_Q) as f:
                queue_state = json.load(f)
        except:
            pass

    # Build deduplicated upload task list
    seen = set()
    uploads = []
    for s in sources:
        if not s.get("enabled") or s.get("complete"):
            continue
        if s.get("method") != "upload":
            continue
        key = (s.get("source"), s.get("name"))
        if key in seen:
            continue
        seen.add(key)
        # Check if already done in queue state
        name = s["name"]
        if queue_state.get(name) == "done":
            s["complete"] = True
            continue
        uploads.append(s)

    if not uploads:
        log("No pending upload tasks")
        return

    log("Processing {} upload tasks...".format(len(uploads)))
    for i, task in enumerate(uploads):
        if is_watch_time():
            log("Watch window approaching, pausing upload queue ({} remaining)".format(len(uploads) - i))
            break

        src = task["source"]
        name = task["name"]
        log("[{}/{}] Upload: {}".format(i+1, len(uploads), name))

        link = None
        if src.startswith("-100"):
            parts = name.split("_")
            if len(parts) >= 3:
                msg_id = parts[-1]
                link = "https://t.me/c/{}/{}".format(src, msg_id)
        elif not src.startswith("-"):
            parts = name.split("_")
            if len(parts) >= 3:
                msg_id = parts[-1]
                link = "https://t.me/{}/{}".format(src, msg_id)

        if not link:
            log("  Cannot determine link for {}".format(name))
            continue

        cmd = "python3 {} --single \"{}\" --method upload".format(ENGINE, link)
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=600)
            output = result.stdout + result.stderr
            if "DONE" in output or "OK" in output or "Uploaded OK" in output:
                log("  OK: {}".format(name))
                task["complete"] = True
                queue_state[name] = "done"
                save_sources(sources)
            elif "No video" in output:
                log("  SKIP (no video): {}".format(name))
                task["complete"] = True
                queue_state[name] = "skip"
                save_sources(sources)
            else:
                log("  FAIL: {} — {}".format(name, output[-200:]))
                queue_state[name] = "failed"
        except subprocess.TimeoutExpired:
            log("  TIMEOUT: {}".format(name))
            queue_state[name] = "timeout"
        except Exception as e:
            log("  ERROR: {} — {}".format(name, e))
            queue_state[name] = "error"

        # Save queue state after each task
        with open(UPLOAD_Q, "w") as f:
            json.dump(queue_state, f, ensure_ascii=False, indent=2)

        time.sleep(5)

    log("Upload queue done")


def load_sources():
    if os.path.exists(SRC_CFG):
        try:
            with open(SRC_CFG) as f:
                return json.load(f)
        except:
            pass
    return []


def save_sources(data):
    with open(SRC_CFG, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ensure_bot_running():
    """Make sure the bot is running, start if not"""
    try:
        result = subprocess.run(["pgrep", "-f", "tg_bot.py"], capture_output=True, text=True, timeout=5)
        if not result.stdout.strip():
            log("Bot not running, starting...")
            subprocess.Popen(
                "python3 {}/tg_bot.py >> {}/bot.log 2>&1".format(SDIR, LOG_DIR),
                shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            send_tg("<b>Bot Restarted</b>\n自动检测到 Bot 停止，已重新启动。")
    except Exception as e:
        log("Health check error: {}".format(e))


def ensure_downloader_running():
    """Check if media_downloader.py is running"""
    try:
        result = subprocess.run(["pgrep", "-f", "media_downloader.py"], capture_output=True, text=True, timeout=5)
        if not result.stdout.strip():
            log("Downloader not running!")
            send_tg("<b>Downloader Down!</b>\nmedia_downloader.py 进程不存在。")
    except:
        pass


def health_check():
    """Run health checks"""
    ensure_bot_running()
    ensure_downloader_running()


def daily_restart():
    """Daily restart: kill engines, let scheduler restart them"""
    log("=== Daily restart ===")
    kill_engines()
    # Restart bot to clear any stale state
    try:
        subprocess.run(["pkill", "-f", "tg_bot.py"], capture_output=True, timeout=5)
    except:
        pass
    time.sleep(10)
    ensure_bot_running()
    send_tg("<b>Daily Restart Complete</b>\n02:00 定时重启完成，服务已恢复。")


def main():
    log("=" * 50)
    log("Scheduler started")
    log("Watch: {:02d}:{:02d} - {:02d}:{:02d}".format(*WATCH_START, *WATCH_END))
    log("Upload: {:02d}:{:02d} - {:02d}:{:02d}".format(*UPLOAD_START, *UPLOAD_END))
    log("Watch interval: {}h, Health check: {}min".format(WATCH_INTERVAL_HOURS, HEALTH_CHECK_MINUTES))

    send_tg("<b>Scheduler Started</b>\n时间窗口:\n- 16:00-02:00 监控转发 (每{}h)\n- 02:00-16:00 下载上传".format(WATCH_INTERVAL_HOURS))

    last_watch_run = 0
    last_health_check = 0
    restarted_today = False
    upload_running = False

    while True:
        now = time.time()
        lt = time.localtime()

        # === Daily restart at 02:00 ===
        if is_restart_time() and not restarted_today:
            graceful_switch()
            daily_restart()
            restarted_today = True
            upload_running = False

        # Reset restart flag after restart window
        if not is_restart_time():
            restarted_today = False

        # === Watch window (16:00-02:00) ===
        if is_watch_time():
            # Gracefully wait for any running upload to finish
            if upload_running:
                graceful_switch()
                upload_running = False

            # Run watch every WATCH_INTERVAL_HOURS
            if now - last_watch_run > WATCH_INTERVAL_HOURS * 3600:
                log("Watch cycle started")
                start_watch()
                last_watch_run = now

        # === Upload window (02:05-16:00) ===
        elif is_upload_time() and not upload_running:
            upload_running = True
            log("Upload cycle started")
            process_uploads()
            upload_running = False

        # === Health check every 30min ===
        if now - last_health_check > HEALTH_CHECK_MINUTES * 60:
            health_check()
            last_health_check = now

        time.sleep(60)  # Check every minute


if __name__ == "__main__":
    main()
