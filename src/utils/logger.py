import os
import sys
from pathlib import Path

from loguru import logger


def setup_logging(
    level: str = "INFO",
    log_file: str = "logs/bot.log",
    rotation: str = "10 MB",
    retention: str = "14 days",
    json_format: bool = False,
) -> None:
    """Configure loguru global logging with console colors and file persistence.

    Sinks are routed via ``__log_channel`` extra field:
    - ``""`` (default) → stderr + bot.log
    - ``"_llm_raw"`` → logs/llm.log only
    - ``"_audit"`` → logs/audit.log only
    """

    level = os.getenv("BOT_LOG_LEVEL", level)

    logger.remove()
    logger.configure(extra={"trace_id": "", "__log_channel": ""})

    # --- Console sink (colorized stderr) ---
    logger.add(
        sys.stderr,
        level=level,
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "trace=<dim>{extra[trace_id]}</dim> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        filter=lambda r: not r["extra"].get("__log_channel", "").startswith("_"),
    )

    # --- Main bot.log file sink ---
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger.add(
        str(log_path),
        level=level,
        rotation=rotation,
        retention=retention,
        encoding="utf-8",
        enqueue=True,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | trace={extra[trace_id]} | {name}:{function}:{line} | {message}",
        filter=lambda r: not r["extra"].get("__log_channel", "").startswith("_"),
        serialize=json_format,
    )

    # --- LLM raw request/response log ---
    Path("logs").mkdir(parents=True, exist_ok=True)
    logger.add(
        "logs/llm.log",
        level="DEBUG",
        rotation=rotation,
        retention="7 days",
        encoding="utf-8",
        enqueue=True,
        format="{time:YYYY-MM-DD HH:mm:ss} UTC | {message}",
        filter=lambda r: r["extra"].get("__log_channel") == "_llm_raw",
    )

    # --- Audit log for destructive tool calls ---
    logger.add(
        "logs/audit.log",
        level="DEBUG",
        rotation=rotation,
        retention="30 days",
        encoding="utf-8",
        enqueue=True,
        format="{message}",
        filter=lambda r: r["extra"].get("__log_channel") == "_audit",
    )
