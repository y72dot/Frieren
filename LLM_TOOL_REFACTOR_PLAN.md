# LLM 工具与插件系统分离重构方案书

> 状态：代码重构完成，待真实 NapCat 验收
> 编制日期：2026-07-22
> 范围：LLM Tool Platform、LLM 插件适配层、工具清单与 Prompt
> 原则：渐进迁移、行为兼容、每阶段可独立回滚

## 1. 背景与基线

当前系统已经具备实例级 `ToolCatalog`、`ToolExecutor`、权限校验、调用持久化和审计，但工具实现仍位于 `plugins/llm_*_tools.py`，由 `Bot.ensure_tool_platform()` 逐个导入。插件系统因而同时承担两种不同职责：

- 消息插件：订阅 MessageBus，匹配并消费 EXTERNAL、INTERNAL、ACTION 或 LIFECYCLE 消息。
- Agent 工具：向模型声明能力，并通过 ToolExecutor 调用领域服务。

重构前基线：

- 常驻工具 54 个；启用 Sandbox 后增加 5 个；当前 weather Skill 再增加 1 个。
- `AgentLoop` 已直接调用 `ToolExecutor`。
- `plugins.llm_tools.llm_tools_handler` 仍保留另一条 INTERNAL 工具执行路径。
- `ToolCatalog.get_defs()` 只按 `requires_admin` 控制展示，展示策略与执行权限策略不一致。
- 系统 Prompt、JSON Schema 和 `tool_help` 同时维护工具说明。

## 2. 重构目标

1. `plugins/` 只保留消息入口、事件适配和出站适配，不承载 ToolDef 实现。
2. 工具注册只经过一个核心 composition root，Bot 不感知具体 Provider。
3. 工具只通过 `AgentLoop -> ToolExecutor` 执行，MessageBus 不承担工具 RPC。
4. 注册全集与单次模型可见工具集分离。
5. 普通对话每轮默认暴露 5～10 个工具，而不是完整目录。
6. 展示过滤和执行授权共享同一策略语义，执行阶段继续二次校验。
7. 工具 schema 成为能力说明的唯一事实源。
8. 保持现有 NapCat、MessageBus 和自研插件架构，不引入新框架。

## 3. 非目标

- 不重写 MessageBus、PluginManager 或 ToolExecutor。
- 不在同一阶段改工具名称、参数和业务行为。
- 不一次删除全部旧导入路径。
- 不把所有操作合并为一个不透明的万能工具。
- 不在本次重构中改变数据库 Schema 或审批模型。

## 4. 目标边界

```text
QQ Event
  -> plugins/llm_gate.py                 消息入口
  -> LlmAgentService                     Agent 编排
  -> AgentLoop
  -> ToolSelector                        生成本轮 ToolView
  -> ToolExecutor                        校验、授权、幂等、审计
  -> Tool Provider                       ToolDef 到领域服务的适配
  -> ApiClient / History / Artifact / Workspace / Web / Scheduler

最终回复
  -> plugins/llm_sender.py               QQ 出站适配
```

目标目录：

```text
src/core/llm/
  tool_selector.py
  tool_view.py
  tool_metrics.py
  tools/
    bootstrap.py
    providers/
      qq.py
      artifact.py
      capability.py
      control.py
      schedule.py
      sandbox.py

plugins/
  llm_gate.py
  llm_core.py       # 最终缩减为 LlmAgentService 的薄适配器
  llm_sender.py
```

## 5. 阶段一：建立边界和统一组装

### 目标

先改变代码归属和依赖方向，不改变工具名称、schema、注册顺序和执行结果。

### 工作项

