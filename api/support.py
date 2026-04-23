from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
from threading import Event, Thread
import time

from fastapi import HTTPException, Request, Response

from services.account_service import account_service
from services.config import config

BASE_DIR = Path(__file__).resolve().parents[1]
WEB_DIST_DIR = BASE_DIR / "web_dist"
ADMIN_SESSION_COOKIE = "chatgpt2api_admin_session"
ADMIN_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60


def extract_bearer_token(authorization: str | None) -> str:
    scheme, _, value = str(authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return ""
    return value.strip()


def has_valid_auth_key(authorization: str | None) -> bool:
    auth_key = str(config.auth_key or "").strip()
    return bool(auth_key) and extract_bearer_token(authorization) == auth_key


def require_auth_key(authorization: str | None) -> None:
    if not has_valid_auth_key(authorization):
        raise HTTPException(status_code=401, detail={"error": "authorization is invalid"})


def _session_secret() -> bytes:
    raw = f"{config.auth_key}:{config.admin_password}:chatgpt2api-admin-session"
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")


def _b64decode(value: str) -> bytes:
    normalized = value + ("=" * (-len(value) % 4))
    return base64.urlsafe_b64decode(normalized.encode("utf-8"))


def _parse_admin_session(token: str | None) -> dict | None:
    token_value = str(token or "").strip()
    if not token_value or "." not in token_value:
        return None
    payload_part, signature_part = token_value.rsplit(".", 1)
    expected_signature = hmac.new(
        _session_secret(),
        payload_part.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature_part, expected_signature):
        return None
    try:
        payload = json.loads(_b64decode(payload_part).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    expires_at = int(payload.get("exp") or 0)
    if expires_at <= int(time.time()):
        return None
    return payload


def create_admin_session_token(*, ttl_seconds: int = ADMIN_SESSION_TTL_SECONDS) -> str:
    payload = {
        "exp": int(time.time()) + max(60, int(ttl_seconds)),
        "iat": int(time.time()),
    }
    payload_text = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_part = _b64encode(payload_text)
    signature = hmac.new(_session_secret(), payload_part.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload_part}.{signature}"


def request_is_secure(request: Request) -> bool:
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").strip().lower()
    if forwarded_proto:
        return forwarded_proto == "https"
    return request.url.scheme == "https"


def set_admin_session_cookie(response: Response, request: Request) -> None:
    response.set_cookie(
        key=ADMIN_SESSION_COOKIE,
        value=create_admin_session_token(),
        max_age=ADMIN_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request_is_secure(request),
        path="/",
    )


def clear_admin_session_cookie(response: Response) -> None:
    response.delete_cookie(key=ADMIN_SESSION_COOKIE, path="/")


def has_valid_admin_session(request: Request | None) -> bool:
    if request is None:
        return False
    return _parse_admin_session(request.cookies.get(ADMIN_SESSION_COOKIE)) is not None


def require_admin_access(request: Request, authorization: str | None = None) -> None:
    if has_valid_auth_key(authorization) or has_valid_admin_session(request):
        return
    raise HTTPException(status_code=401, detail={"error": "admin authorization is invalid"})


def require_api_access(request: Request, authorization: str | None = None) -> None:
    if has_valid_auth_key(authorization) or has_valid_admin_session(request):
        return
    raise HTTPException(status_code=401, detail={"error": "authorization is invalid"})


def resolve_image_base_url(request: Request) -> str:
    return config.base_url or f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"


def raise_image_quota_error(exc: Exception) -> None:
    message = str(exc)
    if "no available image quota" in message.lower():
        raise HTTPException(status_code=429, detail={"error": "no available image quota"}) from exc
    raise HTTPException(status_code=502, detail={"error": message}) from exc


def sanitize_cpa_pool(pool: dict | None) -> dict | None:
    if not isinstance(pool, dict):
        return None
    return {key: value for key, value in pool.items() if key != "secret_key"}


def sanitize_cpa_pools(pools: list[dict]) -> list[dict]:
    return [sanitized for pool in pools if (sanitized := sanitize_cpa_pool(pool)) is not None]


def sanitize_sub2api_server(server: dict | None) -> dict | None:
    if not isinstance(server, dict):
        return None
    sanitized = {key: value for key, value in server.items() if key not in {"password", "api_key"}}
    sanitized["has_api_key"] = bool(str(server.get("api_key") or "").strip())
    return sanitized


def sanitize_sub2api_servers(servers: list[dict]) -> list[dict]:
    return [sanitized for server in servers if (sanitized := sanitize_sub2api_server(server)) is not None]


def start_limited_account_watcher(stop_event: Event) -> Thread:
    interval_seconds = config.refresh_account_interval_minute * 60

    def worker() -> None:
        while not stop_event.is_set():
            try:
                limited_tokens = account_service.list_limited_tokens()
                if limited_tokens:
                    print(f"[account-limited-watcher] checking {len(limited_tokens)} limited accounts")
                    account_service.refresh_accounts(limited_tokens)
            except Exception as exc:
                print(f"[account-limited-watcher] fail {exc}")
            stop_event.wait(interval_seconds)

    thread = Thread(target=worker, name="limited-account-watcher", daemon=True)
    thread.start()
    return thread


def resolve_web_asset(requested_path: str) -> Path | None:
    if not WEB_DIST_DIR.exists():
        return None
    clean_path = requested_path.strip("/")
    base_dir = WEB_DIST_DIR.resolve()
    candidates = [base_dir / "index.html"] if not clean_path else [
        base_dir / Path(clean_path),
        base_dir / clean_path / "index.html",
        base_dir / f"{clean_path}.html",
    ]
    for candidate in candidates:
        try:
            candidate.resolve().relative_to(base_dir)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None
