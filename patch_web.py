"""
TG Portal - 合并版 web.py
在 tg-downloader 原有功能基础上增加转发管理

部署方式：挂载覆盖原 /app/module/web.py
  volumes:
    - ./patch_web.py:/app/module/web.py
    - ./patch_index.html:/app/module/templates/index.html
"""
import json
import logging
import os
import re
import subprocess
import threading
import time

from flask import Flask, jsonify, render_template, request
from flask_login import LoginManager, UserMixin, login_required, login_user

import utils
from module.app import Application
from module.download_stat import (
    DownloadState,
    get_download_result,
    get_download_state,
    get_total_download_speed,
    set_download_state,
)
from utils.crypto import AesBase64
from utils.format import format_byte

log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)

_flask_app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), "templates"))

_flask_app.secret_key = "tdl"
_login_manager = LoginManager()
_login_manager.login_view = "login"
_login_manager.init_app(_flask_app)
web_login_users: dict = {}
deAesCrypt = AesBase64("1234123412ABCDEF", "ABCDEF1234123412")

SESSIONS_DIR = "/app/sessions"
ENGINE_SCRIPT = os.path.join(SESSIONS_DIR, "forward_engine.py")
CONFIG_FILE = os.path.join(SESSIONS_DIR, "forward_config.json")
DEDUP_FILE = os.path.join(SESSIONS_DIR, "downloaded_ids.txt")

# 默认黑名单词
DEFAULT_SKIP_WORDS = [
    "一键清理", "清理僵尸粉", "僵尸粉", "清除", "清粉",
    "加群", "进群", "群号", "复制群", "打开群", "频道链接",
    "免费约", "同城约", "私聊", "一对一", "1v1", "1对1",
    "扫码", "关注公众号", "成人站",
    "看片", "免费看", "裸聊", "同城", "交友约",
    "兼职", "赚钱", "日结", "招人",
]

# 运行中的转发任务
_running_tasks = {}


class User(UserMixin):
    def __init__(self):
        self.sid = "root"
    @property
    def id(self):
        return self.sid


@_login_manager.user_loader
def load_user(_):
    return User()


def get_flask_app() -> Flask:
    return _flask_app


def run_web_server(app: Application):
    get_flask_app().run(app.web_host, app.web_port, debug=app.debug_web, use_reloader=False)


def init_web(app: Application):
    global web_login_users
    if app.web_login_secret:
        web_login_users = {"root": app.web_login_secret}
    else:
        _flask_app.config["LOGIN_DISABLED"] = True
    if app.debug_web:
        threading.Thread(target=run_web_server, args=(app,)).start()
    else:
        threading.Thread(
            target=get_flask_app().run, daemon=True, args=(app.web_host, app.web_port)
        ).start()


# ============================================================
# 原有路由
# ============================================================
@_flask_app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = "root"
        web_login_form = {}
        for key, value in request.form.items():
            if value:
                value = deAesCrypt.decrypt(value)
            web_login_form[key] = value
        if not web_login_form.get("password"):
            return jsonify({"code": "0"})
        password = web_login_form["password"]
        if username in web_login_users and web_login_users[username] == password:
            user = User()
            login_user(user)
            return jsonify({"code": "1"})
        return jsonify({"code": "0"})
    return render_template("login.html")


@_flask_app.route("/")
@login_required
def index():
    return render_template(
        "index.html",
        download_state=(
            "pause" if get_download_state() is DownloadState.Downloading else "continue"
        ),
    )


@_flask_app.route("/get_download_status")
@login_required
def get_download_speed():
    return (
        '{ "download_speed" : "'
        + format_byte(get_total_download_speed())
        + '/s" , "upload_speed" : "0.00 B/s" } '
    )


@_flask_app.route("/set_download_state", methods=["POST"])
@login_required
def web_set_download_state():
    state = request.args.get("state")
    if state == "continue" and get_download_state() is DownloadState.StopDownload:
        set_download_state(DownloadState.Downloading)
        return "pause"
    if state == "pause" and get_download_state() is DownloadState.Downloading:
        set_download_state(DownloadState.StopDownload)
        return "continue"
    return state


@_flask_app.route("/get_app_version")
def get_app_version():
    return utils.__version__


@_flask_app.route("/get_download_list")
@login_required
def get_download_list():
    if request.args.get("already_down") is None:
        return "[]"
    already_down = request.args.get("already_down") == "true"
    download_result = get_download_result()
    result = "["
    for chat_id, messages in download_result.items():
        for idx, value in messages.items():
            is_already_down = value["down_byte"] == value["total_size"]
            if already_down and not is_already_down:
                continue
            if result != "[":
                result += ","
            download_speed = format_byte(value["download_speed"]) + "/s"
            result += (
                '{ "chat":"' + f"{chat_id}" + '", "id":"' + f"{idx}"
                + '", "filename":"' + os.path.basename(value["file_name"])
                + '", "total_size":"' + f'{format_byte(value["total_size"])}'
                + '" ,"download_progress":"'
                + f'{round(value["down_byte"] / value["total_size"] * 100, 1)}'
                + '" ,"download_speed":"' + download_speed
                + '" ,"save_path":"' + value["file_name"].replace("\\", "/") + '"}'
            )
    result += "]"
    return result


