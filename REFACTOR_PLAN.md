# QQBot Agent 能力平台重构方案书

> 文档状态：设计基线  
> 适用项目：qqbot  
> 编制日期：2026-07-21  
> 目标版本：2.0  
> 本文用于指导后续架构重构、开发拆分、测试建设和阶段验收。

## 1. 项目背景

当前项目已经具备自研消息总线、插件系统、NapCat 接入、LLM 工具调用、会话持久化、记忆、Skill 和 Docker 沙箱等基础能力。现阶段 Agent 主要以“收到 QQ 消息后，在有限轮数内调用若干工具并回复”的方式运行，适合即时聊天和短工具链，但尚不能稳定承担以下类型的长期任务：

- 从本地消息库或互联网检索资料，并保留可追溯的来源。
- 接收、理解、创建、转换和发送 QQ 文件、图片、语音等资源。
- 创建一次性或周期性定时任务，并在进程重启后继续执行。
- 在多轮、多步骤、长耗时任务中暂停、恢复、重试和等待外部条件。
- 以受控方式配置自己的设置、Prompt、Skill、工具和插件。
- 将实时消息、离线期间消息、文件和工具结果沉淀为统一、可搜索的数据。
- 通过完整的端到端测试证明一次重构没有破坏真实 QQ 行为。

本次重构的核心不是简单增加更多 LLM 工具，而是将项目升级为一个以 QQ 为主要交互渠道、具备长期状态和可恢复执行能力的单一 Agent 个体。

## 2. 已确认的设计决策

以下决策是本方案的固定前提，后续实现不应随意偏离。

### 2.1 单一 Bot 个体

项目只服务一个 Bot 身份，不设计多 Bot、多租户或不同 Bot 实例之间的资源隔离。

整个 Bot 共享：

- 一个身份与人格。
- 一个全局配置中心。
- 一个全局 Prompt 注册中心。
- 一个消息数据库。
- 一个 Artifact Store。
- 一个全局工作空间。
- 一个长期记忆系统。
- 一个 Tool Registry。
- 一个 Scheduler。

仍然保留触发者权限和 QQ 会话范围检查，但这是安全授权问题，不是多租户模型。

### 2.2 原始 CQ 码和原始事件是事实源

不将 CQ 码强制转换为封闭、枚举式内部模型后丢弃原始信息。

每条消息至少保留：

- NapCat 完整原始事件 JSON。
- `raw_message` 原始 CQ 字符串。
- NapCat 原始消息段数组。

解析、纯文本提取、资源识别和全文搜索均属于可重新生成的派生数据。未知 CQ 类型、未知字段和新版本字段必须原样保留。

### 2.3 数据库优先，NapCat 兜底

所有实时消息首先持久化，再进入插件和 Agent 分发。所有历史查询首先查询本地数据库；只有本地数据覆盖不足时才调用 NapCat 历史或资源接口，并将查询结果写回数据库后再返回给调用者。

### 2.4 文件由数据库统一管理

消息、文件元数据、来源关系、下载状态、Hash、MIME 和生命周期全部存储在数据库中。大文件实体使用内容寻址文件存储，数据库保存唯一索引；不将所有大文件直接保存为 SQLite BLOB。

### 2.5 单一全局工作空间

不为每个用户或群创建独立工作区。Bot 使用一个全局工作空间，通过数据库中的任务、来源、创建者和状态字段管理文件归属和生命周期。

### 2.6 统一配置和 Prompt 中心

所有模块通过统一 `ConfigCenter` 读取有效配置。Prompt 不再作为单个超长字符串散落在 TOML 或代码中，而是拆分、组合、版本化、校验和回归测试。

### 2.7 全量端到端测试是交付条件

重构不能只依赖单元测试。必须建设从 NapCat 输入、数据库持久化、Agent 决策、工具执行、ACTION 出站直到 QQ 回复的完整测试系统，并包含真实 NapCat 验收层。

## 3. 重构目标与非目标

### 3.1 目标

重构完成后，Bot 应具备以下能力：

1. 无损保存并理解所有 QQ 消息，包括未知 CQ 类型。
2. 实时归档消息和资源，并能回补离线期间历史消息。
3. 从本地数据库检索消息、文件、任务、记忆和工具执行记录。
4. 通过受控工具进行网页搜索、网页读取和文件下载。
5. 创建、读取、修改、转换和发送文件。
6. 通过 QQ 消息接收文件，并在需要时从 NapCat 获取资源实体。
7. 创建一次性、周期性和事件驱动任务。
8. 在进程重启、工具超时、网络断开后恢复任务。
9. 通过提案、验证、审批、原子应用和回滚修改配置或插件。
10. 对所有外部副作用提供权限、限流、幂等和审计。
11. 能够准确解释某次行为使用了哪些消息、文件、配置、Prompt 和工具。
12. 通过完整测试矩阵验证功能、恢复能力、安全性和兼容性。

### 3.2 非目标

本次重构明确不包含：

- 多 Bot 管理平台。
- 为不同用户、群聊建立独立租户。
- 为每个用户或群建立物理隔离工作空间。
- 替换现有自研 MessageBus、PluginManager 或 NapCat 技术路线。
- 引入 NoneBot、AstrBot、Koishi 等外部 Bot 框架。
- 默认允许 Agent 修改安全策略、密钥或生产源码。
- 默认使用 UI 自动化模拟点击 QQ 客户端。
- 一次性重写整个项目并中断现有插件运行。

## 4. 总体设计原则

### 4.1 原始事实不可丢失

原始 NapCat 事件、CQ 字符串和消息段数组是不可变事实。任何规范化、搜索文本、摘要和资源映射都可以重新生成。

### 4.2 先持久化，后执行

外部输入先写入 Event Journal 和消息数据库，事务成功后再发布给插件和 Agent。工具调用先生成持久化 Invocation，再进行实际执行。

### 4.3 数据库是系统记录，NapCat 是外部事实来源

Agent 不直接依赖 NapCat 返回格式。NapCat 查询结果必须经过统一摄取并写入数据库，再由 Repository 返回一致的数据模型。

### 4.4 LLM 负责决策，系统负责授权和执行

模型可以提出计划和工具调用，但不能自行决定权限、绕过审批、扩大作用范围或把外部内容提升为系统指令。

### 4.5 所有副作用必须幂等

发送消息、上传文件、创建定时任务、修改配置和安装插件必须有幂等键或等价去重机制。

### 4.6 长任务必须可恢复

任务状态、步骤、工具调用、等待条件、配置快照和结果必须持久化。任何只存在于 Python 局部变量中的关键任务状态都视为不可靠。

### 4.7 配置和 Prompt 必须可追溯

每次 Agent Run 保存有效配置快照、Prompt 版本和内容 Hash，保证可以解释、复现和回归测试。

### 4.8 渐进迁移

通过兼容层并行运行旧 Event、旧 ToolDef 和新模型，逐步替换，不采用一次性大爆炸式重写。

## 5. 目标总体架构

