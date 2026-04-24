from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from threading import Lock
from typing import Any

from services.config import DATA_DIR

REQUEST_LOGS_FILE = DATA_DIR / "request_logs.jsonl"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
MAX_LOG_ENTRIES = 2000
MAX_SUMMARY_TEXT_LENGTH = 512
MAX_SUMMARY_OBJECT_KEYS = 24
MAX_SUMMARY_LIST_ITEMS = 12


def _clamp_int(value: object, default: int, *, minimum: int, maximum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = default
    return max(minimum, min(maximum, normalized))


def _truncate_text(value: object, *, limit: int = MAX_SUMMARY_TEXT_LENGTH) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _sanitize_value(value: object) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _truncate_text(value)
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_SUMMARY_OBJECT_KEYS:
                sanitized["_truncated"] = True
                break
            sanitized[str(key)] = _sanitize_value(item)
        return sanitized
    if isinstance(value, list):
        items = [_sanitize_value(item) for item in value[:MAX_SUMMARY_LIST_ITEMS]]
        if len(value) > MAX_SUMMARY_LIST_ITEMS:
            items.append({"_truncated": True, "remaining": len(value) - MAX_SUMMARY_LIST_ITEMS})
        return items
    return _truncate_text(value)


@dataclass(frozen=True)
class RequestLogPage:
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int


class RequestLogStore:
    def __init__(self, path: Path, *, max_entries: int = MAX_LOG_ENTRIES) -> None:
        self.path = path
        self.max_entries = max(100, int(max_entries))
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_entry(entry)
        with self._lock:
            items = self._read_entries_locked()
            items.append(normalized)
            if len(items) > self.max_entries:
                items = items[-self.max_entries:]
            self._write_entries_locked(items)
        return normalized

    def list(self, *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> RequestLogPage:
        normalized_page = _clamp_int(page, 1, minimum=1, maximum=1_000_000)
        normalized_page_size = _clamp_int(
            page_size,
            DEFAULT_PAGE_SIZE,
            minimum=1,
            maximum=MAX_PAGE_SIZE,
        )
        with self._lock:
            items = list(reversed(self._read_entries_locked()))
        total = len(items)
        start = (normalized_page - 1) * normalized_page_size
        end = start + normalized_page_size
        return RequestLogPage(
            items=items[start:end],
            total=total,
            page=normalized_page,
            page_size=normalized_page_size,
        )

    def _normalize_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(entry or {})
        normalized["request"] = (
            _sanitize_value(normalized.get("request")) if isinstance(normalized.get("request"), dict) else {}
        )
        normalized["response"] = (
            _sanitize_value(normalized.get("response")) if isinstance(normalized.get("response"), dict) else {}
        )
        normalized["error"] = _truncate_text(normalized.get("error"))
        normalized["success"] = bool(normalized.get("success"))
        normalized["status_code"] = _clamp_int(normalized.get("status_code"), 0, minimum=0, maximum=999)
        normalized["duration_ms"] = _clamp_int(normalized.get("duration_ms"), 0, minimum=0, maximum=86_400_000)
        normalized["endpoint"] = _truncate_text(normalized.get("endpoint"), limit=128)
        normalized["method"] = _truncate_text(normalized.get("method"), limit=16).upper() or "POST"
        normalized["model"] = _truncate_text(normalized.get("model"), limit=128)
        normalized["client_ip"] = _truncate_text(normalized.get("client_ip"), limit=128)
        normalized["request_id"] = _truncate_text(normalized.get("request_id"), limit=128)
        normalized["created_at"] = _truncate_text(normalized.get("created_at"), limit=64)
        normalized["user_agent"] = _truncate_text(normalized.get("user_agent"))
        return normalized

    def _read_entries_locked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        items: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                items.append(data)
        return items

    def _write_entries_locked(self, items: list[dict[str, Any]]) -> None:
        if not items:
            self.path.write_text("", encoding="utf-8")
            return
        payload = "\n".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in items) + "\n"
        self.path.write_text(payload, encoding="utf-8")


request_log_store = RequestLogStore(REQUEST_LOGS_FILE)
