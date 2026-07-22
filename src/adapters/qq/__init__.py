"""Lossless NapCat/QQ event helpers."""

from src.adapters.qq.cq_view import CQReference, scan_cq
from src.adapters.qq.event_adapter import (
    extract_message_array,
    extract_raw_message,
    serialize_raw_event,
    to_plain_data,
)
from src.adapters.qq.file_gateway import QQFileGateway, ResolvedQQFile
from src.adapters.qq.history_gateway import (
    HistoryPage,
    QQHistoryGateway,
    RecentConversation,
)

__all__ = [
    "CQReference",
    "extract_message_array",
    "extract_raw_message",
    "scan_cq",
    "serialize_raw_event",
    "to_plain_data",
    "QQFileGateway",
    "ResolvedQQFile",
    "HistoryPage",
    "QQHistoryGateway",
    "RecentConversation",
]
