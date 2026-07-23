# QQBot 插件系统重构详细计划书

> 文档状态：当前执行计划  
> 编制日期：2026-07-22  
> 适用范围：`src/plugin/`、`src/core/message_bus.py`、插件装载相关 Bot 生命周期、`plugins/`、Control Plane 插件部署链路及相关测试  
> 当前阶段：P0+P1+P2+P3+P4+P5+P6 全部完成
> 任务真源：本文第 20 节  
> 原则：渐进迁移、行为兼容、阶段可回滚、不引入 NoneBot/AstrBot/Koishi

## 1. 执行摘要

本次重构将现有“扫描 Python 文件并把任意对象直接订阅到 MessageBus”的插件机制，升级为：

```text
Plugin Package + Manifest
  → Discovery / Validation
  → PluginRuntime（依赖、生命周期、状态、代次）
  → 专用 Registry（Event / Command / Action Middleware / Lifecycle）
  → MessageBus 与既有领域服务
  → PluginContext（最小能力、配置、存储、任务、日志）
```

最终交付必须同时满足两类目标：

1. 平台目标：可靠装载、完整卸载、原子热重载、权限约束、资源托管、可观测和可回滚；
2. 开发体验目标：新手不需要理解 MessageBus、CQ 目标选择或 `handle() -> bool`，即可创建、模拟和测试一个命令插件。

本计划不一次性重写全部插件。现有 `Plugin` Protocol、装饰器和插件文件在兼容期继续工作，每阶段均有独立测试门禁和回滚点。

## 2. 当前基线与已知问题

### 2.1 当前链路

```text
plugins/*.py
  → PluginManager.auto_discover()
  → importlib.import_module()
  → 检查函数 __plugin__ / __subscribe__ 或无参类
  → MessageBus.subscribe(MessageType, handler, priority)
  → match(payload)
  → handle(payload, bot)
  → truthy 时消费 EXTERNAL/ACTION
```

当前稳定约束必须保留：

- 插件使用内部 `Event`，禁止直接依赖 NapCat 类型；
- EXTERNAL 事件最多被一个业务插件消费；
- ACTION 最终仍由 `_QQExec` 调用 `ApiClient._raw_call`；
- INTERNAL 和 LIFECYCLE 是不可消费的广播；
- 全局和插件级 Filter 在业务处理前执行；
- `BusMessage.depth` 上限保持为 10；
- Tool Provider 不回迁到 `plugins/`，Agent 工具继续只经 Tool Platform 执行。

### 2.2 已知结构问题

| ID | 问题 | 影响 |
| --- | --- | --- |
| B-01 | `Plugin` 只有 name/priority/match/handle | 无法表达版本、依赖、权限、配置和生命周期 |
| B-02 | 业务消费者、广播订阅器、ACTION 拦截器共用同一抽象 | 返回值、错误策略和执行模型含义混乱 |
| B-03 | 自动发现只扫描一层 `*.py` | 不支持资源、多模块、迁移和包式插件 |
| B-04 | `import_module` 与复用 MessageBus 的 reload | 模块缓存、旧订阅残留和删除插件继续运行 |
| B-05 | 装饰器把单一实例写入函数属性 | 堆叠装饰器覆盖，静态定义难以检查 |
| B-06 | 插件直接接收完整 Bot | 强耦合、权限不可控、测试构造成本高 |
| B-07 | 无 setup/start/stop 生命周期 | 后台任务、连接、缓存和订阅无法可靠释放 |
| B-08 | 异常只记录后继续 | 无插件健康状态、熔断、超时和告警门槛 |
| B-09 | Control Plane 校验整个候选但只复制入口文件 | 多文件插件安装不完整 |
| B-10 | Manifest permissions 只记录不执行 | 声明权限与运行时能力脱节 |
| B-11 | 禁用只修改配置列表 | 运行中的订阅和后台任务不会立即停止 |
| B-12 | 插件名常取函数名且总线按 name 去重 | 跨模块冲突、过滤身份和部署身份不稳定 |

## 3. 目标、非目标与成功指标

### 3.1 目标

- Manifest 成为插件身份、版本、入口、兼容性、依赖和权限的唯一声明源。
- PluginRuntime 管理从发现到停止的完整状态机。
- 每个插件拥有稳定 ID、装载代次和可一次关闭的资源作用域。
- 新旧版本通过影子装载和原子 Registry 快照切换完成热重载。
- 普通插件只得到声明并获准的 PluginContext 能力。
- Command、Event Consumer、Observer、ACTION Middleware 和 Lifecycle Hook 使用不同契约。
- 配置、存储、后台任务、Scheduler、日志和指标自动按 plugin_id 隔离归属。
- Control Plane 安装、启停、升级、验证和回滚与运行时闭环。
- 提供模板、FakeContext、事件 Fixture、校验命令和本地模拟器。

### 3.2 非目标

- 不引入第三方机器人框架或替换 NapCat 技术路线。
- 不把 Tool Platform 合并回插件总线。
- 不在第一轮实现不可信代码的进程级沙箱；先建立进程内能力边界。
- 不改变 Event Journal、MessageStore、Artifact、History、Durable Runtime 的事实模型。
- 不改变“一次 EXTERNAL 事件最多由一个业务消费者消费”的约束。
- 不承诺 Python 包依赖的运行时在线安装；依赖管理先做声明和校验。
- 不在重构期间顺带改变已有插件业务行为。

