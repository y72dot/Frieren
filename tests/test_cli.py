"""Tests for Plugin CLI and Simulator (PLUG-504)."""

from __future__ import annotations

from pathlib import Path

from src.plugin.cli import main as cli_main
from src.plugin.simulator import make_fake_event

# ---------------------------------------------------------------------------
# CLI: new
# ---------------------------------------------------------------------------


class TestCliNew:
    def test_new_creates_plugin_directory(self, tmp_path: Path, monkeypatch):
        """`new` creates plugin directory with correct files."""
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        # Replace Path("plugins") to point to our tmp.
        import src.plugin.cli as cli_mod
        orig_path = cli_mod.Path
        monkeypatch.setattr(cli_mod, "Path", lambda p: tmp_path / p)

        exit_code = cli_main(["new", "hello"])
        assert exit_code == 0

        plugin_dir = tmp_path / "plugins" / "hello"
        assert plugin_dir.is_dir()
        assert (plugin_dir / "plugin.toml").exists()
        assert (plugin_dir / "plugin.py").exists()
        assert (plugin_dir / "__init__.py").exists()

        # Verify toml content.
        toml_text = (plugin_dir / "plugin.toml").read_text()
        assert 'id = "hello"' in toml_text
        assert 'entrypoint = "hello.plugin:plugin"' in toml_text

    def test_new_rejects_invalid_name(self, tmp_path: Path, monkeypatch):
        """`new` rejects invalid plugin name (not snake_case)."""
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        import src.plugin.cli as cli_mod
        monkeypatch.setattr(cli_mod, "Path", lambda p: tmp_path / p)

        exit_code = cli_main(["new", "Invalid-Name"])
        assert exit_code == 1

    def test_new_skips_existing_directory(self, tmp_path: Path, monkeypatch):
        """`new` returns error if plugin directory already exists."""
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        (plugins_dir / "hello").mkdir()
        monkeypatch.chdir(tmp_path)

        import src.plugin.cli as cli_mod
        monkeypatch.setattr(cli_mod, "Path", lambda p: tmp_path / p)

        exit_code = cli_main(["new", "hello"])
        assert exit_code == 1


# ---------------------------------------------------------------------------
# CLI: validate
# ---------------------------------------------------------------------------


class TestCliValidate:
    def test_validate_valid_manifest(self, tmp_path: Path):
        """`validate` succeeds for a valid plugin.toml."""
        plugin_dir = tmp_path / "valid_plugin"
        plugin_dir.mkdir()
        toml_path = plugin_dir / "plugin.toml"
        toml_path.write_text("""\
[plugin]
id = "valid"
version = "1.0.0"
entrypoint = "valid.plugin:plugin"
sdk = ">=1.0,<2.0"
description = "A valid plugin"
""")

        exit_code = cli_main(["validate", str(toml_path)])
        assert exit_code == 0

    def test_validate_directory(self, tmp_path: Path):
        """`validate` works when given a directory containing plugin.toml."""
        plugin_dir = tmp_path / "plugin_dir"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text("""\
[plugin]
id = "dir_plugin"
version = "0.1.0"
entrypoint = "dir_plugin.main:plugin"
sdk = ">=1.0,<2.0"
""")

        exit_code = cli_main(["validate", str(plugin_dir)])
        assert exit_code == 0

    def test_validate_reports_errors(self, tmp_path: Path):
        """`validate` returns non-zero for malformed manifest."""
        plugin_dir = tmp_path / "bad_plugin"
        plugin_dir.mkdir()
        toml_path = plugin_dir / "plugin.toml"
        toml_path.write_text("""\
[plugin]
id = ""
version = "not-a-version"
entrypoint = ""
sdk = ""
""")

        exit_code = cli_main(["validate", str(toml_path)])
        assert exit_code == 1

    def test_validate_nonexistent_path(self):
        """`validate` returns error for non-existent path."""
        exit_code = cli_main(["validate", "/nonexistent/path/plugin.toml"])
        assert exit_code == 1


# ---------------------------------------------------------------------------
# CLI: list
# ---------------------------------------------------------------------------


class TestCliList:
    def test_list_discovers_plugins(self, tmp_path: Path, monkeypatch):
        """`list` outputs discovered plugins from the default plugins dir."""
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        # Create one valid package plugin
        p_dir = plugins_dir / "test_plugin"
        p_dir.mkdir()
        (p_dir / "plugin.toml").write_text("""\
[plugin]
id = "test_plugin"
version = "1.0.0"
entrypoint = "test_plugin.main:plugin"
sdk = ">=1.0,<2.0"
""")

        monkeypatch.chdir(tmp_path)
        exit_code = cli_main(["list", "--plugin-dirs", str(plugins_dir)])
        assert exit_code == 0


# ---------------------------------------------------------------------------
# CLI: doctor
# ---------------------------------------------------------------------------


class TestCliDoctor:
    def test_doctor_reports_healthy_setup(self, tmp_path: Path, monkeypatch):
        """`doctor` succeeds when plugins are valid."""
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        p_dir = plugins_dir / "healthy"
        p_dir.mkdir()
        (p_dir / "plugin.toml").write_text("""\
[plugin]
id = "healthy"
version = "1.0.0"
entrypoint = "healthy.main:plugin"
sdk = ">=1.0,<2.0"
""")

        monkeypatch.chdir(tmp_path)
        exit_code = cli_main(["doctor", "--plugin-dirs", str(plugins_dir)])
        assert exit_code == 0


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------


class TestSimulator:
    def test_make_fake_event_group_message(self):
        """make_fake_event creates a group message Event."""
        event = make_fake_event(
            event_type="message.group",
            group_id=456,
            user_id=789,
            message="/hello world",
        )
        assert event.type == "message.group"
        assert event.group_id == 456
        assert event.user_id == 789
        assert event.message == "/hello world"
        assert event.is_group is True

    def test_make_fake_event_private_message(self):
        """make_fake_event creates a private message Event."""
        event = make_fake_event(
            event_type="message.private",
            group_id=None,
            user_id=111,
            message="hello",
        )
        assert event.type == "message.private"
        assert event.is_group is False
        assert event.group_id is None
