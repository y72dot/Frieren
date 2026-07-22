"""Guards that keep model-internal tool protocols out of user messages."""

SAFE_FINAL_FALLBACK = "工具调用次数已达到上限，但没有生成可靠的最终回复。请换个说法重试。"


def contains_internal_tool_protocol(text: str) -> bool:
    """Return whether *text* contains a known model tool-control envelope."""
    normalized = text.replace("｜", "|").replace("＜", "<").replace("＞", ">").lower()
    markers = (
        "<||dsml||",
        "<|dsml|",
        "<tool_call",
        "<tool_calls",
        "<function_call",
        "<function_calls",
    )
    return any(marker in normalized for marker in markers)


def user_safe_text(text: str, *, fallback: str = SAFE_FINAL_FALLBACK) -> str:
    """Replace internal protocol output with a user-facing failure message."""
    return fallback if contains_internal_tool_protocol(text) else text