```text
NapCatQQ
  │
  ├─ WebSocket 实时事件
  ├─ 历史消息查询
  ├─ 文件/图片/语音查询
  └─ QQ ACTION
  │
  ▼
QQ Channel Adapter
  ├─ RawEventAdapter
  ├─ CQ/Segment Lossless View
  ├─ HistoryGateway
  └─ FileGateway
  │
  ▼
Ingestion Pipeline
  ├─ EventJournal
  ├─ MessageProjector
  ├─ ArtifactDiscoverer
  └─ SyncStateUpdater
  │
  ▼
Typed MessageBus
  ├─ EXTERNAL
  ├─ ACTION
  ├─ INTERNAL
  └─ LIFECYCLE
  │
  ▼
Agent Runtime
  ├─ RequestClassifier
  ├─ ContextAssembler
  ├─ Planner / ReAct Loop
  ├─ Durable RunController
  ├─ ToolRouter
  └─ ResponseComposer
  │
  ▼
Capability Platform
  ├─ ToolRegistry
  ├─ PolicyEngine
  ├─ ApprovalService
  ├─ ToolExecutor
  ├─ ArtifactStore
  ├─ GlobalWorkspace
  ├─ Web/Search
  ├─ Scheduler
  └─ Plugin/Settings Control Plane
  │
  ▼
ActionQueue → ApiClient → NapCatQQ
```

横向基础设施：

- `ConfigCenter`：统一配置、动态设置、Schema、版本和快照。
- `PromptRegistry`：Prompt 模块、Profile、版本、渲染和回归测试。
- `StateStore`：任务、步骤、工具调用、审批、配置版本。
- `Observability`：Trace、结构化日志、指标、审计和诊断包。
- `Test Harness`：FakeNapCat、FakeLLM、场景 DSL、故障注入。

## 6. 推荐代码结构

遵守“不新增顶层目录”的项目约束，在现有 `src/`、`plugins/`、`config/` 和 `tests/` 内扩展。

```text
src/
  core/
    runtime/
      orchestrator.py
      request_classifier.py
      context_assembler.py
      planner.py
      run_controller.py
      state_machine.py
      recovery.py
      response_composer.py
    ingestion/
      service.py
      event_journal.py
      message_projector.py
      artifact_discoverer.py
      deduplicator.py
    storage/
      database.py
      unit_of_work.py
      migrations.py
      repositories/
        messages.py
        artifacts.py
        tasks.py
        settings.py
        prompts.py
        audit.py
    tools/
      manifest.py
      registry.py
      router.py
      executor.py
      invocation.py
      validation.py
      result.py
    policy/
      engine.py
      capabilities.py
      approvals.py
      quotas.py
      scopes.py
    artifacts/
      models.py
      store.py
      materializer.py
      scanner.py
      extractor.py
      retention.py
    workspace/
      manager.py
      paths.py
      executor.py
    scheduler/
      service.py
      triggers.py
      worker.py
      misfire.py
    config_center/
      service.py
      schema.py
      sources.py
      snapshots.py
      watcher.py
    prompts/
      registry.py
      renderer.py
      profiles.py
      validator.py
    control_plane/
      settings_service.py
      plugin_service.py
      proposal.py
      rollback.py
    memory/
      manager.py
      working.py
      episodic.py
      semantic.py
      procedural.py
    observability/
      tracing.py
      metrics.py
      audit.py
      diagnostics.py
  adapters/
    qq/
      event_adapter.py
      cq_view.py
      history_gateway.py
      file_gateway.py
      action_gateway.py
      capability_probe.py
  capabilities/
    qq/
    local_search/
    web/
    filesystem/
    scheduler/
    settings/
    plugins/

plugins/
  llm_gate.py
  llm_sender.py
  action_queue.py
  ...

config/
  bot.toml
  runtime.toml
  tools.toml
  scheduler.toml
  storage.toml
  permissions.toml
  prompts/
    manifest.toml
    identity.md
    behavior.md
    tool_policy.md
    memory_policy.md
    qq_context.md
    task_planner.md
    response_style.md
    summarizer.md
    fact_extractor.md

tests/
  unit/
  integration/
  contract/
  e2e/
  live/
  security/
  chaos/
  fixtures/
  golden/
```

## 7. QQ Channel Adapter 详细设计

### 7.1 职责

QQ Adapter 是 NapCat 与核心领域模型之间唯一允许了解 NapCat 具体字段和 Action 名称的模块。

它负责：

- 接收和序列化原始事件。
- 保留 CQ 原文和消息段数组。
- 提供非破坏性的 CQ 引用视图。
- 封装历史消息查询和分页。
- 封装消息、图片、语音和文件获取。
- 封装 QQ 出站 Action。
- 启动时探测当前 NapCat 支持的扩展能力。

它不负责：

- 决定是否允许发送或删除内容。
- 决定 Agent 是否应使用某个工具。
- 将原始 CQ 替换为封闭内部类型。
- 直接把 NapCat 查询结果返回给 LLM。

### 7.2 无损消息模型

```python
@dataclass(frozen=True)
class RawQQMessage:
    message_id: int
    conversation_type: str
    conversation_id: int
    sender_id: int
    sent_at: int
    raw_message: str
    message_array: list[dict]
    raw_event: dict
    ingestion_source: str
```

`message_array` 如果 NapCat 没有直接提供，则允许为空；不能通过有损 CQ 解析伪造完整数组。

### 7.3 CQ View

CQ View 只用于辅助定位和索引：

```python
@dataclass(frozen=True)
class CQReference:
    type: str
    raw: str
    start: int
    end: int
    attributes: dict[str, str]
    parse_error: str | None = None
```

要求：

- 扫描失败不阻塞消息持久化。
- 未知类型照常输出。
- 未知属性原样保留。
- `raw` 必须与输入字符串完全一致。
- Parser 只建立派生索引，不承担安全边界。
- LLM 默认接收原始 CQ 码和必要的数据库上下文。

### 7.4 资源解析接口

Agent 不直接依赖 CQ 中的临时路径或 URL，而调用：

```python
resolve_message_resource(
    message_id: int,
    segment_index: int | None = None,
    cq_type: str | None = None,
) -> ArtifactRef
```

内部顺序：

1. 查询数据库现有 Artifact。
2. 查询数据库保存的消息段和 CQ 属性。
3. 如信息不足，调用 NapCat `get_msg`。
4. 根据类型调用文件、图片、语音或群文件接口。
5. 创建或更新 Artifact。
6. 必要时启动下载 Materializer。
7. 返回稳定 `artifact_id`，不向上泄漏短期 URL 依赖。

## 8. 实时摄取与消息数据库

### 8.1 摄取主链路

```text
收到 raw_event
  → 生成 event_id 和 trace_id
  → BEGIN TRANSACTION
  → 写 event_journal
  → 投影 messages
  → 投影 message_segments
  → 发现 artifact 引用
  → 更新 conversation_sync_state
  → COMMIT
  → 发布 message.persisted
  → FilterManager / Plugin / Agent
```

若事务失败：

- 不向 Agent 发布该消息。
- 记录摄取失败日志和指标。
- WebSocket 消费层将原始事件放入有限重试队列。
- 超过重试次数后写入 Dead Letter 表或文件，不能静默丢弃。

### 8.2 Event Journal

