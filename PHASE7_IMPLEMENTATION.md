# 阶段七实施说明：本地/网页搜索与 Control Plane

## 1. 阶段目标

本阶段扩展单一 Bot 个体的本地认知、互联网取证、文件创建和受控自配置能力，同时保持以下边界：

- 本地事实优先从数据库和 Bot 自有工作空间查询；
- 网页内容及下载结果始终作为不可信外部输入处理；
- Agent 可以读取配置并提出变更，但不能批准自己的提案；
- Prompt、设置和插件变更必须可验证、可审计、可回滚；
- 不引入多 Bot，也不为用户或群聊创建独立工作区。

## 2. 端到端结构

```text
AgentLoop
  → ToolCatalog / ToolExecutor / Invocation 审计
    ├→ SearchService
    │   ├→ MessageStore（当前会话或管理员范围）
    │   ├→ ArtifactStore
    │   ├→ WorkspaceService
    │   ├→ RuntimeStore
    │   └→ MemoryManager
    ├→ SafeWebClient → ArtifactStore（untrusted=true）
    ├→ WorkspaceService → Bot 自有工作区 → ArtifactStore
    └→ ControlPlane
        ├→ settings / ConfigCenter
        ├→ prompts / PromptRegistry
        └→ plugin candidates / deployments
```

所有 Agent 调用仍经过阶段五的权限、Schema、超时、结果大小、幂等和 Invocation 持久化，并可归属到阶段六的 Task、Run 和 Step。

## 3. Bot 自有工作空间

`src/core/workspace/service.py` 提供单一 Bot 级工作空间，不按用户或群聊切分。

能力包括：

- UTF-8 文本原子写入；
- 默认禁止覆盖已有文件；
- 读取、目录列举和全文搜索；
- 将工作空间文件导出为 Artifact；
- 独立限制最大写入和读取尺寸。

路径在使用前统一解析并确认仍位于配置根目录内。`..`、绝对路径和符号链接逃逸均被拒绝，避免工具访问 Bot 工作空间之外的文件。

## 4. 统一本地搜索

`src/core/search/service.py` 使用统一 `SearchHit` 返回消息、资源、文件、任务和记忆搜索结果：

```text
source_type / source_id / title / snippet / timestamp
reference / coverage / metadata
```

支持五个搜索域：

- `messages`：查询消息数据库，普通调用仅限当前会话；
- `artifacts`：查询统一资源元数据；
- `workspace`：查询 Bot 自有工作空间文本；
- `tasks`：查询持久化 Task 和 Run；
- `memory`：查询长期事实记忆。

返回值携带稳定引用和覆盖信息，Agent 后续应通过资源解析、工作空间读取等工具获取完整内容，而不是把搜索摘要误当作原始事实。

## 5. 安全 Web 客户端

`src/core/web/client.py` 将互联网能力分成三个明确动作：

- Search：只解析搜索结果页，不自动访问结果链接；
- Fetch：读取受限尺寸的 HTML、文本、JSON 或 XML；
- Download：下载文件并直接进入 Artifact Store。

Fetch 会提取网页标题和可见文本，同时保存原始响应 Artifact。Web Artifact 均标记 `untrusted=true`，明确提醒上层不得把网页中的指令视作系统指令。

网络安全边界：

- 仅允许 HTTP/HTTPS；
- 拒绝 URL 用户名和密码；
- 拒绝 localhost、`.local`、私网、链路本地和云元数据地址；
- 连接前解析并检查全部 DNS 地址；
- 每一次重定向都重新校验目标；
- 连接后在传输层可提供 peer 信息时再次检查实际对端 IP，降低 DNS rebinding 风险；
- 限制超时、响应尺寸、重定向次数和 Fetch MIME 类型。

当前 Web 能力是确定性的搜索、抓取和下载，不包含浏览器交互、登录态接管或任意脚本执行。

## 6. Control Plane

`src/core/control_plane/service.py` 把设置、Prompt 和插件变更统一建模为 `ChangeProposal`，持久化到 `control_proposals`。

核心流程：

```text
Agent 提案 → 结构和策略验证 → PENDING
                                ↓
                    独立外部审批/应用入口
                                ↓
                  APPLIED / REJECTED / FAILED
```

Agent 工具只开放读取、验证和提案，不注册 `approve_and_apply`。因此即使 Agent 拥有管理员工具上下文，也不能在同一工具面自行批准高风险变更。

## 7. 设置变更

设置提案会检查路径、值类型和风险等级。密钥、环境变量和管理员身份等敏感路径禁止通过 Control Plane 读取或修改。