### 3.3 成功指标

| 维度 | 指标 |
| --- | --- |
| 可靠性 | 连续重载同一插件 100 次无订阅、Task 或资源增长 |
| 回滚 | 新版本 import/setup/start 任一步失败时旧版本继续工作 |
| 隔离 | 未声明的 QQ、存储、调度能力在调用点被拒绝并记录审计 |
| 可诊断 | 可查询插件状态、版本、代次、订阅数、任务数和最近错误 |
| 兼容性 | 兼容期内全部现有插件行为和 E2E 场景不变 |
| 开发体验 | 新手只编辑一个生成文件即可完成命令、模拟、测试 |
| 性能 | 事件分发吞吐不低于重构前基线的 90%，P95 不恶化超过 20% |

## 4. 固定设计决策

以下决策是协作默认值。若要修改，必须先写入第 19 节决策日志。

| 决策 | 内容 |
| --- | --- |
| D-01 | 对外身份统一使用 Manifest `id`，函数名只作为 handler_id 的一部分 |
| D-02 | 插件包采用目录 + `plugin.toml`；单文件旧插件由兼容 Loader 支持 |
| D-03 | PluginRuntime 不替代 MessageBus；它负责装载、作用域和 Registry 发布 |
| D-04 | 业务处理结果使用显式 `EventResult`，旧 bool 由兼容层映射 |
| D-05 | ACTION 使用 `call_next` 洋葱中间件；不再伪装成普通 Plugin |
| D-06 | 插件默认接收 PluginContext，不接收完整 Bot |
| D-07 | 核心内建插件可使用受审计的 CorePluginContext，第三方插件不可获得 |
| D-08 | 热重载采用新代次影子装载、原子快照切换、旧代次排空和停止 |
| D-09 | 禁用、卸载和回滚都必须经过同一 Runtime 状态转换 |
| D-10 | 兼容 API 至少保留到全部现有插件迁移且连续两个阶段门禁通过 |
| D-11 | 插件 ID 使用小写 snake_case；版本使用可比较的语义化版本字符串 |
| D-12 | 不新增顶层目录；新核心代码继续位于 `src/plugin/` |

## 5. 目标模块结构

目标结构在现有目录内渐进建立：

```text
src/plugin/
├── __init__.py            # 稳定公共 SDK 导出
├── api.py                 # Plugin、EventResult、装饰器公共入口
├── manifest.py            # Manifest 模型、解析和兼容检查
├── definition.py          # PluginDefinition 与各类 HandlerSpec
├── context.py             # PluginContext 与能力接口
├── runtime.py             # PluginRuntime 与状态机
├── loader.py              # 包式、单文件兼容 Loader
├── scope.py               # Subscription/Task/Resource 作用域
├── registry.py            # 不可变 RegistrySnapshot
├── command.py             # CommandRegistry、参数解析和帮助元数据
├── middleware.py          # ACTION Middleware 契约和管线
├── diagnostics.py         # 状态、健康和 doctor 报告
├── legacy.py              # 旧 Protocol/装饰器适配，迁移结束后删除
├── base.py                # 兼容期保留，最终缩减为 Event 等稳定类型
├── decorators.py          # 兼容转发，最终只保留公共装饰器
└── manager.py             # 兼容 facade，最终删除或转发 Runtime
```

不得在上述模块中反向导入具体 `plugins.*`。PluginRuntime 的组装入口由 Bot 创建，但运行时内部依赖通过构造参数注入。

## 6. 核心模型

### 6.1 PluginManifest

目标 Manifest：

```toml
[plugin]
id = "hello"
name = "Hello Plugin"
version = "1.0.0"
entrypoint = "hello.plugin:plugin"
sdk = ">=1.0,<2.0"
description = "回复 hello 命令"

[dependencies]
plugins = []

[permissions]
qq = ["message.send"]
storage = ["plugin"]
scheduler = false
network = []

[config]
schema = "hello.config:HelloConfig"
```

必填字段为 `plugin.id/version/entrypoint/sdk`。解析阶段完成：

- ID、版本和入口格式校验；
- SDK 兼容范围检查；
- 入口路径不得逃逸插件根目录；
- 依赖存在性、版本范围和环检测；
- 权限名称白名单校验；
- Manifest 未知字段按严格模式报错，避免拼写静默失效。

### 6.2 PluginDefinition

`PluginDefinition` 是 import 后得到的静态能力集合，至少包含：

- plugin_id、版本和描述；
- CommandSpec 列表；
- EventHandlerSpec 列表；
- ObserverSpec 列表；
- ActionMiddlewareSpec 列表；
- LifecycleHookSpec 列表；
- 配置 Schema 引用；
- setup/start/stop hook。

装饰器只追加 Spec，不直接访问 MessageBus，也不在 import 阶段创建后台任务。

### 6.3 LoadedPlugin

每个运行实例保存：

```text
manifest + definition + module_namespace + context
+ generation + state + scope + health + loaded_at
```

