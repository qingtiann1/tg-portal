# TG Portal / TG门户

> 基于 [tangyoha/telegram_media_downloader](https://github.com/tangyoha/telegram_media_downloader) 的增强版，新增群组转发管理 + Bot 通知 + 智能广告过滤。
> Enhanced version with group forwarding, Bot notifications, and intelligent ad filtering.

TG Portal 是一个 Telegram 媒体下载器的增强系统。它能在自动下载视频的同时，将指定群组的内容批量转发到目标群组，支持下载后重新上传（绕过转发限制），并通过 Bot 实时推送操作通知。内置调度器 24 小时自动运行，智能过滤赌博广告和诈骗消息，保持相册完整性。

TG Portal is an enhanced Telegram media downloader system. It auto-downloads videos while batch-forwarding content from source groups to a destination group, supports download-and-reupload (bypassing forward restrictions), and sends real-time Bot notifications. A built-in scheduler runs 24/7, intelligently filters gambling ads and scam messages, and preserves media album integrity.

## 功能 / Features

| 模块 | 说明 |
|------|------|
| 📥 下载管理 | 原版 Downloading / Downloaded 列表 |
| 📤 转发引擎 | Forward 标签页，watch 监控 / once 一次性 / single 单消息 |
| 🛡️ 广告过滤 | 结构性规则：关键词 + 内容弱信号，避免误杀 |
| 🔄 双模式 | `forward_messages` 批量 或 download→re-upload |
| 🤖 Bot 通知 | Web UI 操作自动推送 TG，每日统计报告 |
| ⏰ 调度器 | 24h 自动切换 watch/upload 模式，健康检查 + 崩溃告警 |
| 📝 操作日志 | Web UI 日志面板 |

## 架构

```
tg-downloader (1GB) — 单容器运行
│
├─ media_downloader.py     24h  原版下载器
├─ scheduler.py            24h  调度器（时间窗口管理）
│   ├─ 16:00-02:00  启动 watch 模式（每2h扫描）
│   ├─ 02:00-02:05  每日自动重启
│   └─ 02:05-16:00  处理 upload 队列
├─ forward_engine.py       按需  --watch / --once / --single
├─ tg_bot.py               24h  Bot 轮询 + 统计
├─ notify_send.py              独立通知子进程
├─ notify.py                   每日报告
├─ patch_web.py                Flask 路由增强
├─ patch_index.html            前端 UI
└─ Web UI :5000
```

### Session 分层（避免锁冲突）

| 进程 | Session | 说明 |
|------|---------|------|
| 下载器 | `media_downloader` | 原始 session，24h 使用 |
| 转发引擎 | `fwd_engine` | 独立 copy，转发窗口期使用 |
| 清理脚本 | `_scan` | 临时 copy，用后即弃 |

## 文件说明

```
tg-portal/
├── scheduler.py          # 24h 调度器（时间窗口 + 健康检查）
├── forward_engine.py     # 转发引擎
│   --watch <name>       : 持续监控群组
│   --once <group>        : 一次性处理群组
│   --single <link>       : 处理单条消息（下载上传/转发）
│   --dry                 : 仅扫描不操作
├── tg_bot.py             # Telegram Bot
├── notify.py             # 统计 + 每日报告
├── notify_send.py        # 独立通知脚本（子进程）
├── patch_web.py          # Flask 路由增强
├── patch_index.html      # 前端模板
├── forward_config.json   # 过滤规则配置
├── sources_config.json   # 源群配置
├── env_template          # 环境变量模板
└── README.md
```

## 广告过滤

### 规则层级

```
消息进来
├─ 0. 硬黑名单 → 直接拦截（用户指定内容 + 诈骗钓鱼）
├─ 1. 绝对拦截 → 100% 广告模式（不论视频多长）
│   ├─ 裸域名 + 赌博关键词
│   ├─ 绝对组合对（特定词两两出现）
│   ├─ ≥3 个赌博词 + @mention
│   └─ ≥5 个赌博词（密集命中）
├─ 2. 结构规则 → 赌博词 + 弱信号
│   ├─ 视频 ≤ 25s
│   ├─ 图片 + 链接/域名/@
│   ├─ 纯文本 + 链接/域名/@
│   └─ 高表情密度
└─ 3. 正常通过
```

### 关键原则

- **不纯靠关键词拉黑** — 源群标题自带赌博标签不误杀
- **绝对拦截优先于结构规则** — 组合对 + 裸域名直接拦截
- **清理前先 dry-run** — 避免大规模误删
- **相册消息批量转发** — 同 `media_group_id` 一起转，保持相册关系

## 相册/媒体组

`forward_messages([id1, id2, ...])` 传多 ID 可保持相册关系。
引擎会自动检测 `media_group_id`，将同组消息批量转发。

## 部署 / Deployment

### 换 NAS / 新机部署（3步）

```bash
# 1. 克隆项目，复制到 NAS
git clone https://github.com/qingtiann1/tg-portal.git
scp tg-portal/*.py tg-portal/*.html nas:/vol1/1000/docker/tg-downloader/sessions/

# 2. 安装 alist（115 上传依赖）
curl -L -o /tmp/alist.tar.gz https://github.com/AlistGo/alist/releases/download/v3.43.0/alist-linux-amd64.tar.gz
tar xzf /tmp/alist.tar.gz -C /tmp
cp /tmp/alist /usr/local/bin/alist
# 配置 115：在 alist Web UI (http://NAS:5244) 添加 115 存储

# 3. 启动
docker exec -d tg-downloader python3 /app/sessions/scheduler.py
```

scheduler.py 会自动启动 Bot + 转发引擎 + upload 队列。一个脚本管理所有。

### Docker Compose（最小配置）

```yaml
tg-downloader:
  image: tangyoha/telegram_media_downloader:latest
  mem_limit: 1G
  volumes:
    - ./sessions:/app/sessions
    - ./patch_web.py:/app/module/web.py
    - ./patch_index.html:/app/module/templates/index.html
```

### 手动命令
docker exec tg-downloader python3 /app/sessions/forward_engine.py --watch <group>
docker exec tg-downloader python3 /app/sessions/forward_engine.py --single <link> --method upload
```

## Bot 命令

| 命令 | 说明 |
|------|------|
| `/add` | 添加源群链接 |
| `/stats` | 查看统计（去重文件数/下载数/待处理） |
| `/list` | 列出源群 |
| `/help` | 帮助 |
| `/cancel` | 取消排队 |

## 排错

### Bot 不响应

1. 检查代理: `curl -x http://mihomo:7890 https://api.telegram.org/bot<token>/getMe`
2. 切 mihomo 节点: `PUT http://<nas>:9090/proxies/...` body `{"name":"节点名"}`
3. 重启: `docker exec -d tg-downloader python3 /app/sessions/tg_bot.py`

### Session 锁冲突

- 下载器和转发引擎不能同时用同一个 session
- 解决：转发引擎用 `fwd_engine.session`（独立 copy）
- Watch 模式跑完后释放 session，下载器继续用

### 相册被拆散

- `forward_messages` 单条转发行不通，会丢相册关系
- 引擎已修复：检测 `media_group_id` 批量转发
