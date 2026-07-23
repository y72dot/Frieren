"""Tests for ActionMiddleware protocol and MiddlewarePipeline."""

import pytest

from src.plugin.middleware import CallNext, MiddlewarePipeline

# ---------------------------------------------------------------------------
# test middlewares
# ---------------------------------------------------------------------------


class _LoggingMiddleware:
    """Middleware that records calls for test assertions."""

    def __init__(self, name: str, priority: int, log: list[str] | None = None):
        self.name = name
        self.priority = priority
        self.log: list[str] = log if log is not None else []

    async def process(self, action: str, params: dict, call_next: CallNext) -> dict:
        self.log.append(f"{self.name}:before")
        result = await call_next(action, params)
        self.log.append(f"{self.name}:after")
        return result


class _BlockingMiddleware:
    """Middleware that blocks without calling next."""

    def __init__(self, name: str = "blocker", priority: int = 1):
        self.name = name
        self.priority = priority

    async def process(self, action: str, params: dict, call_next: CallNext) -> dict:
        return {"status": "blocked", "reason": self.name, "action": action}


class _ModifyMiddleware:
    """Middleware that modifies params before calling next."""

    def __init__(self, name: str = "modifier", priority: int = 1):
        self.name = name
        self.priority = priority

    async def process(self, action: str, params: dict, call_next: CallNext) -> dict:
        params = dict(params)
        params["modified"] = True
        return await call_next(action, params)


class _ResultModifyMiddleware:
    """Middleware that modifies the result after calling next."""

    def __init__(self, name: str = "result_mod", priority: int = 1):
        self.name = name
        self.priority = priority

    async def process(self, action: str, params: dict, call_next: CallNext) -> dict:
        result = await call_next(action, params)
        result["wrapped_by"] = self.name
        return result


class _ErrorMiddleware:
    """Middleware that always raises."""

    def __init__(self, name: str = "error_mw", priority: int = 1):
        self.name = name
        self.priority = priority

    async def process(self, action: str, params: dict, call_next: CallNext) -> dict:
        raise RuntimeError(f"{self.name} intentionally failed")


# ---------------------------------------------------------------------------
# terminal helpers
# ---------------------------------------------------------------------------


async def _echo_terminal(action: str, params: dict) -> dict:
    return {"action": action, "params": params, "status": "ok"}


async def _error_terminal(action: str, params: dict) -> dict:
    raise RuntimeError("terminal error")


# ---------------------------------------------------------------------------
# Pipeline tests
# ---------------------------------------------------------------------------


class TestPipelineBasic:
    @pytest.mark.asyncio
    async def test_no_middlewares_calls_terminal_directly(self):
        pipeline = MiddlewarePipeline([], _echo_terminal)
        result = await pipeline.execute("send_msg", {"group_id": 1})
        assert result["status"] == "ok"
        assert result["action"] == "send_msg"
        assert result["params"] == {"group_id": 1}

    @pytest.mark.asyncio
    async def test_single_middleware_receives_correct_action_and_params(self):
        log: list[str] = []
        mw = _LoggingMiddleware("mw1", 1, log)
        pipeline = MiddlewarePipeline([mw], _echo_terminal)
        result = await pipeline.execute("send_msg", {"group_id": 1})
        assert result["status"] == "ok"
        assert log == ["mw1:before", "mw1:after"]

    @pytest.mark.asyncio
    async def test_middleware_can_modify_params(self):
        mw = _ModifyMiddleware("mod", 1)
        pipeline = MiddlewarePipeline([mw], _echo_terminal)
        result = await pipeline.execute("send_msg", {"group_id": 1})
        assert result["params"]["modified"] is True
        assert result["params"]["group_id"] == 1

    @pytest.mark.asyncio
    async def test_middleware_can_block(self):
        mw = _BlockingMiddleware("blocker", 1)
        pipeline = MiddlewarePipeline([mw], _echo_terminal)
        result = await pipeline.execute("send_msg", {"group_id": 1})
        assert result["status"] == "blocked"
        assert result["reason"] == "blocker"

    @pytest.mark.asyncio
    async def test_empty_middlewares_list_still_calls_terminal(self):
        pipeline = MiddlewarePipeline([], _echo_terminal)
        result = await pipeline.execute("any_action", {})
        assert result["status"] == "ok"


class TestPipelineOrdering:
    @pytest.mark.asyncio
    async def test_middlewares_execute_in_priority_order(self):
        """Lowest priority runs first (outermost)."""
        log: list[str] = []
        mw1 = _LoggingMiddleware("mw1", 1, log)
        mw2 = _LoggingMiddleware("mw2", 5, log)
        mw3 = _LoggingMiddleware("mw3", 10, log)
        pipeline = MiddlewarePipeline([mw3, mw1, mw2], _echo_terminal)
        await pipeline.execute("x", {})
        # Priority 1 (mw1) first, then 5 (mw2), then 10 (mw3), then terminal, then unwind.
        assert log == [
            "mw1:before",
            "mw2:before",
            "mw3:before",
            "mw3:after",
            "mw2:after",
            "mw1:after",
        ]

    @pytest.mark.asyncio
    async def test_multiple_middlewares_form_correct_chain(self):
        """Each middleware calls next, forming a proper chain."""
        log: list[str] = []
        mw_a = _LoggingMiddleware("a", 1, log)
        mw_b = _LoggingMiddleware("b", 2, log)
        pipeline = MiddlewarePipeline([mw_a, mw_b], _echo_terminal)
        result = await pipeline.execute("test", {})
        assert result["status"] == "ok"
        assert "a:before" in log
        assert "b:before" in log
        assert "b:after" in log
        assert "a:after" in log

    @pytest.mark.asyncio
    async def test_terminal_result_propagates_back(self):
        mw = _ResultModifyMiddleware("wrap", 1)
        pipeline = MiddlewarePipeline([mw], _echo_terminal)
        result = await pipeline.execute("test", {})
        assert result["wrapped_by"] == "wrap"
        assert result["status"] == "ok"


class TestPipelineErrors:
    @pytest.mark.asyncio
    async def test_exception_in_middleware_propagates(self):
        mw = _ErrorMiddleware("bad", 1)
        pipeline = MiddlewarePipeline([mw], _echo_terminal)
        with pytest.raises(RuntimeError, match="bad intentionally failed"):
            await pipeline.execute("test", {})

    @pytest.mark.asyncio
    async def test_exception_in_terminal_propagates(self):
        mw = _LoggingMiddleware("mw", 1, [])
        pipeline = MiddlewarePipeline([mw], _error_terminal)
        with pytest.raises(RuntimeError, match="terminal error"):
            await pipeline.execute("test", {})


class TestPipelineProperties:
    def test_middleware_names_returns_ordered_names(self):
        mw1 = _LoggingMiddleware("alpha", 5)
        mw2 = _LoggingMiddleware("beta", 1)
        pipeline = MiddlewarePipeline([mw1, mw2], _echo_terminal)
        # Sorted by priority: beta(1), alpha(5)
        assert pipeline.middleware_names == ["beta", "alpha"]


class TestBlockingMiddleware:
    @pytest.mark.asyncio
    async def test_blocker_returns_valid_dict(self):
        mw = _BlockingMiddleware("guard", 1)
        pipeline = MiddlewarePipeline([mw], _echo_terminal)
        result = await pipeline.execute("bad_action", {})
        assert isinstance(result, dict)
        assert result["status"] == "blocked"
        assert result["action"] == "bad_action"
