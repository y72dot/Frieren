# 阶段五实施说明：Tool Platform

## 1. 阶段目标

本阶段把原先由 `plugins/llm_tools.py` 模块全局变量维护的工具集合，重构为每个 Bot 实例独立拥有的工具平台，并统一工具定义、注册、Schema 验证、权限检查、超时、幂等、调用记录和审计。

核心约束：

- Bot 是唯一完整个体，但内部状态仍必须实例化，禁止模块级 `_catalog`、`_executor` 污染测试或重载。
- 工具必须先登记 Invocation，再执行副作用。
- 参数、权限和输出验证失败也属于一次可查询的调用尝试。
- 写操作和破坏性操作默认采用运行级幂等键，模型重试不得重复执行。
- 敏感参数只能以脱敏形式进入数据库。
- 原有 QQ、Artifact 和 LLM 工具保持兼容。

## 2. 端到端执行链路

```text
LLM tool_call
  → AgentLoop / llm_tools_handler
  → Bot.ensure_tool_platform()
  → ToolExecutor.execute
      → ToolCatalog 查找定义与版本
      → 计算或读取 idempotency_key
      → 查询已成功 Invocation
      → InvocationStore.begin(status=validating)
      → JSON Schema 输入验证
      → ToolCallContext 权限、scope、approval 检查
      → transition(status=running)
      → asyncio timeout 包装实际 executor
      → JSON Schema 输出验证
      → 结果大小检查
      → transition(status=succeeded/failed/timed_out)
      → 破坏性操作写审计日志
  → 结构化结果返回 AgentLoop
```

## 3. 实例级工具平台

`Bot.ensure_tool_platform()` 负责创建并维护：

- `bot.tool_catalog`：当前 Bot 的工具注册表；
- `bot.tool_executor`：统一执行管线；
- `bot.invocation_store`：与当前 `MessageStore` 共享 SQLite 连接的调用仓库。

Bot 初始化时完成首次注册；测试或运行期间替换 `msg_store` 后，再次调用会自动重建平台并绑定新连接。不同 Bot 实例之间的工具注册、缓存和调用仓库不会共享 Python 可变状态。

`plugins/llm_tools.py` 只保留静态 `_tool_defs` 和兼容导出的 `TOOL_DEFS`，通过 `register_llm_tools(catalog)` 注入目标实例。Artifact 工具使用相同方式注册。原全局 `_catalog` 和 `_executor` 已删除。

## 4. ToolManifest

当前以扩展后的 `ToolDef` 作为可执行 ToolManifest，包含：

```text
name / version / description / category / provider
parameters / output_schema
risk_level / effects / scopes / requires_admin / approval
timeout_seconds / cache_ttl / idempotency
executor
```

注册时自动补全：

- `READ_ONLY` → `effects={read}`；
- `WRITE` → `effects={write}` 且默认 `idempotency=keyed`；
- `DESTRUCTIVE` → `effects={destructive}` 且默认 `idempotency=keyed`。

OpenAI function schema 保持原结构，并附带 `x-tool-version`。因此旧 Provider 和测试仍可使用 `TOOL_DEFS`，新运行时则可以按版本追踪实际执行定义。

## 5. 输入与输出验证

执行器统一支持本项目当前工具需要的 JSON Schema 子集：

- `object`、`array`、`string`、`integer`、`number`、`boolean`、`null`；
- `required`、`properties`、`additionalProperties=false`；
- `enum`；
- `items`、`minItems`、`maxItems`；
- `minLength`、`maxLength`；
- `minimum`、`maximum`。

Python `bool` 不会被误判为整数或数字。输入失败时工具 executor 不会运行，Invocation 终态为 `invalid`；输出 Schema 不匹配或序列化结果超过 `max_result_bytes` 时终态为 `failed`。

## 6. 权限上下文

`ToolCallContext` 只保存策略判断所需信息：

```text
user_id / group_id / user_is_admin
bot_is_group_owner / bot_is_group_admin
task_id / run_id / invocation_id / trace_id
config_snapshot_id / granted_capabilities / idempotency_key
```

权限顺序：

1. `approval=required` 必须具有 `approval:<tool_name>`，管理员也不能跳过显式审批；
2. 管理员可通过其余角色和 capability 检查；
3. 非管理员不得执行 `DESTRUCTIVE` 或 `requires_admin` 工具；
4. 工具声明的 `scopes` 必须是 `granted_capabilities` 的子集。

