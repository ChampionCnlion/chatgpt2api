from __future__ import annotations

from dataclasses import dataclass
import json
import os
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
CONFIG_FILE = BASE_DIR / "config.json"
VERSION_FILE = BASE_DIR / "VERSION"


@dataclass(frozen=True)
class LoadedSettings:
    auth_key: str
    admin_password: str
    refresh_account_interval_minute: int


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _normalize_auth_key(value: object) -> str:
    return _normalize_text(value)


def _is_invalid_auth_key(value: object) -> bool:
    return _normalize_auth_key(value) == ""


def _normalize_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = _normalize_text(value).lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _normalize_int(value: object, default: int, minimum: int | None = None) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = default
    if minimum is not None:
        normalized = max(minimum, normalized)
    return normalized


def _normalize_newapi_config(raw: object) -> dict[str, object]:
    data = raw if isinstance(raw, dict) else {}
    base_url = _normalize_text(
        os.getenv("CHATGPT2API_NEWAPI_BASE_URL") or data.get("base_url")
    ).rstrip("/")
    return {
        "enabled": _normalize_bool(
            os.getenv("CHATGPT2API_NEWAPI_ENABLED") or data.get("enabled"),
            default=False,
        ),
        "base_url": base_url,
        "api_key": _normalize_text(
            os.getenv("CHATGPT2API_NEWAPI_API_KEY") or data.get("api_key")
        ),
        "timeout_seconds": _normalize_int(
            os.getenv("CHATGPT2API_NEWAPI_TIMEOUT_SECONDS") or data.get("timeout_seconds"),
            120,
            minimum=5,
        ),
    }


def _effective_admin_password(raw_config: dict[str, object], auth_key: str) -> str:
    return _normalize_text(
        os.getenv("CHATGPT2API_ADMIN_PASSWORD") or raw_config.get("admin-password") or auth_key
    )


def _read_json_object(path: Path, *, name: str) -> dict[str, object]:
    if not path.exists():
        return {}
    if path.is_dir():
        print(
            f"Warning: {name} at '{path}' is a directory, ignoring it and falling back to other configuration sources.",
            file=sys.stderr,
        )
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _load_settings() -> LoadedSettings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw_config = _read_json_object(CONFIG_FILE, name="config.json")
    auth_key = _normalize_auth_key(os.getenv("CHATGPT2API_AUTH_KEY") or raw_config.get("auth-key"))
    if _is_invalid_auth_key(auth_key):
        raise ValueError(
            "❌ auth-key 未设置！\n"
            "请在环境变量 CHATGPT2API_AUTH_KEY 中设置，或者在 config.json 中填写 auth-key。"
        )

    refresh_interval = _normalize_int(raw_config.get("refresh_account_interval_minute", 5), 5, minimum=1)
    admin_password = _effective_admin_password(raw_config, auth_key)

    return LoadedSettings(
        auth_key=auth_key,
        admin_password=admin_password,
        refresh_account_interval_minute=refresh_interval,
    )


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.data = self._load()
        if _is_invalid_auth_key(self.auth_key):
            raise ValueError(
                "❌ auth-key 未设置！\n"
                "请按以下任意一种方式解决：\n"
                "1. 在 Render 的 Environment 变量中添加：\n"
                "   CHATGPT2API_AUTH_KEY = your_real_auth_key\n"
                "2. 或者在 config.json 中填写：\n"
                '   "auth-key": "your_real_auth_key"'
            )

    def _load(self) -> dict[str, object]:
        return _read_json_object(self.path, name="config.json")

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _normalized_effective_data(self, raw: dict[str, Any] | None = None) -> dict[str, object]:
        data = dict(raw or self.data)
        auth_key = _normalize_auth_key(os.getenv("CHATGPT2API_AUTH_KEY") or data.get("auth-key"))
        admin_password = _effective_admin_password(data, auth_key)
        return {
            **data,
            "auth-key": auth_key,
            "admin-password": admin_password,
            "refresh_account_interval_minute": _normalize_int(
                data.get("refresh_account_interval_minute", 5),
                5,
                minimum=1,
            ),
            "proxy": _normalize_text(data.get("proxy")),
            "base_url": _normalize_text(os.getenv("CHATGPT2API_BASE_URL") or data.get("base_url")).rstrip("/"),
            "newapi": _normalize_newapi_config(data.get("newapi")),
        }

    @property
    def auth_key(self) -> str:
        return _normalize_auth_key(os.getenv("CHATGPT2API_AUTH_KEY") or self.data.get("auth-key"))

    @property
    def admin_password(self) -> str:
        return _effective_admin_password(self.data, self.auth_key)

    @property
    def accounts_file(self) -> Path:
        return DATA_DIR / "accounts.json"

    @property
    def refresh_account_interval_minute(self) -> int:
        return _normalize_int(self.data.get("refresh_account_interval_minute", 5), 5, minimum=1)

    @property
    def images_dir(self) -> Path:
        path = DATA_DIR / "images"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def base_url(self) -> str:
        return _normalize_text(
            os.getenv("CHATGPT2API_BASE_URL")
            or self.data.get("base_url")
            or ""
        ).rstrip("/")

    @property
    def newapi(self) -> dict[str, object]:
        return _normalize_newapi_config(self.data.get("newapi"))

    @property
    def newapi_enabled(self) -> bool:
        return bool(self.newapi.get("enabled"))

    @property
    def newapi_base_url(self) -> str:
        return _normalize_text(self.newapi.get("base_url")).rstrip("/")

    @property
    def newapi_api_key(self) -> str:
        return _normalize_text(self.newapi.get("api_key"))

    @property
    def newapi_timeout_seconds(self) -> int:
        return _normalize_int(self.newapi.get("timeout_seconds"), 120, minimum=5)

    @property
    def app_version(self) -> str:
        try:
            value = VERSION_FILE.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return "0.0.0"
        return value or "0.0.0"

    def get(self) -> dict[str, object]:
        return self._normalized_effective_data()

    def get_proxy_settings(self) -> str:
        return _normalize_text(self.data.get("proxy"))

    def update(self, data: dict[str, object]) -> dict[str, object]:
        next_data = dict(self.data)
        next_data.update(dict(data or {}))
        if _is_invalid_auth_key(next_data.get("auth-key")):
            next_data["auth-key"] = self.data.get("auth-key") or os.getenv("CHATGPT2API_AUTH_KEY") or ""
        if _normalize_text(next_data.get("admin-password")) == "":
            next_data["admin-password"] = self.data.get("admin-password") or os.getenv("CHATGPT2API_ADMIN_PASSWORD") or next_data.get("auth-key") or ""
        requested_newapi = next_data.get("newapi")
        if not isinstance(requested_newapi, dict):
            requested_newapi = {}
        merged_newapi = _normalize_newapi_config(
            {
                **_normalize_newapi_config(self.data.get("newapi")),
                **requested_newapi,
            }
        )
        next_data["newapi"] = merged_newapi
        next_data["proxy"] = _normalize_text(next_data.get("proxy"))
        next_data["base_url"] = _normalize_text(next_data.get("base_url")).rstrip("/")
        next_data["refresh_account_interval_minute"] = _normalize_int(
            next_data.get("refresh_account_interval_minute", 5),
            5,
            minimum=1,
        )
        self.data = next_data
        self._save()
        return self.get()


config = ConfigStore(CONFIG_FILE)
