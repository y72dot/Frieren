# QQ 机器人 Python 项目计划书

## 一、项目概述

基于 **NapCatQQ（协议层）+ 自研 Python 核心（业务层）** 构建的 QQ 机器人。核心完全从零编写，不依赖 NoneBot / AstrBot / Koishi 等上层框架，支持自定义插件系统和 AI Agent 智能体功能。

### 核心设计原则

- **低层级控制**：每一行核心代码都在掌控中，无名框架魔法
- **插件即函数**：插件注册 = 匹配规则 + 处理函数，零学习成本
- **Agent 原生支持**：内置 tool-calling 引擎，插件可同时是命令处理器和 AI 工具

---

## 二、整体架构

```
┌─────────────────────────────────────────────────────────┐
│                    腾讯 QQ 服务器                         │
└─────────────────────┬───────────────────────────────────┘
                      │ QQ NT 协议
┌─────────────────────┴───────────────────────────────────┐
│               NapCatQQ (Docker / 裸机)                    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │ HTTP API    │  │ WebSocket   │  │ WebUI (:6099)   │  │
│  │ :3000       │  │ Server :3001│  │ 扫码/配置       │  │
│  └──────┬──────┘  └──────┬──────┘  └─────────────────┘  │
└─────────┼────────────────┼──────────────────────────────┘
          │                │
          │  HTTP           │  WebSocket (正向连接)
          │  (发消息用)      │  (收事件用)
          │                │
┌─────────┴────────────────┴──────────────────────────────┐
│                    qqbot Python 核心                       │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │              EventBus (事件总线)                   │   │
│  │   WS 收到事件 → 解析 → 分发给注册的处理器           │   │
│  └────────────────────┬─────────────────────────────┘   │
│                       │                                  │
│  ┌────────────────────┴─────────────────────────────┐   │
│  │             PluginManager (插件管理器)             │   │
│  │   ┌──────────┐  ┌──────────┐  ┌──────────────┐  │   │
│  │   │ Command   │  │ Regex    │  │  Passive     │  │   │
│  │   │ Plugin    │  │ Plugin   │  │  Plugin      │  │   │
│  │   │ /开头命令  │  │ 正则匹配  │  │  关键词/上下文│  │   │
│  │   └──────────┘  └──────────┘  └──────────────┘  │   │
│  └────────────────────┬─────────────────────────────┘   │
│                       │                                  │
│  ┌────────────────────┴─────────────────────────────┐   │
│  │              AgentEngine (智能体引擎)              │   │
│  │   ┌──────────┐  ┌──────────┐  ┌──────────────┐  │   │
│  │   │ LLM      │  │ Tool     │  │  Memory /    │  │   │
│  │   │ Client   │  │ Registry │  │  Session     │  │   │
│  │   │ (OpenAI  │  │ (插件→工具)│  │  Manager     │  │   │
│  │   │ 兼容API) │  │          │  │              │  │   │
│  │   └──────────┘  └──────────┘  └──────────────┘  │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │                Plugin 插件目录                      │   │
│  │   ping.py  weather.py  mc_server.py  admin.py ... │   │
│  └──────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

---

## 三、技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| 语言 | **Python 3.12+** | `match case` 模式匹配，原生类型提示 |
| 异步框架 | **asyncio** | 标准库，无额外依赖 |
| OneBot 协议 | **napcat-sdk** | 轻量类型安全 SDK，专为 NapCat 设计 |
| LLM 客户端 | **openai** | OpenAI 兼容 API（支持 DeepSeek、Qwen 等） |
| 配置管理 | **tomli / tomli-w** | TOML 格式，与 Python 生态一致 |
| 日志 | **loguru** | 比 logging 好用，开箱即用 |
| 数据持久化 | **aiofiles + JSON** | 轻量，插件数据用 JSON 文件存储 |
| 进程管理 | **PM2** | 与现有运维体系统一 |

### 依赖清单 (requirements.txt)

```
napcat-sdk>=0.1.0
openai>=1.0.0
loguru>=0.7.0
tomli>=2.0.0
tomli-w>=1.0.0
aiofiles>=24.0.0
```

---

## 四、目录结构

```
qqbot/
├── PLAN.md                    # 本计划书
├── README.md                  # 项目说明
├── pyproject.toml             # 项目元数据
├── requirements.txt           # Python 依赖
├── .env                       # 环境变量（LLM API Key 等，不提交）
├── .env.example               # 环境变量模板
├── .gitignore
│
├── config/
│   ├── bot.toml               # 机器人主配置（QQ号、管理员、前缀等）
│   ├── llm.toml               # LLM 配置（provider、model、api_key）
│   └── agent.toml             # Agent 配置（system_prompt、max_turns 等）
│
├── src/
│   ├── __init__.py
│   ├── main.py                # 入口：启动核心
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── bot.py             # Bot 主类：组装所有组件
│   │   ├── event_bus.py       # 事件总线：分发 OneBot 事件
│   │   ├── api_client.py      # API 封装：调用 NapCat HTTP API
│   │   └── config.py          # 配置加载器：读取 TOML + 环境变量
│   │
│   ├── plugin/
│   │   ├── __init__.py
│   │   ├── manager.py         # 插件管理器：注册、匹配、调度
│   │   ├── base.py            # 插件基类/协议定义
│   │   └── decorators.py      # 装饰器语法糖（@command, @on_regex 等）
│   │
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── engine.py          # Agent 引擎：tool-calling 循环
│   │   ├── tool_registry.py   # 工具注册表：插件 → OpenAI function schema
│   │   ├── session.py         # 会话管理：用户/群聊对话历史
│   │   └── llm_client.py      # LLM 客户端：封装 OpenAI 兼容 API
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logger.py          # loguru 配置
│       ├── scheduler.py       # 定时任务（APScheduler 或 asyncio）
│       └── helpers.py         # 通用工具函数
│
├── plugins/                   # 插件目录（用户编写的插件放这里）
│   ├── __init__.py
│   ├── ping.py                # 示例：/ping → pong
│   ├── echo.py                # 示例：/echo <内容>
│   └── ai_chat.py             # 示例：AI 对话触发
│
├── data/                      # 运行时数据目录
│   ├── .gitkeep
│   └── sessions/              # 会话持久化存储
│
├── scripts/
│   ├── start.sh               # 启动脚本 (PM2)
│   └── dev.sh                 # 开发模式启动
│
└── tests/
    ├── __init__.py
    ├── test_plugin_manager.py
    ├── test_agent_engine.py
    └── test_event_bus.py