- 新建 `src/core/llm/tools/providers/`。
- 将 QQ、Artifact、Capability、Control、Schedule、Sandbox 工具实现移出 `plugins/`。
- 新建 `bootstrap.py`，集中维护常驻 Provider 注册顺序和可选 Sandbox Provider。
- `Bot.ensure_tool_platform()` 只调用 `register_builtin_tools()`。
- `plugins.llm_core` 的降级目录改为引用核心 Provider。
- 旧 `plugins.llm_*_tools` 路径保留兼容别名。
- INTERNAL `llm_tools_handler` 暂留在插件兼容层，核心 Provider 禁止订阅 MessageBus。

### 验收

- 常驻目录仍为 54 个工具，名称和顺序不变。
- 原有 `plugins.llm_*_tools` 导入继续可用。
- Provider 模块不导入 `src.plugin` 或 `MessageType`。
- Tool Platform、Artifact、Sandbox、LLM Tools 测试通过。
- 全量测试无新增失败。

### 回滚

恢复 Bot 原来的逐模块注册，并让旧插件文件恢复完整实现；本阶段无数据迁移。

## 6. 阶段二：删除重复工具执行链

### 目标

工具调用只有 `AgentLoop -> ToolExecutor` 一条生产路径。

### 工作项

- 将 `plugins/llm_core.py` 的编排逻辑提取为 `LlmAgentService`。
- `llm_core` 只负责把 INTERNAL trigger 转交给服务。
- 删除 `llm_type=tool`、`response_buffer` 和 `llm_tools_handler`。
- 测试直接覆盖 `ToolExecutor.execute()` 和 AgentLoop 工具调用链。
- 删除 `plugins.llm_tools` 中临时兼容适配器；保留一个版本周期的导入告警或别名。
- 将 `plugins.llm_memory._format_msg` 移到 History/Context 格式化服务，消除核心 Provider 反向导入插件。

### 验收

- `rg 'llm_type.*tool|response_buffer' src plugins` 无生产代码命中。
- 一次工具调用只产生一条 Invocation。
- INTERNAL 总线不再承担工具 RPC。

## 7. 阶段三：ToolView、权限可见性和工具包

### 目标

目录保存全部能力，模型只看到当前请求真正可用的子集。

### 设计

为 ToolDef 增加声明式元数据：

```python
contexts: set[str]       # group / private / scheduled
audiences: set[str]      # user / admin
packs: set[str]          # core / moderation / web / control ...
intents: set[str]
default_enabled: bool
```

新增：

```python
ToolSelector.select(
    catalog,
    context,
    permissions,
    enabled_features,
    message,
) -> ToolView
```

初期采用确定性选择，不增加一次额外 LLM Router 调用：

- 始终加载小型 `core` 包。
- 根据群聊/私聊、管理员身份和功能开关做硬过滤。
- 根据明确关键词和消息资源类型加载 moderation、artifact、web、schedule 等包。
- 无法确定时允许一个轻量 `discover_tools` 返回工具包摘要。

### 验收

- 普通闲聊默认工具数不超过 12。
- 普通用户看不到执行时必然拒绝的工具。
- 同一请求的 ToolView 顺序稳定，便于 Provider Prompt 缓存。
- Selector 决策写入会话日志。

## 8. 阶段四：精简和规范化工具

### 删除

- `think`：不使用工具模拟内部推理。
- `tool_help`：由 schema 和可选 `discover_tools` 取代。
- 普通会话中的 `send_message`：最终回复已有统一发送链。

### 合并

- `query_history` + `search_messages` -> 单一消息搜索工具。
- `set_essence` + `remove_essence` -> 带状态参数的精华管理工具。
- `sandbox_read/write/list` 与受控 Workspace 文件能力统一。
- Artifact list/info 按返回模型评估合并，保留发送工具的独立风险边界。

### 隔离为按需工具包

- settings、prompts、plugins：仅 control 管理模式。
- schedule：仅定时任务意图。
- moderation：仅群聊管理意图且调用者有相应权限。
- web、workspace、sandbox：按配置、身份和任务意图启用。

### 约束

合并工具不能模糊风险等级、审批要求或权限边界。高风险动作宁可保持独立，也不塞入通用 `action` 工具。

