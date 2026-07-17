# CLAUDE.md

## 重要：任何时候都要保持本文档精简，所有通用的或不言自明的或仅通过查看代码就能知道的信息不应出现在本文档中，只保留本项目特有的和难以掌握的信息，禁止修改本行，每次更新本文档时均需剔除所有过时信息并保证新信息的精简

## Architecture

```
NapCatQQ WebSocket → Bot._process_events (async for raw_event)
  → EventBus.parse (raw → Event) → EventBus.dispatch
    → MessageBus.dispatch (EXTERNAL, 按 priority 升序遍历插件)
      → Plugin.match → Plugin.handle → return True/False (suppressible)
    → MessageBus.flush (排空 ACTION 队列)
      → _QQExec → ApiClient._raw_call → 实际 HTTP/WS 调用
```

No NoneBot / AstrBot / Koishi — core is self-written.

### Core Subsystems

| 子系统 | 职责 |
|--------|------|
| `MessageBus` | 中央总线，所有插件订阅和 API 调用都经过它 |
| `EventBus` | 原始 napcat 事件 → 内部 `Event`；触发总线 dispatch + flush |
| `PluginManager` | 扫描/导入/注册插件到总线 EXTERNAL 队列 |
| `ApiClient` | API 调用包装成 ACTION 消息入队，由 `_QQExec` 最终执行 |

### Message Types & Dispatch Semantics

| MessageType | 来源 | suppressible | 消费规则 |
|-------------|------|-------------|---------|
| `EXTERNAL` | NapCat 事件 | 是 | 首个 match + handle 返回 truthy 的插件"吃掉"，后续不再执行 |
| `ACTION` | 插件 API 调用 | 是 | 同上，终点是内置 `_QQExec`（priority 100） |
| `INTERNAL` | 插件间通信 | 否 | 所有匹配处理器都执行，无法消费 |
| `LIFECYCLE` | 生命周期事件 | 否 | 同上 |

`BusMessage.depth` 上限 10，防无限递归。

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

### Post-Modification Workflow

- 修改代码并跑通测试后，重启机器人使变更生效