```

---

## 五、核心模块设计

### 5.1 Bot 主类 (`src/core/bot.py`)

```python
class Bot:
    """QQ 机器人类——组装所有组件并管理生命周期"""

    config: BotConfig           # 总配置
    event_bus: EventBus         # 事件总线
    api: ApiClient              # NapCat API 客户端
    plugin_manager: PluginManager  # 插件管理器
    agent_engine: AgentEngine   # AI Agent 引擎
    _client: NapCatClient       # napcat-sdk 实例

    async def start(self): ...
    async def stop(self): ...
    async def reload_plugins(self): ...
```

### 5.2 事件总线 (`src/core/event_bus.py`)

接收 NapCat WebSocket 事件，做第一层分发：

```
OneBot Event
  ├── message.group      → plugin_manager.dispatch(event)
  ├── message.private    → plugin_manager.dispatch(event)
  ├── notice.*           → event_bus.emit("notice", event)
  ├── request.*          → event_bus.emit("request", event)
  └── meta_event.*       → event_bus.emit("meta", event)
```

自定义事件钩子：插件也可以注册监听 `notice.group_increase`（入群欢迎）等。

### 5.3 API 客户端 (`src/core/api_client.py`)

封装 NapCat HTTP API 为类型安全的 Python 方法：

```python
class ApiClient:
    async def send_group_msg(self, group_id: int, message: str | list) -> dict
    async def send_private_msg(self, user_id: int, message: str | list) -> dict
    async def get_group_info(self, group_id: int) -> dict
    async def get_group_member_info(self, group_id: int, user_id: int) -> dict
    async def set_group_kick(self, group_id: int, user_id: int) -> dict
    async def set_group_ban(self, group_id: int, user_id: int, duration: int) -> dict
    async def get_stranger_info(self, user_id: int) -> dict
    # ... 按需添加
```

### 5.4 插件系统 (`src/plugin/`)

#### 插件协议 (`base.py`)

```python
from dataclasses import dataclass
from typing import Protocol, Any

