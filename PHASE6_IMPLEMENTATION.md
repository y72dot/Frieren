# 阶段六实施说明：Durable Runtime 与 Scheduler

## 1. 阶段目标

本阶段把 Agent 从“一个协程内完成的临时 ReAct 循环”提升为具有持久化 Task、Run、Step、等待状态、恢复判定和定时触发能力的运行时。

核心约束：

- 每次 QQ、定时和事件触发都能追溯到 TaskRun；
- Scheduler 只创建 Run，不直接调用工具或发送消息；
- 工具 Invocation 必须归属到具体 Task、Run 和 Step；
- 重启后不得盲目重放结果未知的副作用；
- Schedule 显式保存时区、misfire 和并发策略；
- Bot 仍是单一完整个体，不引入多 Bot 或用户工作区。

## 2. 端到端结构

```text
QQ message / Schedule / Domain event
  → DurableRuntime.create_task_run
  → tasks + task_runs
  → Runtime handler
      → run_steps(kind=agent_loop / agent_prompt)
      → AgentLoop
          → run_steps(kind=tool)
          → ToolExecutor
          → tool_invocations(task_id/run_id/step_id)
      → normal llm_sender / ACTION / QQ pipeline
  → Step、Run、Task 进入终态或 WAITING 状态
```

Scheduler 链路：

```text
schedules.next_run_at 到期
  → SchedulerService.tick
  → 创建 Task + Run(status=SCHEDULED)
  → DurableRuntime.submit(run_id)
  → agent_prompt handler
  → INTERNAL llm_type=trigger
  → 正常 AgentLoop 和 QQ 回复链路
```

## 3. Task、Run 与 Step

`src/core/runtime/store.py` 使用与 MessageStore、Artifact、Invocation 相同的 SQLite 连接。

### 3.1 tasks

```text
task_id / goal / status / trigger_type / trigger_event_id
conversation_type / conversation_id / requested_by
created_at / updated_at / completed_at / error / metadata_json
```

`metadata_json.template` 保存可恢复任务模板，不依赖 Python 局部变量。

### 3.2 task_runs

```text
run_id / task_id / attempt / status
config_snapshot_id / prompt_version
started_at / ended_at / resume_token / error
```

同一 Task 可创建多个 Run，`attempt` 由数据库递增并以 `(task_id, attempt)` 唯一约束。

### 3.3 run_steps

```text
step_id / run_id / position / kind / status
input_json / output_json / started_at / ended_at / error
```

同一 Run 中 `position` 严格递增。当前使用 `agent_prompt`、`agent_loop` 和 `tool` 三类步骤。

## 4. 状态机与等待

```text
CREATED / PLANNING / RUNNING
WAITING_TOOL / WAITING_ARTIFACT / WAITING_APPROVAL / WAITING_USER
SCHEDULED
SUCCEEDED / FAILED / CANCELLED
```

终态自动写结束时间。DurableRuntime 提供：

- `wait(run_id, reason, resume_token)`；
- `resume(run_id, resume_token)`；
- `cancel(run_id)`。

等待原因映射为 tool、artifact、approval、user。令牌不匹配时拒绝恢复，防止无关事件误唤醒任务。

## 5. DurableRuntime

运行时使用显式 handler 注册表执行持久化模板：

```python
runtime.register_handler("agent_prompt", handler)
await runtime.execute_run(run_id)
runtime.submit(run_id)
```

执行时从数据库读取模板，推进 Task/Run，创建 Step，再调用 handler。成功输出、异常和取消都会同步写入 Step、Run 和 Task。后台 Run 由 Runtime 统一追踪，Bot 关闭时取消并等待，避免孤立协程。

## 6. AgentLoop 与 Invocation 归属

普通 QQ 触发创建：

```text
Task(trigger_type=qq_message)
Run(attempt=1)
Step(kind=agent_loop)
```

`ToolCallContext` 随后携带 `task_id/run_id/step_id/config_snapshot_id/trace_id`。

生产 AgentLoop 不再通过 INTERNAL 消息中的临时 `response_buffer` 获取工具结果，而是直接调用实例级 ToolExecutor。每个 ToolCall 先创建 `kind=tool` Step，Invocation 再写入同一个 Task、Run 和 Step。

旧 `llm_tools_handler` 和 inline loop 保留 `response_buffer` 兼容入口，供原有插件测试替身和外部调用者迁移；生产 AgentLoop 已不依赖它。

`tool_invocations` 自动迁移新增 `step_id`。旧数据库启动时通过 `PRAGMA table_info` 检测并执行兼容 `ALTER TABLE`，无需删除历史记录。

## 7. RecoveryController

Bot 在插件和 LLM Provider 初始化后扫描恢复，并发布 `LIFECYCLE event=runtime.recovered`。

恢复规则：

