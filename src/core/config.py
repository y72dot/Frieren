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
            "send_group_poke",
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
class LLMConfig:
    enabled: bool = False
    api_base: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    max_tokens: int = 1024
    temperature: float = 0.7
    system_prompt: str = "你是一个友好的QQ群聊助手。请用简洁自然的中文回复，保持轻松愉快的语气。"
    max_turns: int = 5


@dataclass
class BotConfig:
    bot: BotConfigSection
    napcat: NapCatConfig
    plugin: PluginConfig
    logging: LoggingConfigSection
    filter: FilterConfig = field(default_factory=FilterConfig)
    action_queue: ActionQueueConfig = field(default_factory=ActionQueueConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
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
                    "send_group_poke",
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
                "你是一个友好的QQ群聊助手。请用简洁自然的中文回复，保持轻松愉快的语气。",
            )
        ),
        max_turns=int(data.get("max_turns", 5)),
    )


def _parse_logging_section(data: dict[str, Any]) -> LoggingConfigSection:
    return LoggingConfigSection(
        level=str(data.get("level", "INFO")),
        file=str(data.get("file", "logs/bot.log")),
        rotation=str(data.get("rotation", "10 MB")),
        retention=str(data.get("retention", "14 days")),
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
    logging = _parse_logging_section(raw.get("logging", {}))
    filter_cfg = _parse_filter_section(raw.get("filter", {}))
    action_queue = _parse_action_queue_section(
        raw.get("plugin", {}).get("action_queue", {})
    )
    llm = _parse_llm_section(raw.get("llm", {}))

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
        env=env,
    )
