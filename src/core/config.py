from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from loguru import logger

# Environment variable names for overriding config values.
ENV_NAPCAT_MODE = "NAPCAT_MODE"
ENV_NAPCAT_WS_URL = "NAPCAT_WS_URL"
ENV_NAPCAT_TOKEN = "NAPCAT_TOKEN"
ENV_NAPCAT_REVERSE_PORT = "NAPCAT_REVERSE_PORT"


# ---------------------------------------------------------------------------
# Configuration data classes
# ---------------------------------------------------------------------------


@dataclass
class BotConfigSection:
    qq: int
    nickname: list[str] = field(default_factory=list)
    admin_users: list[int] = field(default_factory=list)


@dataclass
class NapCatConfig:
    mode: str = "active"  # "active" = bot connects to NapCat, "reverse" = NapCat connects to bot
    ws_url: str = "ws://127.0.0.1:3001"
    token: str = ""
    reconnect_interval: int = 5
    reverse_host: str = "0.0.0.0"
    reverse_port: int = 8080


@dataclass
class PluginConfig:
    auto_discover: bool = True
    plugin_dirs: list[str] = field(default_factory=lambda: ["plugins"])
    disabled_plugins: list[str] = field(default_factory=list)
    plugin_configs: dict[str, dict] = field(default_factory=dict)


@dataclass
class FilterModeConfig:
    mode: str = "blacklist"  # "whitelist" | "blacklist" | "off"
    list: list[int] = field(default_factory=list)


@dataclass
class PluginFilterConfig:
    enable: bool = True
    group: FilterModeConfig = field(default_factory=FilterModeConfig)
    private: FilterModeConfig = field(default_factory=FilterModeConfig)


@dataclass
class FilterConfig:
    enable: bool = True
    group: FilterModeConfig = field(default_factory=FilterModeConfig)
    private: FilterModeConfig = field(default_factory=FilterModeConfig)
    plugins: dict[str, PluginFilterConfig] = field(default_factory=dict)


@dataclass
class LoggingConfigSection:
    level: str = "INFO"
    file: str = "logs/bot.log"
    rotation: str = "10 MB"
    retention: str = "14 days"
    json_format: bool = False


@dataclass
class ActionQueueConfig:
    """Per-plugin config for the action-queue rate limiter (:ref:`plugins.action_queue`)."""

    enabled: bool = True
    global_rate: float = 5.0  # max actions per second, 0 = unlimited
    group_cooldown: float = 1.0  # seconds between actions to the same group, 0 = off
    per_action_delay: float = 0.0  # extra fixed delay per action, 0 = off
    spam_window: float = 5.0  # dedup window in seconds, 0 = disabled
    spam_actions: list[str] = field(
        default_factory=lambda: [
            "send_group_msg",
            "send_private_msg",
            "group_poke",
            "send_group_forward_msg",
            "set_group_ban",
            "set_group_kick",
        ]
    )
    bypass_actions: list[str] = field(
        default_factory=lambda: [
            "get_group_info",
            "get_group_member_info",
            "get_group_member_list",
            "get_login_info",
            "get_friend_list",
            "get_stranger_info",
            "get_msg",
        ]
    )
    block_actions: list[str] = field(default_factory=list)


@dataclass
class LLMSessionConfig:
    """Session persistence and pruning configuration."""

    persist: bool = True
    pruning_strategy: str = "hybrid"  # "hybrid" | "recent" | "none"
    keep_recent_pairs: int = 3
    max_context_tokens: int = 4096


@dataclass
class LLMMemoryConfig:
    """Memory subsystem configuration."""

    episodic_enabled: bool = True
    episodic_max: int = 1000
    episodic_search_limit: int = 5
    semantic_enabled: bool = True
    consolidation_enabled: bool = True


@dataclass
class LLMSkillsConfig:
    """Skill system configuration."""

    enabled: bool = True
    skills_dir: str = "config/skills"
    auto_reload: bool = True


@dataclass
class LLMPromptConfig:
    """Versioned prompt registry configuration.

    Disabled by default so programmatically injected ``LLMConfig`` instances
    continue to honour ``system_prompt``. Deployments opt in explicitly.
    """

    enabled: bool = False
    prompts_dir: str = "prompts"
    profile: str = "default"


@dataclass
class SandboxConfig:
    container_name: str = "qqbot-sandbox"
    workspace: str = "/workspace"
    max_file_size: int = 1_048_576
    max_read_size: int = 524_288
    exec_timeout: int = 30
    max_exec_timeout: int = 60
    stdout_limit: int = 102_400
    enabled: bool = True