# ============================================================
# 新增：转发管理路由
# ============================================================
@_flask_app.route("/forward_status")
@login_required
def forward_status():
    """获取转发统计"""
    groups = []
    if os.path.exists(SESSIONS_DIR):
        for fname in sorted(os.listdir(SESSIONS_DIR)):
            if fname.startswith("fwd_") and fname.endswith(".json"):
                name = fname[4:-5]
                try:
                    with open(os.path.join(SESSIONS_DIR, fname)) as f:
                        p = json.load(f)
                    p["name"] = name
                    if name in _running_tasks:
                        p["status"] = _running_tasks[name].get("status", "running")
                        p["progress"] = _running_tasks[name].get("progress", 0)
                    else:
                        p["status"] = "done"
                        p["progress"] = 100
                    groups.append(p)
                except:
                    pass

    dedup = 0
    try:
        if os.path.exists(DEDUP_FILE):
            with open(DEDUP_FILE) as f:
                dedup = sum(1 for _ in f)
    except:
        pass

    # 统计数据
    download_count = 0
    download_mb = 0
    dl_base = os.environ.get("DOWNLOAD_BASE", "/app/downloads")
    try:
        for root, dirs, files in os.walk(os.path.join(dl_base, "TGdown")):
            for fn in files:
                fp = os.path.join(root, fn)
                if time.time() - os.path.getmtime(fp) < 3600:
                    download_count += 1
                    download_mb += os.path.getsize(fp)
    except:
        pass

    return jsonify({
        "groups": groups,
        "dedup": dedup,
        "downloads_1h": download_count,
        "downloads_mb": round(download_mb / 1048576, 1),
        "tasks": {
            tid: {"status": t.get("status", "?"), "group": t.get("group", ""), "progress": t.get("progress", 0)}
            for tid, t in _running_tasks.items()
        },
    })


@_flask_app.route("/start_forward", methods=["POST"])
@login_required
def start_forward():
    """启动转发任务"""
    group_input = request.form.get("group_input", "").strip()
    method = request.form.get("method", "forward")
    if not group_input:
        return jsonify({"error": "missing group_input"}), 400

    task_id = f"task_{int(time.time())}"
    _running_tasks[task_id] = {"status": "running", "progress": 0, "group": group_input}

    is_message = bool(re.search(r"/\d+$", group_input))
    cmd = ["docker", "exec", "tg-login", "python3", ENGINE_SCRIPT]
    if is_message:
        cmd += ["--single", group_input, "--method", method]
    else:
        cmd += ["--oneshot", group_input, "--method", method]

    def _run():
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1, cwd=SESSIONS_DIR)
            for line in proc.stdout:
                _running_tasks[task_id]["log"] = (_running_tasks[task_id].get("log", "") + line)[-5000:]
                m = re.search(r"\[(\d+)-(\d+)/(\d+)\]", line)
                if m:
                    _running_tasks[task_id]["progress"] = round(int(m.group(1)) / int(m.group(3)) * 100, 1)
                if "DONE" in line:
                    _running_tasks[task_id]["progress"] = 100
                    _running_tasks[task_id]["status"] = "done"
            proc.wait()
            _running_tasks[task_id]["status"] = "done" if proc.returncode == 0 else "error"
        except Exception as e:
            _running_tasks[task_id]["status"] = "error"
            _running_tasks[task_id]["log"] = str(e)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"task_id": task_id, "status": "started"})


@_flask_app.route("/ctl_downloader", methods=["POST"])
@login_required
def ctl_downloader():
    """控制 tg-downloader 容器"""
    action = request.form.get("action", "")
    cmds = {
        "restart": ["docker", "restart", "tg-downloader"],
        "stop": ["docker", "stop", "tg-downloader"],
        "start": ["docker", "start", "tg-downloader"],
        "stop_fwd": ["docker", "exec", "tg-login", "pkill", "-9", "-f", "forward_engine"],
    }
    if action in cmds:
        subprocess.Popen(cmds[action])
        return jsonify({"status": "ok"})
    return jsonify({"error": "invalid action"}), 400