ConfigCenter 新增 `runtime_settings` 表。应用后的非敏感覆盖会持久化，重启时重新叠加到类型化配置，不依赖进程内临时状态。应用失败会恢复原配置及相关服务。

动态应用后，Tool、Scheduler、Workspace 和 Web 服务会读取新的有效配置；配置版本继续进入运行快照和审计链路。

## 8. Prompt 变更

Prompt 提案验证内容和版本。应用时对 Prompt 文件及 Manifest 使用原子替换，然后重新加载 PromptRegistry 并验证新版本。

若写入、加载或版本校验任一步失败，会同时恢复原 Prompt 文件和 Manifest，避免出现文件内容、清单与运行时注册表不一致。

## 9. 插件候选、部署与回滚

插件只能从 `plugins/candidates` 候选目录进入验证流程。候选必须包含 `plugin.toml`，声明名称、版本和位于候选目录内的入口文件。

静态验证包括：

- Manifest 字段和入口路径；
- Python AST 语法；
- 禁止的框架或高风险模块导入；
- 明确的资源和权限标记；
- 候选全部文件的 SHA-256 摘要。

应用前会再次计算摘要，防止“验证后替换”导致的 TOCTOU。部署使用原子替换并把上一版本备份、摘要和状态写入 `plugin_deployments`；回滚状态跨重启持久保存，可恢复上一版本，首次安装则删除已安装入口。

当前阶段不在验证过程中执行候选插件，也不允许插件安装提案自行批准。

## 10. Agent 工具

新增 12 个能力工具：

- `search_messages`、`search_artifacts`、`search_workspace`、`search_tasks`、`search_memory`；
- `workspace_write`、`workspace_read`、`workspace_list`、`workspace_export_artifact`；
- `web_search`、`web_fetch`、`web_download`。

新增 9 个 Control Plane 工具：

- `settings_get`、`settings_propose`；
- `prompts_get`、`prompts_propose`；
- `plugins_list`、`plugins_validate`、`plugins_propose_install`、`plugins_propose_state`、`plugins_propose_rollback`。

普通用户只能执行当前会话范围的消息搜索。跨会话、本地文件、Web 和全部 Control Plane 能力均要求管理员权限。完整 ToolCatalog 当前包含 54 个工具。

## 11. 统一配置

生产配置和实例配置均加入：

```toml
[workspace]
enabled = true
root_dir = "data/workspace"
max_file_size = 10485760
max_read_size = 1048576

[web]
enabled = true
timeout = 20.0
max_response_bytes = 10485760
max_redirects = 5
search_url = "https://html.duckduckgo.com/html/"
user_agent = "qqbot-agent/1.0"
```

所有设置通过 ConfigCenter 类型化加载；Control Plane 的运行时覆盖也由同一中心持久化和恢复。

## 12. Bot 生命周期与重绑定

Bot 初始化 Artifact Store 后创建 Workspace、Search、Web 和 Control Plane。测试或配置重载替换 MessageStore、ArtifactStore 或配置对象时，`ensure_capability_services()` 与 `ensure_control_plane()` 会重建依赖，防止服务继续引用旧数据库或旧资源存储。

## 13. 测试与验收

新增测试覆盖：

- 工作空间原子写入、禁止覆盖、路径穿越、符号链接逃逸、搜索和 Artifact 导出；
- 五类本地搜索及稳定引用；
- Web SSRF、云元数据、URL 凭据、重定向、DNS rebinding、MIME、尺寸和搜索/抓取隔离；
- 设置类型校验、敏感路径、持久化覆盖和失败不替换配置；
- Prompt 原子应用、重新加载和回滚；
- 插件禁止导入、摘要校验、两版本部署、持久化备份和回滚；
- Agent 工具权限、Invocation 顺序和提案不自批；
- QQ 触发 Agent 创建文件、导出 Artifact、记录 Task/Run/Step/Invocation 并回复 QQ 的端到端链路。

最终全量回归：`670 passed, 1 skipped`。唯一跳过项是在当前 Windows 环境缺少创建符号链接权限时条件跳过的符号链接逃逸用例；路径穿越和其余安全边界均已实际执行。

## 14. 阶段边界与后续工作

阶段七已经满足“Agent 可提出并验证变更，但不能自行批准”的验收要求。以下内容留到阶段八：

- Docker 全链路与故障恢复演练；
- 真实 NapCat/QQ 账号验收；
- Live Web 网络验收和性能基线；
- 插件隔离运行器与依赖安装策略；
- 浏览器式交互能力；
- 清理不再使用的兼容链路并完成生产切换。
