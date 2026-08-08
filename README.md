# TG Portal

基于 [tangyoha/telegram_media_downloader](https://github.com/tangyoha/telegram_media_downloader) 的增强版，在原下载管理基础上新增 TG 群组转发管理。

## 功能

| 模块 | 说明 |
|------|------|
| 📥 下载管理 | 原版 Downloading / Downloaded 列表 |
| 📤 转发管理 | 新增 Forward 标签页，支持多源群批量转发 |
| 🛡️ 过滤规则 | 黑名单词库 + 视频时长/大小阈值，网页可管理 |
| 🔄 双模式 | 可转发群用 `forward_messages` 批量，不可转发群用下载→上传 |

## 文件说明

```
tg-portal/
├── patch_web.py          # Flask 路由增强（挂载到 /app/module/web.py）
├── patch_index.html      # 前端模板增强（挂载到 /app/module/templates/index.html）
├── forward_engine.py     # 转发引擎（部署到 tg-login 容器）
├── forward_config.json   # 过滤规则配置（黑名单词库 + 阈值）
├── .env.example          # 环境变量模板
└── README.md
```

## NAS Docker 部署

### 1. 环境变量

复制模板并填入真实值：
```bash
cp .env.example .env
# 编辑 .env 填入 TG_API_ID, TG_API_HASH 等
```

### 2. docker-compose 配置

在现有 `tg-downloader` 配置基础上增加以下挂载和环境变量：

```yaml
services:
  tg-downloader:
    image: tangyoha/telegram_media_downloader:latest
    container_name: tg-downloader
    restart: always
    ports:
      - "5000:5000"
    environment:
      - TG_API_ID=${TG_API_ID}
      - TG_API_HASH=${TG_API_HASH}
      - TG_DEST_GROUP=${TG_DEST_GROUP}
      - TG_PROXY_HOST=${TG_PROXY_HOST:-mihomo}
      - TG_PROXY_PORT=${TG_PROXY_PORT:-7890}
    volumes:
      # 原有挂载
      - ./config.yaml:/app/config.yaml
      - ./data.yaml:/app/data.yaml
      - ./sessions:/app/sessions
      - /path/to/downloads:/app/downloads
      # TG Portal 覆盖文件
      - ./tg-portal/patch_web.py:/app/module/web.py
      - ./tg-portal/patch_index.html:/app/module/templates/index.html
      - ./tg-portal/forward_config.json:/app/sessions/forward_config.json

  tg-login:
    image: tangyoha/telegram_media_downloader:latest
    container_name: tg-login
    command: sleep infinity
    environment:
      - TG_API_ID=${TG_API_ID}
      - TG_API_HASH=${TG_API_HASH}
      - TG_DEST_GROUP=${TG_DEST_GROUP}
      - TG_PROXY_HOST=${TG_PROXY_HOST:-mihomo}
      - TG_PROXY_PORT=${TG_PROXY_PORT:-7890}
    volumes:
      - ./sessions:/sessions
      - ./tg-portal/forward_engine.py:/sessions/forward_engine.py
      - ./tg-portal/forward_config.json:/sessions/forward_config.json
```

### 3. 部署

```bash
# 克隆项目
git clone https://github.com/qingtiann1/tg-portal.git
cd tg-portal
cp .env.example .env
# 编辑 .env 填入真实值

# 复制到 NAS docker 目录
scp -r ./* nas:/vol1/1000/docker/tg-downloader/

# 更新 docker-compose 并重启
docker compose up -d
```

### 4. 访问

打开 `http://<nas-ip>:5000`，点击 **Forward** 标签页。

## 配置源群

编辑 `forward_engine.py` 中的 `SOURCES` 列表，或通过 Web UI 的 Forward 标签页添加。

```python
SOURCES = [
    {
        "name": "my_group",
        "source": "https://t.me/xxx",   # 群链接或 ID
        "method": "forward",             # "forward"=批量  "upload"=下载上传
        "enabled": True,
        "min_video_mb": 5,
        "extra_skip_words": [],
    },
]
```

## 过滤规则

在网页 Forward → 过滤规则 面板管理，或直接编辑 `forward_config.json`：

```json
{
  "min_duration": 10,
  "min_video_mb": 5,
  "skip_words": ["广告词1", "广告词2", ...]
}
```

## CLI 使用

```bash
docker exec tg-login python /sessions/forward_engine.py              # 所有源群
docker exec tg-login python /sessions/forward_engine.py --dry        # 仅扫描
docker exec tg-login python /sessions/forward_engine.py --group xxx  # 指定群
```