# ============================================================
# 新增：配置管理（黑名单词库 + 大小阈值）
# ============================================================
def _load_config():
    """加载转发配置"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except:
            pass
    return {"min_duration": 10, "min_video_mb": 5, "skip_words": DEFAULT_SKIP_WORDS}


def _save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


@_flask_app.route("/get_forward_config")
@login_required
def get_forward_config():
    """获取转发配置（黑名单词库 + 参数）"""
    cfg = _load_config()
    if "default_words" not in cfg:
        cfg["default_words"] = DEFAULT_SKIP_WORDS
    return jsonify(cfg)


@_flask_app.route("/save_forward_config", methods=["POST"])
@login_required
def save_forward_config():
    """保存转发配置"""
    cfg = _load_config()

    # 更新参数
    if "min_duration" in request.form:
        cfg["min_duration"] = int(request.form["min_duration"])
    if "min_video_mb" in request.form:
        cfg["min_video_mb"] = int(request.form["min_video_mb"])

    # 更新词库
    action = request.form.get("action", "")
    word = request.form.get("word", "").strip()

    if action == "add" and word:
        if word not in cfg.setdefault("skip_words", []):
            cfg["skip_words"].append(word)
    elif action == "remove" and word:
        cfg["skip_words"] = [w for w in cfg.get("skip_words", []) if w != word]
    elif action == "reset":
        cfg["skip_words"] = list(DEFAULT_SKIP_WORDS)

    _save_config(cfg)
    return jsonify({"status": "ok", "config": cfg})


# ============================================================
# 新增：源群管理（添加/删除/启停监控）
# ============================================================
SRC_CFG_FILE = os.path.join(SESSIONS_DIR, "sources_config.json")

def _load_sources():
    if os.path.exists(SRC_CFG_FILE):
        try:
            with open(SRC_CFG_FILE) as f:
                return json.load(f)
        except:
            pass
    return []

def _save_sources(data):
    with open(SRC_CFG_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@_flask_app.route("/get_sources")
@login_required
def get_sources():
    return jsonify({"sources": _load_sources()})


@_flask_app.route("/save_sources", methods=["POST"])
@login_required
def save_sources():
    action = request.form.get("action", "")
    name = request.form.get("name", "").strip()
    src = request.form.get("source", "").strip()
    method = request.form.get("method", "forward")
    sources = _load_sources()

    if action == "add" and name and src:
        existing = [s for s in sources if s["name"] == name]
        if existing:
            return jsonify({"error": f"Group '{name}' already exists"}), 400
        sources.append({
            "name": name, "source": src, "method": method,
            "enabled": True, "mode": "once", "complete": False,
            "watch_interval_hours": 6,
            "skip_photos": method == "upload",
            "min_video_mb": _load_config().get("min_video_mb", 5),
            "extra_skip_words": [],
        })
    elif action == "remove":
        sources = [s for s in sources if s["name"] != name]
    elif action == "toggle":
        for s in sources:
            if s["name"] == name:
                s["enabled"] = not s.get("enabled", True)
    elif action == "set_mode":
        for s in sources:
            if s["name"] == name:
                mode = request.form.get("value", "once")
                s["mode"] = mode
                if mode == "watch":
                    hours = request.form.get("interval", "6")
                    s["watch_interval_hours"] = int(hours)
    elif action == "set_method":
        for s in sources:
            if s["name"] == name:
                s["method"] = request.form.get("value", method)

    _save_sources(sources)
    return jsonify({"status": "ok", "sources": sources})


# ============================================================
# Bot 通知配置
# ============================================================
BOT_CFG_FILE = os.path.join(SESSIONS_DIR, "bot_config.json")

def _load_bot_cfg():
    if os.path.exists(BOT_CFG_FILE):
        try:
            with open(BOT_CFG_FILE) as f:
                return json.load(f)
        except: pass
    return {"token": "", "chat_id": "", "enabled": False, "daily_report_hour": 20}

@_flask_app.route("/get_bot_config")
@login_required
def get_bot_config():
    return jsonify(_load_bot_cfg())

@_flask_app.route("/save_bot_config", methods=["POST"])
@login_required
def save_bot_config():
    cfg = _load_bot_cfg()
    for k in ["token", "chat_id"]:
        if k in request.form and request.form[k]:
            cfg[k] = request.form[k].strip()
    cfg["enabled"] = request.form.get("enabled", "false") == "true"
    h = request.form.get("daily_report_hour", "")
    if h:
        cfg["daily_report_hour"] = int(h)
    with open(BOT_CFG_FILE, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return jsonify({"status": "ok"})

@_flask_app.route("/test_bot", methods=["POST"])
@login_required
def test_bot():
    cfg = _load_bot_cfg()
    import urllib.request
    try:
        data = json.dumps({"chat_id": cfg["chat_id"], "text": "✅ TG Portal 通知测试成功！", "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{cfg['token']}/sendMessage",
                                     data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:200]})
