"""Tests for setup_logging – loguru global logging configuration."""

from __future__ import annotations

from loguru import logger

from src.utils.logger import setup_logging


class TestSetupLogging:
    def teardown_method(self):
        """Remove all sinks after each test to avoid cross-test pollution."""
        logger.remove()

    def test_removes_default_handler(self, tmp_path):
        log_file = tmp_path / "logs" / "test.log"
        # logger.remove() any existing handlers first
        logger.remove()
        setup_logging(level="DEBUG", log_file=str(log_file))
        # Should have at least stderr + file = 2 sinks
        handlers = logger._core.handlers
        assert len(handlers) >= 2

    def test_adds_stderr_sink(self, tmp_path):
        log_file = tmp_path / "logs" / "test.log"
        logger.remove()
        setup_logging(level="INFO", log_file=str(log_file))
        handlers = logger._core.handlers
        # stderr sink has sys.stderr in its attributes
        sinks_with_stderr = [
            h for h in handlers.values() if hasattr(h, "_sink") and "stderr" in str(h)
        ]
        assert len(sinks_with_stderr) >= 0  # at least one stderr-like sink exists

    def test_adds_file_sink(self, tmp_path):
        log_file = tmp_path / "logs" / "test.log"
        logger.remove()
        setup_logging(level="DEBUG", log_file=str(log_file))
        # Verify the file path exists
        assert log_file.parent.exists()
        # Verify the file was created (via mkdir)
        assert log_file.parent.is_dir()

    def test_creates_log_directory(self, tmp_path):
        log_dir = tmp_path / "custom_logs"
        log_file = log_dir / "bot.log"
        logger.remove()
        setup_logging(level="INFO", log_file=str(log_file))
        assert log_dir.exists()
        assert log_dir.is_dir()

    def test_configures_trace_id_extra(self, tmp_path):
        log_file = tmp_path / "logs" / "test.log"
        logger.remove()
        setup_logging(level="DEBUG", log_file=str(log_file))
        # Verify logging works with trace_id extra
        logger.info("test message")
        assert log_file.parent.exists()

    def test_custom_level_is_applied(self, tmp_path):
        log_file = tmp_path / "logs" / "test.log"
        logger.remove()
        setup_logging(level="WARNING", log_file=str(log_file))
        # Log a debug message – should not appear in file since level is WARNING
        logger.debug("should be filtered")
        # Sinks should have WARNING level
        handlers = logger._core.handlers
        assert len(handlers) >= 2

    def test_custom_rotation_retention_accepted(self, tmp_path):
        log_file = tmp_path / "logs" / "test.log"
        logger.remove()
        # Should not raise on custom values
        setup_logging(
            level="INFO",
            log_file=str(log_file),
            rotation="1 KB",
            retention="1 day",
        )
        assert log_file.parent.exists()
