# 阶段四实施说明：历史回补与离线同步

## 1. 阶段目标

本阶段建立统一的数据库优先历史查询链路，使 Bot 在离线、断线重连或本地历史覆盖不足时，能够从 NapCat 回补消息，并继续使用阶段二和阶段三已经建立的无损消息与 Artifact 投影。

核心约束：

- 实时消息与历史消息使用同一个 `message_id` 去重。
- 历史消息保留原始返回结构，`ingestion_source=backfill`。
- 回补消息只入库，不重新进入插件分发，避免机器人回复旧消息。
- 本地数据库能够满足查询时，绝不调用 NapCat。
- NapCat 无法证明 QQ 历史完整，查询必须携带覆盖状态和 Gap。

## 2. 端到端结构

```text
Bot 与 NapCat 建立连接或重连
  → get_recent_contact
  → 对最近群聊/私聊拉取最新历史页
  → QQHistoryGateway
  → HistorySyncService
      → 无损 EventBus.parse
      → Event Journal(source=backfill)
      → Messages / Message Segments / FTS / Sync State
      → Artifact 发现

Agent query_history
  → HistoryQueryService
  → 本地 MessageStore 查询
      ├─ 满足查询：立即返回
      └─ 覆盖不足：有限页数 NapCat 回补
          → 再次查询本地数据库
          → 返回 messages + coverage + gaps
```

## 3. NapCat History Gateway

`src/adapters/qq/history_gateway.py` 是唯一保存历史接口具体参数的模块，封装：

- `get_recent_contact(count)`；
- `get_group_msg_history(group_id, message_seq, count, ...)`；
- `get_friend_msg_history(user_id, message_seq, count, ...)`。

网关固定传递官方要求的：

```text
reverse_order=false
reverseOrder=false
disable_get_url=false
parse_mult_msg=true
quick_reply=false
```

返回结果归一化为 `RecentConversation` 和 `HistoryPage`。分页锚点从页面中最小的 `message_seq` 推导，领域服务不直接依赖 NapCat 原始字段。

最近会话的 `chatType=2` 映射为群聊，其余当前映射为私聊。原始最近会话对象仍完整保存在 `RecentConversation.raw` 中，方便后续兼容新的会话类型。

## 4. 无损历史写入

每条历史消息只补充投影所需的上下文字段：

- `post_type=message`；
- `message_type=group/private`；
- 群聊补充 `group_id`；
- 私聊显式绑定查询的对端 `conversation_id`。

其余字段不重写。规范化对象随后进入现有 `EventBus.parse`、Event Journal 和 MessageStore。

这保证：

- 原始 CQ 和 `message_array` 不丢失；
- FTS、回复关系和资源段继续自动投影；
- 历史资源进入 Artifact Store；
- 同一消息先实时收到、后在历史页出现时，只更新同一消息记录；
- 即使 NapCat 返回重复页或乱序页，最终数据库仍按消息时间稳定查询。

## 5. 分页、停止和游标

每次同步最多读取 `max_pages_per_sync` 页。停止原因包括：

- `provider_exhausted`：NapCat 当前不再返回更多；
- `local_overlap`：历史页已经碰到本地消息；
- `no_cursor`：响应没有可继续的序号；
- `page_limit`：达到本次同步页数上限。

达到页数上限时，将下一个 `message_seq` 保存到 `conversation_sync_state.cursor_json`，按需查询可从该游标继续向更早历史回补。

连接或重连同步始终从最新页开始，不能直接使用深度回补游标，否则会漏掉机器人离线期间刚产生的消息。

## 6. 覆盖状态与同步 Gap

新增 `sync_gaps` 表：

```text
conversation_type / conversation_id
gap_start / gap_end
reason
detected_at / resolved_at
```

当前原因：

- `pagination_limit`：本轮受配置页数限制；
- `napcat_error`：NapCat 调用失败；
- `provider_history_boundary`：NapCat 已耗尽当前可见历史，但无法证明这是 QQ 会话真正起点。

覆盖状态语义：

