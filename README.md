# qqbot

基于 [NapCatQQ](https://github.com/NapNeko/NapCatQQ) 的单一人格 QQ Agent。项目不依赖 NoneBot、AstrBot 或 Koishi，消息总线、插件系统、持久化运行时、工具平台和 Control Plane 均为自研实现。

Bot 被设计为一个长期运行的完整个体，而不是多 Bot 平台：所有会话共享同一个 Bot 身份、配置中心、长期记忆和 Bot 自有工作空间，同时通过权限与会话范围控制数据访问。

## 当前能力

- 无损接收并保存 NapCat 原始事件、原始 CQ、`message_array` 和未知消息段；
- 所有实时消息先写 SQLite，再进入过滤和插件分发链路；
- 数据库优先查询消息和文件，NapCat 历史接口负责补齐 Bot 离线期间的数据；
- Artifact Store 统一管理 QQ 文件、图片、语音、网页、下载结果和 Agent 创建的文件；
- Agent 可搜索消息、Artifact、工作空间、任务、记忆和互联网；
- Bot 自有安全工作空间支持创建、读取、搜索和导出文件；
- Tool Platform 提供权限、Schema、超时、幂等、结果限制和 Invocation 审计；
- Task、Run、Step 与 Scheduler 持久化，进程重启后按副作用安全策略恢复；
- 设置、Prompt 和插件采用“提案 → 独立审批 → 原子应用/回滚”的 Control Plane；
- 支持版本化 Prompt、长期事实记忆、Skill 和隔离 Docker Sandbox；
- 提供 L0–L6 分层 E2E、跨进程恢复、Docker、健康检查与性能门禁。

## 核心架构

```text
NapCatQQ WebSocket
  → EventBus.parse（保留 raw_event / raw_message / message_array / CQ）
  → Event Journal + MessageStore（先持久化，失败可恢复）
  → FilterManager
  → MessageBus.EXTERNAL
  → Plugin.match / Plugin.handle
      └→ LLM AgentLoop
          → Durable Task / Run / Step
          → ToolExecutor
              ├→ QQ API / Artifact / History
              ├→ Workspace / Local Search / Web
              ├→ Scheduler / Memory
              └→ Control Plane Proposal
  → MessageBus.ACTION
  → MiddlewarePipeline (ActionQueueMiddleware → _raw_call)
  → (compat) block / bypass / spam / rate-limit
  → NapCat API
  → 出站消息持久化
```

关键原则：

- 原始 CQ 是事实数据，不在接入层做有损预处理；
- 消息和资源以数据库为首要事实源，NapCat 查询是回补与兜底；
- 网页内容和下载结果始终标记为不可信外部输入；
- Agent 可以提出高风险变更，但没有自我审批工具；
- 结果未知的写操作在重启后进入人工审批，不盲目重放。

## 快速开始

### 环境要求

- Python 3.12+
- NapCatQQ
- 可选：Docker Desktop / Docker Engine，用于部署、Sandbox 和容器 E2E

### 本地开发

```bash
python -m venv venv

# Linux/macOS
source venv/bin/activate

# Windows PowerShell
# .\venv\Scripts\Activate.ps1

pip install -e ".[dev]"
cp .env.example .env
```

修改 `config/bot.toml`：

```toml
[bot]
qq = 123456789
nickname = ["机器人昵称"]
admin_users = [987654321]

[napcat]
mode = "active"
ws_url = "ws://127.0.0.1:3001"
token = ""

[filter.group]
mode = "whitelist"
list = [测试群号]

[filter.private]
mode = "whitelist"
list = [管理员QQ]
```

在 `.env` 中填写模型密钥：

```env
LLM_API_KEY=sk-your-key
# 也兼容 DEEPSEEK_API_KEY 和 OPENAI_API_KEY
```

先启动并登录 NapCat，然后启动 Bot：

```bash
python -m src.main
```

`scripts/run.sh` 提供 Linux 下的重启、PID 管理和实时日志入口。

## Docker Compose 部署

仓库只部署一个 Bot 和一个 NapCat，不为用户或群聊创建独立实例。

### 1. 配置账号

修改以下位置中的示例 QQ 号和权限配置：

- `instances/frieren/bot.toml`；
- `docker-compose.yml` 中 NapCat 的 `ACCOUNT`；
- `instances/napcat-frieren/config/` 中 OneBot/NapCat 配置。

准备部署密钥：

```bash
cp .env.example instances/frieren/.env
vim instances/frieren/.env
```

### 2. 构建并登录 NapCat

```bash
docker compose build qqbot-frieren
docker compose up -d napcat-frieren sandbox
docker compose logs -f napcat-frieren
```

NapCat WebUI 仅绑定 `127.0.0.1:6099`。远程服务器可使用 SSH 隧道：

```bash
ssh -L 6099:127.0.0.1:6099 user@server
```

浏览器打开 `http://localhost:6099`，完成扫码登录。

### 3. 启动 Bot

```bash
docker compose up -d qqbot-frieren
docker compose logs -f qqbot-frieren
docker compose ps
```

生产服务显式构建 Dockerfile 的 `runtime` target，入口固定为：

```text
python -m src.main
```

容器健康检查会验证配置、Bot 心跳和 `messages.db` 的 SQLite `quick_check`，不再使用无条件成功的伪健康检查。

一键初始部署：

```bash
bash scripts/deploy.sh
```

该脚本构建镜像、准备唯一 Bot 的 `.env` 并启动 NapCat；扫码后再启动 `sandbox` 和 `qqbot-frieren`。

## 配置、Prompt 与数据

### 统一配置

- 本地配置：`config/bot.toml`；
- Docker 配置：`instances/frieren/bot.toml`；
- 密钥：`.env` 或 `instances/frieren/.env`，不会进入配置快照或 Docker 构建上下文；
- 非敏感运行时覆盖：持久化到 `data/config_state.db`；
- 每个 Agent Run 记录配置快照与 Prompt 版本。

主要配置域：

| 配置段 | 用途 |
| --- | --- |
| `bot` / `napcat` | 唯一 Bot 身份与 NapCat 连接 |
| `filter` / `plugin` | 全局及插件级会话过滤 |
| `llm` / `llm.prompts` | 模型、会话和 Prompt Profile |
| `artifacts` / `history` | 资源归档和离线历史回补 |
| `tools` / `runtime` | 工具执行限制和持久化运行时 |
| `scheduler` | 时区、轮询和 misfire 限制 |
| `workspace` / `web` | Bot 工作空间和安全网页访问 |

### 版本化 Prompt

Prompt 统一位于 `config/prompts/`：

```text
manifest.toml
identity.md
behavior.md
response_style.md
qq_context.md
tool_policy.md
memory_policy.md
task_planner.md
```

Control Plane 应用 Prompt 时会原子替换文件与 Manifest、重新加载并校验版本；任一步失败都会恢复旧版本。

### 持久化数据

默认运行数据位于 `data/`：

- `messages.db`：Event Journal、消息、Segments、Artifact、Invocation、Task/Run/Step、Schedule 和 Control Plane；
- `llm_state.db`：会话与长期记忆；
- `config_state.db`：配置快照和非敏感运行时覆盖；
- `artifacts/`：SHA-256 内容寻址资源；
- `workspace/`：唯一 Bot 自有工作空间；
- `health.json`：进程心跳。

## Agent 能力与安全边界

当前 ToolCatalog 包含 QQ 查询/管理、Artifact、搜索、工作空间、Web、调度和 Control Plane 等工具。

权限规则：

- 普通用户只能搜索当前会话消息；
- 跨会话搜索、文件、Web、Schedule 和 Control Plane 要求管理员；
- 路径必须位于 Bot 工作空间内，拒绝 `..`、绝对路径和符号链接逃逸；
- Web 仅允许 HTTP/HTTPS，并拦截 localhost、私网、链路本地、云元数据、危险重定向和 DNS rebinding；
- 下载和网页响应进入 Artifact Store，不自动执行；
- 插件候选先做 Manifest、AST、禁止导入和 SHA-256 复验；
- Agent 只拥有提案工具，审批与应用必须由独立外部入口完成；
- `.env`、管理员列表和密钥路径禁止通过 Control Plane 读取或修改。

## 定时与可恢复任务

Scheduler 支持：

- 单次 `once`；
- 固定间隔 `interval`；
- 五字段 `cron`；
- 领域事件 `event`。

每个 Schedule 保存时区、misfire 策略和并发上限。`skip`、`run_once` 和 `catch_up` 行为显式配置；会发送消息的任务默认禁止批量 catch-up，避免重启后轰炸 QQ。

## 插件开发

插件采用包格式（目录 + `plugin.toml` + `plugin.py` + `__init__.py`）。插件类必须有 `__plugin_id__`，用装饰器注册处理器：

```python
from src.plugin import EventResult, command

class MyPlugin:
    __plugin_id__ = "my_plugin"

    @command("/hello")
    async def hello(self, event, ctx) -> EventResult:
        await ctx.reply(event, "你好！")
        return EventResult.CONSUME
```

`plugin.toml` 声明元数据、入口点和权限：

```toml
[plugin]
id = "my_plugin"
version = "1.0.0"
entrypoint = "plugins.my_plugin.plugin:MyPlugin"
sdk = ">=1.0,<2.0"

[permissions]
qq = ["message.send"]
```

约定：
- `ctx.reply(event, msg)` 快捷回复；`ctx.api` 提供受权限检查的 QQAgency；
- `ctx.config` 为 PluginConfigView（bot_id、nickname、admin_users、llm_enabled）；
- `ctx.storage` 为每插件 KV 存储；`ctx.create_task()` 创建受托管的后台任务；
- 处理器返回 `EventResult.CONSUME` 消费事件，`EventResult.CONTINUE` 继续分发；
- 命令行：`python -m src.plugin.cli new/validate/list/doctor`；
- 禁止直接依赖 NapCat 类型，使用 `from src.plugin import Event`。

## 项目结构

```text
src/
├── adapters/qq/          # 无损 QQ Adapter、历史和文件 Gateway
├── core/
│   ├── artifacts/        # Artifact Store 与物化服务
│   ├── config_center/    # 统一配置、快照和运行时覆盖
│   ├── control_plane/    # 设置、Prompt、插件提案和回滚
│   ├── history/          # 数据库优先查询和 NapCat 回补
│   ├── llm/              # AgentLoop、工具、会话、记忆、Skill、Sandbox
│   ├── prompts/          # PromptRegistry
│   ├── runtime/          # Task/Run/Step、恢复和 Scheduler
│   ├── search/           # 统一本地搜索
│   ├── web/              # SSRF 防护的 Search/Fetch/Download
│   ├── workspace/        # Bot 自有安全工作空间
│   ├── bot.py            # 生命周期和组件装配
│   ├── event_bus.py      # 原始事件解析与 Journal 恢复
│   ├── message_bus.py    # EXTERNAL/ACTION/INTERNAL/LIFECYCLE 总线
│   └── message_store.py  # SQLite 事实存储
plugins/                  # QQ 插件与 Agent 工具注册
config/prompts/           # 版本化 Prompt
instances/                # 唯一 Bot 和 NapCat 部署状态
scripts/                  # 部署、备份、E2E、性能基线
tests/                    # 单元、集成、数据驱动和 Live E2E
```

## 测试与发布门禁

### 主机测试

```bash
# 全量单元与集成测试
pytest -q

# L0-L5 分层 E2E，报告写入 data/test-reports/e2e-report.json
python scripts/run_e2e.py

# 性能门禁
python scripts/benchmark.py --enforce
```

当前验收结果：

- 主机全量：`998 passed, 2 skipped`（含插件系统 P0–P4 新增 175 tests）；
- 主机 L0-L5：`193 passed, 1 conditional skip`；
- Docker/Linux L0-L5：`194 passed`；
- 写入吞吐：约 `2441 messages/s`；
- 消息搜索 P95：约 `0.669 ms`。

主机条件跳过包括 Windows 无符号链接权限；Linux 容器中该安全用例已经实际通过。另一个 skip 是未授权的真实 QQ L6。

### Docker E2E

测试镜像与生产镜像使用不同 target：

```bash
docker compose --profile test build e2e
docker compose --profile test run --rm e2e
```

Docker 契约测试会确认生产服务使用 `runtime`、E2E 服务使用 `test`，并验证敏感配置没有进入测试镜像。

### Live NapCat / QQ（L6）

L6 会产生真实 QQ 副作用，默认不执行。只在专用测试群明确授权：

```bash
QQBOT_LIVE=1 \
NAPCAT_WS_URL=ws://127.0.0.1:3001 \
QQBOT_LIVE_GROUP_ID=123456 \
python scripts/run_e2e.py --levels L6 --require-live
```

L6 会查询登录信息、发送唯一时间戳消息并轮询群历史确认。设置
`QQBOT_LIVE_ARTIFACT` 时，测试会读取本机文件（上限 10 MiB）、以
`base64://` 传给 NapCat，并轮询群根目录确认文件真正出现；因此该路径不必
在 NapCat 容器内可见。

生产放行仍要求：

1. 在专用测试群通过 L6；
2. 在目标服务器完成 Bot 容器重启、NapCat 断线重连和持久卷恢复演练。

## 备份与恢复

```bash
bash scripts/backup.sh
```

脚本备份唯一 NapCat 登录会话和 Bot 配置到 `backups/<时间戳>/`，保留最近 7 份。

恢复示例：

```bash
cp -r backups/20260722_120000/napcat-frieren-session/* instances/napcat-frieren/QQ/
cp -r backups/20260722_120000/frieren-config/* instances/frieren/
```

恢复前应停止相关容器并保留当前目录副本。恢复后执行健康检查、L0-L5 和目标服务器故障演练。

## 开发文档

- `DEVELOPMENT.md`：所有开发文档的统一入口、当前状态和维护规则；
- `PLUGIN_SYSTEM_REFACTOR_PLAN.md`：当前插件系统重构的详细执行计划与任务真源；
- `REFACTOR_PLAN.md`：已完成的 Agent 能力平台总体设计基线；
- `PHASE1_IMPLEMENTATION.md` 至 `PHASE8_IMPLEMENTATION.md`：各阶段历史实施与验收记录；
- `LLM_TOOL_REFACTOR_PLAN.md`：LLM Tool 与消息插件分离的专项完成记录；
- `PLAN.md`：项目建立早期蓝图，仅作历史参考。
