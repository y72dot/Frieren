"""Tests for composable, versioned prompt profiles."""

import tempfile
from pathlib import Path

import pytest

from src.core.bot import Bot
from src.core.config import (
    BotConfig,
    BotConfigSection,
    LLMConfig,
    LLMPromptConfig,
    LoggingConfigSection,
    NapCatConfig,
    PluginConfig,
)
from src.core.prompts import PromptRegistry


def _write_registry(tmp_path, manifest: str, parts: dict[str, str]) -> None:
    (tmp_path / "manifest.toml").write_text(manifest, encoding="utf-8")
    for name, content in parts.items():
        (tmp_path / f"{name}.md").write_text(content, encoding="utf-8")


def test_load_render_and_profile_inheritance() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_registry(
            root,
            '''
version = "test-v1"
[profiles.default]
parts = ["identity", "rules"]
[profiles.planner]
extends = "default"
append = ["planner"]
''',
            {
                "identity": "I am ${bot_name} (${bot_qq}).",
                "rules": "Keep facts intact.",
                "planner": "Plan before acting.",
            },
        )
        registry = PromptRegistry.load(root)
        rendered = registry.render(
            "planner", {"bot_name": "Frieren", "bot_qq": 123456}
        )
        assert rendered.version == "test-v1"
        assert rendered.parts == ("identity", "rules", "planner")
        assert "I am Frieren (123456)." in rendered.text
        assert rendered.text.endswith("Plan before acting.")
        assert len(rendered.sha256) == 64


def test_unknown_template_variables_are_preserved() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_registry(
            root,
            'version = "v1"\n[profiles.default]\nparts = ["identity"]\n',
            {"identity": "Hello ${missing}"},
        )
        rendered = PromptRegistry.load(root).render("default")
        assert rendered.text == "Hello ${missing}"


def test_missing_prompt_part_fails_validation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_registry(
            root,
            'version = "v1"\n[profiles.default]\nparts = ["missing"]\n',
            {},
        )
        with pytest.raises(FileNotFoundError, match="missing.md"):
            PromptRegistry.load(root)


def test_profile_inheritance_cycle_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_registry(
            root,
            '''
version = "v1"
[profiles.a]
extends = "b"
parts = ["x"]
[profiles.b]
extends = "a"
parts = ["x"]
''',
            {"x": "x"},
        )
        with pytest.raises(ValueError, match="inheritance cycle"):
            PromptRegistry.load(root)


def test_project_default_prompt_registry_is_valid() -> None:
    registry = PromptRegistry.load("config/prompts")
    rendered = registry.render(
        "default",
        {
            "bot_qq": 123456,
            "bot_name": "芙莉莲",
            "conversation_type": "group",
            "conversation_id": 789,
        },
    )
    assert registry.version == "2026.07.1"
    assert "原始 CQ 码" in rendered.text
    assert "123456" in rendered.text
    assert "${" not in rendered.text


def test_bot_uses_enabled_prompt_registry() -> None:
    config = BotConfig(
        bot=BotConfigSection(qq=123456, nickname=["芙莉莲"]),
        napcat=NapCatConfig(),
        plugin=PluginConfig(auto_discover=False),
        logging=LoggingConfigSection(),
        llm=LLMConfig(
            prompts=LLMPromptConfig(
                enabled=True,
                prompts_dir="config/prompts",
                profile="default",
            )
        ),
    )

    bot = Bot(config=config)

    assert bot.config_center.config is config
    assert bot.prompt_registry.version == "2026.07.1"
    rendered = bot.prompt_registry.render(
        config.llm.prompts.profile,
        {
            "bot_qq": config.bot.qq,
            "bot_name": config.bot.nickname[0],
            "conversation_type": "private",
            "conversation_id": 1,
        },
    )
    snapshot = bot.config_center.create_snapshot(
        prompt_version=rendered.version,
        prompt_text=rendered.text,
        context_key="private:1",
    )
    assert bot.config_center.get_snapshot(snapshot.snapshot_id) == snapshot
