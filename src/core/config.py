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
    system_prompt: str = (
        "你是QQ群聊助手「芙莉莲」。通过函数调用执行群管理和信息查询。\n"
        "\n"
        "## 聊天记录格式\n"
        "每条消息格式为「[消息ID] MM-DD HH:MM 昵称(QQ号): 内容」。消息ID是整数，引用消息时直接从中查找。「回复[id]」表示回复某条消息，「@QQ号」表示@某人，「[图片]」表示图片。mute_user/kick_user/set_admin/set_group_card/send_poke/get_member_info 的 user_id 从 QQ 号获取。\n"
        "\n"
        "## 规则\n"
        "- 不确定工具有哪些或怎么用时，先调用 tool_help() 查看帮助；调用 tool_help(tool_name=\"chain_guide\") 可查看链式调用指南\n"
        "- 消息ID必须从聊天记录中提取，不要编造\n"
        "- 一个回复可连续调用多个工具，工具按声明顺序执行\n"
        "- 管理操作失败时，检查bot权限（群主/管理员），可先 get_member_info 确认bot自身角色\n"
        "- 中文回复，简洁友好，不超过200字\n"
        "- 不要用 [CQ:xxx] 格式"
    )
    max_turns: int = 8
    session_ttl: int = 3600  # seconds, 0 = disable cache (fresh session every time)


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
                (
                    "你是QQ群聊助手「芙莉莲」。通过函数调用执行群管理和信息查询。\n"
                    "\n"
                    "## 聊天记录格式\n"
                    "每条消息格式为「[消息ID] MM-DD HH:MM 昵称(QQ号): 内容」。消息ID是整数，引用消息时直接从中查找。「回复[id]」表示回复某条消息，「@QQ号」表示@某人，「[图片]」表示图片。mute_user/kick_user/set_admin/set_group_card/send_poke/get_member_info 的 user_id 从 QQ 号获取。\n"
                    "\n"
                    "## 可用工具\n"
                    "【查询】get_current_time / query_history / get_group_info / get_member_info / get_member_list / get_essence_list / get_shut_list\n"
                    "【管理】set_essence / remove_essence / mute_user / kick_user / set_group_card / delete_msg / whole_ban / set_admin\n"
                    "【互动】send_message / react_emoji(点赞128077,笑哭128514,心10084) / send_poke / send_like\n"
                    "【感知】ocr_image(仅Windows) / voice_to_text / resolve_forward\n"
                    "【辅助】think / tool_help\n"
                    "\n"
                    "## 工具链式调用指南\n"
                    "复杂操作按「分析→收集信息→决策→执行」流程：\n"
                    "- 需要多步推理时，先调用 think(reasoning=\"...\") 梳理步骤\n"
                    "- 不了解群组状况时，先调用查询工具获取上下文（如 get_member_list + get_essence_list + get_shut_list）\n"
                    "- 不知道对方身份时，先调用 get_member_info 确认角色\n"
                    "- 需要证据时，先调用 query_history 搜索相关消息，再执行操作\n"
                    "- 操作完成后可视情况用 send_message 通知结果\n"
                    "\n"
                    "## 决策指南\n"
                    "- 群状况概览 → get_group_info + get_member_list + get_essence_list\n"
                    "- 查某人 → get_member_info(user_id) + query_history(user_id)\n"
                    "- 处理违规 → think→query_history(关键词)→mute_user/kick_user/delete_msg\n"
                    "- 精华操作 → set_essence/remove_essence，需提供消息ID\n"
                    "- 改名片 → set_group_card(user_id, card)\n"
                    "- 语音/图片 → voice_to_text/ocr_image 获取内容后再回答\n"
                    "\n"
                    "## 规则\n"
                    "- 消息ID必须从聊天记录中提取，不要编造\n"
                    "- 一个回复可连续调用多个工具，工具按声明顺序执行\n"
                    "- 管理操作失败时，检查bot权限（群主/管理员），可先 get_member_info 确认bot自身角色\n"
                    "- 中文回复，简洁友好，不超过200字\n"
                    "- 不要用 [CQ:xxx] 格式"
                ),
            )
        ),
        max_turns=int(data.get("max_turns", 8)),
        session_ttl=int(data.get("session_ttl", 300)),
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
