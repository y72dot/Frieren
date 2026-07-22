# 阶段三实施说明：Artifact Store 与 QQ 文件能力

## 1. 阶段目标

本阶段将 QQ 消息中的图片、语音、视频和文件从“CQ 字符串中的临时引用”提升为可持久引用、可追溯、可按需获取、可再次发送的一等资源，同时继续满足以下原则：

- 原始 CQ、`message_array` 和原始 NapCat 事件保持不变；Artifact 是派生投影，不替代原始事实。
- 消息接收路径只发现资源并写元数据，不同步下载大文件。
- Artifact 元数据和消息共用一个 SQLite 数据库。
- 文件内容使用 SHA-256 内容寻址，重复内容只保存一份实体。
- Bot 是唯一完整个体，不创建用户级或群级物理工作区。

## 2. 已实现结构

```text
NapCat 原始消息
  → Event Journal / Messages / Message Segments
  → ArtifactStore.discover_message
      → artifacts 元数据
      → message_segments.artifact_id

Agent 或业务按需读取
  → ArtifactService.materialize
      → QQFileGateway.resolve
      → get_file / get_image / get_record
      → Base64、本地路径或受控 HTTP 下载
      → 大小校验 + SHA-256
      → data/artifacts/<hash-prefix>/<sha256>

再次发送
  → ArtifactService.send
      → upload_group_file / upload_private_file
```

## 3. 数据模型

`artifacts` 表保存：

- 稳定的 `artifact_id`；
- 类型、文件名、MIME、大小；
- SHA-256 和内容寻址后的本地路径；
- 来源消息 ID、来源消息段序号和原始 NapCat 文件 ID；
- 远程 URL、发现时间、下载时间、访问时间；
- 状态、失败原因和无损元数据 JSON。

资源和来源消息段通过 `(source_message_id, source_segment_index)` 唯一关联。同一消息重复投影不会产生重复 Artifact。

状态机当前包括：

```text
DISCOVERED → MATERIALIZING → AVAILABLE
                   └──────→ FAILED
AVAILABLE / FAILED → 后续阶段可扩展 EXPIRED / DELETED 与重试策略
```

失败时保留 Artifact 记录及错误，不删除来源引用。

## 4. 资源发现

当前识别 `image`、`record`、`video`、`file`、`online_file` 和 `mface` 消息段。发现器从无损 `raw_segment_json` 中提取常见的 `file_id/file/id`、`file_name/name`、`url`、大小和 MIME 字段。

发现过程发生在消息原始事实和消息投影提交之后、插件分发之前。即使资源尚未下载，Agent 也能查询到来源和状态。

E2E 测试会替换 `MessageStore`，Bot 因此实现了数据库连接一致性检查；一旦消息库被注入替换，Artifact Store 会自动重新绑定，避免持有旧 SQLite 连接。

## 5. 内容寻址存储

物化过程先写入 `data/artifacts/.tmp`，流式计算 SHA-256，并在完成后原子移动到：

```text
data/artifacts/<sha256 前两位>/<完整 sha256>
```

如果目标哈希已存在，则复用已有文件并删除临时文件。原始文件名只作为元数据和发送时的显示名，不参与实际存储路径，因此不能造成路径穿越。

`max_file_size` 同时在写入和 HTTP 下载时执行；带 `Content-Length` 的响应会提前拒绝，流式读取仍会再次限制实际字节数。

## 6. NapCat 文件网关

根据 2026-07-21 读取的 NapCat 官方接口契约，已封装：

- `get_file`：通用文件；
- `get_image`：图片；
- `get_record`：语音及输出格式转换；
- `get_group_file_url` / `get_private_file_url`；
- `get_group_root_files`；
- `upload_group_file`；
- `upload_private_file`。

网关统一检查 NapCat 失败响应，并将 `file/url/base64/file_name/file_size` 归一为 `ResolvedQQFile`。所有调用仍经过现有 ACTION MessageBus 和 action queue。

## 7. 物化与安全边界

物化的内容来源按以下顺序处理：

1. NapCat 返回的 Base64；
2. NapCat 返回且 Bot 进程可访问的本地文件；
3. NapCat 返回的 URL或消息保存的 URL。

HTTP URL 只允许 `http/https`，下载前解析 DNS，并拒绝环回、私网、链路本地、保留地址等非公网目标；不自动跟随重定向。这样可阻断消息构造 URL 对本机、容器内网或云元数据地址的 SSRF。

NapCat 返回的本地路径被视为受信任网关结果，只读取内容后复制到 Bot 自己的内容寻址目录；Agent 无法通过这些工具直接指定任意本地路径。

## 8. Agent 工具

新增四个工具：

- `list_message_artifacts`：按消息 ID 列出资源，READ_ONLY；
- `get_artifact_info`：查询状态、来源和本地可用性，READ_ONLY；
- `materialize_artifact`：按需获取并归档资源，WRITE；
- `send_artifact`：发送到当前或指定群聊/私聊，WRITE。

工具注册仍暂时接入现有全局 `ToolCatalog`；在阶段五 Tool Platform 中会迁移为实例化注册、统一 Invocation 持久化和更细粒度授权。

## 9. 统一配置

`BotConfig.artifacts` 和 TOML `[artifacts]` 提供：

```toml
[artifacts]
enabled = true
root_dir = "data/artifacts"
max_file_size = 104857600
download_timeout = 60
auto_materialize = false
```

`auto_materialize` 已预留但当前不主动下载；这是刻意行为，避免消息高峰被大文件网络 I/O 阻塞。后续可由后台任务执行策略化预取。

## 10. 测试与验收

本阶段新增测试覆盖：

- 消息资源发现、消息段关联和重复投影幂等；
- 相同文件内容的哈希去重；
- 文件大小上限；
- Base64 物化及已下载资源复用；
- 物化后群文件发送；
- NapCat 语音获取和群文件上传参数契约；
- 四个 Agent 工具的注册与风险级别；
- MessageStore 被替换后的 Artifact 数据库自动重绑定；
- 原有事件、消息、过滤、LLM 和 E2E 链路回归。

验收结果：

- 相关变更 Ruff 检查通过；
- 完整测试套件 `614 passed`；
- 未执行真实 QQ 账号的 Live NapCat 上传/下载验收，该项保留给阶段八发布候选环境，当前以官方接口契约和 Fake API 集成测试验证。

## 11. 阶段边界与下一步

本阶段不实现离线消息回补、自动覆盖率计算、下载后台重试队列、磁盘配额清理和病毒扫描。下一阶段进入历史回补：数据库优先查询，在覆盖不足时调用 NapCat 历史接口，并把离线期间的消息及资源引用用同一条投影链路补入数据库。
