from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.core.bot import Bot
from src.core.control_plane import ControlPlane
from src.core.control_plane._coordinator import ActivationReport
from src.core.prompts import PromptRegistry


def _prompts(root: Path) -> None:
    root.mkdir()
    (root / "manifest.toml").write_text(
        'version = "v1"\n[profiles.default]\nparts = ["identity"]\n', encoding="utf-8"
    )
    (root / "identity.md").write_text("old identity", encoding="utf-8")


@pytest.mark.asyncio
async def test_setting_proposal_does_not_self_apply_and_sensitive_paths_are_hidden(
    bot_config, tmp_path
):
    bot = Bot(config=bot_config)
    prompts = tmp_path / "prompts"
    _prompts(prompts)
    control = ControlPlane(
        bot,
        sqlite3.connect(":memory:"),
        prompts_dir=prompts,
        candidate_dir=tmp_path / "candidates",
        plugin_dir=tmp_path / "plugins",
    )
    original = bot.config.llm.temperature
    proposal = control.propose_settings(
        {"llm.temperature": 0.25}, created_by=1, reason="test"
    )
    assert proposal.status == "pending"
    assert bot.config.llm.temperature == original
    assert control.list_proposals(status="pending") == [proposal]
    with pytest.raises(PermissionError):
        control.get_setting("llm.api_key")
    with pytest.raises(PermissionError):
        control.propose_settings({"bot.admin_users": []}, created_by=1)

    applied = await control.approve_and_apply(proposal.proposal_id, approved_by=999)
    assert applied.status == "applied"
    assert bot.config.llm.temperature == 0.25
    assert bot.config_center.settings_version == 2


@pytest.mark.asyncio
async def test_invalid_setting_candidate_never_replaces_effective_config(bot_config, tmp_path):
    bot = Bot(config=bot_config)
    prompts = tmp_path / "prompts"
    _prompts(prompts)
    control = ControlPlane(bot, sqlite3.connect(":memory:"), prompts_dir=prompts)
    with pytest.raises(TypeError):
        control.propose_settings({"scheduler.poll_interval": "fast"}, created_by=1)
    proposal = control.propose_settings(
        {"scheduler.poll_interval": -1.0}, created_by=1
    )
    with pytest.raises(ValueError, match="scheduler limits"):
        await control.approve_and_apply(proposal.proposal_id, approved_by=999)
    assert bot.config.scheduler.poll_interval > 0
    assert control.get(proposal.proposal_id).status == "failed"


@pytest.mark.asyncio
async def test_prompt_apply_is_versioned_validated_and_reloadable(bot_config, tmp_path):
    bot = Bot(config=bot_config)
    prompts = tmp_path / "prompts"
    _prompts(prompts)
    control = ControlPlane(bot, sqlite3.connect(":memory:"), prompts_dir=prompts)
    proposal = control.propose_prompt(
        "identity", "new identity", version="v2", created_by=1
    )
    assert (prompts / "identity.md").read_text(encoding="utf-8") == "old identity"
    await control.approve_and_apply(proposal.proposal_id, approved_by=999)
    assert PromptRegistry.load(prompts).version == "v2"
    assert bot.prompt_registry.render().text == "new identity"


def _candidate(
    root: Path, folder: str, source: str, *, version: str = "1.0.0"
) -> Path:
    candidate = root / folder
    candidate.mkdir(parents=True)
    (candidate / "plugin.toml").write_text(
        "[plugin]\n"
        'id = "demo_plugin"\n'
        'name = "Demo Plugin"\n'
        f'version = "{version}"\n'
        'entrypoint = "plugins.demo_plugin.plugin:DemoPlugin"\n'
        'sdk = ">=1.0,<2.0"\n',
        encoding="utf-8",
    )
    (candidate / "plugin.py").write_text(source, encoding="utf-8")
    return candidate


class _SuccessfulCoordinator:
    async def execute(self, request):
        return ActivationReport(
            deployment_id=request.deployment_id,
            plugin_id=request.plugin_id,
            version=request.version,
            success=True,
        )

    async def reconcile(self, plugin_id, **kwargs):
        return ActivationReport(
            deployment_id="",
            plugin_id=plugin_id,
            version=kwargs.get("expected_version", ""),
            success=True,
        )


class _FailingCoordinator(_SuccessfulCoordinator):
    async def execute(self, request):
        return ActivationReport(
            deployment_id=request.deployment_id,
            plugin_id=request.plugin_id,
            version=request.version,
            success=False,
            error="target plugin did not become active",
        )