1. `CREATED`、`SCHEDULED` Run 可重新提交；
2. `RUNNING` 且没有未决 Invocation 时回到 `CREATED`；
3. Invocation 已成功时把结果补入中断 Step；
4. `validating` 表明 executor 尚未开始，可以中断旧调用后安全重试；
5. `running` 的只读工具可以安全重试；
6. `running` 或 `timed_out` 的副作用工具结果未知，进入 `WAITING_APPROVAL`；
7. 原有 WAITING 状态保持不变，等待 Artifact、审批或用户事件携带令牌恢复。

Controller 不把“数据库没有成功结果”误判为“外部副作用没有发生”，避免重启后重复发消息、群管理或文件发送。

## 8. Scheduler 数据模型

```text
schedule_id / name / enabled
trigger_type / trigger_spec_json / timezone / task_template_json
target_conversation_type / target_conversation_id / created_by
next_run_at / last_run_at
misfire_policy / max_concurrency
created_at / updated_at
```

索引 `(enabled, next_run_at)` 用于快速查找已到期任务。

## 9. 触发类型

- `once`：`{"at": 1780000000}`，执行后自动禁用；
- `interval`：`{"seconds": 3600, "start_at": 1780000000}`；
- `cron`：标准五字段，支持 `*`、整数、范围、列表和步长；
- `event`：`{"event": "artifact.available"}`。

Cron 星期使用标准编号，星期日为 `0`。事件触发时 payload 写入本次任务模板，不修改原 Schedule。

## 10. 时区

每条 Schedule 必须显式存储时区，默认 `Asia/Shanghai`。

优先使用 Python IANA ZoneInfo。考虑 Windows Python 可能没有系统 tzdata，本项目为必要的 `UTC` 和 `Asia/Shanghai` 提供确定性回退；其他时区缺少 IANA 数据时明确拒绝，不静默改成本地时区。

## 11. Misfire 与并发

- `skip`：恢复时跳过全部错过点，并一次性推进到未来；
- `run_once`：只补一个最新执行点，并推进到未来；
- `catch_up`：按顺序补执行，单轮不超过 `max_catch_up`。

会产生消息的 `agent_prompt` 默认禁止 catch-up，防止恢复后批量轰炸 QQ；只有模板显式声明 `allow_catch_up=true` 才能开放。

`max_concurrency` 在创建 Run 前检查同一 Schedule 的所有非终态 Run。

## 12. Agent 调度工具

新增实例级工具：

- `create_schedule`；
- `list_schedules`；
- `set_schedule_enabled`；
- `delete_schedule`。

创建、暂停、恢复和删除需要 Bot 管理员，并继承阶段五的 Invocation 持久化和幂等策略。Schedule 工具只提交 `agent_prompt`，不能注入任意 Python handler。

## 13. Bot 生命周期

Bot 构造时创建实例级 Runtime 和 Scheduler；替换 MessageStore 后 `ensure_runtime_platform()` 自动重绑定。

启动顺序：消息投影恢复 → 插件 → LLM → Run 恢复 → 生命周期事件 → 提交可恢复 Run → misfire → Scheduler 轮询 → NapCat。

关闭时先停止 Scheduler，再取消 Runtime 后台 Run，随后关闭其他存储和服务。

## 14. 统一配置

```toml
[runtime]
enabled = true
recover_on_start = true

[scheduler]
enabled = true
timezone = "Asia/Shanghai"
poll_interval = 1.0
max_catch_up = 10
```

生产配置与实例配置均已加入，并通过 ConfigCenter 统一加载。

## 15. 测试覆盖

新增 `tests/test_durable_runtime.py`、`tests/test_scheduler.py` 和 `tests/test_e2e_runtime.py`，覆盖：

- Task/Run/Step 建表、状态、输出和 attempt；
- handler 成功、异常、等待、令牌恢复和取消；
- 旧 Invocation 表迁移；
- 成功 Invocation 恢复和未知副作用隔离；
- once、interval、Cron、event；
- skip、run_once、catch_up、快进和消息 catch-up 防护；
- 时区、Cron 星期、非法配置和并发边界；
- QQ 消息到 Task、Run、Tool Step、Invocation、QQ 回复全链路；
- 到期 Schedule 到正常 LLM 回复全链路；
- 原有消息、工具、Artifact、历史和 LLM 回归。

阶段验收结果：完整测试套件 `650 passed`；阶段相关 Ruff 检查和 `git diff --check` 通过。

## 16. 阶段边界与下一步

本阶段已具备持久化运行骨架，但尚未实现可视化计划 DAG、跨进程 worker、通用审批 UI 或任意外部事件订阅。当前恢复以安全分类和重新提交模板为主，不伪造未知工具结果。

下一阶段进入本地/网页搜索与 Control Plane：建立统一搜索、受控网络访问、设置和 Prompt 提案，以及插件安装验证与回滚。
