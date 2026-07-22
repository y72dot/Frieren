# 阶段一实施计划与执行记录

> 阶段：ConfigCenter + PromptRegistry  
> 状态：首轮实现完成  
> 执行日期：2026-07-21

## 1. 阶段目标

阶段一为后续消息库、Artifact、Tool Platform 和 Durable Runtime 提供统一配置与 Prompt 基础，要求：

1. 保留现有 `BotConfig` 和 `system_prompt` 兼容性。
2. 新增统一 `ConfigCenter`，为新代码提供唯一配置读取入口。
3. 配置快照不得保存 API Key、Token 或 `.env` 内容。
4. Prompt 从单个 TOML 长字符串拆分为模块和 Profile。
5. 每次 LLM 触发保存配置版本、Prompt 版本和 Prompt Hash。
6. Prompt 变更能够应用于恢复或复用的会话。
7. Docker 部署能够读取同一套 Prompt 文件。
8. 不破坏现有插件、Agent 工具链和测试。

## 2. 具体实施步骤

### P1.1 建立行为基线

- 核对 `load_config()`、`Bot.start()`、LLM Session 和 AgentLoop 的配置读取点。
- 核对 `system_prompt` 的创建、会话复用和恢复路径。
- 运行配置、Bot 生命周期和 LLM 核心测试作为重构前基线。

状态：已完成。

### P1.2 扩展兼容配置模型

新增 `LLMPromptConfig`：

```text
enabled
prompts_dir
profile
```

兼容规则：

- 程序注入的 `LLMConfig` 默认 `enabled=false`，继续使用 `system_prompt`。
- 项目和部署配置显式启用 Prompt Registry。
- `system_prompt` 暂不删除，作为兼容和回滚入口。

状态：已完成。

### P1.3 实现 ConfigCenter

能力：

- 持有当前有效 `BotConfig`。
- 支持 `llm.model` 形式的点路径读取。
- 支持任务级临时 override 查询。
- 保存 `settings_versions`。
- 保存 `config_snapshots`。
- 对 API Key、Token、Password、Secret 和整个 `env` 脱敏。
- 测试注入 Bot 使用内存状态；生产加载使用 `data/config_state.db`。

状态：已完成首版。

动态配置提案、审批、原子发布和自动回滚仍按总方案放在 Control Plane 阶段实现，不在本阶段提前扩大范围。

### P1.4 实现 PromptRegistry

能力：

- 从 `manifest.toml` 加载版本。
- 加载 Markdown Prompt Part。
- 支持 Profile 组合、继承和追加。
- 检测缺失 Part、空 Part、未知 Profile 和继承循环。
- 使用 `${variable}` 做安全模板替换。
- 输出渲染文本、版本、Profile、Part 列表和 SHA-256。
- 支持从旧 `system_prompt` 创建 `legacy` Registry。

状态：已完成。

### P1.5 拆分默认 Prompt

当前模块：

```text
identity
behavior
qq_context
tool_policy
memory_policy
response_style
task_planner
summarizer
fact_extractor
```

当前 Profile：

```text
default
planner
summarizer
fact_extractor
```

`qq_context` 已明确原始 CQ 是消息事实的一部分，未知 CQ 不应被忽略；资源通过消息 ID 和工具解析，不猜测路径或 URL。

状态：已完成。

### P1.6 接入 Bot 和 Agent

- Bot 构造或加载配置时初始化 ConfigCenter 和 PromptRegistry。
- Prompt Registry 启用时启动即校验，错误不会静默降级。
- 每次 LLM Trigger 渲染当前 Profile。
- 新会话使用渲染 Prompt。
- 恢复或复用会话替换首条 System Prompt，保证配置更新生效。
- 每次 Trigger 创建 ConfigSnapshot，并把 ID 放入触发上下文。
- AgentLoop 优先通过 ConfigCenter 获取 LLM 配置。
- Bot 清理时关闭 ConfigCenter 数据库。

状态：已完成。

### P1.7 部署接入

- `config/bot.toml` 启用默认 Prompt Profile。
- `instances/frieren/bot.toml` 启用默认 Prompt Profile。
- Docker Compose 将 `config/prompts` 只读挂载到 `/config/prompts`。

状态：已完成。

### P1.8 测试与验收

新增测试覆盖：

- ConfigCenter 点路径和 override。
- 配置快照持久化。
- API Key、NapCat Token 和 env 脱敏。
- 配置版本递增。
- Prompt Profile 继承。
- Prompt 模板渲染。
- 缺失 Part。
- Profile 继承循环。
- 项目默认 Prompt 完整性。
- Bot 与 PromptRegistry 集成。

验证结果：

```text
阶段一目标测试：49 passed
完整测试套件：595 passed
Ruff：All checks passed
```

状态：已完成。

## 3. 数据库产物

生产启动后创建：

```text
data/config_state.db
```

包含：

- `settings_versions`：脱敏后的有效配置版本。
- `config_snapshots`：LLM 触发时使用的配置版本、Prompt 版本、Prompt Hash 和会话键。

当前快照可通过 `snapshot_id` 或 `context_key + created_at` 追踪。后续 Durable Runtime 会让 `task_runs.config_snapshot_id` 直接引用该记录。

## 4. 兼容边界

阶段一保留：

- `bot.config`。
- `LLMConfig.system_prompt`。
- 旧 Session 表和消息格式。
- 旧插件直接读取 `bot.config` 的行为。

新模块必须使用 `bot.config_center`。旧插件将在各自被重构时迁移，避免仅为形式统一而一次性修改全部稳定插件。

## 5. 阶段验收结论

阶段一首轮实现满足：

- 默认部署使用模块化、版本化 Prompt。
- 旧注入配置继续兼容。
- LLM 每次触发拥有配置快照。
- 配置快照不包含明文秘密。
- Prompt 变更能够进入复用会话。
- Docker 部署路径可用。
- 完整测试无回归。

阶段一可以作为后续重构的稳定基线。下一实施阶段应进入“无损 QQ Adapter、Event Journal 和新消息数据库”，不应继续扩展动态配置 Control Plane，以免打乱总方案依赖顺序。
