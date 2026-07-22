"""Non-destructive CQ-code scanning.

This module deliberately produces a derived view only. Callers must retain the
original CQ string as the source of truth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CQ_PATTERN = re.compile(r"\[CQ:([A-Za-z0-9_-]+)(?:,([^\]]*))?\]")


@dataclass(frozen=True)
class CQReference:
    type: str
    raw: str
    start: int
    end: int
    attributes: dict[str, str]


def scan_cq(raw_message: str) -> list[CQReference]:
    """Return lossless references for every syntactically complete CQ code.

    Unknown types and attributes are accepted. Attribute values stay CQ-escaped
    so scanning can never alter or ambiguously decode the original message.
    """
    references: list[CQReference] = []
    for match in _CQ_PATTERN.finditer(raw_message):
        attrs: dict[str, str] = {}
        raw_attrs = match.group(2) or ""
        if raw_attrs:
            for item in raw_attrs.split(","):
                key, separator, value = item.partition("=")
                if separator and key:
                    attrs[key] = value
        references.append(
            CQReference(
                type=match.group(1),
                raw=match.group(0),
                start=match.start(),
                end=match.end(),
                attributes=attrs,
            )
        )
    return references