- `complete`：仅表示本次查询所需窗口有足够本地结果，或者精确消息已经命中；
- `partial`：已有部分结果、同步状态或明确 Gap；
- `unknown`：本地无数据且没有可靠覆盖信息。

`backfill_complete=1` 只表示 NapCat 当前可见页面已经耗尽，不等价于整个 QQ 历史绝对完整。只要仍有 `provider_history_boundary`，对外覆盖仍为 `partial`。

## 7. 数据库优先查询

`HistoryQueryService.query` 执行：

1. 使用 `conversation_type + conversation_id` 查询本地数据库；
2. 精确消息命中或本地结果足够时直接返回；
3. 覆盖不足且允许 `query_backfill` 时执行有限页回补；
4. 回补结果必须先落库；
5. 再从数据库运行原始查询条件；
6. 返回消息、覆盖状态、Gap 和新增消息数量。

群聊和私聊均按会话 ID 隔离查询。私聊历史同时支持对端发送和机器人自己发送的消息；旧 `recent_private(user_id)` 接口保留按发送者查询的兼容行为。

现有 Agent `query_history` 已接入该服务，并在原有 `text` 字段外返回：

```json
{
  "coverage": "complete | partial | unknown",
  "gaps": []
}
```

精确 `message_id` 在历史页仍未找到时，继续保留原有 `get_msg` 兜底。

## 8. 启动和重连同步

每次 NapCat 客户端连接成功、进入实时事件循环之前：

1. 调用最近会话接口；
2. 对最近会话各拉取最新一页；
3. 将离线消息写入数据库；
4. 更新同步状态和 Gap；
5. 再开始接收实时事件。

同步异常会记录错误，但不会阻止实时事件循环启动。下一次连接或查询可再次尝试。

## 9. 历史资源处理

回补消息投影完成后立即执行 Artifact 元数据发现。若 `[artifacts].auto_materialize=true`，Bot 会创建受生命周期管理的后台任务按需获取实体；默认值为 `false`，只保存资源引用，避免启动同步被大量图片和文件下载阻塞。

Bot 关闭时会取消并回收尚未完成的 Artifact 后台任务。

## 10. 统一配置

```toml
[history]
enabled = true
sync_on_connect = true
recent_contact_count = 50
page_size = 20
max_pages_per_sync = 3
query_backfill = true
```

- `enabled`：历史子系统总开关；
- `sync_on_connect`：连接和重连后同步最近会话；
- `recent_contact_count`：检查的最近会话数量；
- `page_size`：单次 NapCat 历史页大小；
- `max_pages_per_sync`：一次深度回补最大页数；
- `query_backfill`：查询覆盖不足时是否允许调用 NapCat。

## 11. 测试覆盖

新增测试覆盖：

- 官方群聊历史分页参数；
- 最近会话群聊/私聊映射；
- NapCat 错误响应；
- 历史页乱序、重复消息及实时/回补去重；
- 私聊双方消息绑定同一会话；
- 分页游标和 `pagination_limit` Gap；
- `napcat_error` 诊断；
- Provider 历史边界不误报完整；
- 历史资源发现；
- 本地结果足够时零 NapCat 调用；
- 覆盖不足时回补并重新查询数据库；
- 禁用回补时返回部分覆盖；
- 连接时导入离线消息；
- 连接同步失败不阻断运行；
- 原有消息、LLM、插件和 E2E 链路完整回归。

阶段验收结果：完整测试套件 `627 passed`，阶段变更 Ruff 检查及 `git diff --check` 通过。

## 12. 阶段边界与下一步

本阶段没有实现无限历史抓取、定时全账号深度爬取或假定所有会话都可被 NapCat 枚举。当前同步采用最近会话和按需查询组合，符合数据库优先和最小外部调用原则。

下一阶段进入 Tool Platform：移除全局 `_catalog/_executor`，建立 Bot 实例级 Tool Registry、完整输入输出 Schema 验证、权限上下文、Invocation 持久化、幂等和统一审计。