@dataclass
class Event:
    """统一的内部事件对象"""
    type: str                 # "message.group" | "message.private" | "notice" | ...
    raw: dict                 # OneBot 原始事件
    user_id: int
    message: str              # 纯文本消息
    group_id: int | None
    is_group: bool

class Plugin(Protocol):
    """插件协议——所有插件只需实现这三个属性/方法"""
    name: str
    priority: int   # 越小越先匹配，默认 0

    def match(self, event: Event) -> bool: ...
    async def handle(self, event: Event, bot: "Bot") -> bool:
        """返回 True 表示事件已处理，终止后续插件匹配"""
        ...
```

#### 装饰器语法糖 (`decorators.py`)

```python
# 命令插件：/ping
@command("/ping", priority=0)

# 命令插件：带别名和参数
@command(["/weather", "/天气"], priority=0)

# 正则匹配
@on_regex(r"^(https?://[^\s]+)", priority=5)

# 关键词/上下文（你说"早"我就回"早安"）
@on_keyword(["早安", "早上好"], priority=10)

# 被动监听（事件钩子）
@on_notice("group_increase")
```

每个装饰器底层都是构造一个 `Plugin` 实例并注册到 `PluginManager`。

#### 插件管理器 (`manager.py`)

```python
class PluginManager:
    def register(self, plugin: Plugin): ...
    def auto_discover(self, plugin_dir: str): ...
    async def dispatch(self, event: Event, bot: Bot) -> bool:
        """按 priority 排序，依次 match → handle，命中即停"""
```

### 5.5 AI Agent 引擎 (`src/agent/`)

#### 整体流程

```
用户消息 "今天上海天气怎么样"
      │
      ▼
┌─────────────────┐
│  AgentEngine    │
│                 │
│  1. 构建 messages = [system_prompt, ...history, user_msg]
│  2. 调用 LLM (带 tools)
│  3. LLM 返回:
│     - 文本回复 → 直接返回给用户
│     - tool_call → 执行对应函数，结果追加到 messages，回到步骤 2
│  4. 循环最多 max_turns 次
│  5. 返回最终回复
└─────────────────┘
      │
      ▼
  发送到 QQ 群/私聊
```

详见 [六、Agent 引擎详细设计](#六agent-引擎详细设计)。

---

## 六、Agent 引擎详细设计

### 6.1 核心类

```python
class AgentEngine:
    """手写的 tool-calling agent 循环"""

    llm: LLMClient              # OpenAI 兼容客户端
    tool_registry: ToolRegistry # 工具注册表
    session_manager: SessionManager  # 对话历史管理
    config: AgentConfig         # 配置

    async def chat(
        self,
        user_id: int,
        message: str,
        context: dict | None  # 群号等上下文
    ) -> str:
        """主入口：接收用户消息，返回 AI 回复"""

class LLMClient:
    """封装 OpenAI 兼容 API"""
    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        model: str | None
    ) -> ChatResponse:
        """返回: {content, tool_calls, finish_reason}"""

class ToolRegistry:
    """工具注册表——插件可以注册自己为 AI 工具"""
    def register(self, name: str, schema: dict, func: callable): ...
    def get_schemas(self) -> list[dict]: ...
    async def execute(self, name: str, args: dict) -> str: ...

class SessionManager:
    """会话管理——按用户/群聊维护对话历史"""
    def get_history(self, user_id: int, group_id: int | None) -> list[dict]
    def append(self, user_id: int, group_id: int | None, msg: dict): ...
    def clear(self, user_id: int, group_id: int | None): ...
    def trim(self, user_id: int, group_id: int | None, max_messages: int): ...
```

### 6.2 插件作为 AI 工具

插件同时可以是命令处理和 Agent 工具——通过实现额外的 `as_tool()` 方法：

```python
# plugins/weather.py
class WeatherPlugin:
    name = "weather"
    priority = 0

    # ── 命令模式：用户输入 /天气 上海 ──
    def match(self, event: Event) -> bool:
        return event.message.startswith("/天气")

    async def handle(self, event: Event, bot: Bot) -> bool:
        city = event.message.removeprefix("/天气").strip()
        result = await self._get_weather(city)
        await bot.api.send_group_msg(event.group_id, result)
        return True

    # ── Agent 工具模式：LLM 自动决定是否调用 ──
    def as_tool(self) -> dict:
        """返回 OpenAI function schema + callable"""
        return {
            "schema": {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "查询指定城市的天气",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "城市名称"}
                        },
                        "required": ["city"]
                    }
                }
            },
            "handler": self._get_weather,
        }

    async def _get_weather(self, city: str) -> str:
        # 实际调用天气 API
        ...
