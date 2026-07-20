import sys
from pathlib import Path

from loguru import logger


def setup_logging(
    level: str = "INFO",
    log_file: str = "logs/bot.log",
    rotation: str = "10 MB",
    retention: str = "14 days",
) -> None:
    """Configure loguru global logging with console colors and file persistence."""

    logger.remove()

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
    )

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
    )

    logger.configure(extra={"trace_id": ""})
