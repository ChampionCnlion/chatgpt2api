from __future__ import annotations

from collections.abc import Iterable, Mapping

from curl_cffi.requests import Session
from fastapi.responses import StreamingResponse

from services.config import config
from services.proxy_service import proxy_settings


class NewAPIRequestError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class NewAPIService:
    _HOP_HEADERS = {
        "authorization",
        "connection",
        "content-length",
        "host",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }

    def is_enabled(self) -> bool:
        return config.newapi_enabled

    def _ensure_configured(self) -> tuple[str, str, int]:
        base_url = config.newapi_base_url
        api_key = config.newapi_api_key
        timeout_seconds = config.newapi_timeout_seconds
        if not base_url:
            raise NewAPIRequestError(503, "newapi base_url is not configured")
        if not api_key:
            raise NewAPIRequestError(503, "newapi api_key is not configured")
        return base_url, api_key, timeout_seconds

    def _build_headers(
        self,
        request_headers: Mapping[str, str] | None = None,
        *,
        include_content_type: bool,
    ) -> dict[str, str]:
        _, api_key, _ = self._ensure_configured()
        headers: dict[str, str] = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        if request_headers:
            for key, value in request_headers.items():
                lowered = key.lower()
                if lowered in self._HOP_HEADERS:
                    continue
                if not include_content_type and lowered == "content-type":
                    continue
                headers[key] = value
        headers["Authorization"] = f"Bearer {api_key}"
        return headers

    @staticmethod
    def _extract_error_message(response) -> str:
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, dict):
                message = str(detail.get("error") or detail.get("message") or "").strip()
                if message:
                    return message
            message = str(payload.get("error") or payload.get("message") or "").strip()
            if message:
                return message
        try:
            text = response.text.strip()
        except Exception:
            text = ""
        return text or f"upstream request failed: HTTP {response.status_code}"

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        request_headers: Mapping[str, str] | None = None,
    ) -> dict:
        base_url, _, timeout_seconds = self._ensure_configured()
        session = Session(**proxy_settings.build_session_kwargs(verify=True))
        try:
            response = session.request(
                method=method,
                url=f"{base_url}{path}",
                headers=self._build_headers(request_headers, include_content_type=True),
                json=payload,
                timeout=timeout_seconds,
            )
            if not response.ok:
                raise NewAPIRequestError(response.status_code, self._extract_error_message(response))
            data = response.json()
            if not isinstance(data, dict):
                raise NewAPIRequestError(502, "newapi response payload is invalid")
            return data
        finally:
            session.close()

    def _request_multipart(
        self,
        path: str,
        *,
        data: dict[str, str],
        files: list[tuple[str, tuple[str, bytes, str]]],
        request_headers: Mapping[str, str] | None = None,
    ) -> dict:
        base_url, _, timeout_seconds = self._ensure_configured()
        session = Session(**proxy_settings.build_session_kwargs(verify=True))
        try:
            response = session.post(
                f"{base_url}{path}",
                headers=self._build_headers(request_headers, include_content_type=False),
                data=data,
                files=files,
                timeout=timeout_seconds,
            )
            if not response.ok:
                raise NewAPIRequestError(response.status_code, self._extract_error_message(response))
            payload = response.json()
            if not isinstance(payload, dict):
                raise NewAPIRequestError(502, "newapi response payload is invalid")
            return payload
        finally:
            session.close()

    def _stream_request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        data: dict[str, str] | None = None,
        files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
        request_headers: Mapping[str, str] | None = None,
    ) -> StreamingResponse:
        base_url, _, timeout_seconds = self._ensure_configured()
        session = Session(**proxy_settings.build_session_kwargs(verify=True))
        response = None
        try:
            response = session.request(
                method=method,
                url=f"{base_url}{path}",
                headers=self._build_headers(request_headers, include_content_type=files is None),
                json=payload,
                data=data,
                files=files,
                timeout=timeout_seconds,
                stream=True,
            )
            if not response.ok:
                raise NewAPIRequestError(response.status_code, self._extract_error_message(response))
        except Exception:
            if response is not None:
                response.close()
            session.close()
            raise

        content_type = str(response.headers.get("content-type") or "text/event-stream").strip() or "text/event-stream"

        def iter_bytes() -> Iterable[bytes]:
            try:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
            finally:
                response.close()
                session.close()

        return StreamingResponse(iter_bytes(), media_type=content_type)

    def list_models(self, request_headers: Mapping[str, str]) -> dict:
        return self._request_json("GET", "/v1/models", request_headers=request_headers)

    def generate_images(self, request_headers: Mapping[str, str], payload: dict) -> dict:
        return self._request_json("POST", "/v1/images/generations", payload=payload, request_headers=request_headers)

    def stream_generate_images(self, request_headers: Mapping[str, str], payload: dict) -> StreamingResponse:
        return self._stream_request("POST", "/v1/images/generations", payload=payload, request_headers=request_headers)

    def edit_images(
        self,
        request_headers: Mapping[str, str],
        *,
        form_data: dict[str, str],
        files: list[tuple[str, tuple[str, bytes, str]]],
    ) -> dict:
        return self._request_multipart(
            "/v1/images/edits",
            data=form_data,
            files=files,
            request_headers=request_headers,
        )

    def stream_edit_images(
        self,
        request_headers: Mapping[str, str],
        *,
        form_data: dict[str, str],
        files: list[tuple[str, tuple[str, bytes, str]]],
    ) -> StreamingResponse:
        return self._stream_request(
            "POST",
            "/v1/images/edits",
            data=form_data,
            files=files,
            request_headers=request_headers,
        )

    def create_chat_completion(self, request_headers: Mapping[str, str], payload: dict) -> dict:
        return self._request_json("POST", "/v1/chat/completions", payload=payload, request_headers=request_headers)

    def stream_chat_completion(self, request_headers: Mapping[str, str], payload: dict) -> StreamingResponse:
        return self._stream_request("POST", "/v1/chat/completions", payload=payload, request_headers=request_headers)

    def create_response(self, request_headers: Mapping[str, str], payload: dict) -> dict:
        return self._request_json("POST", "/v1/responses", payload=payload, request_headers=request_headers)

    def stream_response(self, request_headers: Mapping[str, str], payload: dict) -> StreamingResponse:
        return self._stream_request("POST", "/v1/responses", payload=payload, request_headers=request_headers)


newapi_service = NewAPIService()
