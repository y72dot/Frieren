# 阶段八实施说明：全量 E2E 与生产切换门禁

## 1. 阶段目标

本阶段把前七个阶段形成的能力纳入可重复、可分层、可生成机器报告的发布验证体系，并建立容器健康、跨进程恢复、真实性能基线和 Live NapCat 门禁。

生产切换采用“所有门禁显式通过”的原则。未获得真实 QQ 操作授权或外部网络不可用时，必须显示为待验收，不允许用 skip 或模拟结果代替生产放行。

## 2. L0–L6 测试分层

`scripts/run_e2e.py` 定义统一矩阵：

| 层级 | 验证范围 |
| --- | --- |
| L0 | 配置、ConfigCenter、PromptRegistry、QQ Adapter 契约 |
| L1 | 消息、Artifact、历史回补及数据库完整性 |
| L2 | MessageBus、Bot 生命周期、历史服务和完整管线集成 |
| L3 | Tool Platform、Control Plane、Durable Runtime、Scheduler |
| L4 | QQ→插件/Agent→工具→Task/Run/Step→QQ 的进程内 E2E |
| L5 | 跨进程恢复、安全 Web、工作空间边界、运行健康和故障场景 |
| L6 | 真实 NapCat 登录、QQ 发消息、历史查询和可选文件上传 |

运行器逐层启动独立 pytest 进程，将临时目录和缓存限定在 `data/test-*`，避免依赖操作系统临时目录权限。最终生成带命令、耗时、退出码和输出尾部的 JSON 报告。

默认命令只执行完全自动化的 L0–L5：

```bash
python scripts/run_e2e.py
```

## 3. 数据驱动场景 Harness

`tests/e2e_harness.py` 加载 `tests/e2e_scenarios/*.json`，直接向 EventBus 注入原始 NapCat 字典，不预处理 CQ。

首批黄金场景覆盖：

- 未知 CQ、未知消息段和未来事件字段无损落库；
- Agent 创建工作空间文件、导出 Artifact、写入 Invocation/Task/Run 并回复 QQ；
- Agent 提交设置提案后保持 pending，有效配置不被自行修改。

场景断言可同时检查 QQ API 调用、原始消息、message_array、raw_event、工作空间文件、Artifact、Runtime、Invocation 和 Control Plane。

## 4. 真实跨进程恢复

`tests/test_e2e_restart.py` 不复用同一 Python 对象或 SQLite 连接，而是分别启动创建进程和恢复进程。

验证结果：

- 已成功的工具 Invocation 在新进程补写 Step 输出，Run 回到 `CREATED`；
- 状态为 running 且结果未知的写操作在新进程进入 `WAITING_APPROVAL`；
- 未知副作用不会被自动重放。

这项测试补足了仅在内存中重建 Store 无法证明真正重启恢复的问题。

## 5. 运行健康与容器健康检查

`HealthMonitor` 每十秒原子写入 `data/health.json`，记录：

- 进程状态和 PID；
- 心跳时间；
- NapCat 是否连接；
- 可扩展诊断详情。

Bot 在 starting、running、连接、断线和 stopped 时更新状态。NapCat 客户端退出上下文后会立即清除，避免干净断线仍被误报为已连接。

`python -m src.healthcheck` 同时验证：

- 配置可以被 ConfigCenter 的源配置解析；
- 心跳新鲜且进程状态为 running；
- `messages.db` 可读写打开并通过 `PRAGMA quick_check`；
- 可选 `--require-napcat` 强制检查连接状态。

Docker Compose 已用该检查替换原先无条件 `exit(0)` 的伪健康检查。默认不因外部 NapCat 暂时断线重启 Bot；需要严格就绪判定时可显式启用连接要求。

## 6. Docker E2E

Dockerfile 新增 `test` target，安装 pytest 依赖并复制 config、tests 和 scripts。Compose 新增 `test` profile 下的 `e2e` 服务：

```bash
docker compose --profile test run --rm e2e
```

该容器执行与主机一致的 L0–L5 矩阵，不连接真实 QQ，也不挂载 Docker Socket。

本轮 `docker compose config --quiet` 已通过。补齐基础镜像后，构建首先发现 `.dockerignore` 错误排除了测试所需的 `config/`；现已改为只放行 bot.toml、角色资料、性能基线、Prompt 和 Skill，不把 `.env` 或实例状态带入镜像。

