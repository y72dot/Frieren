# qqbot

基于 [NapCatQQ](https://github.com/NapNeko/NapCatQQ) 的自建 QQ 机器人框架。

## 本地开发

```bash
# 安装依赖
pip install -e .        # 从 pyproject.toml 安装

# 配置
cp .env.example .env    # 按需填写 API Key
# 编辑 config/bot.toml  -- 填入 QQ 号、管理员、群白名单
# 编辑 instances/napcat-frieren/config/onebot11_3632757457.json  -- 填入你的 QQ 号

# 先启动 NapCat（Docker 或本地均可），确认扫码登录成功后启动 Bot
python -m src.main
```

本地 NapCat 的 WebSocket 端口默认 `127.0.0.1:3001`，与 `config/bot.toml` 中的 `ws_url` 一致。

## 服务器部署（Docker Compose）

### 环境要求

- Ubuntu 20.04+，已安装 Docker 和 Docker Compose
- QQ 号一个（已过风控，能正常登录）

### 1. 克隆项目

```bash
git clone https://github.com/y72dot/Frieren.git
cd Frieren
```

### 2. 创建实例配置

项目已内置 `frieren` 实例（QQ=3632757457）作为参考。如果要使用新的 QQ 号，需要创建专属实例：

```bash
# 复制 Bot 配置
mkdir -p instances/mybot
cp config/bot.toml instances/mybot/bot.toml
cp .env.example instances/mybot/.env

# 复制 NapCat 配置
cp -r instances/napcat-frieren/config instances/napcat-mybot/config
```

修改 `instances/mybot/bot.toml` 中的关键字段：
```toml
[bot]
qq = <你的QQ号>
nickname = "<机器人昵称>"
admin_users = [<你的QQ号>]

[napcat]
ws_url = "ws://napcat-mybot:3001"    # 与 docker-compose 服务名一致

[plugin]
plugin_dirs = ["/app/plugins"]         # Docker 内部路径
```

修改 NapCat 配置中所有 `3632757457` 改为你的 QQ 号，并在 `docker-compose.yml` 中新增对应的服务对。

### 3. 填写环境变量

```bash
# 如果 instances/<name>/.env 不存在会自动从 .env.example 复制
vim instances/mybot/.env
```

```env
DEEPSEEK_API_KEY=sk-your-real-key
OPENAI_API_KEY=sk-your-real-key
NAPCAT_WEBUI_TOKEN=     # 留空，NapCat 会自动生成
```

### 4. 构建镜像

```bash
docker compose build
```

### 5. 启动 NapCat 并扫码登录

```bash
# 只启动 NapCat 容器
docker compose up -d napcat-frieren

# 查看 WebUI token
docker compose logs napcat-frieren | grep -i token
```

然后通过 SSH 隧道访问 WebUI 扫码：

```bash
# 在本地机器执行（不是服务器）：
ssh -L 6099:127.0.0.1:6099 user@your-server

# 浏览器打开 http://localhost:6099
# 输入上面获取的 token 登录，使用手机 QQ 扫码
```

扫码成功后 NapCat 容器内 `QQ/` 目录会保存登录会话，后续重启无需重新扫码。

### 6. 启动 Bot

```bash
docker compose up -d qqbot-frieren
docker compose logs -f qqbot-frieren
```

看到 `Connected to NapCat` 即为成功。

### 一键部署脚本

也可以使用提供的部署脚本：

```bash
bash scripts/deploy.sh
```

脚本会自动构建镜像、补全 .env 模板、启动所有 NapCat 容器。后续只需扫码登录后 `docker compose up -d` 启动 Bot。

## 备份与恢复

### 备份

```bash
bash scripts/backup.sh
```

备份内容包括：
- 各 NapCat 实例的 `QQ/` 目录（登录会话，避免反复扫码）
- 各 Bot 实例的配置目录

备份存放于 `backups/<时间戳>/`，自动保留最近 7 份。

### 恢复

```bash
# 恢复 QQ 会话
cp -r backups/20250101_120000/napcat-frieren-session/* instances/napcat-frieren/QQ/

# 恢复 Bot 配置
cp -r backups/20250101_120000/frieren-config/* instances/frieren/
```

## 常用命令

```bash
# 查看所有容器状态
docker compose ps

# 查看日志
docker compose logs -f qqbot-frieren        # Bot
docker compose logs -f napcat-frieren       # NapCat

# 重启 Bot（加载新插件/配置）
docker compose restart qqbot-frieren

# 更新部署（拉取最新代码后）
git pull
docker compose build
docker compose up -d
```

## 项目结构

```
qqbot/
├── src/
│   ├── main.py               # 入口
│   └── core/
│       ├── bot.py             # Bot 主控，组装各组件
│       ├── config.py          # 配置加载（bot.toml + .env）
│       ├── event_bus.py       # 事件总线：解析 napcat 事件 → 内部 Event
│       ├── message_bus.py     # 消息总线：按优先级分发，支持抑制
│       ├── message_store.py   # SQLite 消息持久化
│       ├── filter_manager.py  # 全局 + 插件级过滤
│       └── api_client.py      # API 调用封装
├── plugins/                   # 插件目录（文件以 _ 开头则跳过）
│   ├── history.py             # 消息历史记录（JSONL 日志）
│   ├── ping.py                # /ping → Pong!
│   ├── echo.py                # /echo <msg> → 复读
│   ├── poke.py                # 戳一戳反击
│   ├── repeater.py            # 复读机（两人连续相同消息）
│   └── essence.py             # 群精华消息管理（设精 / 寸止）
├── config/
│   └── bot.toml               # 默认本地开发配置
├── instances/                 # 多实例配置（每个 QQ 号一份）
│   ├── frieren/               # Bot 配置 + .env
│   └── napcat-frieren/        # NapCatQQ 配置 + QQ 会话
├── scripts/
│   ├── deploy.sh              # Docker Compose 一键部署
│   ├── backup.sh              # 备份 QQ 会话和配置
│   ├── run.sh                 # PID-based 本地后台启动
│   └── start.sh               # 简单本地启动
├── tests/                     # 测试
├── Dockerfile
└── docker-compose.yml
```

## 架构

```
NapCatQQ WebSocket → EventBus.parse（原始事件 → 内部 Event）
  → MessageStore.record（持久化）
    → MessageBus.dispatch（按 priority 升序遍历插件）
      → FilterManager 拦截（全局 → 插件级）
        → Plugin.match → Plugin.handle
          → MessageBus.flush（排空 ACTION 队列）
            → ApiClient → HTTP/WS 调用
```

## 插件开发

插件只需实现 `match(event) -> bool` 和 `handle(event, bot) -> bool`：

```python
from src.plugin.base import Event

class MyPlugin:
    name = "my_plugin"
    priority = 10

    def match(self, event: Event) -> bool:
        return event.is_group and "你好" in event.message

    async def handle(self, event: Event, bot) -> bool:
        await bot.api.send_group_msg(event.group_id, "你好呀！")
        return True  # 消费事件，后续插件不再执行
```

也可用装饰器：

```python
from src.plugin.decorators import command, on_regex, on_keyword, on_notice

@command("/hello")
async def hello(event, bot):
    await bot.api.send_group_msg(event.group_id, "Hi!")
    return True

@on_regex(r"^复读\s+(.+)")
async def repeat(event, bot, match):
    await bot.api.send_group_msg(event.group_id, match.group(1))
    return True
```

- `@command(cmds)` — 精确命令匹配
- `@on_regex(pattern)` — 正则匹配，`match` 对象作为 handler 第三参数
- `@on_keyword(keywords)` — 关键词包含匹配
- `@on_notice(notice_type)` — 通知事件匹配

## 配置参考

```toml
[bot]
qq = 123456789
nickname = ["机器人"]
admin_users = [987654321]

[napcat]
mode = "active"              # active: Bot 连 NapCat | reverse: NapCat 连 Bot
ws_url = "ws://127.0.0.1:3001"
token = ""                   # WebSocket 鉴权 token，为空则不验证
reconnect_interval = 5       # 断线重连间隔（秒），指数退避最大 300s

[plugin]
auto_discover = true
plugin_dirs = ["plugins"]
disabled_plugins = []        # 禁用的插件 name

[filter]
enable = true

[filter.group]
mode = "whitelist"           # whitelist | blacklist | off
list = [123456789]

[filter.private]
mode = "off"
list = []

# 每个插件可独立配置过滤规则
[filter.plugins.ping]
enable = true
[filter.plugins.ping.group]
mode = "whitelist"
list = [123456789]

[logging]
level = "INFO"               # DEBUG | INFO | WARNING | ERROR
file = "logs/bot.log"
rotation = "10 MB"
retention = "14 days"
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `BOT_CONFIG_DIR` | 配置目录路径（Docker 中设为 `/config`） |
| `NAPCAT_MODE` | 覆盖 `napcat.mode` |
| `NAPCAT_WS_URL` | 覆盖 `napcat.ws_url` |
| `NAPCAT_TOKEN` | 覆盖 `napcat.token` |
| `NAPCAT_REVERSE_PORT` | 覆盖反向模式端口 |
| `DEEPSEEK_API_KEY` | DeepSeek API Key（从 `.env` 加载） |

## 多实例扩展

每个 QQ 号需要一对服务。在 `docker-compose.yml` 中复制 `napcat-frieren` + `qqbot-frieren` 服务对，替换名称和端口即可。配置和运行时数据完全隔离。
