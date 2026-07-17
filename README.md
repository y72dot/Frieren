# qqbot

基于 [NapCatQQ](https://github.com/NapNeko/NapCatQQ) 的自建 QQ 机器人框架。

## 快速开始

### 本地开发

```bash
# 安装依赖
pip install napcat-sdk loguru python-dotenv aiofiles

# 复制配置文件
cp .env.example .env   # 按需填写 API Key

# 编辑 config/bot.toml（QQ 号、群白名单等）

# 启动
python -m src.main
```

### Docker 多实例部署

```bash
# 1. 创建实例配置
mkdir -p instances/frieren
cp config/bot.toml instances/frieren/bot.toml
# 编辑 instances/frieren/bot.toml：
#   plugin_dirs = ["/app/plugins"]
#   ws_url = "ws://napcat-frieren:3001"

# 2. 构建镜像
docker compose build

# 3. 启动 NapCat 并扫码登录
docker compose up -d napcat-frieren
docker compose logs napcat-frieren   # 获取 WebUI 地址和 token
# 打开 http://host:6099 扫码登录

# 4. 启动 Bot
docker compose up -d qqbot-frieren
docker compose logs -f qqbot-frieren # 确认连接成功
```

每个 QQ 号对应一对 `napcat-<name>` + `qqbot-<name>` 服务，配置和运行时数据完全隔离。详见 `docker-compose.yml`。

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
├── plugins/                   # 插件目录
│   ├── history.py             # 消息历史记录（JSONL 日志）
│   ├── ping.py                # /ping → Pong!
│   ├── echo.py                # /echo <msg> → 复读
│   ├── poke.py                # 戳一戳反击
│   ├── repeater.py            # 复读机（两人连续相同消息）
│   └── essence.py             # 群精华消息管理（设精 / 寸止）
├── config/
│   └── bot.toml               # 默认配置文件
├── instances/                 # Docker 多实例配置
├── scripts/                   # 启动脚本
├── tests/                     # 测试
├── Dockerfile
└── docker-compose.yml
```

## 架构

```
NapCatQQ WebSocket → EventBus.parse → MessageBus.dispatch
  → FilterManager 拦截 → Plugin.match → Plugin.handle
    → MessageBus.flush → ApiClient → HTTP/WS 调用
```

- **MessageBus**：中央总线，按 priority 升序遍历插件，首个返回 truthy 的插件"吃掉"事件
- **FilterManager**：全局过滤 + 插件级过滤（whitelist/blacklist），管理员和 Bot 自身不受限
- **EventBus**：napcat 原始事件 → 内部 Event；记录历史；触发分发
- **MessageStore**：SQLite 持久化，支持按群/用户/关键词查询

## 插件开发

插件只需实现 `match(event) -> bool` 和 `handle(event, bot) -> bool`：

```python
from src.plugin.base import Event, Plugin

class MyPlugin:
    name = "my_plugin"
    priority = 10

    def match(self, event: Event) -> bool:
        return event.is_group and "你好" in event.message

    async def handle(self, event: Event, bot) -> bool:
        await bot.api.send_group_msg(event.group_id, "你好呀！")
        return True  # 消费事件，后续插件不再执行
```

也可用装饰器快速实现：

```python
from src.plugin.decorators import command, on_regex, on_keyword, on_notice

@command("/hello")
async def hello(event, bot):
    await bot.api.send_group_msg(event.group_id, "Hi!")
    return True
```

## 配置参考

```toml
[bot]
qq = 123456789
nickname = ["机器人"]
admin_users = [987654321]

[napcat]
mode = "active"              # active | reverse
ws_url = "ws://127.0.0.1:3001"
token = ""

[plugin]
plugin_dirs = ["plugins"]
disabled_plugins = []

[filter]
enable = true

[filter.group]
mode = "whitelist"           # whitelist | blacklist | off
list = [123456789]

[logging]
level = "INFO"
```