```sql
CREATE TABLE event_journal (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    received_at INTEGER NOT NULL,
    occurred_at INTEGER,
    raw_json TEXT NOT NULL,
    projected INTEGER NOT NULL DEFAULT 0,
    projection_error TEXT,
    trace_id TEXT NOT NULL
);
```

用途：

- 恢复事务提交后、事件发布前的崩溃。
- 重新生成消息段或搜索索引。
- 回放真实事件进行测试。
- 兼容未来 NapCat 字段变化。
- 审计 Bot 当时实际收到的内容。

### 8.3 Messages

```sql
CREATE TABLE messages (
    message_id INTEGER PRIMARY KEY,
    conversation_type TEXT NOT NULL,
    conversation_id INTEGER NOT NULL,
    group_id INTEGER,
    peer_id INTEGER,
    sender_id INTEGER NOT NULL,
    sender_name TEXT,
    sent_at INTEGER NOT NULL,
    raw_message TEXT NOT NULL,
    message_array_json TEXT,
    raw_event_json TEXT NOT NULL,
    search_text TEXT NOT NULL DEFAULT '',
    reply_to_message_id INTEGER,
    is_from_bot INTEGER NOT NULL DEFAULT 0,
    is_recalled INTEGER NOT NULL DEFAULT 0,
    ingestion_source TEXT NOT NULL,
    first_seen_at INTEGER NOT NULL,
    last_synced_at INTEGER NOT NULL
);
```

约束：

- `message_id` 是实时事件与历史回补的主要去重键。
- 同一消息从多来源出现时更新缺失字段，不覆盖更完整的原始事件。
- `raw_message` 不进行不可逆清洗。
- Bot 出站消息也必须进入同一表。
- 撤回不删除原记录，而设置 `is_recalled` 并追加撤回事件。

### 8.4 Message Segments

```sql
CREATE TABLE message_segments (
    message_id INTEGER NOT NULL,
    segment_index INTEGER NOT NULL,
    segment_type TEXT NOT NULL,
    raw_segment_json TEXT NOT NULL,
    raw_cq TEXT,
    artifact_id TEXT,
    PRIMARY KEY (message_id, segment_index),
    FOREIGN KEY (message_id) REFERENCES messages(message_id)
);
```

此表用于高效定位资源、回复、@、合并转发等内容，但永远不替代原始消息。

### 8.5 全文搜索

使用 SQLite FTS5 保存派生搜索文本：

```text
原始可见文本
+ 文件名
+ OCR/语音识别文本
+ 发送者显示名
+ 可搜索的结构化字段
```

搜索结果必须同时返回：

- 命中的消息 ID。
- 原始 CQ。
- 命中片段。
- 来源会话和时间。
- 数据覆盖状态。
- 是否来自实时、历史回补或 API 兜底。

## 9. Artifact Store 详细设计

### 9.1 统一资源定义

Artifact 表示 Bot 可以持久引用的任何资源：

- QQ 文件。
- 图片。
- 语音。
- 视频。
- 合并转发快照。
- 网页正文。
- 搜索结果集合。
- 下载文件。
- Agent 创建的文件。
- 工具输出的大型结构化数据。

### 9.2 数据模型

```sql
CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    file_name TEXT,
    mime_type TEXT,
    size INTEGER,
    sha256 TEXT,
    local_path TEXT,
    status TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_message_id INTEGER,
    source_segment_index INTEGER,
    napcat_file_id TEXT,
    remote_url TEXT,
    created_at INTEGER NOT NULL,
    discovered_at INTEGER NOT NULL,
    downloaded_at INTEGER,
    last_accessed_at INTEGER,
    expires_at INTEGER,
    error TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

状态机：

```text
DISCOVERED
  → MATERIALIZING
  → AVAILABLE
  → EXPIRED
  → DELETED

MATERIALIZING → FAILED → MATERIALIZING
```

### 9.3 内容寻址存储

文件实体路径：

```text
data/artifacts/<sha256[0:2]>/<sha256>
```

同一文件被不同消息重复发送时，只保存一个实体，通过数据库建立多个来源关系。

补充关系表：

```sql
CREATE TABLE artifact_sources (
    artifact_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (artifact_id, source_type, source_id)
);
```

### 9.4 实时归档策略

默认策略建议：

- 图片：发现后自动物化。
- 语音：发现后自动物化，可选异步转写。
- 小文件：发现后自动物化。
- 大文件：先记录引用，按需或后台低优先级物化。
- 视频：按大小、磁盘配额和保留策略决定。
- 在线文件：优先物化，因为临时链接可能失效。

文件阈值全部由 ConfigCenter 管理，不在代码中硬编码。

### 9.5 文件安全

物化后执行：

- 文件大小校验。
- SHA-256。
- 魔数/MIME 识别。
- 文件名规范化，仅用于展示，不作为真实路径。
- 压缩包条目数量、解压大小和嵌套深度检查。
- 符号链接和路径穿越检查。
- 可选病毒扫描。
- 来源和可信度标记。

Artifact 内容是数据，不是系统指令。文档或网页中出现的“忽略规则”“调用工具”等文本不能改变 Agent 权限。

## 10. 历史消息回补和离线同步

### 10.1 HistorySyncService

封装以下 NapCat 能力：

- 获取最近会话。
- 获取群历史消息。
- 获取好友历史消息。
- 按 message_id 获取消息。
- 获取群文件列表和文件 URL。
- 获取私聊文件 URL。
- 获取图片、语音和合并转发内容。

具体 NapCat Action 参数只存在于 `adapters/qq/history_gateway.py` 和 `file_gateway.py`。

### 10.2 数据库优先查询算法

```text
接收查询条件
  → MessageRepository 查询本地数据
  → CoverageService 判断覆盖范围
  → 覆盖完整：直接返回
  → 覆盖不足：确定最小回补窗口
  → HistoryGateway 查询 NapCat
  → IngestionService 以 backfill 来源写库
  → 再次从 MessageRepository 查询
  → 返回统一格式和 coverage 信息
```

### 10.3 同步状态

```sql
CREATE TABLE conversation_sync_state (
    conversation_type TEXT NOT NULL,
    conversation_id INTEGER NOT NULL,
    earliest_message_id INTEGER,
    latest_message_id INTEGER,
    earliest_time INTEGER,
    latest_time INTEGER,
    last_live_event_at INTEGER,
    last_backfill_at INTEGER,
    backfill_complete INTEGER NOT NULL DEFAULT 0,
    cursor_json TEXT,
    last_error TEXT,
    PRIMARY KEY (conversation_type, conversation_id)
);
```

分页锚点和游标视 NapCat 实际响应保存为 JSON，不将某一种 API 的游标形式泄漏到领域层。

### 10.4 启动同步

启动时：

1. 恢复未投影 Event Journal。
2. 获取最近会话。
3. 将未知会话加入同步队列。
4. 对活跃会话拉取最新一页历史。
5. 遇到本地已有 message_id 后停止向后拉取。
6. 根据配置决定是否继续补更早历史。
7. 更新覆盖范围和同步诊断信息。
8. 异步物化新发现的资源。

### 10.5 重连同步

记录断线开始时间和最后实时消息。重连后优先回补断线窗口，实时新消息与回补消息统一使用 message_id 去重。

### 10.6 同步缺口

NapCat 和 QQ 不能保证无限历史或永久文件 URL，因此系统必须表达“不完整”，不能伪装为全量数据。

```sql
CREATE TABLE sync_gaps (
    id INTEGER PRIMARY KEY,
    conversation_type TEXT NOT NULL,
    conversation_id INTEGER NOT NULL,
    gap_start INTEGER,
    gap_end INTEGER,
    reason TEXT NOT NULL,
    detected_at INTEGER NOT NULL,
    resolved_at INTEGER
);
```

查询返回：

```json
{
  "coverage": "complete | partial | unknown",
  "gaps": [],
  "messages": []
}
```

## 11. 全局工作空间

### 11.1 目录结构

```text
data/workspace/
  inbox/
  working/
  output/
  archive/
  tmp/