## 9. 阶段五：Prompt、兼容层和质量收口

### 工作项

- 删除 `config.py` 中硬编码工具清单和链式调用说明。
- `tool_policy.md` 只保留跨工具通用规则。
- 删除所有 `plugins.llm_*_tools` 兼容别名。
- 将测试从固定总数断言改为 Provider 契约、ToolView 和权限矩阵断言。
- 增加指标：注册数、单轮可见数、schema 字节数、工具选择命中率、拒绝率和未知工具率。
- 记录重构前后 Prompt 大小、首个正确工具率和平均工具轮数。

### 最终验收

- `plugins/` 下不存在 ToolDef 注册实现。
- Bot 不导入具体 Provider。
- 普通对话工具 schema 字节数相对基线下降至少 60%。
- 管理工具不会出现在普通用户 ToolView。
- 全量自动化测试通过，真实 NapCat 验收无行为回归。

## 10. 风险与控制

| 风险 | 控制措施 |
|---|---|
| 移动模块导致资源相对路径失效 | 资源路径改为项目根或配置中心解析，并增加测试 |
| 旧测试依赖私有函数和模块变量 | 阶段一使用模块别名兼容，后续逐项迁移测试 |
| 动态选择漏掉必要工具 | 默认 core 包、确定性规则、discover_tools 回退、日志观测 |
| 工具合并造成权限扩大 | 风险和审批边界优先于数量指标 |
| ToolView 顺序变化影响模型稳定性 | Provider 和工具包均使用确定性顺序 |
| 兼容层长期残留 | 每个兼容入口标明删除阶段，并在阶段五设置零命中验收 |

## 11. 阶段状态

| 阶段 | 状态 | 交付物 |
|---|---|---|
| 阶段一：边界和组装 | 已完成 | Provider 包、bootstrap、兼容入口 |
| 阶段二：单一执行链 | 已完成 | LlmAgentService、删除工具 RPC |
| 阶段三：动态 ToolView | 已完成 | Selector、权限可见性、工具包 |
| 阶段四：工具精简 | 已完成 | 能力审计、双向动作合并、重复工具删除 |
| 阶段五：质量收口 | 已完成（待真实 NapCat 验收） | Prompt、指标、兼容层和测试收口 |

### 阶段一执行记录（2026-07-22）

- 六类工具实现已迁移到 `src/core/llm/tools/providers/`。
- `Bot` 已改为通过 `src.core.llm.tools.bootstrap` 注册常驻和可选工具。
- 常驻 54 个、启用 Sandbox 后 59 个工具的名称和顺序保持不变。
- 旧 `plugins.llm_*_tools` 路径保留兼容入口。
- QQ 设定文档路径已随模块迁移修正为项目根解析。
- 消息格式化已移到 `src/core/llm/message_format.py`，Provider 不再反向导入插件。
- INTERNAL 工具适配器仍作为阶段二待删除项保留。
- 验证结果：全量自动化测试通过，700 passed、2 skipped。

### 阶段二执行记录（2026-07-22）

- LLM 会话、Runtime、Prompt 和 AgentLoop 编排已移入 `src/core/llm/agent_service.py`。
- `plugins/llm_core.py` 已缩减为 INTERNAL trigger 的薄 MessageBus 适配器。
- 删除 `_inline_loop`，不再维护第二套 ReAct 循环。
- 删除 `plugins.llm_tools.llm_tools_handler` 及其 MessageBus 订阅。
- 生产代码中已不存在 `llm_type=tool`、`response_buffer` 或工具 RPC。
- 工具调用唯一生产路径为 `AgentLoop -> ToolExecutor -> Tool Provider`。
- 工具行为测试已通过 `tests/tool_runner.py` 直接调用生产 ToolExecutor；旧 payload 形状仅保留在测试适配器内，供后续测试夹具渐进清理。
- 验证结果：全量自动化测试通过，699 passed、2 skipped。

