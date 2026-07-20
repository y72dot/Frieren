# CLAUDE.md

## 重要：任何时候都要保持本文档精简，不超过 100 行，所有通用的或不言自明的或仅通过查看代码就能知道的信息不应出现在本文档中，只保留本项目特有的和难以掌握的信息，禁止修改本行，每次更新本文档时均需剔除所有过时信息并保证新信息的精简

tip：用 Bash 工具 + curl 来抓取网页内容，不用 WebFetch。llms.txt 包含 napcat 接口文档

## Architecture

```
NapCatQQ WebSocket → Bot._process_events (async for raw_event)
  → EventBus.parse (raw → Event) → bot.msg_store.record(event)
    → FilterManager.check (全局+插件级过滤) → 不通过则丢弃
    → MessageBus.dispatch (EXTERNAL, 按 priority 升序遍历插件)
      → Plugin.match → Plugin.handle → return True/False (suppressible)
  → 插件调用 bot.api.xxx() → MessageBus.dispatch (ACTION)
    → action_queue (p=1): block → bypass → spam → rate-limit
    → _QQExec (p=100) → ApiClient._raw_call → 实际 HTTP/WS 调用
```

No NoneBot / AstrBot / Koishi — core is self-written.

### Core Subsystems

| 子系统               | 职责                                                                           |
| -------------------- | ------------------------------------------------------------------------------ |
| `MessageBus`       | 中央总线，所有插件订阅和 API 调用都经过它                                      |
| `FilterManager`    | 统一过滤（全局 + 插件级），挂载于`bot.filter_mgr`，dispatch 前拦截           |
| `EventBus`         | 原始 napcat 事件 → 内部`Event`；记录历史；触发总线 dispatch                 |
| `MessageStore`     | SQLite 持久化消息历史，插件可通过`bot.msg_store` 同步查询                    |
| `PluginManager`    | 扫描/导入/注册插件到总线 EXTERNAL 队列；`@subscribe` 适配器                  |
| `ApiClient`        | API 调用包装成 ACTION 消息入队，由`_QQExec` 最终执行                         |
| `action_queue`     | p=1 拦截所有 ACTION，四层过滤：block → bypass → spam(去重) → rate-limit     |
| `LlmSessionLogger` | 每会话 LLM 对话日志 →`logs/llm_sessions/`，loguru sink + session-key filter |

### Message Types & Dispatch Semantics

| MessageType   | 来源          | suppressible | 消费规则                                                   |
| ------------- | ------------- | ------------ | ---------------------------------------------------------- |
| `EXTERNAL`  | NapCat 事件   | 是           | 首个 match + handle 返回 truthy 的插件"吃掉"，后续不再执行 |
| `ACTION`    | 插件 API 调用 | 是           | 同上，终点是内置`_QQExec`（priority 100）                |
| `INTERNAL`  | 插件间通信    | 否           | 所有匹配处理器都执行，无法消费                             |
| `LIFECYCLE` | 生命周期事件  | 否           | 同上                                                       |

`BusMessage.depth` 上限 10，防无限递归。

### `@subscribe` Direct Handler

`@subscribe(MessageType, priority=N)` 注册的函数签名是 `(payload: object, bot: Bot) -> bool`，`_SubscribeAdapter` 会解包 `msg.payload` 传入，**不是** `BusMessage`。用于非插件式处理器（如 action_queue 拦截 ACTION）。

### Plugin Return Value Convention

- `match()` → `True`：进入 `handle()`；`False`：跳过
- `handle()` → `True`：消费事件（EXTERNAL/ACTION 场景停止遍历后续插件）
- `handle()` → `False`：未处理，让下一个插件试

### Event Type Mapping (EventBus.parse)

napcat 原始类型 → `Event.type`：

- `GroupMessageEvent` → `"message.group"`
- `PrivateMessageEvent` → `"message.private"`
- `NoticeEvent` → `"notice.{notice_type}"`
- `dict` → 按 `post_type` 分发（message/notice/request/meta_event）
- 其他 → `None`（丢弃）

### Plugin Discovery

- `auto_discover()` scans `plugins/*.py`，文件名以 `_` 开头则跳过
- 修饰器 (`@command`, `@on_regex`, `@on_keyword`, `@on_notice`) 自动附加 `__plugin__`
- 禁用插件：将其 `__plugin__.name` 列入 `config/bot.toml` → `[plugin].disabled_plugins`

### Bot Lifecycle

- `Bot(config=...)` 注入配置用于测试，省略则自动从 `config/bot.toml` + `.env` 加载
- Active 模式：bot 主动连接 NapCat WS，断线自动重连
- Reverse 模式：bot 启动 HTTP 服务等待 NapCat 连接
- `Bot.start()` 阻塞直到 SIGINT/SIGTERM

### Constraints

- 插件只能用 `from src.plugin.base import Event`，禁止导入 napcat 类型
- 不新增 `src/` 和 `plugins/` 之外的顶层 Python 目录
- 不引入新依赖框架
- Phase 1 无中间件链：一个事件最多被一个插件消费
- Commit format: `type: description` (feat/fix/refactor/test/docs/chore)

### Logging & Tracing

- loguru `contextualize(trace_id=...)` 实现全链路追踪：`MessageBus.dispatch()` 对 EXTERNAL 类型设 trace_id，ACTION/INTERNAL/LIFECYCLE 用 `nullcontext()` 继承外层
- `BusMessage.trace_id` = uuid4 hex[:8]，grep 一个 id 即可还原事件从进入到 API 调用的完整链路
- `logs/llm_sessions/`：每会话独立日志，`LlmSessionLogger` 通过 session-key filter 写入
- 日志级别：连接/断开/模式/重连 → INFO；match/handle/API调用/事件解析 → DEBUG
- **注意**：`llm_core` 的 `_session_cache` 是内存 dict，bot 重启即清空，即使 TTL 未过期也会触发 `[NEW]`

### Startup

- `scripts/run.sh`：杀掉旧进程后启动，实时输出到控制台 + 追加到 `logs/bot.log`，Ctrl+C 停止