```

- `inbox`：从 QQ、网页或其他工具接收的资源入口。
- `working`：Agent 当前处理的文件。
- `output`：准备发送或交付的文件。
- `archive`：明确需要长期保留的文件。
- `tmp`：可自动清理的中间数据。

### 11.2 路径规则

- Agent 工具优先使用 `artifact_id`，不直接操作任意宿主路径。
- 所有相对路径由 WorkspaceManager 解析。
- 禁止 `..`、绝对路径、设备文件、符号链接逃逸。
- 文件创建先写临时文件，完成后原子移动。
- 输出文件进入 ArtifactStore 后才能发送至 QQ。
- 删除操作默认进入可恢复状态或回收区。

### 11.3 工作空间索引

```sql
CREATE TABLE workspace_entries (
    entry_id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    artifact_id TEXT,
    state TEXT NOT NULL,
    created_by_task_id TEXT,
    created_at INTEGER NOT NULL,
    modified_at INTEGER NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

虽然物理空间全局共享，任务仍需记录读写集合，便于冲突检测、审计和恢复。

## 12. Durable Agent Runtime

### 12.1 任务模型

```sql
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    trigger_event_id TEXT,
    conversation_type TEXT,
    conversation_id INTEGER,
    requested_by INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    completed_at INTEGER,
    error TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

状态：

```text
CREATED
→ PLANNING
→ RUNNING
→ WAITING_TOOL
→ WAITING_ARTIFACT
→ WAITING_APPROVAL
→ WAITING_USER
→ SCHEDULED
→ SUCCEEDED / FAILED / CANCELLED
```

### 12.2 Run 和 Step

一个 Task 可以有多次 Run，例如重试、定时触发或人工重新执行。

```sql
CREATE TABLE task_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL,
    config_snapshot_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    started_at INTEGER,
    ended_at INTEGER,
    resume_token TEXT,
    error TEXT
);
```

```sql
CREATE TABLE run_steps (
    step_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json TEXT,
    output_json TEXT,
    started_at INTEGER,
    ended_at INTEGER,
    error TEXT
);
```

### 12.3 执行模式

- `chat`：无工具或极短回复。
- `react`：短工具链，兼容当前 AgentLoop。
- `plan_execute`：显式步骤和依赖关系。
- `background`：脱离当前请求异步执行。
- `scheduled`：由 Scheduler 创建 Run。
- `event_driven`：由文件可用、成员变化等事件触发。

### 12.4 恢复

启动 RecoveryController：

1. 查找非终态 Run。
2. 检查正在执行的 Invocation 是否已有持久化结果。
3. 对幂等工具安全重试。
4. 对结果未知的副作用工具进入人工确认或查询验证。
5. 恢复等待中的 Artifact、审批、用户输入和定时触发。
6. 发布恢复事件并继续 Run。

当前通过 `response_buffer` 回填工具结果的方式必须被持久化 Invocation 取代。

## 13. Tool Platform

### 13.1 Tool Manifest

```python
@dataclass(frozen=True)
class ToolManifest:
    name: str
    version: str
    description: str
    input_schema: dict
    output_schema: dict
    effects: set[str]
    risk: str
    scopes: set[str]
    timeout_seconds: int
    retry_policy: RetryPolicy
    idempotency: str
    approval: str
    provider: str
```

工具命名空间：

- `qq.*`
- `messages.*`
- `artifacts.*`
- `workspace.*`
- `search.*`
- `web.*`
- `schedule.*`
- `memory.*`
- `settings.*`
- `plugins.*`
- `system.*`

### 13.2 Tool Invocation

```sql
CREATE TABLE tool_invocations (
    invocation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_id TEXT,
    tool_name TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    status TEXT NOT NULL,
    idempotency_key TEXT,
    result_json TEXT,
    error TEXT,
    started_at INTEGER,
    ended_at INTEGER,
    trace_id TEXT NOT NULL
);
```

### 13.3 执行管线

```text
解析 LLM tool call
→ ToolRegistry 查找版本
→ JSON Schema 完整验证
→ Artifact/资源引用解析
→ PolicyEngine 权限判断
→ Quota 限额检查
→ 必要时创建 Approval
→ 持久化 Invocation
→ 执行并处理取消/超时
→ 输出 Schema 验证
→ 结果脱敏和大小限制
→ 持久化结果
→ 审计
→ 发布 tool.completed
```

### 13.4 ToolContext

工具不再接收完整 `bot`，使用最小上下文：

```python
@dataclass
class ToolContext:
    task_id: str
    run_id: str
    invocation_id: str
    requested_by: int | None
    conversation_type: str | None
    conversation_id: int | None
    granted_capabilities: set[str]
    config_snapshot_id: str
    trace_id: str