### 阶段三执行记录（2026-07-22）

- `ToolDef` 已增加 contexts、audiences、packs、intents 和 default_enabled 元数据。
- 新增不可变 `ToolView` 与确定性 `ToolSelector`，保持 ToolCatalog 注册顺序。
- 现有 59 个内置及 Sandbox 工具均已声明工具包、上下文、受众和 Provider。
- AgentLoop 每轮根据最新用户消息、群聊/私聊和权限生成工具视图，并写入会话日志。
- 普通群聊默认从 54 个常驻工具缩减为 8 个，私聊缩减为 6 个。
- 默认群聊工具 schema 从 20401 bytes 降至 3941 bytes，减少 80.7%。
- moderation、web、workspace、control、schedule、sandbox 等工具包按明确意图加载。
- 普通用户看不到 destructive、admin-only 或缺少 scope/approval 的工具。
- ToolExecutor 权限检查同步执行 contexts 和 audiences，隐藏能力不能通过伪造调用绕过。
- Skill 默认渐进加载，可由名称、描述或自定义 intents 激活。
- 验证结果：全量自动化测试通过，706 passed、2 skipped。

### 阶段四执行记录（2026-07-22）

- 审计 `llms.txt` 的 NapCat API 索引，并抽查官方 schema：文件获取、URL、上传已由 QQFileGateway 和 Artifact 工具覆盖，无需暴露原始文件 API。
- `set_essence(message_id, enabled)` 统一设置/取消精华，删除 `remove_essence`。
- `react_emoji(message_id, emoji_id, enabled)` 统一添加/取消表情回应。
- `send_poke` 使用 NapCat `group_poke` 统一 action，同时支持群聊和私聊。
- 删除与 `query_history` 重复的 `search_messages`，保留覆盖同步、时间和消息 ID 查询的实现。
- 删除复述 schema 的 `tool_help` 和无业务效果的 `think`；相应帮助表和硬编码 Prompt 已清理。
- Sandbox 工具从 exec/read/write/list/delete 收敛为 `sandbox_exec` 与独立审计的 `sandbox_delete`。
- 账号凭据、退出/重启、原始包、群文件删除等敏感能力未暴露；相册、公告、待办、群文件管理留作后续按需工具包。
- 常驻注册数从 54 降到 50，启用 Sandbox 从 59 降到 52；普通群聊可见 7 个、私聊 5 个。
- 普通群聊 schema 从阶段三的 3941 bytes 继续降至 3465 bytes，相对最初 20401 bytes 减少 83.0%。
- 验证结果：生产代码 Ruff 通过；全量自动化测试 692 passed、18 skipped。

### 阶段五执行记录（2026-07-22）

- 默认 System Prompt 收敛为单一常量，删除配置加载路径中的第二份工具清单、链式调用示例和决策表。
- `tool_policy.md` 仅保留 schema 优先、证据、权限、副作用、错误处理和秘密保护等跨工具规则。
- 删除 `plugins/llm_tools.py`、artifact/capability/control/schedule/sandbox 五个同类兼容别名；插件目录不再包含 Tool Provider。
- 所有测试直接引用 `src/core/llm/tools/providers`；删除阶段四遗留的 16 个旧工具 skip 测试。
- 固定工具总数断言改为 Provider 能力契约、ToolView、受众和权限边界断言。
- 新增进程内 `ToolMetrics`：注册数、平均可见数、平均 schema 字节、工具调用轮数、首个/总体选择命中率、平均每轮调用数、执行数、权限拒绝率和未知工具率。
- 当前默认 System Prompt 为 674 bytes；普通群聊 ToolView 为 7 个工具、3465 schema bytes。
- `AGENTS.md` 保持 99 行，并补充 ToolMetrics 架构职责。
- 验证结果：相关代码 Ruff 通过；全量自动化测试 694 passed、2 skipped。真实 NapCat 行为验收需在连接实例后执行。