@dataclass
class ArtifactConfig:
    """Unified QQ resource discovery and content-addressed storage."""

    enabled: bool = True
    root_dir: str = "data/artifacts"
    max_file_size: int = 104_857_600
    download_timeout: int = 60
    auto_materialize: bool = False


@dataclass
class HistoryConfig:
    """Database-first history synchronization policy."""

    enabled: bool = True
    sync_on_connect: bool = True
    recent_contact_count: int = 50
    page_size: int = 20
    max_pages_per_sync: int = 3
    query_backfill: bool = True


@dataclass
class ToolPlatformConfig:
    """Policy and persistence limits for agent tool execution."""

    default_timeout: float = 30.0
    invocation_persist: bool = True
    max_result_bytes: int = 262_144


@dataclass
class RuntimeConfig:
    """Durable task execution and restart recovery policy."""

    enabled: bool = True
    recover_on_start: bool = True


@dataclass
class SchedulerConfig:
    """Persistent schedule polling and misfire policy limits."""

    enabled: bool = True
    timezone: str = "Asia/Shanghai"
    poll_interval: float = 1.0
    max_catch_up: int = 10


@dataclass
class WorkspaceConfig:
    enabled: bool = True
    root_dir: str = "data/workspace"
    max_file_size: int = 1_048_576
    max_read_size: int = 524_288


@dataclass
class WebConfig:
    enabled: bool = True
    timeout: float = 20.0
    max_response_bytes: int = 2_097_152
    max_redirects: int = 3
    search_url: str = "https://www.bing.com/search?setlang={lang}&q={query}"
    news_search_url: str = "https://www.bing.com/news/search?format=rss&setlang={lang}&q={query}"
    search_fallback_urls: list[str] = field(
        default_factory=lambda: ["https://html.duckduckgo.com/html/?q={query}"]
    )
    user_agent: str = "qqbot-agent/1.0"


_DEFAULT_LLM_SYSTEM_PROMPT = (
    "你是QQ群聊助手「芙莉莲」。通过当前声明的工具执行操作和查询。\n\n"
    "## 聊天记录格式\n"
    "每条消息格式为「[消息ID] MM-DD HH:MM 昵称(QQ号): 内容」。"
    "「回复[id]」表示回复消息，「@QQ号」表示提及用户。\n\n"
    "## 规则\n"
    "- 当前工具 schema 是能力和参数的唯一依据，不要假设未声明的工具存在\n"
    "- 需要事实或操作依据时先收集证据，再执行动作并检查结果\n"
    "- 消息ID和用户QQ号必须从上下文或工具结果中提取，不要编造\n"
    "- 管理、删除、配置等操作必须服从权限和审批结果\n"
    "- 中文回复，简洁友好，不超过200字；不要使用 [CQ:xxx] 格式"
)


@dataclass
class LLMConfig:
    enabled: bool = False
    api_base: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    max_tokens: int = 1024
    temperature: float = 0.7
    system_prompt: str = _DEFAULT_LLM_SYSTEM_PROMPT
    max_turns: int = 8
    session_ttl: int = 3600  # seconds, 0 = disable cache (fresh session every time)
    session: LLMSessionConfig = field(default_factory=LLMSessionConfig)
    memory: LLMMemoryConfig = field(default_factory=LLMMemoryConfig)
    skills: LLMSkillsConfig = field(default_factory=LLMSkillsConfig)
    prompts: LLMPromptConfig = field(default_factory=LLMPromptConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)