状态机：

```text
DISCOVERED → VALIDATED → LOADED → STARTING → ACTIVE
                    ↘ FAILED       ↘ FAILED
ACTIVE → DEGRADED
ACTIVE/DEGRADED → STOPPING → STOPPED
```

非法状态转换直接拒绝。每次转换记录 plugin_id、version、generation、原因和 trace_id。

## 7. 面向新手的 SDK

### 7.1 最小插件

目标接口示例，尚未实现前不得放入 README 当作当前接口：

```python
from src.plugin import Plugin, command

plugin = Plugin("hello")


@plugin.on_start
async def start(ctx):
    ctx.logger.info("hello started")


@command("hello", aliases=["你好"])
async def hello(ctx, event, args):
    await ctx.reply("你好！")
```

默认行为：

- `ctx.reply()` 自动选择群或私聊目标；
- 命令前缀、别名、空白和参数解析由 CommandRegistry 处理；
- 正常返回默认视为 `CONSUME`，显式返回 `CONTINUE` 才继续；
- 异常由 Runtime 捕获、计数并生成用户安全日志；
- 插件不需要导入 Bot、MessageBus、ApiClient 或 NapCat 类型。

### 7.2 进阶事件订阅

```python
from src.plugin import EventResult


@plugin.on_event("notice.notify", priority=20)
async def on_poke(ctx, event):
    if event.notice_sub_type != "poke":
        return EventResult.CONTINUE
    await ctx.qq.poke(event.conversation)
    return EventResult.CONSUME
```

### 7.3 API 稳定性

- `src.plugin` 是公共 SDK 唯一导入入口；
- `src.plugin.runtime/loader/registry` 属于内部 API，插件不得导入；
- 公共 API 破坏性修改需要提升 SDK major；
- 废弃接口至少保留一个完整迁移周期并产生一次警告；
- 示例、类型提示和测试 Fixture 与公共 API 同版本维护。

## 8. PluginContext 与能力控制

### 8.1 上下文组成

```text
PluginContext
├── identity       plugin_id/version/generation
├── logger         自动绑定 plugin/trace 字段
├── config         类型化只读快照与变更订阅
├── qq             获准的 QQ 操作
├── storage        插件命名空间存储
├── events         INTERNAL/LIFECYCLE 发布
├── scheduler      插件归属的定时任务
├── tasks          托管后台协程
├── tools          仅注册工具 Provider 元数据，不绕过 ToolExecutor
└── reply()        当前会话快捷回复
```

### 8.2 权限执行

权限检查必须发生在能力调用点，不依赖插件自觉：

```text
Manifest 声明
  → 安装审批确认
  → Runtime 构造最小 Capability
  → 每次敏感调用复核 scope
  → 审计结果
```

第一批权限：

- `qq.message.send`
- `qq.message.react`
- `qq.group.manage`
- `storage.plugin.read/write`
- `scheduler.create/manage`
- `events.publish`
- `network.http`（仍必须经过安全 Web 能力）
- `artifacts.read/write/send`

插件不得通过 Context 获得 `.env`、NapCat 登录目录、Docker Socket、原始数据库连接或 `ApiClient._raw_call`。

### 8.3 兼容上下文

旧插件由 `LegacyAdapter` 继续收到 Bot，但必须：

- 日志标记 `legacy=true`；
- 禁止新插件使用该入口；
- 在迁移清单中记录剩余使用者；
- 兼容层删除前用静态搜索和测试证明没有外部调用者。

## 9. Registry 与 MessageBus 边界

### 9.1 四类处理器

| 类型 | 语义 | 返回值 | 执行方式 |
| --- | --- | --- | --- |
| Command/Event Consumer | 可消费 EXTERNAL | CONTINUE/CONSUME | priority 升序，首次消费停止 |
| Observer | 只观察 | 无 | 全部执行，不可消费 |
| Internal/Lifecycle Handler | 广播处理 | 无 | 全部执行 |
| Action Middleware | 包裹 ACTION | API result | `call_next` 洋葱链 |

Filter 继续在任何 EXTERNAL consumer 前执行。Observer 默认只看到通过全局过滤的事件；需要观察被拦截事件的安全审计器属于核心能力，不作为普通插件开放。

### 9.2 RegistrySnapshot

Runtime 构建不可变快照：

```text
generation
+ commands_by_name
+ consumers_by_event_type
+ observers_by_event_type
+ internal_handlers_by_topic
+ lifecycle_handlers
+ action_middlewares
```

分发只读取一个快照引用。热重载完成验证后一次替换引用，避免部分订阅已新、部分仍旧。

### 9.3 SubscriptionScope

在迁移到完整快照前，MessageBus 先增加过渡能力：

```python
scope = bus.create_scope(plugin_id="hello", generation=3)
token = scope.subscribe(...)
scope.close()
```

`close()` 必须幂等，并一次移除该作用域所有订阅。内建 `_QQExec` 使用独立核心作用域，普通插件不得覆盖。

## 10. 生命周期与资源托管

标准顺序：

