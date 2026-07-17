# CLAUDE.md — QQ Bot Project Guidelines

## Project Overview

A QQ bot built on **NapCatQQ** (protocol layer) + **custom Python core** (business layer). The core is written from scratch without depending on NoneBot / AstrBot / Koishi, using a custom plugin system and (planned) AI Agent engine.

### Core Design Principles
- **Low-level control**: Every line of core code is intentional; no framework magic
- **Plugin-as-function**: Plugin registration = match rule + async handler
- **Agent-native**: Built-in tool-calling engine (Phase 2); plugins double as AI tools

### Architecture
```
NapCatQQ (WebSocket) → EventBus (parse + route) → PluginManager (match + dispatch)
                                                          ↓
                                                    Plugin.handle(event, bot)
```

Key modules:
- `src/core/bot.py` — Bot orchestrator, lifecycle management
- `src/core/event_bus.py` — Parses OneBot events into internal `Event` objects
- `src/core/api_client.py` — Wraps NapCat HTTP API calls
- `src/core/config.py` — TOML + dataclass config loading
- `src/plugin/manager.py` — Plugin registry, auto-discovery, dispatch
- `src/plugin/base.py` — `Event` dataclass and `Plugin` Protocol
- `src/plugin/decorators.py` — `@command`, `@on_regex`, `@on_keyword`, `@on_notice`

## Directory Structure
```
qqbot/
├── src/               # Core source code (do NOT add new top-level source dirs)
│   ├── core/          # bot.py, event_bus.py, api_client.py, config.py
│   ├── plugin/        # Plugin Protocol, manager, decorators
│   ├── agent/         # Agent engine (planned, not yet implemented)
│   └── utils/         # logger, helpers, scheduler
├── plugins/           # User plugins (one .py file per plugin, no _ prefix)
├── config/            # TOML configuration files
├── tests/             # pytest test suite
├── data/              # Runtime data (QQ sessions, napcat config) — gitignored
├── scripts/           # Shell scripts (start.sh, deploy.sh)
└── logs/              # Log output — gitignored
```

## Coding Conventions

### Python Version
- **Python 3.12+** required (`match case`, native type hints, PEP 695)

### Type Annotations
- All public functions and methods MUST have type annotations
- Use `from __future__ import annotations` for forward references
- Use `| None` instead of `Optional[...]`
- Protocol-based duck typing over ABC inheritance

### Async
- Async-first: use `asyncio` standard library (no trio, no gevent)
- All plugin `handle()` methods are async
- API calls are async

### Logging
- Use `from loguru import logger` exclusively
- Do NOT use the standard `logging` module
- Format: `logger.info("message")` — loguru handles formatting

### Configuration
- Config files: TOML in `config/` directory
- Config dataclasses in `src/core/config.py`
- Secrets: `.env` file (never commit, see `.gitignore`)
- Environment variable overrides for dev ↔ deploy parity

## Plugin Development Rules

### Plugin Protocol
A valid plugin must have:
- `name: str` — unique identifier
- `priority: int` — lower = matched first
- `match(self, event: Event) -> bool` — synchronous check
- `async handle(self, event: Event, bot: Bot) -> bool` — return `True` to consume event

### Decorators (preferred approach)
```python
@command("/ping", priority=0)
async def ping(event: Event, bot: Bot) -> bool: ...

@on_regex(r"^https?://", priority=5)
async def url_handler(event: Event, bot: Bot, match: re.Match) -> bool: ...

@on_keyword(["早安", "早上好"], priority=10)
async def greet(event: Event, bot: Bot) -> bool: ...

@on_notice("group_increase", priority=0)
async def welcome(event: Event, bot: Bot) -> bool: ...
```

### Plugin File Rules
- Place plugin files in `plugins/` directory
- File names must NOT start with `_` (underscore files are skipped)
- Use the decorator approach — decorators automatically attach `__plugin__` to the function
- PluginManager's `auto_discover()` scans for functions with `__plugin__` attribute

### What Plugins Can Access (via `bot` parameter)
- `bot.config` — global BotConfig
- `bot.api` — ApiClient (send messages, get group info, etc.)
- `bot.event_bus` — EventBus (publish/listen for custom events)
- `bot.plugin_manager` — PluginManager (register/unregister)
- `bot.agent_engine` — AgentEngine (Phase 2, not yet available)

### Event Object
```python
@dataclass
class Event:
    type: str        # "message.group", "message.private", "notice.group_increase", etc.
    user_id: int     # QQ ID of sender/subject
    message: str     # Plain-text raw_message
    group_id: int | None
    is_group: bool
    raw: Any         # Original OneBot event
```

## Testing Rules

### Running Tests
```bash
# Run all tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ -v --cov=src --cov-report=term-missing

# Run specific test file
python -m pytest tests/test_manager.py -v
```

### Test File Conventions
- Test files in `tests/`, named `test_*.py`
- Use `pytest` + `pytest-asyncio` (asyncio_mode = "auto" in pyproject.toml)
- Async tests decorated with `@pytest.mark.asyncio`
- Use `tests/conftest.py` for shared fixtures
- Use `unittest.mock` or manual test doubles (no heavy mocking frameworks)

### Test Double Pattern
For classes needing a fake Bot or ApiClient, create minimal classes that implement the needed protocol methods. See existing test files for examples.

## Constraints & Boundaries

### DO NOT
- Modify `Bot.start()` lifecycle flow
- Introduce new dependency frameworks (NoneBot, FastAPI, Flask, etc.) without proposal
- Hardcode API keys or secrets — always read from `.env`
- Add new top-level Python source directories besides `src/` and `plugins/`
- Import napcat types directly in plugins — use the internal `Event` type

### ALWAYS
- Write tests for new functionality
- Add type annotations to new public functions
- Use loguru for logging
- Respect the Plugin Protocol when creating plugins
- Keep the plugin match/handle pattern simple — no middleware chains yet

### Git Rules
- Never commit: `.env`, `logs/`, `data/`, `__pycache__/`, `*.pyc`
- Commit messages in English, format: `type: brief description`
  - Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`
- Run tests before committing: `python -m pytest tests/ -v`

## Dependencies

### Production
- `napcat-sdk>=0.1.0` — NapCatQQ WebSocket/API client
- `loguru>=0.7.0` — logging
- `python-dotenv>=1.0.0` — .env loading
- `aiofiles>=24.0.0` — async file I/O

### Dev
- `pytest`, `pytest-asyncio`, `pytest-cov` — testing
- `mypy` — type checking
- `ruff` — linting

### Adding Dependencies
1. Add to `pyproject.toml` `[project.dependencies]` or `[project.optional-dependencies] dev`
2. Update `Dockerfile` if it hard-codes pip install commands
3. Document any new system dependencies
