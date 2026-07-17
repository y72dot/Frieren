# CLAUDE.md

## Architecture

NapCatQQ (WebSocket) → EventBus (parse + route) → PluginManager (match + dispatch) → Plugin.handle(event, bot)

No NoneBot / AstrBot / Koishi — core is self-written. Plugins double as AI Agent tools (Phase 2).

## Non-Obvious Rules

### Plugin Discovery
- `PluginManager.auto_discover()` scans `plugins/*.py` for functions with a `__plugin__` attribute
- Files starting with `_` are **skipped**
- Decorators (`@command`, `@on_regex`, `@on_keyword`, `@on_notice`) auto-attach `__plugin__`
- To disable a plugin: list its `__plugin__.name` in `config/bot.toml` → `[plugin].disabled_plugins`

### Bot Lifecycle
- `Bot.__init__` now accepts optional `config` — inject for tests, omit for production
- `Bot.start()` will block until shutdown (reconnect loop for active mode, polling loop for reverse)
- Do NOT modify `start()` / `_run_event_loop()` flow

### Type Hints
- Use `from __future__ import annotations` everywhere (forward refs work without quotes)
- Protocol-based duck typing (not ABC)

### Constraints
- Do NOT add new top-level Python source dirs besides `src/` and `plugins/`
- Do NOT import napcat types in plugins — use the internal `Event` type from `src.plugin.base`
- No new dependency frameworks without proposal
- No middleware chains in plugin dispatch yet (Phase 1: one plugin consumes, stops)
- Commit format: `type: description` (feat/fix/refactor/test/docs/chore)

### Post-Modification Workflow
- After making code changes and running tests, restart the bot so changes take effect
