# TG Portal — Telegram 下载 + 转发管理面板

在 [tangyoha/telegram_media_downloader](https://github.com/tangyoha/telegram_media_downloader) 基础上增加转发管理功能。

## 功能

- 📥 原有下载管理（Downloading / Downloaded 列表）
- 📤 **新增** Forward 标签页：查看转发进度、添加转发任务
- 📊 实时统计：下载数、去重ID、源群进度
- 🔧 容器控制：启停 tg-downloader

## 部署

```bash
# 1. 备份原文件
docker exec tg-downloader cp /app/module/web.py /app/module/web.py.bak
docker exec tg-downloader cp /app/module/templates/index.html /app/module/templates/index.html.bak

# 2. 覆盖文件
docker cp patch_web.py tg-downloader:/app/module/web.py
docker cp patch_index.html tg-downloader:/app/module/templates/index.html

# 3. 安装 forward_engine.py
docker cp forward_engine.py tg-login:/sessions/forward_engine.py

# 4. 重启
docker restart tg-downloader
```

或挂载方式（docker-compose）：

```yaml
volumes:
  - ./patch_web.py:/app/module/web.py
  - ./patch_index.html:/app/module/templates/index.html
```

## 转发引擎

`forward_engine.py` 支持：
- `--dry` 仅扫描预览
- `--group=xxx` 指定群组
- `--single <link>` 单条消息下载上传
- `--oneshot <link> --method forward|upload` 一次性转发
