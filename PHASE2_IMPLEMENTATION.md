# 阶段二实施计划与执行记录

> 阶段：无损 QQ Adapter、Event Journal 与新消息数据库  
> 状态：首轮实现完成  
> 执行日期：2026-07-21

## 1. 阶段目标

阶段二将 QQ 输入从“解析后文本”升级为可恢复、可重建的数据事实系统，同时保持现有插件完全兼容。

目标：

1. 原样保留 NapCat 原始事件、`raw_message` 和消息段数组。
2. 未知 CQ 类型、未知消息段和未知字段不丢失。
3. `Event.message` 继续兼容旧插件。
4. 所有已识别事件先写 Event Journal，再进入插件分发。
5. 消息、消息段、搜索索引和会话同步范围事务投影。
6. 投影失败保留可恢复 Journal，Bot 启动时自动回放。
7. 实时和历史来源使用 message_id 去重，但保留各自 Journal 事实。
8. 旧 `messages.db` 自动迁移，不丢失现有记录。
9. 所有插件发送的文本消息统一持久化，而不只记录 LLM 回复。

## 2. 具体实施步骤

### P2.1 无损 QQ Adapter

新增：

```text
src/adapters/qq/cq_view.py
src/adapters/qq/event_adapter.py
```

提供：

- SDK 对象到 JSON-safe 数据的开放式转换。
- 稳定的原始事件 JSON 序列化。
- 精确读取 NapCat `raw_message`，不使用合成值替代。
- 原始消息段数组提取。
- 非破坏性 CQ 扫描视图。

CQ 扫描规则：

- 接受未知类型。
- 接受未知属性。
- 属性值保持 CQ 转义状态。
- 保存原文和字符位置。
- 畸形 CQ 留在普通文本中，不阻塞消息摄取。

状态：已完成。

### P2.2 扩展兼容 Event

在现有 Event 上追加：

```text
raw_message
message_array
raw_event_json
ingestion_source
peer_id
```

兼容约束：

- 旧插件继续读取 `event.message`。
- 合并转发在原始文本为空时可生成兼容 `event.message`。
- 该兼容文本不会写入 `raw_message`。
- 私聊出站消息通过 `peer_id` 保存真实会话对象。

状态：已完成。

### P2.3 Event Journal

新表 `event_journal` 保存：

- event_id。
- event_type。
- source。
- received_at / occurred_at。
- raw_json。
- projected。
- projection_error。
- trace_id。

写入顺序：

```text
提交原始 Journal
→ 开始消息投影事务
→ 投影 Messages / Segments / FTS / SyncState
→ 标记 Journal projected=1
→ 提交
→ MessageBus 分发
```

未识别事件也写入 Journal，并标记为已经处理，避免原始输入静默消失。

状态：已完成。

### P2.4 Messages 无损扩展和自动迁移

在兼容旧字段的基础上增加：

```text
conversation_type
conversation_id
raw_message
message_array_json
raw_event_json
search_text
reply_to_message_id
is_from_bot
is_recalled
ingestion_source
first_seen_at
last_synced_at
```

旧数据库启动时通过 `PRAGMA table_info` 检查并追加缺失字段，旧 `content` 同步为初始 `raw_message` 和 `search_text`，标记来源为 `legacy`。

状态：已完成。

### P2.5 消息段投影

新表 `message_segments`：

- 保存原始 segment JSON。
- 保存 segment_index 和 segment_type。
- 尽可能关联对应原始 CQ。
- 未知 segment 完整保存。
- 没有 message_array 时，可从 CQ 建立明确标记为派生的索引。

状态：已完成。

### P2.6 全文搜索和同步状态

新增：

- `message_fts`：SQLite FTS5 派生索引。
- `conversation_sync_state`：记录会话最早/最新消息和 live/backfill 时间。

搜索文本包含：

- 原始 CQ/文本。
- 发送者显示名。
- 文本段。
- 文件名。
- 消息段中可搜索的摘要字段。

FTS5 不可用时自动回退 LIKE；兼容查询保留子串匹配语义。

状态：已完成。

### P2.7 去重与来源合并

- Event Journal 以来源、类型和完整原始 JSON 计算 SHA-256 event_id。
- Messages 继续以 NapCat message_id 作为主去重键。
- 同一消息的 live 和 backfill 事件分别保留 Journal。
- live 投影优先，不允许后来的较差历史表示覆盖完整实时原文。
- backfill 仍更新同步时间和缺失字段。

状态：已完成。

### P2.8 投影恢复

`EventBus.recover_unprojected()` 在 Bot 启动时执行：

1. 查询 `projected=0` 的 Journal。
2. 从 raw_json 重新解析 Event。
3. 使用原 source 和 trace_id 重做幂等投影。
4. 成功后标记 projected。
5. 持续失败时保留错误和日志。

状态：已完成。

### P2.9 出站消息统一持久化

文本发送记录下沉到 `ApiClient._raw_call()` 的成功边界：

- `send_group_msg`。
- `send_private_msg`。

这样 ping、echo、repeater、普通插件和 Agent 回复都会进入同一个消息库。LLM Sender 只在测试 Fake API 或不具备统一记录能力的客户端上执行兼容记录，避免生产环境双写。

状态：已完成。

## 3. 新增读取能力

MessageStore 新增：

```text
get_message_record(message_id)
get_segments(message_id)
get_journal_event(event_id)
unprojected_events(limit)
get_sync_state(conversation_type, conversation_id)
```

原有接口保持：

```text
record
record_bot_message
recent
recent_private
by_user
search
query
trim
stats
```

## 4. 测试覆盖

新增测试：

- 未知 CQ 类型和属性保真。
- CQ 原始字符位置。
- 畸形 CQ 不阻塞。
- SDK 风格对象序列化。
- 未知 NapCat 字段保留。
- message_array 未知 segment 保留。
- 合并转发兼容文本不污染 raw_message。
- Journal、Message、Segment 三层一致性。
- Trace ID 持久化。
- 投影失败留下可恢复 Journal。
- 启动回放成功。
- live/backfill 去重和来源保留。
- 文件名派生搜索。
- 旧数据库自动迁移。
- 所有插件公共发送边界持久化。
- 私聊出站 conversation_id 正确。

验证结果：

```text
阶段二新增与相关测试：全部通过
完整测试套件：606 passed
Ruff：All checks passed
```

## 5. 兼容边界

当前保留：

- `Event.message`。
- `StoredMessage` 六字段结构。
- MessageStore 原有查询方法和排序。
- HistoryPlugin JSONL 日志。
- 现有 LLM 历史格式。

阶段二尚不下载文件实体，也不调用 NapCat 历史接口；它只完整保存实时发现的资源引用。文件物化属于阶段三，历史回补属于阶段四。

## 6. 验收结论与下一阶段

阶段二首轮实现满足：

- QQ 原始事实无损保存。
- 数据先持久化后分发。
- 消息投影失败可恢复。
- 新旧数据库兼容。
- 实时和历史来源模型已就绪。
- 所有文本出站消息统一落库。
- 完整测试无回归。

下一阶段应进入 Artifact Store 与 QQ 文件能力：建立 Artifact 表、内容寻址存储、资源发现与物化状态机，并封装 NapCat 图片、语音、群文件和私聊文件接口。