具体工具实现目前继续通过兼容执行器参数获得 Bot 服务；权限决策只依赖 `ToolCallContext`，不会由工具实现自行放宽。后续新增 Provider 应优先注入窄化服务，而不是增加新的权限旁路。

## 7. Invocation 持久化

`tool_invocations` 与消息、Artifact、历史状态位于同一数据库连接，核心字段为：

```text
invocation_id / task_id / run_id
tool_name / tool_version / arguments_json
status / idempotency_key / result_json / error
started_at / ended_at
trace_id / user_id / group_id / config_snapshot_id
```

状态包括：

- `validating`：已登记，正在验证；
- `running`：权限通过，executor 已开始；
- `succeeded`：输出验证通过并已持久化；
- `invalid`：未知工具或输入不合法；
- `denied`：权限、scope 或审批不通过；
- `timed_out`：超过工具或平台超时；
- `failed`：工具异常、输出不合法或结果过大。

参数键名包含 `token`、`secret`、`password`、`api_key` 时递归写为 `***`。错误信息截断到 2000 字符，避免异常对象无限膨胀调用表。

未知工具调用也会用 `tool_version=unknown` 登记为 `invalid`，因此审计查询不会遗漏模型幻觉产生的工具名。

## 8. 幂等与失败重试

`idempotency=keyed` 时优先使用上游显式 `ToolCallContext.idempotency_key`；否则使用：

```text
SHA256(run_id + tool_name + canonical_json(arguments))
```

相同运行、工具和参数如果已经成功，执行器直接返回持久化结果，不再调用实际工具。唯一索引只覆盖 `status=succeeded`，因此验证失败、权限拒绝、超时和执行失败均可使用同一幂等键重试。

READ_ONLY 工具仍可使用短时内存结果缓存；缓存命中同样会把当前 Invocation 记为成功。

## 9. 统一配置

工具平台配置进入统一 `BotConfig`：

```toml
[tools]
default_timeout = 30.0
invocation_persist = true
max_result_bytes = 262144
```

- `default_timeout`：工具未单独声明超时时使用的平台默认值；
- `invocation_persist`：是否启用数据库调用记录；生产配置默认开启；
- `max_result_bytes`：单次结构化结果序列化后的最大字节数。

配置存在于 `config/bot.toml` 和实例配置中，并随阶段一 ConfigCenter 快照进入调用记录的 `config_snapshot_id`。

## 10. 兼容策略

- `TOOL_DEFS` 继续作为静态 OpenAI schema 列表导出；
- 非标准 Bot 测试替身没有 `ensure_tool_platform` 时，handler 会建立局部 catalog/executor；
- 旧 `_session_cache` 保留为 SessionManager 的兼容视图，但不承担工具注册或执行；
- 运行时修改 `session_ttl` 会同步到 SessionManager，`0` 仍表示每次创建新会话；
- `AgentLoop` 和兼容 inline loop 均使用实例级工具执行器。

## 11. 测试覆盖

新增 `tests/test_tool_platform.py`，覆盖：

- 成功调用、工具版本、trace/config snapshot 和敏感参数脱敏；
- 完整输入 Schema 拒绝且 executor 不运行；
- 输出 Schema 和结果大小限制；
- capability 拒绝、管理员显式审批、超时终态；
- 写工具 effect/idempotency 默认值；
- 成功调用幂等复用和失败调用可重试；
- 未知工具调用持久化；
- 多 Bot 工具平台隔离；
- MessageStore 替换后的 InvocationStore 重绑定；
- 原有 LLM 工具、会话、多轮和端到端链路回归。

阶段验收结果：完整测试套件 `635 passed`；阶段相关 Ruff 检查和 `git diff --check` 通过。

## 12. 阶段边界与下一步

本阶段解决的是单次工具调用的可靠边界，尚未建立长期 Task/Run/Step 状态机，也没有 Scheduler、misfire、审批等待恢复和进程崩溃后的步骤级续跑。当前 `run_id` 仍主要由会话键提供。

下一阶段进入 Durable Runtime 与 Scheduler：建立任务状态机，把 Invocation 归属到持久化 Run/Step，以任务恢复控制器替换临时响应缓冲，并实现一次性、间隔和 Cron 调度。