```text
validate
→ import
→ build definition
→ create context/scope
→ setup（注册资源，不接收业务事件）
→ start（允许启动托管任务）
→ publish ACTIVE snapshot
→ serve
→ remove from ACTIVE snapshot
→ drain in-flight handlers
→ stop
→ close tasks/subscriptions/resources
```

要求：

- setup/start/stop 均有超时；
- setup/start 失败自动按逆序释放已创建资源；
- stop 失败不能阻止 scope 最终关闭，但要进入审计和 DEGRADED/FAILED 记录；
- `ctx.tasks.create()` 登记所有 Task，停止时先协作取消再强制取消；
- 同一插件事件默认串行或按 conversation key 串行，具体策略由 HandlerSpec 声明；
- Runtime 关闭顺序先停止接收新事件，再排空，最后释放 Bot 依赖服务。

## 11. 原子热重载

热重载算法：

1. 为目标版本分配新 generation 和独立 module namespace；
2. 解析 Manifest、静态检查、导入并构建 Definition；
3. 校验 handler ID、命令冲突、依赖和权限；
4. 创建影子 Context/Scope，执行 setup/start；
5. 构建包含新代次的 RegistrySnapshot；
6. 原子发布新快照；
7. 旧代次停止接收新事件，等待在途处理达到超时；
8. 执行旧代次 stop 并关闭 Scope；
9. 持久化成功部署状态；
10. 任一步失败则关闭影子代次，旧快照保持不变。

必须覆盖以下场景：

- 代码删除一个 handler；
- 插件改名或版本回退；
- 新版 import 失败；
- setup 创建部分资源后失败；
- start 超时；
- reload 时旧 handler 正在发送 ACTION；
- reload 与 disable 同时发生；
- Runtime 重启后部署记录与磁盘不一致。

每个 plugin_id 的状态转换使用独立异步锁；全局快照发布使用短临界区，不持锁执行插件代码。

## 12. 发现、安装与 Control Plane

### 12.1 发现

发现顺序：

1. 配置中显式插件目录；
2. 目录内带 `plugin.toml` 的插件包；
3. 兼容期内目录第一层旧 `*.py`；
4. 按 plugin_id 排序后执行依赖拓扑排序。

发现只产生 Candidate，不执行插件代码。重复 ID、循环依赖或 SDK 不兼容在 import 前失败。

### 12.2 安装

Control Plane 从“复制入口文件”改为“原子部署整个插件包”：

```text
candidate validation
→ package digest
→ staging directory
→ offline contract tests
→ approval
→ atomic directory swap
→ Runtime shadow load
→ activation record
```

部署记录保存：plugin_id、version、digest、manifest、权限快照、目标目录、备份目录、激活 generation、测试摘要和状态。

### 12.3 启停与回滚

- enable：Runtime load/start/publish 成功后再写 enabled 状态；
- disable：先从快照移除并 stop，再持久化 disabled；
- rollback：部署旧包为新 generation，不复活旧 Python module 对象；
- install/upgrade 权限扩大必须重新审批；
- 同版本但 digest 不同视为不可变性违规，默认拒绝。

## 13. 插件基础设施

### 13.1 配置

- 每插件拥有类型化配置 Schema 和默认值；
- 配置位于统一 ConfigCenter 的 plugin namespace；
- 激活前校验，失败不替换运行中快照；
- handler 每次读取同一 ConfigSnapshot，避免单次执行中途漂移；
- 配置变更 hook 不能直接绕过重新验证。

### 13.2 存储与迁移

- `PluginStorage` 自动限定 plugin_id；
- 不向插件暴露共享 sqlite connection；
- Schema version 与迁移函数属于插件包；
- 迁移在事务中执行，先备份或支持向前恢复；
- 禁用不删除数据，卸载是否清理数据必须单独审批。

### 13.3 Scheduler 与后台任务

- Schedule 和 Task 记录 plugin_id/version/generation；
- 禁用插件时暂停其 Schedule，重启后不自动调用不存在的 handler；
- 更新后只有 Manifest 显式声明兼容的 durable handler 可以恢复；
- 后台 Task 异常上报 Runtime，不允许成为无人读取异常。

### 13.4 日志、指标与健康

所有插件日志至少携带：

```text
trace_id plugin_id plugin_version generation handler_id event_type elapsed_ms result
```

每插件指标：

- loaded/active/degraded 状态；
- match、handle、consume、continue 次数；
- 异常、超时、权限拒绝次数；
- 当前订阅、后台 Task、在途 handler 数；
- P50/P95 耗时；
- 最近成功、最近错误和连续错误数。

连续错误达到阈值时先 DEGRADED 并告警。自动停用策略在有足够生产数据前不默认开启。

## 14. 错误、超时与并发策略

- match/路由条件必须是同步纯函数，不允许网络或数据库访问；
- handler、hook 和 middleware 均设置可配置超时；
- 单插件异常不得中断其他插件和 Bot 主循环；
- `CancelledError` 必须继续传播到 Runtime，不能被普通异常处理吞掉；
- ACTION middleware 必须保持调用一次 `call_next` 的约束，重复调用视为错误；
- 同一 conversation 的消费型 handler 默认顺序执行；不同 conversation 可并发；
- Observer 使用有界并发，慢 Observer 不得无限堆积；
- 过载策略、队列上限和丢弃行为必须有指标，不做静默丢弃。

