"""Tests for unified configuration access and snapshot persistence."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.core.config import (
    BotConfig,
    BotConfigSection,
    LLMConfig,
    LoggingConfigSection,
    NapCatConfig,
    PluginConfig,
)
from src.core.config_center import ConfigCenter


def _config() -> BotConfig:
    return BotConfig(
        bot=BotConfigSection(qq=123456, nickname=["test"], admin_users=[1]),
        napcat=NapCatConfig(token="napcat-secret"),
        plugin=PluginConfig(auto_discover=False),
        logging=LoggingConfigSection(),
        llm=LLMConfig(api_key="llm-secret", model="test-model"),
        env={"SEARCH_API_KEY": "search-secret"},
    )


def test_dotted_get_and_task_override() -> None:
    center = ConfigCenter(_config())
    assert center.get("llm.model") == "test-model"
    assert center.get("missing.path", "fallback") == "fallback"
    assert center.get("llm.model", overrides={"llm.model": "override"}) == "override"


def test_snapshot_redacts_secrets_and_persists() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "config_state.db"
        center = ConfigCenter(_config(), db_path=str(db_path))
        snapshot = center.create_snapshot(
            prompt_version="v1",
            prompt_text="system prompt",
            context_key="group:123",
        )
        assert center.get_snapshot(snapshot.snapshot_id) == snapshot
        content = json.loads(snapshot.effective_config_json)
        assert content["llm"]["api_key"] == "***REDACTED***"
        assert content["napcat"]["token"] == "***REDACTED***"
        assert content["env"] == {"values": "***REDACTED***"}
        assert "llm-secret" not in snapshot.effective_config_json
        assert snapshot.prompt_hash

        center.close()
        reopened = ConfigCenter(_config(), db_path=str(db_path))
        assert reopened.get_snapshot(snapshot.snapshot_id) == snapshot
        reopened.close()


def test_replace_config_advances_version() -> None:
    center = ConfigCenter(_config())
    replacement = _config()
    replacement.llm.model = "next-model"
    center.replace_config(replacement)
    assert center.settings_version == 2
    assert center.get("llm.model") == "next-model"


def test_runtime_setting_override_survives_restart(tmp_path) -> None:
    db_path = tmp_path / "config_state.db"
    first = ConfigCenter(_config(), db_path=str(db_path))
    replacement = _config()
    replacement.llm.temperature = 0.2
    first.replace_config(replacement, changes={"llm.temperature": 0.2})
    first.close()

    reopened = ConfigCenter(_config(), db_path=str(db_path))
    assert reopened.get("llm.temperature") == 0.2
    with pytest.raises(PermissionError):
        reopened.replace_config(_config(), changes={"llm.api_key": "no"})
    reopened.close()
