"""Lossless serialization helpers for dict and napcat-sdk events."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from typing import Any


def to_plain_data(value: Any) -> Any:
    """Convert SDK models into JSON-safe data without a closed field schema."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): to_plain_data(v) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [to_plain_data(item) for item in value]
    # NapCat SDK events are dataclasses, but their public ``to_dict`` method
    # returns the original OneBot payload.  Prefer explicit serializers before
    # generic dataclass handling so private runtime fields such as ``_client``
    # are neither copied nor leaked into the journal.
    for method_name in ("to_dict", "model_dump", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                return to_plain_data(method())
            except (TypeError, ValueError):
                continue
    if dataclasses.is_dataclass(value):
        return {
            field.name: to_plain_data(getattr(value, field.name))
            for field in dataclasses.fields(value)
            if not field.name.startswith("_")
        }
    raw_dict = getattr(value, "__dict__", None)
    if isinstance(raw_dict, dict):
        return {
            str(k): to_plain_data(v)
            for k, v in raw_dict.items()
            if not str(k).startswith("_")
        }
    return str(value)


def serialize_raw_event(raw_event: Any) -> str:
    """Serialize a raw event deterministically for journaling and hashing."""
    return json.dumps(
        to_plain_data(raw_event),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def extract_raw_message(raw_event: Any) -> str:
    """Return only NapCat's real raw_message value, never a synthetic fallback."""
    if isinstance(raw_event, Mapping):
        value = raw_event.get("raw_message", "")
    else:
        value = getattr(raw_event, "raw_message", "")
    return str(value) if value is not None else ""


def extract_message_array(raw_event: Any) -> list[dict[str, Any]]:
    """Return the original message segment array when one is available."""
    if isinstance(raw_event, Mapping):
        value = raw_event.get("message", [])
    else:
        value = getattr(raw_event, "message", [])
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    plain = to_plain_data(value)
    if not isinstance(plain, list):
        return []
    return [item for item in plain if isinstance(item, dict)]