## 15. 测试与验收体系

### 15.1 单元测试

- Manifest 正常、未知字段、路径逃逸、版本和权限校验；
- 依赖拓扑、缺失依赖和循环依赖；
- Definition handler 收集、堆叠装饰器和冲突检测；
- EventResult 与旧 bool 映射；
- Scope 幂等关闭；
- Context 权限允许/拒绝；
- 状态机合法和非法转换。

### 15.2 集成测试

- 包式与旧单文件插件共同发现；
- setup/start/stop 顺序和失败补偿；
- 新旧代次原子切换；
- disable/re-enable/rollback；
- ACTION middleware 顺序和 `_QQExec` 唯一终点；
- Filter 与 Command/Event Registry 顺序；
- Config、Storage、Scheduler 的 plugin_id 归属。

### 15.3 E2E 与故障测试

- NapCat Fixture → Event Journal → Filter → 新 Registry → ACTION；
- reload 时在途事件只由一个代次消费；
- 连续 reload 无泄漏；
- 进程在部署目录切换、激活记录写入前后崩溃的恢复；
- 恶意 Manifest、禁止导入、路径逃逸和权限绕过；
- Docker/Linux 权限与原子目录替换；
- 真实 L6 只在已有显式授权门禁下执行。

### 15.4 每阶段统一门禁

```bash
ruff check <本阶段新增或修改文件>
pytest <本阶段定向测试>
python scripts/run_e2e.py --levels L0,L1,L2,L3
```

涉及部署、恢复或文件权限的阶段增加 Docker L4/L5。全量旧测试必须通过；条件 skip 必须说明环境原因。

## 16. 现有插件迁移顺序

| 批次 | 插件 | 验证目标 |
| --- | --- | --- |
| M1 | `ping`、`echo` | 新手命令 API、reply、CommandRegistry |
| M2 | `poke`、`sticker_react` | Notice/Event API、QQ 权限能力 |
| M3 | `repeater`、`history`、`essence` | 存储、复杂 match、群管理权限 |
| M4 | `llm_gate`、`llm_core`、`llm_sender` | INTERNAL、上下文、出站链路 |
| M5 | `action_queue` | ACTION 洋葱中间件、状态与限流 |

每个迁移 PR 只能改变接口接入，不改变业务规则。迁移前后使用同一事件 Fixture 对比：是否匹配、是否消费、QQ ACTION 参数和持久化副作用。

## 17. 分阶段实施计划

### P0：基线和重载止血

目标：先消除继续开发最危险的订阅残留，建立可重构安全网。

任务：

- 建立当前插件清单、事件类型、优先级和消费行为快照；
- 为 MessageBus 增加 SubscriptionToken/Scope 和按 scope 退订；
- 修复 reload 对旧订阅和 import cache 的处理；
- 增加重复 reload、删除插件和改名插件测试；
- 记录分发吞吐和 P95 基线。

验收：旧 API 不变，重载后只存在当前插件订阅，全部现有测试通过。

回滚：Scope 是增量 API；Bot 可继续使用旧 Manager 注册路径。

### P1：Manifest、Definition 与 Loader

目标：建立稳定身份和静态插件定义，尚不改变事件分发语义。

任务：

- 实现严格 Manifest 模型和诊断；
- 实现 PackageLoader 与 LegacyLoader；
- 实现 PluginDefinition 和可堆叠 Spec 收集；
- 检测 ID、handler、命令和依赖冲突；
- 为现有插件生成隐式 legacy Manifest；
- 给 `ping` 增加首个包式候选 Fixture。

验收：发现阶段不执行业务代码；新旧插件均能生成统一 Candidate/Definition 报告。

回滚：PluginManager 仍可只使用 LegacyLoader 输出。

### P2：PluginRuntime 与生命周期

目标：统一装载、状态、资源作用域和原子热重载。

任务：

- 实现 LoadedPlugin、状态机、generation 和诊断；
- 实现 setup/start/stop、超时和失败补偿；
- 实现 TaskSupervisor 和 ResourceScope；
- 实现 RegistrySnapshot 原子切换；
- Bot 启停接入 Runtime；
- reload/enable/disable 改为状态转换。

验收：影子版本失败不影响旧版本；100 次 reload 无资源增长；关闭顺序可测试。

回滚：保留 PluginManager facade，将调用转发到 Runtime；必要时配置切回 legacy activation。

### P3：PluginContext 与权限

目标：停止新插件依赖完整 Bot，连接 Manifest 权限与运行时能力。

任务：

- 定义公共 PluginContext；
- 实现 reply、qq、events、config、tasks 最小能力；
- 接入权限检查和审计；
- 实现 LegacyAdapter；
- 迁移 `ping`、`echo`、`poke`；
- 建立 FakeContext 和 Fixture。

验收：新插件代码中无 Bot/ApiClient/MessageBus 导入；权限拒绝有明确错误和审计。

回滚：已迁移插件可暂时通过 Context 内部委托现有服务，不回退公共接口。