```

在 `PluginManager` 中，自动发现每个插件的 `as_tool()` 并注册到 `ToolRegistry`。

### 6.3 Agent 触发策略

| 触发方式 | 配置 | 说明 |
|---------|------|------|
| **@机器人** | 群聊中 at 机器人才触发 | 默认行为，避免群聊干扰 |
| **命令前缀** | `/ai <内容>` 显式唤醒 | 需要时手动调用 |
| **关键词** | 消息包含 "bot" 等关键词 | 可选 |
| **所有消息** | 私聊默认、群聊可选 | 注意 LLM 费用 |

配置在 `config/agent.toml`：

```toml
[trigger]
# 群聊触发方式
group_require_at = true       # 需要 @机器人
group_command = "/ai"         # 或显式命令
group_keywords = []           # 或无前缀关键词触发

# 私聊触发方式
private_auto = true           # 私聊默认全走 Agent

[llm]
provider = "deepseek"
model = "deepseek-chat"
api_base = "https://api.deepseek.com"
# api_key 从 .env 读取

[agent]
system_prompt = """
你是72的QQ机器人助手。请用简洁友好的语气回复。
你可以使用工具来查询天气、搜索信息等。
"""
max_turns = 10                # 最大工具调用轮数
temperature = 0.7
max_history = 20              # 保留最近 N 轮对话
session_ttl = 3600            # 会话过期时间（秒）

[cooldown]
enabled = true
per_user = 5                  # 每用户每小时最多 N 次 Agent 调用
per_group = 30                # 每群每小时最多 N 次
```

---

## 七、配置系统

### 主配置 (`config/bot.toml`)

```toml
[bot]
qq = 1234567890
nickname = ["小72"]
admin_users = [9876543210]
command_prefix = "/"

[napcat]
http_host = "127.0.0.1"
http_port = 3000
ws_host = "127.0.0.1"
ws_port = 3001
reconnect_interval = 5  # 断线重连间隔（秒）

[plugin]
auto_discover = true
plugin_dirs = ["plugins"]
disabled_plugins = []

[logging]
level = "INFO"
file = "logs/bot.log"
rotation = "10 MB"
retention = "14 days"
```

### 环境变量 (`.env`)

```
# LLM API Keys
DEEPSEEK_API_KEY=sk-xxxxx
OPENAI_API_KEY=sk-xxxxx

# 可选：NapCat WebUI Token
NAPCAT_WEBUI_TOKEN=xxxxx
```

---

## 八、插件系统规范

### 插件生命周期

```
启动时: __init__ → 注册到 PluginManager
         ↓
运行时: match(event) → True → handle(event, bot) → 结束
         └→ False → 下一个插件