修正后 `qqbot-e2e:test` 和 Compose 的 `qqbot-e2e:latest` 均成功构建，项目 wheel、运行依赖和测试依赖在 Python 3.12 slim 镜像内安装成功。`docker compose --profile test run --rm e2e` 实际执行结果为 L0-L5 共 194 passed；Linux 下符号链接逃逸用例也已执行，不再条件跳过。

由于 Dockerfile 的最后一个 stage 是 `test`，生产 Compose 还必须显式声明 `target: runtime`，否则默认构建会把 Bot 服务变成 E2E 运行器。该风险已修正并加入静态契约测试；`qqbot-qqbot-frieren:latest` 实际构建成功，镜像 CMD 已核验为 `["python", "-m", "src.main"]`。

## 7. Live NapCat 门禁

L6 默认 skip，只有设置 `QQBOT_LIVE=1` 才表示操作者授权真实副作用。还必须提供：

- `NAPCAT_WS_URL`；
- 可选 `NAPCAT_TOKEN`；
- `QQBOT_LIVE_GROUP_ID` 测试群。

L6 会执行登录查询、发送唯一时间戳消息并从群历史中确认。设置 `QQBOT_LIVE_ARTIFACT` 时还会上传指定测试文件。

发布门禁使用：

```bash
python scripts/run_e2e.py --levels L6 --require-live
```

`--require-live` 在缺少 `QQBOT_LIVE=1` 时直接失败，防止 pytest 的条件 skip 被误当成生产验收通过。

## 8. 性能基线

`scripts/benchmark.py` 使用真实 EventBus 解析、MessageStore 持久化和 FTS 查询，输出 JSON 报告。基线由 `config/performance_baseline.json` 管理：

- 最低写入吞吐：250 messages/s；
- 搜索 P95 上限：100 ms。

本机正式规模结果（2000 条写入、100 次搜索）：

- 写入吞吐：2441.55 messages/s；
- 搜索 P50：0.326 ms；
- 搜索 P95：0.669 ms。

门禁命令：

```bash
python scripts/benchmark.py --enforce
```

这些阈值是单机回归下限，不代表最终容量规划；生产磁盘、消息峰值和长期数据库规模仍需持续压测。

## 9. 单一 Bot 部署收口

README、Compose、部署脚本和备份脚本已统一为一个 Bot + 一个 NapCat：

- 不再指导复制多 Bot 服务对；
- 唯一部署状态位于 `instances/frieren` 与 `instances/napcat-frieren`；
- Prompt 挂载改为可写，使独立审批后的原子 Prompt 应用在容器中有效；
- 部署和备份脚本只处理唯一 Bot。

LLM 的旧 INTERNAL `response_buffer` 入口仍被既有工具级调用者和大量契约测试直接使用，因此本阶段没有把“仍有调用”误判成死代码。生产 AgentLoop 已在阶段六脱离该入口；后续删除必须先迁移明确的外部兼容调用者。

## 10. 本轮验收结果

自动化结果：

- L0：26 passed；
- L1：18 passed；
- L2：22 passed；
- L3：27 passed；
- L4：83 passed；
- L5：17 passed、1 conditional skip；
- 全量测试：682 passed、2 skipped；
- 阶段八新增及修改 Python 文件 Ruff：通过；
- Compose 配置解析：通过；
- Docker test target 构建：通过；
- Docker Compose L0-L5：194 passed；
- 性能门禁：通过。

两个 skip 的含义：

1. 当前 Windows 没有创建符号链接的权限，符号链接逃逸测试条件跳过；
2. 未设置 `QQBOT_LIVE=1`，真实 QQ L6 未获授权。

## 11. 生产切换状态

代码、自动化 Harness、Docker 构建和容器内 L0–L5 门禁已经完成，但当前版本尚未标记为“生产切换完成”。剩余外部门禁：

1. 由操作者在专用测试群明确授权并执行 L6；
2. 在目标服务器执行一次容器重启、NapCat 断线重连和持久卷恢复演练。

两项完成后，才能满足重构方案第 27 节的全部发布标准。

补充债务：全仓 `ruff check .` 仍报告 115 个阶段八之前已存在的告警，主要位于旧 E2E fixture、sandbox 测试和历史导入顺序。本阶段未对无关旧文件执行批量自动改写；生产放行前应单独建立 lint 清债变更。