> **完成记录 (2026-07-22)**：`src/plugin/context.py`（PluginContext、QQAgency、PluginConfigView、PermissionDeniedError）、`src/plugin/legacy.py`（is_legacy_plugin、mark_legacy_dispatch、wrap_legacy_context）。适配器修改（4 个 adapter 类接受可选 plugin_context），Runtime 在 _prepare_plugin() 中为包插件构建 PluginContext，LifecycleRunner._run_single_hook() 按 plugin.context 分发。34 new tests (25 context + 9 legacy)，953 total pass，ruff clean。旧插件 ping/echo/poke 保持 unchanged（legacy 路径继续传 Bot），新式 Fixture 证明 ctx 端到端可工作。

### P4：专用 Registry 和 ACTION Middleware

目标：拆开消费者、观察者、广播处理器和 ACTION 中间件语义。

任务：

- 实现 EventResult、EventRegistry 和 ObserverRegistry；
- 实现 CommandRegistry、别名、参数和冲突检测；
- 实现 ActionMiddleware `call_next` 管线；
- 保持 Filter 和 `_QQExec` 顺序；
- 迁移 `action_queue`；
- 删除新路径对 `_SubscribeAdapter` 的依赖。

验收：四类 handler 的执行和返回语义有独立契约测试；原 ACTION API 响应不变。

回滚：LegacyAdapter 可继续把旧 handler 注册到兼容 Registry。

> **完成记录 (2026-07-22)**：`src/plugin/definition.py`（EventResult CONSUME/CONTINUE + from_bool/to_bool）、`src/plugin/registry.py`（EventRegistry 类型化消费者分发含 wildcard 优先级排序、ObserverRegistry 广播观察者分发）、`src/plugin/command.py`（CommandRegistry 不可变命令索引含别名解析与 CQ 码剥离、CommandMatch dataclass）、`src/plugin/middleware.py`（ActionMiddleware Protocol + MiddlewarePipeline call_next 洋葱链）、`src/plugin/manager.py`（_MiddlewarePipelineAdapter 桥接 pipeline 到 Plugin 协议 p=0、4 个 adapter 类 normalize EventResult→bool）、`src/plugin/runtime.py`（_register_action_pipeline / _build_action_pipeline / _make_action_terminal，pipeline 优先于 legacy handler 注册）、`plugins/action_queue.py`（ActionQueueMiddleware 类含实例级 rate-limit/spam/block/bypass，legacy @subscribe handler 与模块级状态完整保留）。45 new tests（16 registry + 17 command + 12 middleware），998 total pass，ruff clean。四类 handler 语义分离完成，ACTION pipeline 在 Runtime 激活时于 p=0 注册、_QQExec 保留为兜底，legacy 测试路径不受影响。

### P5：配置、存储、Scheduler 与开发工具

> **完成记录 (2026-07-23)**：`src/plugin/config.py`（类型化插件配置 + ConfigSnapshot + `[plugin_config.<id>]` TOML 注入）、`src/plugin/storage.py`（PluginStorage KV 存储 + 权限门控 + schema 版本迁移）、`src/plugin/scheduler_agency.py`（SchedulerAgency 插件归属 + 健康检查）、`src/plugin/cli.py`（CLI new/validate/list/doctor）、`src/plugin/simulator.py`（离线事件模拟器）。迁移 sticker_react、repeater、history、essence 到包格式。全量测试通过。

### P6：Control Plane 闭环与兼容层退场

> **完成记录 (2026-07-23)**：PLUG-603 + PLUG-604 完整执行——7 个剩余插件（ping、echo、poke、llm_core、llm_sender、llm_gate、action_queue）全部迁移到包格式。删除所有旧代码：`src/plugin/legacy.py`、`Plugin` Protocol（base.py）、旧装饰器（decorators.py）、`LegacyLoader`、`_import_legacy_plugin()`、`_is_legacy_class_plugin()`、bot 兜底（lifecycle.py）。Adapter 类移至 `src/plugin/bridge.py`。1015 tests pass，ruff clean。增刊上下文新增 `llm_enabled` 与 `record_bot_message()`。
>
> **PLUG-601/602 完成记录 (2026-07-23)**：Control Plane 复用正式 `[plugin]` Manifest，整包校验后在 staging 再次核对摘要，同版本不同内容拒绝部署；两阶段重命名在任一切换点失败均恢复旧包。`DeploymentCoordinator` 调用 `PluginRuntime.reload_plugin()` 并核验目标插件、版本和 RegistrySnapshot，未激活时禁止写入 ACTIVE。`ControlPlane.approve_and_apply()` 仅在 Runtime 成功后标记 applied，失败时恢复文件、持久化禁用配置和旧 Runtime 快照。启动恢复按文件系统状态和待激活记录完成补偿。相关专项 73 tests、全量 1064 tests、ruff clean，宿主机与 Docker L0–L5 全绿。

## 18. 协作者拆分与合并边界

### 18.1 工作流