重载: 删除旧引用 → importlib.reload → 重新注册
```

### 插件能做什么

| 能力 | 通过 |
|------|------|
| 收发消息 | `bot.api.send_*_msg()` |
| 获取群/用户信息 | `bot.api.get_*_info()` |
| 群管理（踢/禁） | `bot.api.set_group_*()` |
| 注册为 AI 工具 | 实现 `as_tool()` |
| 定时任务 | `bot.scheduler.add_job()` |
| 监听系统事件 | `@on_notice("group_increase")` |
| 访问数据库 | 自由引入 aiosqlite / tortoise-orm |
| HTTP 请求 | 自由引入 aiohttp / httpx |

### 插件能访问什么（通过 `bot` 参数）

```python
bot.config          # 全局配置
bot.api             # NapCat API 客户端
bot.event_bus       # 事件总线（可发布自定义事件）
bot.plugin_manager  # 插件管理器（可注册/卸载）
bot.agent_engine    # Agent 引擎
bot.scheduler       # 定时任务调度器
```

---

## 九、开发阶段

### 第一阶段：最小可用核心 (MVP)

**目标**：跑通 NapCat ↔ Python 核心 ↔ 基本命令插件

- [ ] 项目脚手架搭建（目录、pyproject.toml、.gitignore）
- [ ] 配置系统（`config.py` 读取 TOML + .env）
- [ ] `ApiClient` 封装 NapCat HTTP API
- [ ] `EventBus` + napcat-sdk WebSocket 连接
- [ ] `PluginManager` 基础功能（注册、match、dispatch）
- [ ] `Bot` 主类组装 + `main.py` 启动入口
- [ ] 示例插件：`/ping`、`/echo`
- [ ] 日志系统 (loguru)
- [ ] PM2 部署配置

### 第二阶段：Agent 引擎

**目标**：插件可以同时是 Agent 工具，LLM 自动调用

- [ ] `LLMClient`：封装 OpenAI 兼容 API（支持 DeepSeek）
- [ ] `ToolRegistry`：插件工具注册 + OpenAI function schema 生成
- [ ] `AgentEngine`：手写 tool-calling 循环
- [ ] `SessionManager`：对话历史管理 + 自动裁剪
- [ ] Agent 触发策略（@机器人、/ai 命令、私聊自动）
- [ ] 调用频率限制 (cooldown)
- [ ] 示例插件：`ai_chat.py`（纯对话）、`weather.py`（展示 tool calling）

### 第三阶段：增强与扩展

**目标**：完善生态，按需实现

- [ ] 插件热重载（`/reload` 命令不重启进程）
- [ ] 定时任务调度器
- [ ] 数据持久化（插件数据存储 API）
- [ ] 权限系统（管理员 / 白名单 / 黑名单）
- [ ] 消息队列（避免频繁调用被限流）
- [ ] Web 管理面板（可选，FastAPI + 简单前端）
- [ ] 更多插件：Minecraft 服务器查询、RSS 订阅、GitHub Webhook 等

---

## 十、部署架构

### 开发环境

```
┌─────────────┐     ┌──────────────┐
│ NapCatQQ     │────→│ qqbot (python│
│ Docker/裸机  │←────│ main.py      │
│ :3000 :3001  │     │ :6185 管理   │
└─────────────┘     └──────┬───────┘
                           │
                    ┌──────┴───────┐
                    │ LLM API      │
                    │ (DeepSeek等) │
                    └──────────────┘
```

### 生产环境（腾讯云服务器）

```bash
# 1. 部署 NapCatQQ（Docker 推荐）
docker compose -f napcat.yml up -d
# WebUI: http://124.221.182.13:6099 → 扫码登录

# 2. 部署 qqbot
cd /opt/qqbot
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp config/bot.example.toml config/bot.toml  # 编辑配置
vim .env  # 填写 API Key

# 3. PM2 管理
pm2 start scripts/start.sh --name qqbot
pm2 save
```

### PM2 启动脚本 (`scripts/start.sh`)

```bash
#!/bin/bash
cd "$(dirname "$0")/.."
source venv/bin/activate
python -m src.main
```

---

## 十一、安全注意事项

1. **QQ 小号**：非官方协议有风控风险，务必使用不重要的 QQ 号
2. **API Key 保护**：`.env` 文件加入 `.gitignore`，绝不提交
3. **频率控制**：Agent 调用加 cooldown，避免 LLM 费用失控
4. **权限检查**：管理员命令需验证 `user_id` 在 `admin_users` 中
5. **输入过滤**：插件处理用户输入时注意注入风险
6. **NapCat WebUI**：6099 端口不要暴露公网，或设置强 Token

---

## 十二、附录

### A. OneBot v11 常用消息段

```json
// 纯文本
{"type": "text", "data": {"text": "你好"}}

// @某人
{"type": "at", "data": {"qq": "123456"}}

// 图片
{"type": "image", "data": {"file": "http://...", "url": "http://..."}}

// 回复
{"type": "reply", "data": {"id": "消息ID"}}

// 表情
{"type": "face", "data": {"id": "1"}}
```

### B. 参考资源

- [NapCatQQ 仓库](https://github.com/NapNeko/NapCatQQ)
- [NapCat Docker 部署](https://github.com/NapNeko/NapCat-Docker)
- [OneBot v11 协议](https://github.com/botuniverse/onebot-11)
- [napcat-sdk 文档](https://github.com/faithleysath/napcat-sdk)
- [OpenAI Function Calling](https://platform.openai.com/docs/guides/function-calling)
- [DeepSeek API](https://platform.deepseek.com/api-docs)