```

具体服务通过依赖注入提供，避免工具任意访问 Bot 所有内部状态。

## 14. 权限、审批、配额和安全

### 14.1 风险级别

| 级别 | 示例 | 默认行为 |
|---|---|---|
| none | 时间、公开状态 | 自动允许 |
| low | 本地搜索、读取已归档文件 | 自动允许并限额 |
| medium | 网页抓取、创建普通文件、发送普通消息 | 按范围允许 |
| high | 发文件、创建定时任务、修改非敏感设置 | 条件审批 |
| critical | 删除资源、安装插件、执行代码、改变群管理状态 | 强制审批 |
| forbidden | 读取密钥、修改安全策略、绕过审计 | 永不允许 |

### 14.2 权限输入

权限由以下因素共同决定：

```text
触发者
× 当前 QQ 会话
× 工具 effects
× 目标资源
× 风险级别
× 已有授权
× 配额
× 时间
× 配置策略
```

Bot 是单一身份，但不能让任意 QQ 用户借用 Bot 的全部能力。

### 14.3 Approval

```sql
CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    invocation_id TEXT,
    requested_action TEXT NOT NULL,
    risk TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_at INTEGER NOT NULL,
    decided_at INTEGER,
    decided_by INTEGER,
    scope TEXT,
    expires_at INTEGER
);
```

审批选择：

- 允许一次。
- 本任务允许。
- 本会话在限定时间内允许。
- 拒绝。

安全策略、密钥访问和绕过审计不能通过普通审批开放。

### 14.4 Prompt Injection 防护

- QQ 消息、网页、文件、OCR 和工具结果全部标记为不可信数据。
- Prompt 中明确区分系统指令与外部内容。
- 工具权限只由 PolicyEngine 决定。
- 外部文本不能创建授权。
- 工具输出中出现伪造 `tool_call` 不会被当作真正调用。
- 高风险动作展示目标、影响和来源后再审批。

## 15. 本地与网络搜索

### 15.1 本地搜索

统一搜索接口：

- `search.messages`
- `search.artifacts`
- `search.workspace`
- `search.tasks`
- `search.memory`

返回结构必须包含来源 ID、时间、命中片段、覆盖范围和可追溯引用。

### 15.2 网络能力拆分

- `web.search`：返回搜索结果列表。
- `web.fetch`：获取指定页面。
- `web.extract`：抽取正文和元数据。
- `web.download`：下载并形成 Artifact。
- `web.browser`：仅用于确实需要交互的页面，后期实现。

Search 与 Fetch 分离，防止搜索结果被自动访问。

### 15.3 网络安全

- 默认拒绝 localhost、环回、私网、链路本地和云元数据地址。
- 限制协议为 HTTP/HTTPS。
- 限制重定向次数、响应大小、下载时间和 MIME。
- DNS 解析后再次检查目标 IP。
- 保存最终 URL、抓取时间和内容 Hash。
- 网页正文作为 Artifact 存储，摘要进入 LLM 上下文。

## 16. QQ 文件收发能力

核心工具：

- `qq.resolve_message_resource`
- `qq.get_message`
- `qq.get_group_file`
- `qq.list_group_files`
- `qq.send_artifact`
- `qq.upload_group_file`
- `qq.send_image`
- `qq.send_voice`
- `qq.get_forward_message`

发送流程：

```text
Agent 选择 artifact_id
→ ArtifactStore 确认 AVAILABLE
→ PolicyEngine 检查目标和风险
→ ActionQueue 限流/去重
→ QQ FileGateway 调用 NapCat
→ 保存 NapCat 响应
→ 出站消息写 messages
→ Artifact 建立 sent_to 来源关系
```

文件不能通过模型提供的任意宿主路径直接发送。

## 17. Scheduler

### 17.1 数据模型

```sql
CREATE TABLE schedules (
    schedule_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    trigger_type TEXT NOT NULL,
    trigger_spec_json TEXT NOT NULL,
    timezone TEXT NOT NULL,
    task_template_json TEXT NOT NULL,
    target_conversation_type TEXT,
    target_conversation_id INTEGER,
    created_by INTEGER,
    next_run_at INTEGER,
    last_run_at INTEGER,
    misfire_policy TEXT NOT NULL,
    max_concurrency INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
```

### 17.2 触发类型

- 一次性时间。
- 固定间隔。
- Cron。
- 事件驱动。

默认时区为 `Asia/Shanghai`，但必须显式存储。

### 17.3 Misfire

- `skip`：错过则跳过。
- `run_once`：恢复后补执行一次。
- `catch_up`：补执行全部，默认禁止用于消息发送任务。

每次触发创建新的 TaskRun，不能直接在 Scheduler 内执行工具。

## 18. ConfigCenter

### 18.1 配置来源

```text
代码默认值
< config/*.toml
< runtime_settings 数据库
< 当前任务临时覆盖
```

`.env` 只保存密钥，不参与普通动态配置覆盖。

### 18.2 配置文件

```text
config/bot.toml          Bot、NapCat、日志、基础开关
config/runtime.toml      Agent Runtime、并发、恢复、上下文
config/tools.toml        工具启用、超时、配额和网络策略
config/scheduler.toml    调度精度、misfire、并发
config/storage.toml      数据库、Artifact、保留和磁盘阈值
config/permissions.toml  权限和审批策略
```

### 18.3 Config Schema

每个配置项声明：

- 类型。
- 默认值。
- 是否必填。
- 允许范围。
- 是否敏感。
- 是否支持热更新。
- 修改风险。
- 负责组件。

配置加载失败必须阻止新版本生效，继续使用上一个有效版本。

### 18.4 版本和快照

```sql
CREATE TABLE settings_versions (
    version INTEGER PRIMARY KEY,
    content_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    created_by INTEGER,
    reason TEXT,
    status TEXT NOT NULL
);
```

```sql
CREATE TABLE config_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    settings_version INTEGER NOT NULL,
    prompt_version TEXT NOT NULL,
    effective_config_json TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
```

### 18.5 修改流程

```text
生成 SettingsProposal
→ Schema 验证
→ 计算 diff
→ 风险分类
→ 必要时审批
→ 写入候选版本
→ 组件 dry-run
→ 原子生效
→ 健康检查
→ 失败自动回滚
```

## 19. PromptRegistry

### 19.1 Prompt 模块

建议拆分：

- `identity.md`：身份、人格、长期角色。
- `behavior.md`：一般行为准则。
- `tool_policy.md`：工具使用与证据要求。
- `memory_policy.md`：记忆写入和读取规则。
- `qq_context.md`：CQ、消息 ID、QQ 场景说明。
- `task_planner.md`：计划与任务完成标准。
- `response_style.md`：回复风格。
- `summarizer.md`：摘要专用 Prompt。
- `fact_extractor.md`：事实提取专用 Prompt。

### 19.2 Profile

```toml
version = "2026.07.1"

[profiles.default]
parts = [
  "identity",
  "behavior",
  "tool_policy",
  "memory_policy",
  "qq_context",
  "response_style",
]

[profiles.planner]
extends = "default"
append = ["task_planner"]

[profiles.summarizer]
parts = ["summarizer"]
tools_enabled = false
```

### 19.3 渲染

PromptRenderer 接收：

- Profile。
- 当前配置快照。
- 当前时间和时区。
- QQ 会话上下文。
- Tool 摘要。
- 检索记忆。
- 当前任务目标。

渲染结果保存 Hash 和版本，不必为每次请求重复保存完整文本，但必须可通过版本重建；关键运行可选择保存完整渲染结果。

### 19.4 Prompt 变更测试

发布新 Prompt 前执行：

- 变量完整性检查。
- 不允许的工具名检查。
- 长度预算检查。
- CQ 理解黄金样例。
- 工具选择黄金样例。
- 安全拒绝黄金样例。
- 历史查询和文件处理回归。
- 与上一版本的行为差异报告。

## 20. Skill、工具和插件自配置

### 20.1 三者边界

- Skill：流程知识和 Prompt 模块，不执行任意代码。
- Tool Provider：实现具体能力并注册 ToolManifest。
- Plugin：订阅事件、提供工具、配置和迁移的部署包。

### 20.2 Control Plane

Agent 可调用：

- `settings.get`
- `settings.propose`
- `prompts.get`
- `prompts.propose`
- `plugins.list`
- `plugins.propose_install`
- `plugins.validate`
- `plugins.propose_enable`
- `plugins.propose_disable`
- `plugins.rollback`

Agent 只能提出变更，不能批准自己的高风险变更。

### 20.3 插件包

```text
plugin-name/
  plugin.toml
  README.md
  handlers/
  tools/
  migrations/
  tests/
  permissions.toml
```

安装流程：

```text
下载或生成候选包
→ 静态检查
→ 隔离环境安装依赖
→ 运行插件测试
→ 展示权限差异
→ 管理员审批
→ 原子切换版本
→ 健康检查
→ 失败自动回滚
```

禁止插件直接读取 `.env`、QQ 登录目录、Docker Socket 或绕过 ToolExecutor 调用外部能力。

## 21. Memory System

### 21.1 分层

- Working Memory：当前 Run 上下文。
- Conversation Memory：当前 QQ 会话近期消息，由消息库提供。
- Episodic Memory：完成任务、关键结果和失败经验。
- Semantic Memory：经确认的长期事实和偏好。
- Procedural Memory：成功的工具组合和 Skill 使用经验。

### 21.2 记忆记录

```sql
CREATE TABLE memories (
    memory_id TEXT PRIMARY KEY,
    memory_type TEXT NOT NULL,
    content TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    confidence REAL NOT NULL,
    sensitivity TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_verified_at INTEGER,
    expires_at INTEGER,
    superseded_by TEXT
);
```

群聊随口信息不能自动升级为永久事实。长期记忆必须有来源、置信度和过期策略。

## 22. 可观测性与审计

### 22.1 Trace

统一 trace 链：

```text
raw_event
→ event_journal
→ message_id
→ task_id
→ run_id
→ step_id
→ invocation_id
→ action_id
→ NapCat response
```

所有结构化日志包含可用的 ID。

### 22.2 关键指标

- 实时消息摄取延迟。
- Event Journal 未投影数量。
- 历史同步延迟和 Gap 数量。
- Artifact 物化成功率和积压。
- Agent Run 成功率、平均轮数、恢复次数。
- Tool 调用成功率、超时率、审批率。
- Scheduler misfire 数量。
- 数据库大小、Artifact 磁盘占用。
- NapCat 重连次数。

### 22.3 审计

审计记录至少包括：

- 谁触发。
- 在哪个 QQ 会话触发。
- 使用哪个工具和版本。
- 参数脱敏摘要。
- 权限判断结果。
- 是否审批及审批者。
- 外部副作用结果。
- 配置和 Prompt 版本。
- Trace ID。

## 23. 全量测试体系

### 23.1 测试原则

- 单元测试验证局部逻辑。
- 集成测试验证数据库和组件边界。
- 契约测试验证 NapCat 和 Tool Schema。
- 进程内 E2E 验证完整业务链路。
- Docker E2E 验证真实进程、网络和恢复。
- Live 测试验证真实 NapCat/QQ。
- 安全和 Chaos 测试验证异常条件。

任何仅直接调用插件 handler 的测试不能被标记为完整 E2E。

### 23.2 测试目录

```text
tests/
  unit/
  integration/
  contract/
    napcat/
    tools/
  e2e/
    in_process/
    docker/
    scenarios/
  live/
  security/
  chaos/
  fixtures/
    napcat/
    llm/
    files/
  golden/
    prompts/
    cq/
    plans/
```

### 23.3 L0 单元测试

覆盖：

- CQ 扫描和未知类型保真。
- 原始事件序列化。
- Message Projector。
- Config Schema 和 Prompt 渲染。
- Artifact Hash、MIME、路径安全。
- Policy Matrix。
- Scheduler 时间和时区。
- Tool 输入输出验证。
- 状态机合法迁移。

### 23.4 L1 数据库集成测试

覆盖：

- Event Journal 与消息投影事务。
- live/backfill 重复消息合并。
- FTS 索引同步和重建。
- Artifact 状态迁移和内容去重。
- 数据库 Migration 升级。
- 崩溃后未投影事件恢复。
- 并发读写。
- 配置版本原子切换和回滚。

必须长期验证的不变量：

```text
每条 projected 消息都能追溯到 Event Journal
每个 Segment 都属于存在的 Message
AVAILABLE Artifact 的实体存在且 Hash 正确
同一 message_id 不产生重复消息
每个终态 Invocation 都有结果或错误
每个 Run 都绑定配置快照和 Prompt 版本
```

### 23.5 L2 NapCat 契约测试

Fixture 至少包含：

- 群文本。
- 私聊文本。
- @、回复和表情。
- 图片、语音、视频和文件。
- 混合消息段。
- 合并转发。
- 在线文件。
- 未知 CQ 和未知消息段。
- 群历史页。
- 好友历史页。
- 最近会话。
- 群文件列表和文件 URL。

同一消息的实时事件和历史响应必须投影为同一数据库记录。

### 23.6 L3 进程内 E2E

真实启动：

- Bot。
- MessageBus。
- SQLite。
- ConfigCenter。
- PromptRegistry。
- Agent Runtime。
- ToolExecutor。
- Scheduler。
- ArtifactStore。

替换：

- FakeNapCatServer。
- ScriptedLLMProvider。
- FakeSearchServer。

完整链路：

```text
FakeNapCat WebSocket 事件
→ Event Journal
→ Message/Artifact 投影
→ LLM Gate
→ Agent Runtime
→ Tool Invocation
→ Policy/Approval
→ ACTION MessageBus
→ FakeNapCat API
→ 出站消息持久化
→ 最终断言
```

### 23.7 L4 Docker E2E

测试栈：

```text
qqbot-test
fake-napcat
fake-llm
fake-search
test-runner
```

场景：

- WebSocket 断线重连。
- Bot 强制终止并恢复任务。
- 文件上传下载中断。
- NapCat 超时、重复响应和错误响应。
- SQLite WAL 恢复。
- 定时任务跨重启。
- Config 热更新失败回滚。
- Prompt 版本切换。
- Artifact 磁盘配额。

### 23.8 L5 真实 NapCat 测试

使用专门测试 QQ 和测试群，测试默认不进入普通 CI。

覆盖：

- 真实收发文本。
- 图片、语音和文件。
- 引用和合并转发。
- 群历史回补。
- 好友历史回补。
- Bot 离线后消息回补。
- 文件下载与 SHA-256 校验。
- 定时发送。
- 撤回和链接过期。

所有写操作限制在配置的测试群和测试好友范围。

### 23.9 L6 安全和 Chaos

安全场景：

- CQ 属性逃逸。
- 未知或畸形 CQ。
- 超大消息。
- 文件名路径穿越。
- 符号链接。
- Zip Bomb。
- MIME 伪造。
- SSRF 和 DNS Rebinding。
- 网页/文档 Prompt Injection。
- 普通用户尝试修改 Prompt 或权限。
- 插件尝试读取密钥。

故障场景：

- 数据库提交前崩溃。
- 数据库提交后、事件发布前崩溃。
- 工具成功但结果写库前崩溃。
- QQ Action 成功但响应丢失。
- NapCat 返回重复历史页。
- 历史页乱序。
- Artifact 下载一半断线。
- Scheduler 触发时 Bot 离线。
- Config 生效后健康检查失败。

### 23.10 场景 DSL

使用 YAML 表达 E2E 场景：

```yaml
name: offline_file_backfill

given:
  database: empty
  bot_state: offline

napcat_history:
  - fixture: group_file_message.json

when:
  - start_bot
  - wait_for_history_sync
  - send_qq_message: "处理我离线时发的文件"

llm:
  responses:
    - tool: search.messages
      arguments:
        keyword: "文件"
    - tool: qq.resolve_message_resource
      arguments:
        message_id: 9001
        segment_index: 0
    - text: "已经找到并读取了文件"

then:
  database:
    message_exists: 9001
    artifact_status: AVAILABLE
    ingestion_source: backfill
  qq:
    sent_text_contains: "已经找到"
  invariants:
    - no_duplicate_messages
    - no_unresolved_tool_calls
```

## 24. 数据迁移

### 24.1 当前数据

需要兼容：

- `data/messages.db`。
- `data/llm_state.db`。
- `logs/message-history.log`。
- `logs/llm_sessions/`。
- 现有 `config/bot.toml`。
- 现有 `config/frieren.md`。
- 现有 `config/skills/`。

### 24.2 迁移策略

1. 迁移脚本只追加新表或复制数据，不直接破坏旧表。
2. 为现有 messages 生成最小 raw_event 包装，标记 `ingestion_source=legacy`。
3. 能从 JSONL 找到完整原始事件时补全 raw_event 和 message_array。
4. 旧会话和记忆迁入新表，保留原数据库备份。
5. `system_prompt` 拆分到 Prompt 文件，并保存初始版本。
6. 旧 Skill 转为新 Skill Manifest，保持名称兼容。
7. 迁移后运行数据不变量检查。
8. 新版本稳定前保留只读旧库和回滚路径。

## 25. 分阶段实施计划

### Phase 0：基线与保护

目标：冻结现有行为，建立可重构的安全网。

工作：

- 整理当前全部测试和覆盖率。
- 建立 NapCat 原始事件 Fixture。
- 为现有关键链路增加进程内冒烟测试。
- 记录当前数据库 Schema。
- 建立迁移备份和回滚脚本。

验收：现有功能测试全部通过，关键 NapCat 输入有可回放 Fixture。

### Phase 1：ConfigCenter 与 PromptRegistry

目标：先统一配置和 Prompt，避免后续模块继续增加散乱读取。

工作：

- 建立 Config Schema 和来源优先级。
- 拆分 Prompt 文件和 Profile。
- Bot 组件改为从 ConfigCenter 读取。
- 保存 ConfigSnapshot。
- 建立 Prompt 黄金测试。

验收：现有 Agent 行为保持兼容，每次 Run 可查询配置和 Prompt 版本。

执行记录（2026-07-21）：阶段一首轮实现已完成。已加入 ConfigCenter、脱敏配置快照、模块化 PromptRegistry、Profile 渲染与校验、Agent 接入和 Docker Prompt 挂载；完整测试结果为 595 passed，详细记录见 `PHASE1_IMPLEMENTATION.md`。

### Phase 2：无损 QQ Adapter 与新消息库

目标：原始 CQ、message_array 和 raw_event 完整落库。

工作：

- 引入 Event Journal。
- 建立 Messages、Segments、FTS、SyncState。
- 先持久化后分发。
- 旧 Event 兼容适配。
- Bot 出站消息统一落库。

验收：未知 CQ 不丢失；live/backfill 同一消息正确去重；数据库可重建派生索引。

执行记录（2026-07-21）：阶段二首轮实现已完成。已加入无损 QQ/CQ Adapter、Event Journal、Messages 自动迁移、消息段投影、FTS、同步状态、失败投影启动恢复和统一出站文本持久化；完整测试结果为 606 passed，详细记录见 `PHASE2_IMPLEMENTATION.md`。

### Phase 3：Artifact Store 与 QQ 文件

目标：文件和图片成为一等资源。

工作：

- 建立 Artifact 表和内容寻址存储。
- 实现 ArtifactDiscoverer 和 Materializer。
- 封装 NapCat 文件、图片、语音和群文件接口。
- 实现文件安全检查。
- 实现 `qq.resolve_message_resource` 和 `qq.send_artifact`。

验收：QQ 收文件、归档、处理、再发送全链路通过；重复文件实体去重。

执行记录（2026-07-21）：阶段三首轮实现已完成。已加入 Artifact 元数据表、消息段关联、SHA-256 内容寻址存储、延迟物化、安全 HTTP 下载、NapCat 文件网关、群/私聊文件上传和四个 Agent Artifact 工具；完整测试结果为 614 passed，详细记录见 `PHASE3_IMPLEMENTATION.md`。真实 QQ 账号 Live NapCat 验收留在阶段八执行。

### Phase 4：历史回补

目标：数据库优先，NapCat 自动补齐离线数据。

工作：

- 实现最近会话、群历史、好友历史 Gateway。
- 实现 Coverage 和 Gap。
- 启动同步和重连同步。
- 查询工具触发按需回补。
- 历史资源异步物化。

验收：Bot 离线期间测试消息在重启后进入数据库；查询明确返回覆盖状态。

执行记录（2026-07-21）：阶段四首轮实现已完成。已加入最近会话与群/好友历史 Gateway、数据库优先 HistoryQueryService、分页回补、启动/重连同步、同步状态、游标、Gap 诊断、历史 Artifact 投影和查询覆盖信息；完整测试结果为 627 passed，详细记录见 `PHASE4_IMPLEMENTATION.md`。

### Phase 5：Tool Platform

目标：工具实例化、验证、授权、调用和审计统一。

工作：

- ToolManifest 和实例化 ToolRegistry。
- 删除全局 `_catalog` 和 `_executor`。
- 完整 JSON Schema 输入输出验证。
- ToolContext 最小权限依赖。
- Invocation 持久化和幂等。
- 迁移现有 QQ 管理工具。

验收：现有工具兼容；每次调用可审计；重试不产生重复副作用。

执行记录（2026-07-21）：阶段五首轮实现已完成。已建立 Bot 实例级 ToolCatalog/ToolExecutor、版本化 ToolDef Manifest、输入输出 Schema 验证、scope/approval 权限上下文、结果大小和超时限制、Invocation 全生命周期持久化、敏感参数脱敏及写操作默认幂等；原全局 `_catalog/_executor` 已删除。完整测试结果为 635 passed，详细记录见 `PHASE5_IMPLEMENTATION.md`。

### Phase 6：Durable Runtime 与 Scheduler

目标：长任务、后台任务和定时任务可恢复。

工作：

- Task、Run、Step 状态机。
- 替换 `response_buffer`。
- RecoveryController。
- Scheduler 和 misfire。
- 等待 Artifact、审批和用户输入。

验收：在每个非终态强制结束进程，重启后任务按策略恢复。

执行记录（2026-07-21）：阶段六首轮实现已完成。已建立持久化 Task/Run/Step 状态机、等待与恢复令牌、RecoveryController、Invocation-Step 归属、Bot 生命周期恢复、once/interval/Cron/event Scheduler、skip/run_once/catch_up misfire、并发限制和四个 Agent 调度工具；生产 AgentLoop 已移除对临时 response_buffer 的依赖。完整测试结果为 650 passed，详细记录见 `PHASE6_IMPLEMENTATION.md`。

### Phase 7：本地/网页搜索与 Control Plane

目标：扩展认知能力并支持受控自配置。

工作：

- 本地统一搜索。
- Web Search/Fetch/Download。
- SettingsProposal。
- PromptProposal。
- Plugin 安装验证与回滚。

验收：Agent 可以提出并验证设置或插件变更，但无法自行批准高风险权限变化。

执行记录（2026-07-22）：阶段七首轮实现已完成。已建立单一 Bot 自有安全工作空间、消息/Artifact/工作空间/任务/记忆统一搜索、带 SSRF 与 DNS rebinding 防护的 Web Search/Fetch/Download、设置与 Prompt 提案、持久化运行时配置、插件候选静态验证与摘要复验、原子部署和持久化回滚；新增 21 个 Agent 工具且没有暴露自审批入口。完整测试结果为 670 passed、1 skipped（Windows 无符号链接权限时条件跳过），详细记录见 `PHASE7_IMPLEMENTATION.md`。

### Phase 8：全量 E2E 和生产切换

目标：完成系统测试和旧链路退场。

工作：

- 完成 L0-L6 测试。
- 建立 Docker E2E。
- 建立 Live NapCat 验收。
- 故障演练。
- 性能基线。
- 删除已无调用的兼容代码。

验收：满足第 27 节全部发布标准。

执行记录（2026-07-22）：阶段八自动化实现已完成。已建立 L0-L6 分层运行器、JSON E2E 场景、真实跨进程恢复测试、进程心跳与 SQLite 健康检查、Docker test target、Live NapCat 显式授权门禁及性能基线，并将部署文档和脚本收口为单一 Bot。L0-L5 共 190 个分层用例通过；全量测试为 679 passed、2 skipped；性能结果为 2441.55 messages/s、搜索 P95 0.669 ms。生产切换尚未放行：Docker Hub 网络超时导致测试镜像未完成构建，真实 QQ L6 未获授权。详细记录见 `PHASE8_IMPLEMENTATION.md`。

## 26. 开发拆分和依赖关系

```text
ConfigCenter ─┬─ PromptRegistry
              ├─ Tool Platform
              └─ Scheduler

QQ Adapter → Event Journal → Message Store → History Sync
                              └→ Artifact Store → QQ File Tools

Tool Platform + State Store → Durable Runtime → Scheduler

上述全部 → Full E2E Harness → Production Cutover
```

优先顺序不能颠倒：

- 没有无损消息库前，不应大量开发文件工具。
- 没有 Invocation 持久化前，不应宣称任务可恢复。
- 没有 ConfigSnapshot 前，不应开放动态 Prompt 修改。
- 没有 E2E Harness 前，不应删除旧链路。

## 27. 发布验收标准

### 27.1 数据完整性

- 100% 保存可序列化的 NapCat 原始事件。
- 未知 CQ 类型不会导致摄取失败或信息丢失。
- 实时和回补消息按 message_id 去重。
- 出站消息也能在数据库查询。
- Artifact 可追溯至来源消息、网页或任务。

### 27.2 恢复能力

- Event Journal 未投影记录可恢复。
- 非终态 Run 重启后可恢复或明确失败。
- 定时任务重启后按 misfire 策略执行。
- Artifact 下载失败可重试。
- 配置发布失败自动回滚。

### 27.3 安全

- 普通用户不能修改 Prompt、安全配置或安装插件。
- Agent 不能读取密钥或绕过审批。
- 文件路径不能逃逸全局工作空间。
- Web 工具不能访问默认禁止的内部地址。
- 所有高风险副作用有审计记录。

### 27.4 测试

- 单元和集成测试全部通过。
- NapCat 契约 Fixture 全部通过。
- 核心进程内 E2E 全部通过。
- Docker 恢复场景全部通过。
- Live NapCat 验收在发布候选版本通过。
- 安全和 Chaos 阻断级用例全部通过。

### 27.5 可观测性

- 任一 QQ 回复可以通过 trace_id 追溯完整链路。
- 任一工具调用可以查询输入摘要、权限判断和结果。
- 任一 Agent Run 可以查询配置快照和 Prompt 版本。
- 同步 Gap、Artifact 积压和任务失败有指标或诊断接口。

## 28. 风险与缓解措施

### 28.1 SQLite 写入压力

风险：消息、任务、Artifact 和日志都写入 SQLite，可能产生锁竞争。

缓解：

- WAL 模式。
- 统一异步写入队列或短事务 UnitOfWork。
- Event Journal 和业务投影批量提交。
- 避免在事务中执行网络操作。
- 达到实际瓶颈后再评估 PostgreSQL，不提前复杂化。

### 28.2 NapCat 历史不完整

风险：QQ 客户端或接口无法提供无限历史，文件链接可能过期。

缓解：

- 实时优先归档。
- 显式 Coverage 和 Gap。
- 在线文件优先物化。
- 不向 Agent 虚构完整性。

### 28.3 存储膨胀

风险：长期保存图片、语音、视频和文件导致磁盘增长。

缓解：

- 内容 Hash 去重。
- 类型、大小和年龄保留策略。
- 高低水位磁盘告警。
- LRU 清理可重新获取的缓存资源。
- 永久 Artifact 与可清理 Cache 分离。

### 28.4 动态配置导致行为漂移

风险：Prompt 或设置频繁修改导致无法复现行为。

缓解：

- 版本化。
- Run 绑定快照。
- Prompt 黄金测试。
- Diff、审批和回滚。

### 28.5 重构范围过大

风险：同时替换所有模块导致长时间无法交付。

缓解：

- 按 Phase 独立验收。
- 保持旧接口兼容层。
- 新旧数据双读或双写只在有限阶段使用。
- 每个阶段完成后清理临时兼容代码。

## 29. 推荐的首个实施迭代

第一个可交付迭代应控制在“统一配置和无损消息存储”，不立即实现全部 Agent 能力。

建议范围：

1. 建立 ConfigCenter 最小骨架。
2. 将 `system_prompt` 拆为 PromptRegistry 的 default Profile。
3. 新增 Event Journal。
4. 扩展 Messages 保存 raw_event、raw_message、message_array 和 ingestion_source。
5. 新增 Message Segments 无损投影。
6. 保持旧 `Event.message` 和现有插件行为不变。
7. 建立 CQ 未知类型、实时去重、原始事件回放测试。
8. 建立第一条完整进程内 E2E：NapCat 文本事件到 QQ 回复。

该迭代完成后，后续 Artifact、历史回补、Tool Platform 和 Durable Runtime 都拥有稳定的数据与配置基础。

## 30. 最终架构判断

本次重构的关键不是把 QQ 消息全部转换成系统自定义对象，而是建立一个能够长期保存事实、按需理解事实并可靠采取行动的 Agent Runtime。

最终系统应满足以下闭环：

```text
观察：无损接收并持久化 QQ、文件、网页和工具结果
→ 理解：从数据库、记忆和外部搜索组装上下文
→ 计划：形成可持久化、可恢复的任务和步骤
→ 行动：通过受权限控制的工具影响 QQ 和本地环境
→ 验证：检查结果、保存 Artifact、记录审计
→ 学习：沉淀经过验证的情景、事实和流程记忆
→ 调整：以提案、测试、审批和回滚方式修改设置、Prompt 和插件
```

原始 CQ、数据库优先、单一 Bot 个体、统一配置与 Prompt、全量 E2E 是本方案的五个长期架构支点。后续新增能力必须围绕这五点扩展，而不应重新引入有损消息转换、进程内临时状态或散落配置。