| 工作流 | 主要文件 | 可并行前提 |
| --- | --- | --- |
| A Runtime | `runtime.py`、`scope.py`、Bot 生命周期 | P1 模型稳定后 |
| B SDK/Definition | `api.py`、`definition.py`、公共类型 | 可与 Manifest 并行，先冻结接口 |
| C Loader/Manifest | `loader.py`、`manifest.py` | P0 后可独立进行 |
| D Registry/Bus | `registry.py`、`command.py`、`middleware.py`、MessageBus | Scope 契约合并后 |
| E Infrastructure | `context.py`、配置/存储/Scheduler 适配 | Context 接口冻结后 |
| F Control Plane | 插件验证、部署、回滚 | P2 Runtime API 稳定后 |
| G Migration/DX | `plugins/`、模板、CLI、文档 | 相应公共 API 合并后 |
| H Tests | contract、integration、E2E、performance | 各工作流同步进行 |

### 18.2 冲突控制

- 同一阶段只指定一个合作者修改 `src/core/bot.py` 和 `src/core/message_bus.py`；
- 公共类型先以小 PR 合并，其他工作流基于已合并契约开发；
- 插件迁移按批次拆分，禁止多人同时改同一插件；
- Control Plane 与 Runtime 通过明确 facade 集成，双方不直接修改对方内部状态；
- 测试 Fixture 名称包含阶段和能力，避免复用隐式全局状态；
- 每个 PR 在描述中列出任务 ID、依赖任务、行为变化、回滚方法和验证命令。

### 18.3 Definition of Ready

任务开始前必须具备：

- 对应任务 ID 和阶段；
- 输入/输出接口；
- 不变量和非目标；
- 至少一个失败场景；
- 可本地执行的验收命令；
- 与其他工作流的文件所有权边界。

### 18.4 Definition of Done

- 代码、类型、测试和文档同一 PR 更新；
- 不新增 NapCat 类型到插件公共 API；
- 新增资源有明确关闭路径；
- 新增副作用有权限、超时、审计和失败补偿；
- 定向测试、相关 E2E、Ruff 通过；
- 计划任务状态和验收证据已更新；
- 没有无期限 TODO，延期项有新任务 ID。

## 19. 决策日志

所有偏离固定设计决策的变更先追加到此表，再实现代码。

| 日期 | ID | 决策 | 原因 | 影响 |
| --- | --- | --- | --- | --- |
| 2026-07-22 | ADR-P-001 | 采用 Manifest + Runtime + Context + 专用 Registry | 解决现有身份、生命周期、权限和扩展性问题 | 全部阶段 |
| 2026-07-22 | ADR-P-002 | 首轮只做进程内能力隔离 | 子进程隔离成本高，先建立可执行权限边界 | P3，后续可扩展 |
| 2026-07-22 | ADR-P-003 | 保留 MessageBus 与单消费者语义 | 维持现有架构和插件行为兼容 | P0–P6 |
| 2026-07-22 | ADR-P-004 | Control Plane 部署整个包而非入口文件 | 候选校验范围必须与实际部署范围一致 | P6 |
| 2026-07-22 | ADR-P-005 | P3 不修改 ping/echo/poke 源码，改以测试 Fixture 验证新式 API | 旧插件仍经 legacy 路径接收 Bot，强行改源码会导致运行路径不一致；P5 包格式完整接入后再迁移实际插件 | P3, P5 |

## 20. 任务真源

状态只使用 `TODO / DOING / BLOCKED / DONE`。任何合作者领取任务时更新负责人标识；完成时附 PR/commit 和测试证据。

