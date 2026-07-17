"""QQ Bot entry point."""

import asyncio
import os

from src.core.bot import Bot


async def main() -> None:
    config_dir = os.getenv("BOT_CONFIG_DIR")
    bot = Bot()
    bot.load_config(config_dir=config_dir)
    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
