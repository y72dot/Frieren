"""Bot main class – assembles all components and manages the lifecycle."""

from __future__ import annotations

import asyncio
import signal
import time
from pathlib import Path

from loguru import logger

from src.adapters.qq import QQFileGateway, QQHistoryGateway
from src.core.api_client import ApiClient
from src.core.artifacts import ArtifactService, ArtifactStore
from src.core.config import BotConfig, load_config
from src.core.event_bus import EventBus
from src.core.filter_manager import FilterManager
from src.core.health import HealthMonitor
from src.core.history import HistoryQueryService, HistorySyncService
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
        self._artifact_tasks: set[asyncio.Task] = set()
        self.msg_store = MessageStore()
        artifact_cfg = config.artifacts if config is not None else None
        self.artifact_store = ArtifactStore(
            root_dir=artifact_cfg.root_dir if artifact_cfg else "data/artifacts",
            connection=self.msg_store.connection,
            max_file_size=(artifact_cfg.max_file_size if artifact_cfg else 104_857_600),
        )
        self.file_gateway = QQFileGateway(self.api)
        self.artifact_service = ArtifactService(
            self.artifact_store,
            self.file_gateway,
            download_timeout=(artifact_cfg.download_timeout if artifact_cfg else 60),
        )
        self.filter_mgr = FilterManager(config)
        self.event_bus = EventBus()
        self.tool_catalog = None
        self.tool_executor = None
        self.invocation_store = None
        self.ensure_tool_platform()
        self.runtime_store = None
        self.runtime = None
        self.recovery_controller = None
        self.schedule_store = None
        self.scheduler = None
        self.ensure_runtime_platform()
        self.workspace = None
        self.search_service = None
        self.web_client = None
        self.ensure_capability_services()
        self.control_plane = None
        self.ensure_control_plane()
        self.history_gateway = QQHistoryGateway(self.api)
        self._configure_history()
        self.plugin_manager = PluginManager(bus=self.message_bus)
        self.llm_provider = None
        self.config_center = None
        self.prompt_registry = None
        # LLM subsystems (initialized in _init_llm_subsystems)
        self.session_mgr = None
        self.agent_loop = None
        self.memory_mgr = None
        self.skill_mgr = None
        self.sandbox = None
        self._running = False
        self._main_task: asyncio.Task[None] | None = None
        self.health_monitor = HealthMonitor()
        self._health_task: asyncio.Task[None] | None = None
        self._health_started = False
        self._last_event_success_at: float | None = None
        self._last_event_error_at: float | None = None
        self._consecutive_event_errors = 0
        self._last_event_error = ""
        if config is not None:
            self._init_configuration_services(persistent=False)

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
        self._configure_artifacts()
        self._configure_history()
        self.workspace = None
        self.web_client = None
        self.search_service = None
        self.ensure_capability_services()
        self.control_plane = None
        self.ensure_control_plane()
        self._init_configuration_services(config_dir=config_dir, persistent=True)
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
        if self.config_center is None or self.prompt_registry is None:
            self._init_configuration_services(persistent=True)
        elif not self.config_center.persistent:
            self.config_center.init_db("data/config_state.db")

        setup_logging(
            level=cfg.logging.level,
            log_file=cfg.logging.file,
            rotation=cfg.logging.rotation,
            retention=cfg.logging.retention,
            json_format=cfg.logging.json_format,
        )

        logger.info("Bot starting …")
        self._health_started = True
        self.health_monitor.write("starting", napcat_connected=False)

        recovered_events = self.event_bus.recover_unprojected(self)
        if recovered_events:
            logger.info(f"Message projections recovered: {recovered_events}")

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
            logger.info(
                f"LLM provider initialized: {cfg.llm.model} @ {cfg.llm.api_base}"
            )
            self._init_llm_subsystems()
        else:
            logger.info("LLM is disabled")

        self._running = True
        self.health_monitor.write("running", napcat_connected=False)
        self._health_task = asyncio.create_task(
            self._heartbeat_loop(), name="bot-health-heartbeat"
        )
        self.ensure_runtime_platform()
        if self.runtime is not None and cfg.runtime.recover_on_start:
            recovered_runs = self.recovery_controller.recover()
            if recovered_runs:
                from src.core.message_bus import BusMessage, MessageType

                await self.message_bus.emit_and_wait(
                    BusMessage(
                        type=MessageType.LIFECYCLE,
                        payload={"event": "runtime.recovered", "run_ids": recovered_runs},
                        source="recovery_controller",
                    ),
                    self,
                )
            for run_id in recovered_runs:
                run = self.runtime_store.get_run(run_id)
                if run is not None and run.status == "CREATED":
                    self.runtime.submit(run_id)
            if recovered_runs:
                logger.info(f"Durable runs recovered: {len(recovered_runs)}")
        if self.scheduler is not None and cfg.scheduler.enabled:
            await self.scheduler.start()
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

    def discover_message_artifacts(self, message_id: int) -> None:
        """Discover resources, rebinding after an injected MessageStore swap."""
        if self.config is not None and not self.config.artifacts.enabled:
            return
        if (
            self.artifact_store.connection is not self.msg_store.connection
            or self.file_gateway.api is not self.api
        ):
            self._configure_artifacts()
        artifacts = self.artifact_store.discover_message(message_id)
        if self.config is not None and self.config.artifacts.auto_materialize:
            for artifact in artifacts:
                if artifact.status == "discovered":
                    self._schedule_artifact_materialization(artifact.artifact_id)

    def _configure_artifacts(self) -> None:
        cfg = self.config.artifacts if self.config is not None else None
        self.file_gateway = QQFileGateway(self.api)
        self.artifact_store = ArtifactStore(
            root_dir=cfg.root_dir if cfg else "data/artifacts",
            connection=self.msg_store.connection,
            max_file_size=cfg.max_file_size if cfg else 104_857_600,
        )
        self.artifact_service = ArtifactService(
            self.artifact_store,
            self.file_gateway,
            download_timeout=cfg.download_timeout if cfg else 60,
        )

    def _schedule_artifact_materialization(self, artifact_id: str) -> None:
        async def materialize() -> None:
            try:
                await self.artifact_service.materialize(artifact_id)
            except Exception:
                logger.opt(exception=True).warning(
                    f"Background artifact materialization failed: {artifact_id}"
                )

        try:
            task = asyncio.create_task(materialize())
        except RuntimeError:
            return
        self._artifact_tasks.add(task)
        task.add_done_callback(self._artifact_tasks.discard)

    def _configure_history(self) -> None:
        cfg = self.config.history if self.config is not None else None
        self.history_gateway = QQHistoryGateway(self.api)
        self.history_sync = HistorySyncService(
            self.history_gateway,
            self.msg_store,
            self.event_bus,
            artifact_discoverer=self.discover_message_artifacts,
            page_size=cfg.page_size if cfg else 20,
            max_pages=cfg.max_pages_per_sync if cfg else 3,
        )
        self.history_query = HistoryQueryService(
            self.msg_store,
            self.history_sync,
            query_backfill=cfg.query_backfill if cfg else True,
        )

    def ensure_tool_platform(self) -> None:
        """Create or rebind this Bot's isolated tool registry and executor."""
        cfg = self.config.tools if self.config is not None else None
        persist = cfg is None or cfg.invocation_persist
        if (
            self.tool_catalog is not None
            and self.tool_executor is not None
            and (
                (persist and self.invocation_store is not None
                 and self.invocation_store.connection is self.msg_store.connection)
                or (not persist and self.invocation_store is None)
            )
        ):
            return
        from plugins.llm_artifact_tools import register_artifact_tools
        from plugins.llm_capability_tools import register_capability_tools
        from plugins.llm_control_tools import register_control_tools
        from plugins.llm_schedule_tools import register_schedule_tools
        from plugins.llm_tools import register_llm_tools
        from src.core.llm.invocation_store import InvocationStore
        from src.core.llm.tool_catalog import ToolCatalog
        from src.core.llm.tool_executor import ToolExecutor

        self.tool_catalog = ToolCatalog()
        register_llm_tools(self.tool_catalog)
        register_artifact_tools(self.tool_catalog)
        register_capability_tools(self.tool_catalog)
        register_control_tools(self.tool_catalog)
        register_schedule_tools(self.tool_catalog)
        self.invocation_store = (
            InvocationStore(self.msg_store.connection)
            if cfg is None or cfg.invocation_persist
            else None
        )
        self.tool_executor = ToolExecutor(
            self.tool_catalog,
            default_timeout=cfg.default_timeout if cfg else 30.0,
            invocation_store=self.invocation_store,
            max_result_bytes=cfg.max_result_bytes if cfg else 262_144,
        )

    def ensure_runtime_platform(self) -> None:
        """Create or rebind durable runtime and scheduler to MessageStore."""
        cfg = self.config.runtime if self.config is not None else None
        if cfg is not None and not cfg.enabled:
            self.runtime_store = None
            self.runtime = None
            self.recovery_controller = None
            self.schedule_store = None
            self.scheduler = None
            return
        if (
            self.runtime_store is not None
            and self.runtime_store.connection is self.msg_store.connection
        ):
            return
        self.ensure_tool_platform()
        from src.core.runtime import (
            DurableRuntime,
            RecoveryController,
            RuntimeStore,
            SchedulerService,
            ScheduleStore,
        )

        self.runtime_store = RuntimeStore(self.msg_store.connection)
        self.runtime = DurableRuntime(self.runtime_store)
        self.runtime.register_handler("agent_prompt", self._execute_scheduled_prompt)
        self.recovery_controller = RecoveryController(
            self.runtime, self.invocation_store, self.tool_catalog
        )
        self.schedule_store = ScheduleStore(self.msg_store.connection)
        scheduler_cfg = self.config.scheduler if self.config is not None else None
        self.scheduler = SchedulerService(
            self.schedule_store,
            self.runtime,
            poll_interval=scheduler_cfg.poll_interval if scheduler_cfg else 1.0,
            max_catch_up=scheduler_cfg.max_catch_up if scheduler_cfg else 10,
        )

    async def _execute_scheduled_prompt(self, template, context):
        """Dispatch one scheduled prompt through the normal LLM pipeline."""
        if self.llm_provider is None:
            raise RuntimeError("LLM provider is not available for scheduled prompt")
        from src.core.message_bus import BusMessage, MessageType

        is_group = context.conversation_type == "group"
        target_id = context.conversation_id or context.requested_by or 0
        payload = {
            "llm_type": "trigger",
            "session_key": f"schedule:{context.task_id}",
            "user_id": context.requested_by or 0,
            "group_id": target_id if is_group else None,
            "is_group": is_group,
            "text": str(template["prompt"]),
            "nickname": "Scheduler",
            "task_id": context.task_id,
            "run_id": context.run_id,
            "step_id": context.step_id,
        }
        await self.message_bus.emit_and_wait(
            BusMessage(type=MessageType.INTERNAL, payload=payload, source="scheduler"),
            self,
        )
        return {"dispatched": True, "target_id": target_id}

    def ensure_capability_services(self) -> None:
        """Create or rebind workspace, unified search and safe web services."""
        if self.artifact_store.connection is not self.msg_store.connection:
            self._configure_artifacts()
        workspace_cfg = self.config.workspace if self.config is not None else None
        if (
            self.workspace is not None
            and self.workspace.artifact_store is self.artifact_store
        ):
            return
        from src.core.search import SearchService
        from src.core.web import SafeWebClient
        from src.core.workspace import WorkspaceService

        self.workspace = WorkspaceService(
            workspace_cfg.root_dir if workspace_cfg else "data/workspace",
            artifact_store=self.artifact_store,
            max_file_size=workspace_cfg.max_file_size if workspace_cfg else 1_048_576,
            max_read_size=workspace_cfg.max_read_size if workspace_cfg else 524_288,
        )
        self.search_service = SearchService(self)
        web_cfg = self.config.web if self.config is not None else None
        self.web_client = SafeWebClient(
            self.artifact_store,
            timeout=web_cfg.timeout if web_cfg else 20.0,
            max_response_bytes=web_cfg.max_response_bytes if web_cfg else 2_097_152,
            max_redirects=web_cfg.max_redirects if web_cfg else 3,
            search_url=(web_cfg.search_url if web_cfg else "https://html.duckduckgo.com/html/?q={query}"),
            user_agent=web_cfg.user_agent if web_cfg else "qqbot-agent/1.0",
        )

    def ensure_control_plane(self) -> None:
        """Create or rebind the proposal-only control plane."""
        if (
            self.control_plane is not None
            and self.control_plane.connection is self.msg_store.connection
        ):
            return
        from src.core.control_plane import ControlPlane

        prompts_dir = "config/prompts"
        if self.config is not None and self.config.llm.prompts.prompts_dir:
            configured = Path(self.config.llm.prompts.prompts_dir)
            prompts_dir = str(
                configured
                if configured.is_absolute() or configured.parts[:1] == ("config",)
                else Path("config") / configured
            )
        self.control_plane = ControlPlane(
            self,
            self.msg_store.connection,
            prompts_dir=prompts_dir,
        )

    def ensure_history_services(self) -> None:
        if (
            self.history_sync.message_store is not self.msg_store
            or self.history_gateway.api is not self.api
        ):
            self._configure_history()

    async def _sync_history_on_connect(self) -> None:
        cfg = self.config.history if self.config is not None else None
        if cfg is None or not cfg.enabled or not cfg.sync_on_connect:
            return
        self.ensure_history_services()
        try:
            results = await self.history_sync.sync_recent(cfg.recent_contact_count)
            logger.info(
                f"History sync on connect completed: conversations={len(results)}"
            )
        except Exception:
            logger.opt(exception=True).error(
                "History sync on connect failed; live event processing continues"
            )

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
                    self.health_monitor.write("running", napcat_connected=False)
                    await asyncio.sleep(delay)

    async def _connect_and_process(self, ws_url: str, token: str) -> None:
        """Active mode: bot connects to NapCatQQ WebSocket."""
        from napcat import NapCatClient  # type: ignore[import-untyped]

        logger.info(f"Connecting to NapCat: {ws_url}")
        async with NapCatClient(ws_url, token) as nc:
            self.api.set_client(nc)
            self.health_monitor.write("running", napcat_connected=True)
            logger.info("Connected to NapCat")
            try:
                await self._process_events(nc)
            finally:
                self.api.clear_client()
                self.health_monitor.write("running", napcat_connected=False)

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
            self.health_monitor.write("running", napcat_connected=True)
            logger.info("NapCat connected (reverse mode)")
            try:
                await self._process_events(nc)
            finally:
                self.api.clear_client()
                self.health_monitor.write("running", napcat_connected=False)

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
        await self._sync_history_on_connect()
        async for raw_event in nc:
            if not self._running:
                break
            try:
                await self.event_bus.dispatch(raw_event, self)
                self._last_event_success_at = time.time()
                self._consecutive_event_errors = 0
                self._last_event_error = ""
            except Exception as exc:
                self._last_event_error_at = time.time()
                self._consecutive_event_errors += 1
                self._last_event_error = f"{type(exc).__name__}: {exc}"[:500]
                ctx = f"type={type(raw_event).__name__}"
                try:
                    uid = getattr(raw_event, "user_id", None) or (
                        raw_event.get("user_id")
                        if isinstance(raw_event, dict)
                        else None
                    )
                    if uid:
                        ctx += f" user={uid}"
                    gid = getattr(raw_event, "group_id", None) or (
                        raw_event.get("group_id")
                        if isinstance(raw_event, dict)
                        else None
                    )
                    if gid:
                        ctx += f" group={gid}"
                    pt = getattr(raw_event, "post_type", None) or (
                        raw_event.get("post_type")
                        if isinstance(raw_event, dict)
                        else None
                    )
                    if pt:
                        ctx += f" post_type={pt}"
                except Exception:
                    pass
                logger.opt(exception=True).error(
                    f"Error dispatching event ({ctx}), skipping …"
                )

    # ------------------------------------------------------------------
    # internal: signal handling
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        while self._running:
            self.health_monitor.write(
                "running",
                napcat_connected=self.api._client is not None,
                details={
                    "last_event_success_at": self._last_event_success_at,
                    "last_event_error_at": self._last_event_error_at,
                    "consecutive_event_errors": self._consecutive_event_errors,
                    "last_event_error": self._last_event_error,
                },
            )
            await asyncio.sleep(10)

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
        if self._health_task is not None:
            self._health_task.cancel()
            await asyncio.gather(self._health_task, return_exceptions=True)
            self._health_task = None
        if self.scheduler is not None:
            await self.scheduler.stop()
        if self.runtime is not None:
            await self.runtime.shutdown()
        # Shut down LLM subsystems
        if self.session_mgr is not None:
            self.session_mgr.shutdown()
        if self.memory_mgr is not None:
            self.memory_mgr.close()
        if self.config_center is not None:
            self.config_center.close()
        for task in self._artifact_tasks:
            task.cancel()
        if self._artifact_tasks:
            await asyncio.gather(*self._artifact_tasks, return_exceptions=True)
        self.artifact_store.close()
        self.msg_store.close()
        self.api.clear_client()
        if self._health_started:
            self.health_monitor.write("stopped", napcat_connected=False)
        logger.info("Bot stopped")

    # ------------------------------------------------------------------
    # internal: configuration and prompts
    # ------------------------------------------------------------------

    def _init_configuration_services(
        self,
        *,
        config_dir: str | None = None,
        persistent: bool,
    ) -> None:
        """Attach the unified config facade and validated prompt registry."""
        if self.config is None:
            return

        from src.core.config_center import ConfigCenter
        from src.core.prompts import PromptRegistry

        if self.config_center is not None:
            self.config_center.close()
        self.config_center = ConfigCenter(
            self.config,
            db_path="data/config_state.db" if persistent else None,
        )

        prompt_cfg = self.config.llm.prompts
        if prompt_cfg.enabled:
            prompt_dir = self._resolve_prompt_dir(prompt_cfg.prompts_dir, config_dir)
            self.prompt_registry = PromptRegistry.load(prompt_dir)
            logger.info(
                f"Prompt registry loaded: version={self.prompt_registry.version} "
                f"profile={prompt_cfg.profile} dir={prompt_dir}"
            )
        else:
            self.prompt_registry = PromptRegistry.from_legacy(
                self.config.llm.system_prompt
            )

    @staticmethod
    def _resolve_prompt_dir(prompts_dir: str, config_dir: str | None) -> Path:
        requested = Path(prompts_dir)
        if requested.is_absolute():
            return requested

        project_root = Path(__file__).resolve().parents[2]
        candidates: list[Path] = []
        if config_dir:
            candidates.append(Path(config_dir) / requested)
        candidates.extend(
            [
                Path.cwd() / requested,
                project_root / requested,
                project_root / "config" / requested,
            ]
        )
        for candidate in candidates:
            if (candidate / "manifest.toml").is_file():
                return candidate
        # Return the most contextually useful path for the validation error.
        return candidates[0]

    # ------------------------------------------------------------------
    # internal: LLM subsystem initialization
    # ------------------------------------------------------------------

    def _init_llm_subsystems(self) -> None:
        """Initialise agent subsystems: SessionManager, AgentLoop, Memory, Skills."""
        cfg = self.config
        if cfg is None or not cfg.llm.enabled:
            return

        from src.core.llm.agent_loop import AgentLoop, LoopConfig
        from src.core.llm.circuit_breaker import CircuitBreaker
        from src.core.llm.memory_manager import MemoryConfig, MemoryManager
        from src.core.llm.sandbox_manager import SandboxConfig as _SandboxConfig
        from src.core.llm.sandbox_manager import SandboxManager
        from src.core.llm.session_manager import SessionManager
        from src.core.llm.skill_manager import SkillManager, SkillsConfig

        self.ensure_tool_platform()

        # Session manager
        self.session_mgr = SessionManager(
            ttl=cfg.llm.session_ttl,
            keep_recent_pairs=cfg.llm.session.keep_recent_pairs,
            max_context_tokens=cfg.llm.session.max_context_tokens,
        )
        self.session_mgr.init_db()
        recovered = self.session_mgr.recover()
        if recovered:
            logger.info(f"LLM sessions recovered: {recovered}")

        # Agent loop
        self.agent_loop = AgentLoop(
            catalog=self.tool_catalog,
            session_mgr=self.session_mgr,
            executor=self.tool_executor,
            breaker=CircuitBreaker(),
            config=LoopConfig(max_turns=cfg.llm.max_turns),
        )

        # Memory manager
        memory_config = MemoryConfig(
            episodic_enabled=cfg.llm.memory.episodic_enabled,
            episodic_max=cfg.llm.memory.episodic_max,
            episodic_search_limit=cfg.llm.memory.episodic_search_limit,
            semantic_enabled=cfg.llm.memory.semantic_enabled,
            consolidation_enabled=cfg.llm.memory.consolidation_enabled,
        )
        self.memory_mgr = MemoryManager(config=memory_config)
        self.memory_mgr.init_db()

        # Skill manager
        skills_config = SkillsConfig(
            enabled=cfg.llm.skills.enabled,
            skills_dir=cfg.llm.skills.skills_dir,
            auto_reload=cfg.llm.skills.auto_reload,
        )
        self.skill_mgr = SkillManager(catalog=self.tool_catalog, config=skills_config)
        self.skill_mgr.discover(self)

        # Sandbox manager
        if cfg.llm.sandbox.enabled:
            sandbox_config = _SandboxConfig(
                enabled=cfg.llm.sandbox.enabled,
                container_name=cfg.llm.sandbox.container_name,
                workspace=cfg.llm.sandbox.workspace,
                max_file_size=cfg.llm.sandbox.max_file_size,
                max_read_size=cfg.llm.sandbox.max_read_size,
                exec_timeout=cfg.llm.sandbox.exec_timeout,
                max_exec_timeout=cfg.llm.sandbox.max_exec_timeout,
                stdout_limit=cfg.llm.sandbox.stdout_limit,
            )
            self.sandbox = SandboxManager(sandbox_config)
            self.sandbox.init_client()
            from plugins.llm_sandbox_tools import register_sandbox_tools

            register_sandbox_tools(self.tool_catalog)
            logger.info(
                f"Sandbox manager initialized (container: {sandbox_config.container_name})"
            )
        else:
            self.sandbox = None
            logger.info("Sandbox disabled via config")

        self._agent_initialized = True
        logger.info("LLM agent subsystems initialized")