| ID | 阶段 | 任务 | 状态 | 依赖 | 验收证据 |
| --- | --- | --- | --- | --- | --- |
| PLUG-001 | P0 | 冻结现有插件行为和性能基线 | DONE | 无 | `tests/test_plugin_baseline.py` (4 tests) |
| PLUG-002 | P0 | MessageBus SubscriptionToken/Scope | DONE | PLUG-001 | `tests/test_message_bus.py` scope tests (6 tests) |
| PLUG-003 | P0 | 修复 reload 残留与模块缓存 | DONE | PLUG-002 | `tests/test_reload.py` (6 tests incl. 100x reload) |
| PLUG-101 | P1 | Manifest 模型与严格校验 | DONE | PLUG-001 | `tests/test_manifest.py` (33 tests) |
| PLUG-102 | P1 | Candidate、PackageLoader、LegacyLoader | DONE | PLUG-101 | `tests/test_loader.py` (28 tests) |
| PLUG-103 | P1 | PluginDefinition 与 HandlerSpec | DONE | PLUG-101 | `tests/test_definition.py` (32 tests) |
| PLUG-104 | P1 | 依赖拓扑和 SDK 兼容检查 | DONE | PLUG-101 | `tests/test_topology.py` (37 tests) |
| PLUG-201 | P2 | LoadedPlugin 状态机与诊断 | DONE | PLUG-102, PLUG-103 | `tests/test_loaded_plugin.py` (20 tests) |
| PLUG-202 | P2 | ResourceScope 与 TaskSupervisor | DONE | PLUG-201 | `tests/test_plugin_scope.py` (26 tests) |
| PLUG-203 | P2 | setup/start/stop 生命周期 | DONE | PLUG-202 | `tests/test_plugin_lifecycle.py` (20 tests) |
| PLUG-204 | P2 | RegistrySnapshot 与原子 reload | DONE | PLUG-203 | `tests/test_plugin_registry.py` (21 tests) |
| PLUG-205 | P2 | Bot 生命周期接入 Runtime | DONE | PLUG-204 | `tests/test_plugin_runtime.py` (29 tests) |
| PLUG-301 | P3 | PluginContext 公共接口 | DONE | PLUG-201 | `tests/test_plugin_context.py` (25 tests) |
| PLUG-302 | P3 | QQ/reply/events/tasks 能力 | DONE | PLUG-301 | `tests/test_plugin_context.py` (25 tests, 同上) |
| PLUG-303 | P3 | Manifest 权限执行与审计 | DONE | PLUG-302 | `tests/test_plugin_context.py` QQAgency allow/deny tests |
| PLUG-304 | P3 | LegacyAdapter | DONE | PLUG-301 | `tests/test_plugin_legacy.py` (9 tests) |
| PLUG-305 | P3 | 迁移 ping/echo/poke | DONE | PLUG-302, PLUG-304 | 旧插件行为保持不变(legacy 路径); 新式 Fixture 已建立 |
| PLUG-401 | P4 | EventResult 与 Event/Observer Registry | DONE | PLUG-204 | `tests/test_plugin_registry.py` EventResult/EventRegistry/ObserverRegistry tests (16 new) |
| PLUG-402 | P4 | CommandRegistry | DONE | PLUG-401 | `tests/test_command_registry.py` (17 tests) |
| PLUG-403 | P4 | ActionMiddleware 管线 | DONE | PLUG-204 | `tests/test_action_middleware.py` (12 tests) |
| PLUG-404 | P4 | 迁移 action_queue | DONE | PLUG-403 | ActionQueueMiddleware class + pipeline wiring; legacy handler preserved; full regression 998 pass |
| PLUG-501 | P5 | 类型化插件配置 | DONE | PLUG-301 | `tests/test_plugin_config.py` |
| PLUG-502 | P5 | PluginStorage 与迁移 | DONE | PLUG-301 | `tests/test_plugin_storage.py` |
| PLUG-503 | P5 | Scheduler/Task 插件归属 | DONE | PLUG-301 | `tests/test_scheduler_agency.py` |
| PLUG-504 | P5 | CLI、模板、模拟器、doctor | DONE | PLUG-103, PLUG-301 | `tests/test_cli.py` |
| PLUG-505 | P5 | 迁移 M2–M4 插件 | DONE | PLUG-401, PLUG-501, PLUG-502 | sticker_react, repeater, history, essence 迁移完成 |
| PLUG-601 | P6 | Control Plane 整包原子部署 | DONE | PLUG-204 | `tests/test_coordinator.py` (13 tests), `tests/test_control_plane.py` (4 tests), `tests/test_deployer.py`, `tests/test_security.py` |
| PLUG-602 | P6 | 启停升级回滚接入 Runtime | DONE | PLUG-601 | `_coordinator.py` `_reload_runtime()`, `runtime.py` `reload_plugin()`, `service.py` async `approve_and_apply()` + coordinator wiring |
| PLUG-603 | P6 | 完成插件迁移与旧调用清零 | DONE | PLUG-505 | 7 插件迁移，全量 1015 tests pass |
| PLUG-604 | P6 | 删除兼容层并更新当前文档 | DONE | PLUG-603 | legacy.py/decorators.py/LegacyLoader 删除，ruff clean |

## 21. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 重构范围膨胀 | 阶段独立门禁，业务行为变更另开任务 |
| 新旧双轨长期存在 | 为每个兼容入口维护迁移计数和删除任务 |
| 热重载竞态 | 每插件状态锁、不可变快照、插件代码不在全局锁内执行 |
| Context 变成另一个大 Bot | 能力按接口拆分，Manifest 最小授权，禁止 raw escape hatch |
| 权限声明形同虚设 | 调用点强制检查，Control Plane 保存审批快照 |
| 插件数据迁移破坏回滚 | 事务、备份、向前恢复，代码回滚不假设 Schema 自动回退 |
| Observer 或插件阻塞主链路 | 超时、有界并发、在途指标和降级状态 |
| 文档与代码漂移 | 公共 API、模板、测试和 README 同 PR 更新 |

## 22. 最终完成标准

只有同时满足以下条件，插件系统重构才可标记完成：

1. 所有现有插件迁移且行为差分测试一致；
2. Runtime 统一管理发现、启停、禁用、升级、回滚和关闭；
3. 普通插件不再接收 Bot，也无法获得未声明能力；
4. 业务消费者、Observer、广播处理和 ACTION middleware 契约分离；
5. 多文件插件能够完整验证、安装、激活和回滚；
6. 100 次热重载无订阅、Task、线程、文件句柄或存储连接泄漏；
7. 全量 L0–L5、Docker、安全和性能门禁通过；
8. 真实 QQ L6 仍保持显式授权，不以模拟结果代替；
9. 旧 `_SubscribeAdapter`、函数魔法属性和完整 Bot 注入无调用者；
10. README 展示的插件教程与已发布 SDK 完全一致；
11. `DEVELOPMENT.md` 和本文状态更新为完成记录，后续未完成项另立计划。