@pytest.mark.asyncio
async def test_plugin_static_validation_install_and_durable_rollback(bot_config, tmp_path):
    bot = Bot(config=bot_config)
    prompts = tmp_path / "prompts"
    _prompts(prompts)
    candidates = tmp_path / "candidates"
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    control = ControlPlane(
        bot,
        sqlite3.connect(":memory:"),
        prompts_dir=prompts,
        candidate_dir=candidates,
        plugin_dir=plugins,
        coordinator=_SuccessfulCoordinator(),
    )
    _candidate(candidates, "bad", "import subprocess\n")
    report = control.validate_plugin_candidate("bad")
    assert report["valid"] is False
    assert "forbidden import subprocess" in report["violations"][0]
    with pytest.raises(ValueError, match="failed validation"):
        control.propose_plugin_install("bad", created_by=1)

    _candidate(candidates, "good", "VALUE = 'v1'\n")
    first = control.propose_plugin_install("good", created_by=1)
    await control.approve_and_apply(first.proposal_id, approved_by=999)
    # Multi-file packages are deployed as directories under plugins/<name>/
    target_dir = plugins / "demo_plugin"
    target_file = target_dir / "plugin.py"
    assert target_dir.is_dir()
    assert "v1" in target_file.read_text(encoding="utf-8")
    _candidate(candidates, "next", "VALUE = 'v2'\n", version="2.0.0")
    second = control.propose_plugin_install("next", created_by=1)
    await control.approve_and_apply(second.proposal_id, approved_by=999)
    assert target_dir.is_dir()
    assert "v2" in target_file.read_text(encoding="utf-8")
    rollback = control.propose_plugin_rollback("demo_plugin", created_by=1)
    await control.approve_and_apply(rollback.proposal_id, approved_by=999)
    assert target_dir.is_dir()
    assert "v1" in target_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_failed_activation_restores_files_and_fails_proposal(
    bot_config, tmp_path
):
    bot = Bot(config=bot_config)
    prompts = tmp_path / "prompts"
    _prompts(prompts)
    candidates = tmp_path / "candidates"
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    existing = _candidate(plugins, "demo_plugin", "VALUE = 'old'\n")
    candidate = _candidate(
        candidates,
        "upgrade",
        "VALUE = 'new'\n",
        version="2.0.0",
    )
    control = ControlPlane(
        bot,
        sqlite3.connect(":memory:"),
        prompts_dir=prompts,
        candidate_dir=candidates,
        plugin_dir=plugins,
        coordinator=_FailingCoordinator(),
    )

    proposal = control.propose_plugin_install(candidate.name, created_by=1)
    with pytest.raises(RuntimeError, match="did not become active"):
        await control.approve_and_apply(proposal.proposal_id, approved_by=999)

    assert "old" in (existing / "plugin.py").read_text(encoding="utf-8")
    assert control.get(proposal.proposal_id).status == "failed"
    deployment = control.connection.execute(
        "SELECT status, deployment_phase FROM plugin_deployments "
        "WHERE name='demo_plugin' ORDER BY installed_at DESC LIMIT 1"
    ).fetchone()
    assert deployment == ("rolled_back", "rolled_back")


@pytest.mark.asyncio
async def test_plugin_state_is_persisted_only_after_runtime_success(
    bot_config, tmp_path
):
    bot = Bot(config=bot_config)
    prompts = tmp_path / "prompts"
    _prompts(prompts)
    control = ControlPlane(
        bot,
        sqlite3.connect(":memory:"),
        prompts_dir=prompts,
        coordinator=_SuccessfulCoordinator(),
    )
    proposal = control.propose_plugin_state("demo_plugin", False, created_by=1)

    result = await control.approve_and_apply(
        proposal.proposal_id, approved_by=999
    )

    assert result.status == "applied"
    assert "demo_plugin" in bot.config.plugin.disabled_plugins
    assert bot.config_center.get("plugin.disabled_plugins") == ["demo_plugin"]


@pytest.mark.asyncio
async def test_plugin_state_failure_restores_persisted_config(
    bot_config, tmp_path
):
    bot = Bot(config=bot_config)
    prompts = tmp_path / "prompts"
    _prompts(prompts)
    control = ControlPlane(
        bot,
        sqlite3.connect(":memory:"),
        prompts_dir=prompts,
        coordinator=_FailingCoordinator(),
    )
    proposal = control.propose_plugin_state("demo_plugin", False, created_by=1)

    with pytest.raises(RuntimeError, match="did not become active"):
        await control.approve_and_apply(proposal.proposal_id, approved_by=999)

    assert control.get(proposal.proposal_id).status == "failed"
    assert "demo_plugin" not in bot.config.plugin.disabled_plugins
    assert bot.config_center.get("plugin.disabled_plugins") == []
