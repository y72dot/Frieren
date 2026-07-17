"""QQ Bot entry point."""

import asyncio

from src.core.bot import Bot


async def main() -> None:
    bot = Bot()
    bot.load_config()
    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
