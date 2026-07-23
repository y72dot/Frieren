# QQBot 开发文档索引

> 状态：当前有效  
> 最后整理：2026-07-23  
> 用途：统一项目开发文档的阅读顺序、状态和维护规则。

## 1. 当前工作入口

所有合作者开始工作前按以下顺序阅读：

1. `AGENTS.md`：项目特有约束、消息链路和协作规则；
2. `README.md`：当前已实现能力、运行和部署方式；
3. `PLUGIN_SYSTEM_REFACTOR_PLAN.md`：当前插件系统重构的唯一执行计划；
4. 与任务相关的 `PHASE*_IMPLEMENTATION.md` 或专项完成记录。

当前主线只有一项：按照 `PLUGIN_SYSTEM_REFACTOR_PLAN.md` 渐进重构插件系统。生产切换的外部门禁仍按 `PHASE8_IMPLEMENTATION.md` 执行，两者互不替代。

## 2. 文档清单与状态

| 文档 | 状态 | 定位 | 是否用于新增任务 |
| --- | --- | --- | --- |
| `README.md` | 当前有效 | 用户与开发者入口、当前能力和运行说明 | 是 |
| `AGENTS.md` | 当前有效 | 合作者必须遵守的项目特有约束 | 是 |
| `CLAUDE.md` | 当前有效 | 与 `AGENTS.md` 同步的兼容协作说明 | 是 |
| `PLUGIN_SYSTEM_REFACTOR_PLAN.md` | 当前执行 | 插件系统目标架构、阶段、任务和验收真源 | 是 |
| `REFACTOR_PLAN.md` | 已完成基线 | Agent 能力平台总体设计及 Phase 1–8 历史决策 | 仅作约束与背景 |
| `PHASE1_IMPLEMENTATION.md` | 完成记录 | ConfigCenter 与 PromptRegistry | 否 |
| `PHASE2_IMPLEMENTATION.md` | 完成记录 | 无损 QQ Adapter、Event Journal、消息数据库 | 否 |
| `PHASE3_IMPLEMENTATION.md` | 完成记录 | Artifact Store 与 QQ 文件 | 否 |
| `PHASE4_IMPLEMENTATION.md` | 完成记录 | 历史回补与离线同步 | 否 |
| `PHASE5_IMPLEMENTATION.md` | 完成记录 | Tool Platform | 否 |
| `PHASE6_IMPLEMENTATION.md` | 完成记录 | Durable Runtime 与 Scheduler | 否 |
| `PHASE7_IMPLEMENTATION.md` | 完成记录 | 搜索、工作空间与 Control Plane | 否 |
| `PHASE8_IMPLEMENTATION.md` | 完成记录/门禁待办 | E2E、Docker、生产切换门禁 | 仅处理外部门禁 |
| `LLM_TOOL_REFACTOR_PLAN.md` | 专项完成记录 | LLM Tool 与消息插件分离 | 否 |
| `PLAN.md` | 历史归档 | 项目建立早期蓝图，部分结构已过时 | 否 |

## 3. 信息冲突时的优先级

出现文档或实现不一致时，按以下顺序判断：

1. 可执行代码、数据库迁移和自动化测试代表当前实际行为；
2. `AGENTS.md` 中的项目特有约束不可绕过；
3. `README.md` 描述当前对外行为；
4. 当前执行计划描述尚未实现的目标行为；
5. Phase 完成记录解释历史实现与当时验收；
6. 历史计划只提供背景，不应直接转化为新任务。

若代码与 `README.md` 不一致，应在同一变更中修正文档。若实现需要偏离当前执行计划，先在计划的“决策日志”中记录原因和替代方案。

## 4. 文档维护规则

- 当前计划只描述未完成和正在执行的工作；完成后将结果写入对应实施记录。
- 不在多个文档复制任务清单；其他文档只链接到任务真源。
- 每个阶段合并后，更新计划中的阶段状态、验收证据和决策日志。
- 测试数字必须注明命令、日期和运行环境；旧数字保留为历史快照，不称为“当前”。
- 文档中的接口示例必须标注“当前接口”或“目标接口”，避免示例先于实现造成误导。
- `AGENTS.md` 和 `CLAUDE.md` 保持不超过 100 行，只记录项目特有且难以从代码推断的信息。
- 不新增顶层目录；开发文档继续放在仓库根目录，插件文档和模板后续放在既有 `src/plugin/` 或插件包内部。

## 5. 当前基线

截至 2026-07-22：

- Agent 能力平台 Phase 1–8 的自动化实现已经完成；
- 生产切换仍缺少真实 QQ L6 和目标服务器故障恢复演练；
- LLM Tool Provider 已从 `plugins/` 迁出，工具只经 Tool Platform 执行；
- 插件系统重构 P0–P4 已交付：
  - P0：MessageBus SubscriptionScope、reload 残留修复；
  - P1：Manifest 模型与严格校验、PackageLoader/LegacyLoader、PluginDefinition/HandlerSpec、依赖拓扑与 SDK 兼容检查；
  - P2：LoadedPlugin 状态机、ResourceScope/TaskSupervisor、setup/start/stop 生命周期、RegistrySnapshot 原子热重载、Bot 生命周期接入 PluginRuntime；
  - P3：PluginContext 公共接口（QQAgency 权限检查、PluginConfigView 只读快照、reply/emit_internal/create_task）、Manifest 权限运行时执行与审计日志、LegacyAdapter（旧插件继续收 Bot，legacy=true 标记）；
  - P4：EventResult (CONSUME/CONTINUE) 语义、EventRegistry/ObserverRegistry 类型化分发、CommandRegistry 命令索引与别名解析、ActionMiddleware 洋葱管线 + MiddlewarePipeline、ACTION 中间件适配器（p=0）、action_queue 迁移为 ActionQueueMiddleware 类；
- 剩余阶段 P5–P6 已完成：
  - P5：类型化插件配置、PluginStorage 与迁移、Scheduler/Task 插件归属、CLI/模板/模拟器/doctor、sticker_react/repeater/history/essence 迁移完成；
  - P6：PLUG-603 + PLUG-604 完成——7 个剩余插件迁移到包格式、删除旧代码（legacy.py/decorators.py/LegacyLoader 等）、1015 tests pass、ruff clean。PLUG-601/602（Control Plane 部署）推迟至后续阶段。

下一项架构工作以 `PLUGIN_SYSTEM_REFACTOR_PLAN.md` 为准。