@dataclass
class BotConfig:
    bot: BotConfigSection
    napcat: NapCatConfig
    plugin: PluginConfig
    logging: LoggingConfigSection
    filter: FilterConfig = field(default_factory=FilterConfig)
    action_queue: ActionQueueConfig = field(default_factory=ActionQueueConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    artifacts: ArtifactConfig = field(default_factory=ArtifactConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    tools: ToolPlatformConfig = field(default_factory=ToolPlatformConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    web: WebConfig = field(default_factory=WebConfig)
    env: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_project_root() -> Path:
    """Find project root by walking up from this file until a config/ directory
    or pyproject.toml is found."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if (current / "config").is_dir() or (current / "pyproject.toml").is_file():
            return current
        current = current.parent
    raise FileNotFoundError(
        "Cannot locate project root: no 'config/' directory or 'pyproject.toml' found"
    )


def _require_str(data: dict[str, Any], key: str, section: str) -> str:
    value = data.get(key)
    if value is None:
        raise ValueError(f"[{section}] missing required field: {key}")
    return str(value)


def _require_int(data: dict[str, Any], key: str, section: str) -> int:
    value = data.get(key)
    if value is None:
        raise ValueError(f"[{section}] missing required field: {key}")
    return int(value)


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------


def _parse_bot_section(data: dict[str, Any]) -> BotConfigSection:
    return BotConfigSection(
        qq=_require_int(data, "qq", "bot"),
        nickname=list(data.get("nickname", [])),
        admin_users=[int(u) for u in data.get("admin_users", [])],
    )


def _parse_napcat_section(data: dict[str, Any]) -> NapCatConfig:
    # Base values from config file.
    token = str(data.get("token", ""))
    mode = str(data.get("mode", "active"))

    ws_url = data.get("ws_url", "")
    if not ws_url:
        host = data.get("ws_host", "127.0.0.1")
        port = data.get("ws_port", 3001)
        ws_url = f"ws://{host}:{port}"

    reconnect_interval = int(data.get("reconnect_interval", 5))
    reverse_host = str(data.get("reverse_host", "0.0.0.0"))
    reverse_port = int(data.get("reverse_port", 8080))

    # Environment variable overrides (for dev ↔ deploy compatibility).
    mode = os.getenv(ENV_NAPCAT_MODE, mode)
    ws_url = os.getenv(ENV_NAPCAT_WS_URL, ws_url)
    token = os.getenv(ENV_NAPCAT_TOKEN, token)
    reverse_port = int(os.getenv(ENV_NAPCAT_REVERSE_PORT, str(reverse_port)))

    return NapCatConfig(
        mode=mode,
        ws_url=ws_url,
        token=token,
        reconnect_interval=reconnect_interval,
        reverse_host=reverse_host,
        reverse_port=reverse_port,
    )


def _parse_plugin_section(data: dict[str, Any]) -> PluginConfig:
    return PluginConfig(
        auto_discover=bool(data.get("auto_discover", True)),
        plugin_dirs=list(data.get("plugin_dirs", ["plugins"])),
        disabled_plugins=list(data.get("disabled_plugins", [])),
    )


def _parse_filter_section(data: dict[str, Any]) -> FilterConfig:
    def _parse_mode_config(raw: dict[str, Any]) -> FilterModeConfig:
        return FilterModeConfig(
            mode=str(raw.get("mode", "blacklist")),
            list=[int(x) for x in raw.get("list", [])],
        )

    def _parse_plugin_config(raw: dict[str, Any]) -> PluginFilterConfig:
        return PluginFilterConfig(
            enable=bool(raw.get("enable", True)),
            group=_parse_mode_config(raw.get("group", {})),
            private=_parse_mode_config(raw.get("private", {})),
        )

    plugins_raw = data.get("plugins", {})
    plugins = {name: _parse_plugin_config(cfg) for name, cfg in plugins_raw.items()}

    return FilterConfig(
        enable=bool(data.get("enable", True)),
        group=_parse_mode_config(data.get("group", {})),
        private=_parse_mode_config(data.get("private", {})),
        plugins=plugins,
    )


def _parse_action_queue_section(data: dict[str, Any]) -> ActionQueueConfig:
    return ActionQueueConfig(
        enabled=bool(data.get("enabled", True)),
        global_rate=float(data.get("global_rate", 5.0)),
        group_cooldown=float(data.get("group_cooldown", 1.0)),
        per_action_delay=float(data.get("per_action_delay", 0.0)),
        spam_window=float(data.get("spam_window", 5.0)),
        spam_actions=list(
            data.get(
                "spam_actions",
                [
                    "send_group_msg",
                    "send_private_msg",
                    "group_poke",
                    "send_group_forward_msg",
                    "set_group_ban",
                    "set_group_kick",
                ],
            )
        ),
        bypass_actions=list(data.get("bypass_actions", [])),
        block_actions=list(data.get("block_actions", [])),
    )


def _parse_llm_section(data: dict[str, Any]) -> LLMConfig:
    session_raw = data.get("session", {})
    memory_raw = data.get("memory", {})
    skills_raw = data.get("skills", {})
    prompts_raw = data.get("prompts", {})
    sandbox_raw = data.get("sandbox", {})

    return LLMConfig(
        enabled=bool(data.get("enabled", False)),
        api_base=str(data.get("api_base", "https://api.openai.com/v1")),
        api_key=str(data.get("api_key", "")),
        model=str(data.get("model", "gpt-4o-mini")),
        max_tokens=int(data.get("max_tokens", 1024)),
        temperature=float(data.get("temperature", 0.7)),
        system_prompt=str(
            data.get(
                "system_prompt",
                _DEFAULT_LLM_SYSTEM_PROMPT,
            )
        ),
        max_turns=int(data.get("max_turns", 8)),
        session_ttl=int(data.get("session_ttl", 300)),
        session=LLMSessionConfig(
            persist=bool(session_raw.get("persist", True)),
            pruning_strategy=str(session_raw.get("pruning_strategy", "hybrid")),
            keep_recent_pairs=int(session_raw.get("keep_recent_pairs", 3)),
            max_context_tokens=int(session_raw.get("max_context_tokens", 4096)),
        ),
        memory=LLMMemoryConfig(
            episodic_enabled=bool(memory_raw.get("episodic_enabled", True)),
            episodic_max=int(memory_raw.get("episodic_max", 1000)),
            episodic_search_limit=int(memory_raw.get("episodic_search_limit", 5)),
            semantic_enabled=bool(memory_raw.get("semantic_enabled", True)),
            consolidation_enabled=bool(memory_raw.get("consolidation_enabled", True)),
        ),
        skills=LLMSkillsConfig(
            enabled=bool(skills_raw.get("enabled", True)),
            skills_dir=str(skills_raw.get("skills_dir", "config/skills")),
            auto_reload=bool(skills_raw.get("auto_reload", True)),
        ),
        prompts=LLMPromptConfig(
            enabled=bool(prompts_raw.get("enabled", False)),
            prompts_dir=str(prompts_raw.get("prompts_dir", "prompts")),
            profile=str(prompts_raw.get("profile", "default")),
        ),
        sandbox=SandboxConfig(
            enabled=bool(sandbox_raw.get("enabled", True)),
            container_name=str(sandbox_raw.get("container_name", "qqbot-sandbox")),
            workspace=str(sandbox_raw.get("workspace", "/workspace")),
            max_file_size=int(sandbox_raw.get("max_file_size", 1_048_576)),
            max_read_size=int(sandbox_raw.get("max_read_size", 524_288)),
            exec_timeout=int(sandbox_raw.get("exec_timeout", 30)),
            max_exec_timeout=int(sandbox_raw.get("max_exec_timeout", 60)),
            stdout_limit=int(sandbox_raw.get("stdout_limit", 102_400)),
        ),
    )


def _parse_logging_section(data: dict[str, Any]) -> LoggingConfigSection:
    return LoggingConfigSection(
        level=str(data.get("level", "INFO")),
        file=str(data.get("file", "logs/bot.log")),
        rotation=str(data.get("rotation", "10 MB")),
        retention=str(data.get("retention", "14 days")),
        json_format=bool(data.get("json_format", False)),
    )


def _parse_artifact_section(data: dict[str, Any]) -> ArtifactConfig:
    return ArtifactConfig(
        enabled=bool(data.get("enabled", True)),
        root_dir=str(data.get("root_dir", "data/artifacts")),
        max_file_size=int(data.get("max_file_size", 104_857_600)),
        download_timeout=int(data.get("download_timeout", 60)),
        auto_materialize=bool(data.get("auto_materialize", False)),
    )


def _parse_history_section(data: dict[str, Any]) -> HistoryConfig:
    return HistoryConfig(
        enabled=bool(data.get("enabled", True)),
        sync_on_connect=bool(data.get("sync_on_connect", True)),
        recent_contact_count=int(data.get("recent_contact_count", 50)),
        page_size=int(data.get("page_size", 20)),
        max_pages_per_sync=int(data.get("max_pages_per_sync", 3)),
        query_backfill=bool(data.get("query_backfill", True)),
    )


def _parse_tool_platform_section(data: dict[str, Any]) -> ToolPlatformConfig:
    return ToolPlatformConfig(
        default_timeout=float(data.get("default_timeout", 30.0)),
        invocation_persist=bool(data.get("invocation_persist", True)),
        max_result_bytes=int(data.get("max_result_bytes", 262_144)),
    )


def _parse_runtime_section(data: dict[str, Any]) -> RuntimeConfig:
    return RuntimeConfig(
        enabled=bool(data.get("enabled", True)),
        recover_on_start=bool(data.get("recover_on_start", True)),
    )


def _parse_scheduler_section(data: dict[str, Any]) -> SchedulerConfig:
    return SchedulerConfig(
        enabled=bool(data.get("enabled", True)),
        timezone=str(data.get("timezone", "Asia/Shanghai")),
        poll_interval=float(data.get("poll_interval", 1.0)),
        max_catch_up=int(data.get("max_catch_up", 10)),
    )


def _parse_workspace_section(data: dict[str, Any]) -> WorkspaceConfig:
    return WorkspaceConfig(
        enabled=bool(data.get("enabled", True)),
        root_dir=str(data.get("root_dir", "data/workspace")),
        max_file_size=int(data.get("max_file_size", 1_048_576)),
        max_read_size=int(data.get("max_read_size", 524_288)),
    )


def _parse_web_section(data: dict[str, Any]) -> WebConfig:
    default_search_url = "https://search.yahoo.com/search?p={query}"
    fallback_urls = data.get(
        "search_fallback_urls",
        [
            "https://www.bing.com/search?setlang={lang}&cc={country}"
            "&mkt={market}&q={query}",
            "https://html.duckduckgo.com/html/?q={query}",
        ],
    )
    return WebConfig(
        enabled=bool(data.get("enabled", True)),
        timeout=float(data.get("timeout", 20.0)),
        max_response_bytes=int(data.get("max_response_bytes", 2_097_152)),
        max_redirects=int(data.get("max_redirects", 3)),
        search_url=str(data.get("search_url", default_search_url)),
        news_search_url=str(
            data.get(
                "news_search_url",
                "https://www.bing.com/news/search?format=rss&setlang={lang}"
                "&cc={country}&mkt={market}&q={query}",
            )
        ),
        search_fallback_urls=[str(item) for item in fallback_urls],
        user_agent=str(data.get("user_agent", "qqbot-agent/1.0")),
    )


# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------


def _load_env(project_root: Path) -> dict[str, str]:
    env_path = project_root / ".env"
    if not env_path.exists():
        logger.warning(".env file not found, continuing without API keys")
        return {}

    try:
        values = dotenv_values(str(env_path))
        return {k: v for k, v in values.items() if v is not None}
    except Exception as exc:
        logger.warning(f"Failed to load .env: {exc}")
        return {}


# ---------------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------------


def load_config(
    config_dir: str | None = None,
    env_file: str | None = None,
) -> BotConfig:
    """Load and validate bot configuration from bot.toml and .env.

    Parameters
    ----------
    config_dir:
        Path to the directory containing ``bot.toml``.
        If *None*, auto-detects from the project root.
    env_file:
        Path to the ``.env`` file. If *None*, uses ``<project_root>/.env``.

    Returns
    -------
    BotConfig
        Fully populated, type-safe configuration object.

    Raises
    ------
    FileNotFoundError
        If ``bot.toml`` cannot be found.
    ValueError
        If a required field is missing.
    """
    if config_dir is not None:
        project_root = Path(config_dir)
    else:
        project_root = _find_project_root()
        config_dir = str(project_root / "config")

    config_path = Path(config_dir) / "bot.toml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "rb") as fh:
        raw = tomllib.load(fh)

    bot = _parse_bot_section(raw.get("bot", {}))
    napcat = _parse_napcat_section(raw.get("napcat", {}))
    plugin = _parse_plugin_section(raw.get("plugin", {}))

    # Parse [plugin_config.<id>] sections into PluginConfig.plugin_configs.
    plugin_configs_raw = raw.get("plugin_config", {})
    if isinstance(plugin_configs_raw, dict):
        for pid, cfg in plugin_configs_raw.items():
            if isinstance(cfg, dict):
                plugin.plugin_configs[pid] = cfg
    logging = _parse_logging_section(raw.get("logging", {}))
    filter_cfg = _parse_filter_section(raw.get("filter", {}))
    action_queue = _parse_action_queue_section(
        raw.get("plugin", {}).get("action_queue", {})
    )
    llm = _parse_llm_section(raw.get("llm", {}))
    artifacts = _parse_artifact_section(raw.get("artifacts", {}))
    history = _parse_history_section(raw.get("history", {}))
    tools = _parse_tool_platform_section(raw.get("tools", {}))
    runtime = _parse_runtime_section(raw.get("runtime", {}))
    scheduler = _parse_scheduler_section(raw.get("scheduler", {}))
    workspace = _parse_workspace_section(raw.get("workspace", {}))
    web = _parse_web_section(raw.get("web", {}))

    env_path = env_file if env_file else str(project_root / ".env")
    env = _load_env(Path(env_path).parent) if env_file else _load_env(project_root)

    # Override api_key from .env (LLM_API_KEY first, then DEEPSEEK_API_KEY)
    if not llm.api_key:
        for key in ("LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
            if env.get(key):
                llm.api_key = env[key]
                break

    return BotConfig(
        bot=bot,
        napcat=napcat,
        plugin=plugin,
        logging=logging,
        filter=filter_cfg,
        action_queue=action_queue,
        llm=llm,
        artifacts=artifacts,
        history=history,
        tools=tools,
        runtime=runtime,
        scheduler=scheduler,
        workspace=workspace,
        web=web,
        env=env,
    )
