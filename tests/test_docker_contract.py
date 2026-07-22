from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def test_production_compose_builds_runtime_stage():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    production = compose.split("  qqbot-frieren:", 1)[1].split("  e2e:", 1)[0]
    assert "target: runtime" in production
    assert 'python", "scripts/run_e2e.py' not in production


def test_test_service_builds_test_stage():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    service = compose.split("  e2e:", 1)[1].split("  sandbox:", 1)[0]
    assert 'profiles: ["test"]' in service
    assert "target: test" in service


def test_docker_context_includes_only_non_secret_test_config():
    ignore_path = ROOT / ".dockerignore"
    if ignore_path.exists():
        ignored = ignore_path.read_text(encoding="utf-8")
        assert "config/*" in ignored
        assert "!config/prompts/**" in ignored
        assert "!config/performance_baseline.json" in ignored
        assert ".env" in ignored
        assert "instances/" in ignored
    else:
        # Inside the built test image, verify the resulting context rather than
        # copying the ignore policy itself into the image.
        assert (ROOT / "config" / "bot.toml").is_file()
        assert (ROOT / "config" / "prompts" / "manifest.toml").is_file()
        assert not (ROOT / ".env").exists()
        assert not (ROOT / "instances").exists()


def test_sandbox_has_a_long_running_command():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    sandbox = compose.split("  sandbox:", 1)[1].split("\nvolumes:", 1)[0]
    assert 'command: ["sleep", "infinity"]' in sandbox


def test_deployed_instance_enables_llm_when_present():
    instance = ROOT / "instances" / "frieren" / "bot.toml"
    if not instance.exists():
        pytest.skip("deployment instance is excluded from the Docker test image")
    import tomllib

    config = tomllib.loads(instance.read_text(encoding="utf-8"))
    assert config["llm"]["enabled"] is True
    assert config["llm"]["api_base"] == "https://api.deepseek.com"
    assert config["llm"]["model"]
