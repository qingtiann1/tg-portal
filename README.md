# TG Portal

基于 [tangyoha/telegram_media_downloader](https://github.com/tangyoha/telegram_media_downloader) 的增强版，在原下载管理基础上新增 TG 群组转发管理 + Bot 通知。

## 功能

| 模块 | 说明 |
|------|------|
| 📥 下载管理 | 原版 Downloading / Downloaded 列表 |
| 📤 转发管理 | Forward 标签页，多源群批量转发 + 持续监控 |
| 🛡️ 广告过滤 | 结构性规则：赌博关键词 + 内容弱信号（短视频/少图/链接） |
| 🔄 双模式 | `forward_messages` 批量 / download→upload 上传 |
| 🤖 Bot 通知 | @tgdown1BOT，网页添加链接自动推送 TG 消息 |
| 📝 操作日志 | Web UI 日志面板，每次操作可见 |

## 文件说明

```
tg-portal/
├── patch_web.py          # Flask 路由增强（挂载到 /app/module/web.py）
│                         #   - start_forward / save_sources 路由
│                         #   - _send_tg_notification() → notify_send.py 子进程
│                         #   - _add_web_log() + /get_web_log 端点
├── patch_index.html      # 前端模板增强（挂载到 /app/module/templates/index.html）
│                         #   - 添加链接/群组表单
│                         #   - 操作日志面板
│                         #   - Bot 配置 + 测试
├── forward_engine.py     # 转发引擎（部署到 tg-login/tg-downloader 容器）
│                         #   - --watch: 持续监控模式
│                         #   - --oneshot: 一次性转发
│                         #   - is_spam(): 结构性广告判断
├── notify_send.py        # 独立通知脚本（子进程方式，避免 Flask 线程冲突）
├── tg_bot.py             # Telegram Bot（@tgdown1BOT，轮询 Bot API）
├── notify.py             # Bot 通知模块（每日统计报告）
├── clean_spam.py         # 广告清理脚本（扫描已转发消息，删除广告）
├── recover_fp.py         # 误删恢复脚本（从源群重新转发）
├── extract_deleted.py    # 提取已删除消息列表
├── forward_config.json   # 过滤规则配置
├── sources_config.json   # 源群配置（Web UI 可编辑）
├── env_template          # 环境变量模板
└── README.md
```

## 广告过滤规则

### 结构性判断（`is_spam()` in forward_engine.py）

```
消息进来 → 检查文本
  ├─ 硬黑名单（男娘/变性/人妖/gay/VR） → 直接跳过
  ├─ 含赌博广告词 → 检查内容结构
  │   ├─ 视频 ≤ 25s → 拦截（广告短视频）
  │   ├─ 1-2张图 + 链接/@ → 拦截（广告图片）
  │   ├─ 纯文本 + 链接 → 拦截（广告引流）
  │   ├─ 高表情密度 → 拦截（emoji刷屏）
  │   └─ 视频 > 25s → ✅ 正常转发
  └─ 无广告词 → ✅ 正常转发
```

### 赌博广告词库

| 类别 | 词汇 |
|------|------|
| 平台名 | HPAY, hpay, 金运国际, 金运娱乐, 金运, BBIN |
| 游戏名 | PG电子, 赏金女王, 百家乐 |
| 金融术语 | 爆奖, 爆分, 汇旺, 彩金, 体验金, 首充, 首存, 救援金, 回归彩金, 生日礼金, 提款, 喜提, USDT, 充值赠送, 注册即享 |
| 赌博用语 | 下注, 赌场, 单注金额, 单注倍数, 爆分金额, 视频奖励, 一点配三边, 四点配两边, 电子大水中 |
| 推广用语 | 官方网址, 官方客服号, 官网注册, 频道赞助商, 频道专属代码, 赞助直播, 会员爆奖, hpay77, 帕拉梅拉 |

### 误删恢复

如果正常内容被误删，使用 `recover_fp.py` 从源群重新转发：

```bash
# 1. 停止 tg-downloader（释放 session）
docker stop tg-downloader

# 2. 运行恢复脚本
docker exec tg-login python3 /sessions/recover_fp.py

# 3. 重启
docker start tg-downloader
```

## 部署

### NAS Docker（与主 docker-compose 集成）

tg-downloader 容器挂载：
```yaml
volumes:
  - ./patch_web.py:/app/module/web.py
  - ./patch_index.html:/app/module/templates/index.html
  - ./sessions:/app/sessions   # 共享 session + 脚本
```

tg-login 容器挂载：
```yaml
volumes:
  - ./sessions:/sessions       # 同目录，共享文件
```

### Bot 启动

```bash
# tg_bot.py 需要代理环境变量（tg-downloader 内代理通）
docker exec -d tg-downloader sh -c "python3 /app/sessions/tg_bot.py >> /app/log/bot.log 2>&1"
```

### 转发引擎

```bash
# 持续监控
docker exec -d tg-login python3 /sessions/forward_engine.py --watch zuoai_caobi

# 一次性
docker exec tg-login python3 /sessions/forward_engine.py --oneshot <group> --method forward

# 下载上传
docker exec tg-login python3 /sessions/forward_engine.py --oneshot <group> --method upload
```

## Bot 命令（@tgdown1BOT）

| 命令 | 说明 |
|------|------|
| `/add` | 添加源群链接 |
| `/stats` | 查看转发统计 |
| `/list` | 列出所有源群 |
| `/help` | 帮助 |
| `/cancel` | 取消排队任务 |

## 排错

### Bot 不响应

1. 检查节点：`curl -x http://127.0.0.1:7890 https://api.telegram.org/bot<token>/getMe`
2. 切节点：`PUT http://192.168.8.109:9090/proxies/🚀%20节点选择` body `{"name":"节点名"}`
3. 重启 bot：`docker exec -d tg-downloader python3 /app/sessions/tg_bot.py`

### 转发引擎 crash

- Session 互斥：tg-downloader 和转发引擎不能共用 `media_downloader.session`
- 解决：转发引擎用单独 session (`fwd_engine.session`) 或从 tg-login 运行
