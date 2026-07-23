# CLAUDE.md

## 重要：任何时候都要保持本文档精简，不超过 100 行，所有通用的或不言自明的或仅通过查看代码就能知道的信息不应出现在本文档中，只保留本项目特有的和难以掌握的信息，禁止修改本行，每次更新本文档时均需剔除所有过时信息并保证新信息的精简

tip：用 Bash 工具 + curl 来抓取网页内容，不用 WebFetch。llms.txt 包含 napcat 接口文档

## Architecture

```
NapCatQQ WebSocket → Bot._process_events (async for raw_event)
  → EventBus.parse (raw → Event) → bot.msg_store.record(event)
    → FilterManager.check → 不通过则丢弃
    → MessageBus.dispatch (EXTERNAL, 按 priority 升序遍历插件)
      → Plugin.match → Plugin.handle → return True/False
  → 插件调用 bot.api.xxx() → MessageBus.dispatch (ACTION)
    → MiddlewarePipeline (p=0): ActionQueueMiddleware → _raw_call
    → _QQExec (p=100) 最终执行
```

### Core Subsystems

| 子系统               | 职责                                                   |
| -------------------- | ------------------------------------------------------ |
| `MessageBus`       | 中央总线，所有消息经此流转                              |
| `FilterManager`    | 全局+插件级过滤，`bot.filter_mgr`，dispatch 前拦截    |
| `EventBus`         | napcat 事件→内部`Event`；记录历史；触发 dispatch      |
| `MessageStore`     | SQLite 消息历史，`bot.msg_store` 同步查询              |
| `PluginManager`    | 扫描/注册插件；包格式加载                              |
| `PluginRuntime`    | 协调器：发现→解析→加载→启动→快照→热重载              |
| `PluginContext`    | 受限能力表面：QQAgency、ConfigView、Storage、Scheduler |
| `PluginStorage`    | 每插件 KV 存储，权限门控，schema 版本迁移              |
| `ApiClient`        | API 调用的 ACTION 封装，`_QQExec` 最终执行            |
| `action_queue`     | p=1 拦截 ACTION：block→bypass→spam→rate-limit            |
| `CommandRegistry`  | 命令索引，别名解析，CQ 码剥离                           |
| `EventRegistry`    | 类型化消费者分发（CONSUME/CONTINUE），含 wildcard       |
| `ActionMiddleware` | ACTION 洋葱中间件链（call_next），MiddlewarePipeline    |
| `LlmSessionLogger` | 每会话日志 →`logs/llm_sessions/`                       |

### LLM Agent 子系统（`src/core/llm/`，`Bot._init_llm_subsystems()` 挂载）

| 类                  | 职责                                                  |
| ------------------- | ----------------------------------------------------- |
| `ToolCatalog`     | `ToolDef` 注册，`get_defs(user_is_admin)` 过滤       |
| `ToolExecutor`    | 验证→权限→缓存→执行→审计，DESTRUCTIVE 写 `logs/audit.log` |
| `SessionManager`  | TTL + `data/llm_state.db` 持久化 + crash 恢复 + 剪枝 |
| `AgentLoop`       | ReAct 循环，运行时读`bot.llm_provider`               |
| `CircuitBreaker`  | 连续错误/重复工具调用熔断                              |
| `MemoryManager`   | 工作记忆(内存)+情景(episodes表)+语义(facts表)         |
| `SkillManager`    | `config/skills/` 渐进式加载+热重载                    |

### Message Types & Dispatch Semantics

| MessageType   | 来源          | suppressible | 消费规则                                                   |
| ------------- | ------------- | ------------ | ---------------------------------------------------------- |
| `EXTERNAL`  | NapCat 事件   | 是           | 首个 match + handle 返回 truthy 的插件"吃掉"，后续不再执行 |
| `ACTION`    | 插件 API 调用 | 是           | 同上，终点是内置`_QQExec`（priority 100）                |
| `INTERNAL`  | 插件间通信    | 否           | 所有匹配处理器都执行，无法消费                             |
| `LIFECYCLE` | 生命周期事件  | 否           | 同上                                                       |

`BusMessage.depth` 上限 10，防无限递归。

### Plugin Return Value Convention

- `match(event)` → `True`：进入 `handle(event, ctx)`；`False`：跳过
- `handle()` → `EventResult.CONSUME` / `True`：消费，停止遍历
- `handle()` → `EventResult.CONTINUE` / `False`：未处理，继续
- `bridge.py` Adapter 自动 `EventResult`↔`bool` 互转

### Plugin Discovery

- 包插件：`plugins/<id>/plugin.toml` + `plugin.py`（`__plugin_id__` + `@on_event` / `@command` / `@on_internal`）
- `[plugin_config.<id>]` TOML → typed dataclass 注入 `ctx.plugin_config`
- 禁用：`[plugin].disabled_plugins`；CLI：`python -m src.plugin.cli new/validate/list/doctor`

### Bot Lifecycle

- `Bot(config=...)` 注入配置用于测试，省略则自动从 `config/bot.toml` + `.env` 加载
- Active 模式：bot 主动连接 NapCat WS，断线自动重连
- Reverse 模式：bot 启动 HTTP 服务等待 NapCat 连接
- `Bot.start()` 阻塞直到 SIGINT/SIGTERM

### Constraints

- 插件禁导入 napcat 类型（用 `from src.plugin.base import Event`）；不新增顶层目录；不引入新框架
- 一个事件最多被一个插件消费（无中间件链）；Commit: `type: description`

### Logging & Tracing

- 格式串：`time | level | trace={extra[trace_id]} | name:func:line | message`
- grep trace_id：`REQUEST START` → `Filter pass/block` → `match/handle+耗时` → `API ok` → `REQUEST END`
- LLM：`llm_gate trigger` → `session [NEW]/reuse` → `final reply` → `llm_sender chunks` → `session end`
- INFO：生命周期/REQUEST/插件触发/LLM；DEBUG：match/handle/API/Filter
- Session 持久化到 `data/llm_state.db`，重启可恢复

### Startup

- `scripts/run.sh`：杀掉旧进程后启动，实时输出到控制台 + 追加到 `logs/bot.log`，Ctrl+C 停止
