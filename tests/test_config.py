"""Tests for config loading and validation."""

import tempfile
from pathlib import Path

import pytest

from src.core.config import (
    BotConfig,
    BotConfigSection,
    LoggingConfigSection,
    NapCatConfig,
    PluginConfig,
    load_config,
)


def _write_toml(content: str, dir_path: Path) -> Path:
    config_dir = dir_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "bot.toml"
    config_file.write_text(content, encoding="utf-8")
    return config_file


def _write_pyproject(dir_path: Path) -> None:
    (dir_path / "pyproject.toml").write_text("[project]\nname='test'\n")


# -------------------------------------------------------------------
# valid config
# -------------------------------------------------------------------


def test_load_valid_config():
    content = """\
[bot]
qq = 123456
nickname = ["test"]
admin_users = [111]

[napcat]
ws_url = "ws://127.0.0.1:3001"
token = "abc"

[plugin]
auto_discover = true
plugin_dirs = ["plugins"]

[logging]
level = "DEBUG"
"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_pyproject(root)
        _write_toml(content, root)

        cfg = load_config(config_dir=str(root / "config"))
        assert cfg.bot.qq == 123456
        assert cfg.bot.nickname == ["test"]
        assert cfg.napcat.ws_url == "ws://127.0.0.1:3001"
        assert cfg.napcat.token == "abc"
        assert cfg.logging.level == "DEBUG"


# -------------------------------------------------------------------
# missing config file
# -------------------------------------------------------------------


def test_missing_config_file_raises():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_pyproject(root)
        with pytest.raises(FileNotFoundError):
            load_config(config_dir=str(root / "nonexistent"))


# -------------------------------------------------------------------
# missing required fields
# -------------------------------------------------------------------


def test_missing_qq_raises():
    content = """\
[bot]
nickname = ["test"]

[napcat]
ws_url = "ws://127.0.0.1:3001"
"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_pyproject(root)
        _write_toml(content, root)

        with pytest.raises(ValueError, match="qq"):
            load_config(config_dir=str(root / "config"))


# -------------------------------------------------------------------
# ws_host + ws_port fallback
# -------------------------------------------------------------------


def test_ws_host_port_fallback():
    content = """\
[bot]
qq = 123

[napcat]
ws_host = "10.0.0.1"
ws_port = 9090
"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_pyproject(root)
        _write_toml(content, root)

        cfg = load_config(config_dir=str(root / "config"))
        assert cfg.napcat.ws_url == "ws://10.0.0.1:9090"


# -------------------------------------------------------------------
# malformed toml
# -------------------------------------------------------------------


def test_malformed_toml():
    content = "this is not valid toml [[["
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_pyproject(root)
        config_dir = root / "config"
        config_dir.mkdir()
        (config_dir / "bot.toml").write_text(content, encoding="utf-8")

        with pytest.raises(Exception):
            load_config(config_dir=str(config_dir))


# -------------------------------------------------------------------
# env loading (without .env file)
# -------------------------------------------------------------------


def test_no_env_file():
    content = """\
[bot]
qq = 123

[napcat]
ws_url = "ws://127.0.0.1:3001"
"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_pyproject(root)
        _write_toml(content, root)

        cfg = load_config(config_dir=str(root / "config"))
        assert cfg.env == {}


# -------------------------------------------------------------------
# data class defaults
# -------------------------------------------------------------------


def test_default_sections():
    bc = BotConfig(
        bot=BotConfigSection(qq=123),
        napcat=NapCatConfig(),
        plugin=PluginConfig(),
        logging=LoggingConfigSection(),
    )
    assert bc.bot.qq == 123
    assert bc.napcat.ws_url == "ws://127.0.0.1:3001"
    assert bc.plugin.auto_discover is True
    assert bc.logging.level == "INFO"
