from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path
from threading import Lock
import time
from typing import Any
from urllib.parse import urlparse
import uuid

from PIL import Image, ImageOps

from services.config import DATA_DIR, config

REQUEST_LOGS_FILE = DATA_DIR / "request_logs.jsonl"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
MAX_LOG_ENTRIES = 2000
MAX_SUMMARY_TEXT_LENGTH = 512
MAX_SUMMARY_OBJECT_KEYS = 24
MAX_SUMMARY_LIST_ITEMS = 12
REQUEST_LOG_PREVIEW_SUBDIR = "request-logs"
REQUEST_LOG_PREVIEW_URL_PREFIX = f"/images/{REQUEST_LOG_PREVIEW_SUBDIR}/"
REQUEST_LOG_PREVIEW_ROOT = config.images_dir / REQUEST_LOG_PREVIEW_SUBDIR
MAX_PREVIEW_IMAGES_PER_LOG = 4
PREVIEW_MAX_EDGE = 512
PREVIEW_WEBP_QUALITY = 68


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


def _join_base_url(base_url: str | None, path: str) -> str:
    normalized_path = f"/{str(path or '').lstrip('/')}"
    normalized_base = str(base_url or config.base_url or "").strip().rstrip("/")
    if not normalized_base:
        return normalized_path
    return f"{normalized_base}{normalized_path}"


def save_request_log_preview(
    image_data: bytes,
    *,
    base_url: str | None = None,
    preview_root: Path | None = None,
    preview_url_prefix: str = REQUEST_LOG_PREVIEW_URL_PREFIX,
) -> str | None:
    if not image_data:
        return None

    root = preview_root or REQUEST_LOG_PREVIEW_ROOT
    relative_dir = Path(time.strftime("%Y"), time.strftime("%m"), time.strftime("%d"))
    target_dir = root / relative_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    preview_name = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:12]}.webp"
    preview_path = target_dir / preview_name
    try:
        with Image.open(BytesIO(image_data)) as raw_image:
            image = ImageOps.exif_transpose(raw_image)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image.thumbnail((PREVIEW_MAX_EDGE, PREVIEW_MAX_EDGE))
            image.save(preview_path, format="WEBP", quality=PREVIEW_WEBP_QUALITY, method=6)
    except Exception:
        preview_path = target_dir / f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:12]}.png"
        preview_path.write_bytes(image_data)

    preview_url = f"{preview_url_prefix.rstrip('/')}/{relative_dir.as_posix()}/{preview_path.name}"
    return _join_base_url(base_url, preview_url)


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
    def __init__(
        self,
        path: Path,
        *,
        max_entries: int = MAX_LOG_ENTRIES,
        preview_root: Path | None = REQUEST_LOG_PREVIEW_ROOT,
        preview_url_prefix: str = REQUEST_LOG_PREVIEW_URL_PREFIX,
    ) -> None:
        self.path = path
        self.max_entries = max(100, int(max_entries))
        self.preview_root = preview_root
        self.preview_url_prefix = f"/{str(preview_url_prefix or '').strip('/').rstrip('/')}/"
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.preview_root is not None:
            self.preview_root.mkdir(parents=True, exist_ok=True)

    def append(self, entry: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_entry(entry)
        with self._lock:
            items = self._read_entries_locked()
            items.append(normalized)
            removed_items: list[dict[str, Any]] = []
            if len(items) > self.max_entries:
                removed_items = items[:-self.max_entries]
                items = items[-self.max_entries:]
            self._write_entries_locked(items)
            if removed_items:
                self._delete_preview_files_locked(removed_items)
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

    def _preview_file_paths_from_entry(self, entry: dict[str, Any]) -> list[Path]:
        if self.preview_root is None:
            return []
        response = entry.get("response")
        if not isinstance(response, dict):
            return []
        preview_urls = response.get("preview_urls")
        if not isinstance(preview_urls, list):
            return []

        paths: list[Path] = []
        preview_root = self.preview_root.resolve()
        for preview_url in preview_urls:
            parsed_path = urlparse(str(preview_url or "").strip()).path
            if not parsed_path.startswith(self.preview_url_prefix):
                continue
            relative_path = parsed_path[len(self.preview_url_prefix):].strip("/")
            if not relative_path:
                continue
            candidate = (preview_root / relative_path).resolve()
            try:
                candidate.relative_to(preview_root)
            except ValueError:
                continue
            paths.append(candidate)
        return paths

    def _delete_preview_files_locked(self, entries: list[dict[str, Any]]) -> None:
        if self.preview_root is None:
            return
        deleted_paths: set[Path] = set()
        for entry in entries:
            for path in self._preview_file_paths_from_entry(entry):
                if path in deleted_paths:
                    continue
                deleted_paths.add(path)
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    continue
                self._prune_empty_preview_dirs(path.parent)

    def _prune_empty_preview_dirs(self, path: Path) -> None:
        if self.preview_root is None:
            return
        preview_root = self.preview_root.resolve()
        current = path.resolve()
        while current != preview_root:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

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
