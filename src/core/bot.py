"""Bot main class – assembles all components and manages the lifecycle."""

from __future__ import annotations

import asyncio
import signal

from loguru import logger

from src.core.api_client import ApiClient
from src.core.config import BotConfig, load_config
from src.core.event_bus import EventBus
from src.core.filter_manager import FilterManager
from src.core.message_bus import MessageBus
from src.core.message_store import MessageStore
from src.plugin.manager import PluginManager
from src.utils.logger import setup_logging


class Bot:
    """Central bot orchestrator.

    Typical usage::

        bot = Bot()
        bot.load_config()
        await bot.start()
    """

    def __init__(self, config: BotConfig | None = None) -> None:
        self.config: BotConfig | None = config
        self.message_bus = MessageBus()
        self.api = ApiClient(bus=self.message_bus)
        self.api.set_bot(self)
        self.msg_store = MessageStore()
        self.filter_mgr = FilterManager(config)
        self.event_bus = EventBus()
        self.plugin_manager = PluginManager(bus=self.message_bus)
        self.llm_provider = None
        self._running = False
        self._main_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # configuration
    # ------------------------------------------------------------------

    def load_config(
        self,
        config_dir: str | None = None,
        env_file: str | None = None,
    ) -> BotConfig:
        """Load and store configuration.

        If *config* was already injected via the constructor this is a no-op.

        Must be called before :meth:`start`.
        """
        if self.config is not None:
            return self.config
        self.config = load_config(config_dir=config_dir, env_file=env_file)
        self.filter_mgr.update_config(self.config)
        logger.info(f"Configuration loaded (QQ: {self.config.bot.qq})")
        return self.config

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the bot (blocking until shutdown)."""
        if self.config is None:
            raise RuntimeError("Configuration not loaded – call load_config() first")

        cfg = self.config

        setup_logging(
            level=cfg.logging.level,
            log_file=cfg.logging.file,
            rotation=cfg.logging.rotation,
            retention=cfg.logging.retention,
        )

        logger.info("Bot starting …")

        if cfg.plugin.auto_discover:
            count = self.plugin_manager.auto_discover(
                plugin_dirs=cfg.plugin.plugin_dirs,
                disabled=cfg.plugin.disabled_plugins,
            )
            logger.info(f"{count} plugin(s) loaded")

        # Initialize LLM provider if enabled
        if cfg.llm.enabled:
            from src.core.llm.provider import OpenAICompatibleProvider

            self.llm_provider = OpenAICompatibleProvider(
                api_base=cfg.llm.api_base,
                api_key=cfg.llm.api_key,
            )
            logger.info(f"LLM provider initialized: {cfg.llm.model} @ {cfg.llm.api_base}")
        else:
            logger.info("LLM is disabled")

        self._running = True
        self._setup_signal_handlers()

        try:
            await self._run_event_loop()
        except asyncio.CancelledError:
            logger.info("Bot main task cancelled")
        finally:
            await self._cleanup()

    async def stop(self) -> None:
        """Signal the bot to shut down gracefully."""
        logger.info("Shutdown requested …")
        self._running = False
        if self._main_task:
            self._main_task.cancel()

    async def reload_plugins(self) -> None:
        """Hot-reload plugins (re-run auto-discovery on configured dirs)."""
        if self.config is None:
            logger.warning("Cannot reload plugins: config not loaded")
            return
        self.plugin_manager = PluginManager(bus=self.message_bus)
        self.plugin_manager.auto_discover(
            plugin_dirs=self.config.plugin.plugin_dirs,
            disabled=self.config.plugin.disabled_plugins,
        )
        logger.info("Plugins reloaded")

    # ------------------------------------------------------------------
    # internal: event loop
    # ------------------------------------------------------------------

    async def _run_event_loop(self) -> None:
        """Reconnect / listen loop, depending on napcat.mode."""
        cfg = self.config
        assert cfg is not None

        mode = cfg.napcat.mode
        logger.info(f"Bot running in {mode} mode")
        if mode == "reverse":
            await self._run_reverse_server()
        else:
            attempt = 0
            base_interval = cfg.napcat.reconnect_interval
            while self._running:
                attempt += 1
                try:
                    await self._connect_and_process(cfg.napcat.ws_url, cfg.napcat.token)
                    # Connection closed cleanly (e.g. NapCat restarted) — reset backoff
                    attempt = 0
                except (ConnectionError, OSError, TimeoutError):
                    delay = min(base_interval * (2 ** (attempt - 1)), 300)
                    logger.opt(exception=True).error(
                        f"Connection error, retrying in {delay}s (attempt {attempt}) …"
                    )
                    self.api.clear_client()
                    await asyncio.sleep(delay)

    async def _connect_and_process(self, ws_url: str, token: str) -> None:
        """Active mode: bot connects to NapCatQQ WebSocket."""
        from napcat import NapCatClient  # type: ignore[import-untyped]

        logger.info(f"Connecting to NapCat: {ws_url}")
        async with NapCatClient(ws_url, token) as nc:
            self.api.set_client(nc)
            logger.info("Connected to NapCat")
            await self._process_events(nc)

    async def _run_reverse_server(self) -> None:
        """Reverse mode: NapCatQQ connects to bot."""
        from napcat import (  # type: ignore[import-untyped]
            NapCatClient,
            ReverseWebSocketServer,
        )

        cfg = self.config
        assert cfg is not None

        async def handle_client(nc: NapCatClient) -> None:
            self.api.set_client(nc)
            logger.info("NapCat connected (reverse mode)")
            await self._process_events(nc)

        server = ReverseWebSocketServer(
            handler=handle_client,
            host=cfg.napcat.reverse_host,
            port=cfg.napcat.reverse_port,
            token=cfg.napcat.token or None,
        )
        logger.info(
            f"Reverse server listening on {cfg.napcat.reverse_host}:{cfg.napcat.reverse_port}"
        )
        # run_forever blocks until stop() is called
        self._main_task = asyncio.ensure_future(server.run_forever())
        # Wait for shutdown signal
        while self._running:
            await asyncio.sleep(0.5)
        server.stop()

    async def _process_events(self, nc) -> None:
        """Process incoming events from a connected NapCatClient."""
        async for raw_event in nc:
            if not self._running:
                break
            try:
                await self.event_bus.dispatch(raw_event, self)
            except Exception:
                logger.opt(exception=True).error("Error dispatching event, skipping …")

    # ------------------------------------------------------------------
    # internal: signal handling
    # ------------------------------------------------------------------

    def _setup_signal_handlers(self) -> None:
        """Register OS signal handlers for graceful shutdown."""
        loop = asyncio.get_running_loop()

        def _shutdown() -> None:
            self._running = False

        try:
            loop.add_signal_handler(signal.SIGINT, _shutdown)
            loop.add_signal_handler(signal.SIGTERM, _shutdown)
        except NotImplementedError:
            # Windows fallback – SIGINT raises KeyboardInterrupt via signal.signal
            logger.debug("Using Windows signal fallback (signal.signal)")
            signal.signal(signal.SIGINT, lambda s, f: _shutdown())
            signal.signal(signal.SIGTERM, lambda s, f: _shutdown())

    # ------------------------------------------------------------------
    # internal: cleanup
    # ------------------------------------------------------------------

    async def _cleanup(self) -> None:
        """Release resources on shutdown."""
        self.api.clear_client()
        logger.info("Bot stopped")
