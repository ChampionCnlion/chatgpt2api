from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import re
from time import perf_counter
from urllib.parse import urlparse
import uuid

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from api.support import raise_image_quota_error, require_api_access, resolve_image_base_url
from services.account_service import account_service
from services.chatgpt_service import ChatGPTService, ImageGenerationError
from services.newapi_service import NewAPIRequestError, NewAPIService
from services.request_log_service import MAX_PREVIEW_IMAGES_PER_LOG, request_log_store, save_request_log_preview
from utils.helper import (
    extract_chat_prompt,
    extract_response_prompt,
    extract_response_image_options,
    has_response_image_generation_tool,
    is_image_chat_request,
    normalize_image_options,
)

DATA_URL_IMAGE_RE = re.compile(r"(data:image/[^;]+;base64,[A-Za-z0-9+/=]+)")
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
PRIVATE_IPV4_RE = re.compile(r"^(10\.|127\.|192\.168\.|172\.(1[6-9]|2\d|3[0-1])\.)")


class ImageGenerationRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str = "gpt-image-2"
    n: int = Field(default=1, ge=1, le=4)
    response_format: str = "b64_json"
    size: str = "1024x1024"
    quality: str = "auto"
    background: str = "auto"
    output_format: str = "png"
    output_compression: int = 100
    moderation: str = "auto"
    partial_images: int = 0
    history_disabled: bool = True
    stream: bool | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    prompt: str | None = None
    n: int | None = None
    stream: bool | None = None
    modalities: list[str] | None = None
    messages: list[dict[str, object]] | None = None
    size: str | None = None
    quality: str | None = None
    background: str | None = None
    output_format: str | None = None
    output_compression: int | None = None
    moderation: str | None = None
    partial_images: int | None = None
    input_fidelity: str | None = None


class ResponseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    input: object | None = None
    tools: list[dict[str, object]] | None = None
    tool_choice: object | None = None
    stream: bool | None = None
    size: str | None = None
    quality: str | None = None
    background: str | None = None
    output_format: str | None = None
    output_compression: int | None = None
    moderation: str | None = None
    partial_images: int | None = None
    input_fidelity: str | None = None


def _raise_newapi_http_error(exc: NewAPIRequestError) -> None:
    raise HTTPException(status_code=exc.status_code, detail={"error": exc.message}) from exc


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_image_options_summary(
    summary: dict[str, object],
    image_options,
    *,
    include_input_fidelity: bool = False,
) -> None:
    summary["size"] = image_options.size
    summary["quality"] = image_options.quality
    summary["background"] = image_options.background
    summary["output_format"] = image_options.output_format
    summary["output_compression"] = image_options.output_compression
    summary["moderation"] = image_options.moderation
    summary["partial_images"] = image_options.partial_images
    if include_input_fidelity:
        summary["input_fidelity"] = image_options.input_fidelity


def _truncate_text(value: object, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _extract_error_message(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("error") or detail.get("message") or exc.status_code)
    return str(detail or exc.status_code)


def _request_client_ip(request: Request) -> str:
    forwarded = str(request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client is not None and request.client.host:
        return str(request.client.host)
    return ""


def _to_image_http_exception(exc: Exception) -> HTTPException:
    try:
        raise_image_quota_error(exc)
    except HTTPException as http_exc:
        return http_exc


def _decode_base64_image(image_b64: object) -> bytes | None:
    text = str(image_b64 or "").strip()
    if not text:
        return None
    try:
        return base64.b64decode(text, validate=True)
    except Exception:
        return None


def _append_preview_url(preview_urls: list[str], preview_url: str) -> None:
    normalized = str(preview_url or "").strip()
    if normalized:
        parsed = urlparse(normalized)
        hostname = str(parsed.hostname or "").strip().lower()
        is_internal_host = (
            not hostname
            or hostname in {"chatgpt2api", "localhost", "0.0.0.0", "::1"}
            or bool(PRIVATE_IPV4_RE.match(hostname))
        )
        if parsed.path.startswith("/images/") and is_internal_host:
            normalized = parsed.path
            if parsed.query:
                normalized = f"{normalized}?{parsed.query}"
            if parsed.fragment:
                normalized = f"{normalized}#{parsed.fragment}"
    if not normalized or normalized in preview_urls:
        return
    if len(preview_urls) >= MAX_PREVIEW_IMAGES_PER_LOG:
        return
    preview_urls.append(normalized)


def _add_preview_from_bytes(preview_urls: list[str], image_data: bytes | None, *, base_url: str | None = None) -> None:
    if not image_data or len(preview_urls) >= MAX_PREVIEW_IMAGES_PER_LOG:
        return
    preview_url = save_request_log_preview(image_data, base_url=base_url)
    if preview_url:
        _append_preview_url(preview_urls, preview_url)


def _add_preview_from_source(preview_urls: list[str], source: object, *, base_url: str | None = None) -> None:
    normalized = str(source or "").strip()
    if not normalized:
        return
    if normalized.lower().startswith("data:image/"):
        _, _, image_b64 = normalized.partition(",")
        _add_preview_from_bytes(preview_urls, _decode_base64_image(image_b64), base_url=base_url)
        return
    _append_preview_url(preview_urls, normalized)


def _collect_preview_urls_from_content(content: object, *, base_url: str | None = None) -> list[str]:
    preview_urls: list[str] = []
    seen_sources: set[str] = set()

    def add_source(source: object) -> None:
        normalized = str(source or "").strip()
        if not normalized or normalized in seen_sources:
            return
        seen_sources.add(normalized)
        _add_preview_from_source(preview_urls, normalized, base_url=base_url)

    def add_base64(image_b64: object) -> None:
        normalized = str(image_b64 or "").strip()
        if not normalized or normalized in seen_sources:
            return
        seen_sources.add(normalized)
        _add_preview_from_bytes(preview_urls, _decode_base64_image(normalized), base_url=base_url)

    def visit(value: object) -> None:
        if len(preview_urls) >= MAX_PREVIEW_IMAGES_PER_LOG:
            return
        if isinstance(value, str):
            for source in MARKDOWN_IMAGE_RE.findall(value):
                add_source(source)
                if len(preview_urls) >= MAX_PREVIEW_IMAGES_PER_LOG:
                    return
            for source in DATA_URL_IMAGE_RE.findall(value):
                add_source(source)
                if len(preview_urls) >= MAX_PREVIEW_IMAGES_PER_LOG:
                    return
            return
        if isinstance(value, list):
            for item in value:
                visit(item)
                if len(preview_urls) >= MAX_PREVIEW_IMAGES_PER_LOG:
                    return
            return
        if not isinstance(value, dict):
            return

        for key in ("b64_json", "result"):
            if key in value:
                add_base64(value.get(key))
                if len(preview_urls) >= MAX_PREVIEW_IMAGES_PER_LOG:
                    return

        for key in ("image_url", "url"):
            candidate = value.get(key)
            if isinstance(candidate, dict):
                candidate = candidate.get("url") or candidate.get("image_url")
            add_source(candidate)
            if len(preview_urls) >= MAX_PREVIEW_IMAGES_PER_LOG:
                return

        for key in ("content", "text", "input_text", "output_text"):
            if key in value:
                visit(value.get(key))
                if len(preview_urls) >= MAX_PREVIEW_IMAGES_PER_LOG:
                    return

        for key in ("message", "delta", "item", "response", "choices", "output", "data"):
            if key in value:
                visit(value.get(key))
                if len(preview_urls) >= MAX_PREVIEW_IMAGES_PER_LOG:
                    return

    visit(content)
    return preview_urls


def _collect_preview_urls_from_result(result: object, *, base_url: str | None = None) -> list[str]:
    if not isinstance(result, dict):
        return []

    preview_urls: list[str] = []

    data = result.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            _append_preview_url(preview_urls, str(item.get("url") or ""))
            _add_preview_from_bytes(preview_urls, _decode_base64_image(item.get("b64_json")), base_url=base_url)
            if len(preview_urls) >= MAX_PREVIEW_IMAGES_PER_LOG:
                break

    choices = result.get("choices")
    if isinstance(choices, list):
        for item in choices:
            for preview_url in _collect_preview_urls_from_content(item, base_url=base_url):
                _append_preview_url(preview_urls, preview_url)
            if len(preview_urls) >= MAX_PREVIEW_IMAGES_PER_LOG:
                break

    output = result.get("output")
    if isinstance(output, list):
        for item in output:
            for preview_url in _collect_preview_urls_from_content(item, base_url=base_url):
                _append_preview_url(preview_urls, preview_url)
            if len(preview_urls) >= MAX_PREVIEW_IMAGES_PER_LOG:
                break

    return preview_urls


def _image_response_summary(result: object, *, base_url: str | None = None) -> dict[str, object]:
    if not isinstance(result, dict):
        return {}
    data = result.get("data")
    image_count = len(data) if isinstance(data, list) else 0
    created = result.get("created")
    summary = {
        "image_count": image_count,
        "created": int(created) if str(created or "").strip() else None,
    }
    for key in (
        "size",
        "quality",
        "background",
        "output_format",
        "output_compression",
        "moderation",
        "partial_images",
        "input_fidelity",
    ):
        value = result.get(key)
        if value not in (None, ""):
            summary[key] = value
    preview_urls = _collect_preview_urls_from_result(result, base_url=base_url)
    if preview_urls:
        summary["preview_urls"] = preview_urls
    return summary


def _chat_response_summary(result: object, *, base_url: str | None = None) -> dict[str, object]:
    if not isinstance(result, dict):
        return {}
    summary: dict[str, object] = {}
    choices = result.get("choices")
    if isinstance(choices, list):
        summary["choice_count"] = len(choices)
    output = result.get("output")
    if isinstance(output, list):
        summary["output_count"] = len(output)
    usage = result.get("usage")
    if isinstance(usage, dict):
        total_tokens = usage.get("total_tokens")
        if isinstance(total_tokens, int):
            summary["total_tokens"] = total_tokens
    status = result.get("status")
    if isinstance(status, str) and status.strip():
        summary["status"] = status.strip()
    preview_urls = _collect_preview_urls_from_result(result, base_url=base_url)
    if preview_urls:
        summary["preview_urls"] = preview_urls
    return summary


def _write_request_log(
    request: Request,
    *,
    request_id: str,
    started_at: float,
    endpoint: str,
    model: str,
    request_summary: dict[str, object],
    status_code: int,
    success: bool,
    error: str = "",
    response_summary: dict[str, object] | None = None,
) -> None:
    try:
        request_log_store.append(
            {
                "request_id": request_id,
                "created_at": _utcnow_iso(),
                "method": request.method,
                "endpoint": endpoint,
                "model": str(model or "").strip(),
                "success": bool(success),
                "status_code": int(status_code),
                "duration_ms": int(max(0, (perf_counter() - started_at) * 1000)),
                "error": str(error or "").strip(),
                "client_ip": _request_client_ip(request),
                "user_agent": str(request.headers.get("user-agent") or "").strip(),
                "request": request_summary,
                "response": response_summary or {},
            }
        )
    except Exception as exc:
        print(f"[request-log] failed to append log: {exc}")


def _to_stream_http_exception(exc: Exception, *, image_request: bool = False) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if image_request:
        return _to_image_http_exception(exc)
    return HTTPException(status_code=502, detail={"error": str(exc) or exc.__class__.__name__})


def _logged_sse_json_stream(
    items,
    request: Request,
    *,
    request_id: str,
    started_at: float,
    endpoint: str,
    model: str,
    request_summary: dict[str, object],
    response_summary: dict[str, object] | None = None,
    base_url: str | None = None,
    image_request: bool = False,
):
    preview_urls: list[str] = []
    success = True
    status_code = 200
    error = ""
    finalized_summary = dict(response_summary or {})

    yield ": stream-open\n\n"
    try:
        for item in items:
            for preview_url in _collect_preview_urls_from_content(item, base_url=base_url):
                _append_preview_url(preview_urls, preview_url)
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
    except Exception as exc:
        success = False
        http_exc = _to_stream_http_exception(exc, image_request=image_request)
        status_code = http_exc.status_code
        error = _extract_error_message(http_exc)
        yield (
            f"data: {json.dumps({'error': {'message': error, 'type': exc.__class__.__name__}}, ensure_ascii=False)}\n\n"
        )
    yield "data: [DONE]\n\n"

    if preview_urls:
        finalized_summary["preview_urls"] = preview_urls
    _write_request_log(
        request,
        request_id=request_id,
        started_at=started_at,
        endpoint=endpoint,
        model=model,
        request_summary=request_summary,
        status_code=status_code,
        success=success,
        error=error,
        response_summary=finalized_summary,
    )


def create_router(chatgpt_service: ChatGPTService, newapi_service: NewAPIService) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/models")
    async def list_models(request: Request, authorization: str | None = Header(default=None)):
        require_api_access(request, authorization)
        request_id = uuid.uuid4().hex
        started_at = perf_counter()
        request_summary = {"stream": False}

        if newapi_service.is_enabled():
            request_headers = dict(request.headers)
            try:
                result = await run_in_threadpool(newapi_service.list_models, request_headers)
                _write_request_log(
                    request,
                    request_id=request_id,
                    started_at=started_at,
                    endpoint=request.url.path,
                    model="",
                    request_summary=request_summary,
                    status_code=200,
                    success=True,
                    response_summary={"model_count": len(result.get("data") or []) if isinstance(result, dict) else None},
                )
                return result
            except NewAPIRequestError as exc:
                _write_request_log(
                    request,
                    request_id=request_id,
                    started_at=started_at,
                    endpoint=request.url.path,
                    model="",
                    request_summary=request_summary,
                    status_code=exc.status_code,
                    success=False,
                    error=exc.message,
                )
                _raise_newapi_http_error(exc)

        try:
            result = await run_in_threadpool(chatgpt_service.list_models)
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model="",
                request_summary=request_summary,
                status_code=200,
                success=True,
                response_summary={"model_count": len(result.get("data") or []) if isinstance(result, dict) else None},
            )
            return result
        except Exception as exc:
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model="",
                request_summary=request_summary,
                status_code=502,
                success=False,
                error=str(exc),
            )
            raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc

    @router.post("/v1/images/generations")
    async def generate_images(
        body: ImageGenerationRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        require_api_access(request, authorization)
        request_id = uuid.uuid4().hex
        started_at = perf_counter()
        preview_base_url = resolve_image_base_url(request)
        image_options = normalize_image_options(body.model_dump(mode="python"))
        request_summary = {
            "prompt_preview": _truncate_text(body.prompt),
            "n": body.n,
            "response_format": body.response_format,
            "size": image_options.size,
            "quality": image_options.quality,
            "background": image_options.background,
            "output_format": image_options.output_format,
            "output_compression": image_options.output_compression,
            "moderation": image_options.moderation,
            "partial_images": image_options.partial_images,
            "stream": bool(body.stream),
        }

        if newapi_service.is_enabled():
            payload = body.model_dump(mode="python")
            request_headers = dict(request.headers)
            try:
                if body.stream:
                    result = await run_in_threadpool(newapi_service.stream_generate_images, request_headers, payload)
                    _write_request_log(
                        request,
                        request_id=request_id,
                        started_at=started_at,
                        endpoint=request.url.path,
                        model=body.model,
                        request_summary=request_summary,
                        status_code=200,
                        success=True,
                        response_summary={"stream": True},
                    )
                    return result

                result = await run_in_threadpool(newapi_service.generate_images, request_headers, payload)
                _write_request_log(
                    request,
                    request_id=request_id,
                    started_at=started_at,
                    endpoint=request.url.path,
                    model=body.model,
                    request_summary=request_summary,
                    status_code=200,
                    success=True,
                    response_summary=_image_response_summary(result, base_url=preview_base_url),
                )
                return result
            except NewAPIRequestError as exc:
                _write_request_log(
                    request,
                    request_id=request_id,
                    started_at=started_at,
                    endpoint=request.url.path,
                    model=body.model,
                    request_summary=request_summary,
                    status_code=exc.status_code,
                    success=False,
                    error=exc.message,
                )
                _raise_newapi_http_error(exc)

        base_url = preview_base_url
        try:
            if body.stream:
                await run_in_threadpool(account_service.get_available_access_token)
                result = StreamingResponse(
                    _logged_sse_json_stream(
                        chatgpt_service.stream_image_generation(
                            body.prompt,
                            body.model,
                            body.n,
                            body.response_format,
                            base_url,
                            image_options,
                        ),
                        request,
                        request_id=request_id,
                        started_at=started_at,
                        endpoint=request.url.path,
                        model=body.model,
                        request_summary=request_summary,
                        response_summary={"stream": True},
                        base_url=preview_base_url,
                        image_request=True,
                    ),
                    media_type="text/event-stream",
                )
                return result

            result = await run_in_threadpool(
                chatgpt_service.generate_with_pool,
                body.prompt,
                body.model,
                body.n,
                body.response_format,
                base_url,
                image_options,
            )
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model=body.model,
                request_summary=request_summary,
                status_code=200,
                success=True,
                response_summary=_image_response_summary(result, base_url=preview_base_url),
            )
            return result
        except RuntimeError as exc:
            http_exc = _to_image_http_exception(exc)
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model=body.model,
                request_summary=request_summary,
                status_code=http_exc.status_code,
                success=False,
                error=_extract_error_message(http_exc),
            )
            raise http_exc
        except ImageGenerationError as exc:
            http_exc = _to_image_http_exception(exc)
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model=body.model,
                request_summary=request_summary,
                status_code=http_exc.status_code,
                success=False,
                error=_extract_error_message(http_exc),
            )
            raise http_exc
        except HTTPException as exc:
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model=body.model,
                request_summary=request_summary,
                status_code=exc.status_code,
                success=False,
                error=_extract_error_message(exc),
            )
            raise
        except Exception as exc:
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model=body.model,
                request_summary=request_summary,
                status_code=500,
                success=False,
                error=str(exc),
            )
            raise

    @router.post("/v1/images/edits")
    async def edit_images(
        request: Request,
        authorization: str | None = Header(default=None),
        image: list[UploadFile] | None = File(default=None),
        image_list: list[UploadFile] | None = File(default=None, alias="image[]"),
        prompt: str = Form(...),
        model: str = Form(default="gpt-image-2"),
        n: int = Form(default=1),
        response_format: str = Form(default="b64_json"),
        size: str = Form(default="1024x1024"),
        quality: str = Form(default="auto"),
        background: str = Form(default="auto"),
        output_format: str = Form(default="png"),
        output_compression: int = Form(default=100),
        moderation: str = Form(default="auto"),
        partial_images: int = Form(default=0),
        input_fidelity: str = Form(default="low"),
        stream: bool | None = Form(default=None),
    ):
        require_api_access(request, authorization)
        request_id = uuid.uuid4().hex
        started_at = perf_counter()
        preview_base_url = resolve_image_base_url(request)
        image_options = normalize_image_options(
            {
                "size": size,
                "quality": quality,
                "background": background,
                "output_format": output_format,
                "output_compression": output_compression,
                "moderation": moderation,
                "partial_images": partial_images,
                "input_fidelity": input_fidelity,
            }
        )
        uploads = [*(image or []), *(image_list or [])]
        request_summary = {
            "prompt_preview": _truncate_text(prompt),
            "n": n,
            "response_format": response_format,
            "size": image_options.size,
            "quality": image_options.quality,
            "background": image_options.background,
            "output_format": image_options.output_format,
            "output_compression": image_options.output_compression,
            "moderation": image_options.moderation,
            "partial_images": image_options.partial_images,
            "input_fidelity": image_options.input_fidelity,
            "stream": bool(stream),
            "upload_count": len(uploads),
            "upload_names": [str(upload.filename or "image.png") for upload in uploads[:5]],
        }

        if n < 1 or n > 4:
            http_exc = HTTPException(status_code=400, detail={"error": "n must be between 1 and 4"})
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model=model,
                request_summary=request_summary,
                status_code=http_exc.status_code,
                success=False,
                error=_extract_error_message(http_exc),
            )
            raise http_exc

        if not uploads:
            http_exc = HTTPException(status_code=400, detail={"error": "image file is required"})
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model=model,
                request_summary=request_summary,
                status_code=http_exc.status_code,
                success=False,
                error=_extract_error_message(http_exc),
            )
            raise http_exc

        images: list[tuple[bytes, str, str]] = []
        for upload in uploads:
            image_data = await upload.read()
            if not image_data:
                http_exc = HTTPException(status_code=400, detail={"error": "image file is empty"})
                _write_request_log(
                    request,
                    request_id=request_id,
                    started_at=started_at,
                    endpoint=request.url.path,
                    model=model,
                    request_summary=request_summary,
                    status_code=http_exc.status_code,
                    success=False,
                    error=_extract_error_message(http_exc),
                )
                raise http_exc
            images.append((image_data, upload.filename or "image.png", upload.content_type or "image/png"))

        if newapi_service.is_enabled():
            form_data = {
                "prompt": prompt,
                "model": model,
                "n": str(n),
                "response_format": response_format,
                "size": image_options.size,
                "quality": image_options.quality,
                "background": image_options.background,
                "output_format": image_options.output_format,
                "output_compression": str(image_options.output_compression),
                "moderation": image_options.moderation,
                "partial_images": str(image_options.partial_images),
                "input_fidelity": image_options.input_fidelity,
            }
            if stream is not None:
                form_data["stream"] = "true" if stream else "false"
            upload_files = [("image", (filename, content, content_type)) for content, filename, content_type in images]
            request_headers = dict(request.headers)
            try:
                if stream:
                    result = await run_in_threadpool(
                        newapi_service.stream_edit_images,
                        request_headers,
                        form_data=form_data,
                        files=upload_files,
                    )
                    _write_request_log(
                        request,
                        request_id=request_id,
                        started_at=started_at,
                        endpoint=request.url.path,
                        model=model,
                        request_summary=request_summary,
                        status_code=200,
                        success=True,
                        response_summary={"stream": True},
                    )
                    return result

                result = await run_in_threadpool(
                    newapi_service.edit_images,
                    request_headers,
                    form_data=form_data,
                    files=upload_files,
                )
                _write_request_log(
                    request,
                    request_id=request_id,
                    started_at=started_at,
                    endpoint=request.url.path,
                    model=model,
                    request_summary=request_summary,
                    status_code=200,
                    success=True,
                    response_summary=_image_response_summary(result, base_url=preview_base_url),
                )
                return result
            except NewAPIRequestError as exc:
                _write_request_log(
                    request,
                    request_id=request_id,
                    started_at=started_at,
                    endpoint=request.url.path,
                    model=model,
                    request_summary=request_summary,
                    status_code=exc.status_code,
                    success=False,
                    error=exc.message,
                )
                _raise_newapi_http_error(exc)

        base_url = preview_base_url
        try:
            if stream:
                if not account_service.has_available_account():
                    raise_image_quota_error(RuntimeError("no available image quota"))
                result = StreamingResponse(
                    _logged_sse_json_stream(
                        chatgpt_service.stream_image_edit(
                            prompt,
                            images,
                            model,
                            n,
                            response_format,
                            base_url,
                            image_options,
                        ),
                        request,
                        request_id=request_id,
                        started_at=started_at,
                        endpoint=request.url.path,
                        model=model,
                        request_summary=request_summary,
                        response_summary={"stream": True},
                        base_url=preview_base_url,
                        image_request=True,
                    ),
                    media_type="text/event-stream",
                )
                return result

            result = await run_in_threadpool(
                chatgpt_service.edit_with_pool,
                prompt,
                images,
                model,
                n,
                response_format,
                base_url,
                image_options,
            )
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model=model,
                request_summary=request_summary,
                status_code=200,
                success=True,
                response_summary=_image_response_summary(result, base_url=preview_base_url),
            )
            return result
        except ImageGenerationError as exc:
            http_exc = _to_image_http_exception(exc)
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model=model,
                request_summary=request_summary,
                status_code=http_exc.status_code,
                success=False,
                error=_extract_error_message(http_exc),
            )
            raise http_exc
        except HTTPException as exc:
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model=model,
                request_summary=request_summary,
                status_code=exc.status_code,
                success=False,
                error=_extract_error_message(exc),
            )
            raise
        except Exception as exc:
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model=model,
                request_summary=request_summary,
                status_code=500,
                success=False,
                error=str(exc),
            )
            raise

    @router.post("/v1/chat/completions")
    async def create_chat_completion(
        body: ChatCompletionRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        require_api_access(request, authorization)
        payload = body.model_dump(mode="python")
        request_id = uuid.uuid4().hex
        started_at = perf_counter()
        image_request = is_image_chat_request(payload)
        preview_base_url = resolve_image_base_url(request)
        request_summary = {
            "prompt_preview": _truncate_text(extract_chat_prompt(payload)),
            "stream": bool(payload.get("stream")),
            "image_request": image_request,
            "message_count": len(payload.get("messages") or []) if isinstance(payload.get("messages"), list) else 0,
        }
        if image_request:
            try:
                _append_image_options_summary(request_summary, normalize_image_options(payload))
            except HTTPException as exc:
                _write_request_log(
                    request,
                    request_id=request_id,
                    started_at=started_at,
                    endpoint=request.url.path,
                    model=str(payload.get("model") or ""),
                    request_summary=request_summary,
                    status_code=exc.status_code,
                    success=False,
                    error=_extract_error_message(exc),
                )
                raise

        if newapi_service.is_enabled():
            request_headers = dict(request.headers)
            try:
                if bool(payload.get("stream")):
                    result = await run_in_threadpool(newapi_service.stream_chat_completion, request_headers, payload)
                    _write_request_log(
                        request,
                        request_id=request_id,
                        started_at=started_at,
                        endpoint=request.url.path,
                        model=str(payload.get("model") or ""),
                        request_summary=request_summary,
                        status_code=200,
                        success=True,
                        response_summary={"stream": True, "image_request": image_request},
                    )
                    return result

                result = await run_in_threadpool(newapi_service.create_chat_completion, request_headers, payload)
                response_summary = _chat_response_summary(result, base_url=preview_base_url)
                response_summary["image_request"] = image_request
                _write_request_log(
                    request,
                    request_id=request_id,
                    started_at=started_at,
                    endpoint=request.url.path,
                    model=str(payload.get("model") or ""),
                    request_summary=request_summary,
                    status_code=200,
                    success=True,
                    response_summary=response_summary,
                )
                return result
            except NewAPIRequestError as exc:
                _write_request_log(
                    request,
                    request_id=request_id,
                    started_at=started_at,
                    endpoint=request.url.path,
                    model=str(payload.get("model") or ""),
                    request_summary=request_summary,
                    status_code=exc.status_code,
                    success=False,
                    error=exc.message,
                )
                _raise_newapi_http_error(exc)

        try:
            if bool(payload.get("stream")):
                if image_request:
                    try:
                        await run_in_threadpool(account_service.get_available_access_token)
                    except RuntimeError as exc:
                        raise_image_quota_error(exc)
                result = StreamingResponse(
                    _logged_sse_json_stream(
                        chatgpt_service.stream_chat_completion(payload),
                        request,
                        request_id=request_id,
                        started_at=started_at,
                        endpoint=request.url.path,
                        model=str(payload.get("model") or ""),
                        request_summary=request_summary,
                        response_summary={"stream": True, "image_request": image_request},
                        base_url=preview_base_url,
                        image_request=image_request,
                    ),
                    media_type="text/event-stream",
                )
                return result

            result = await run_in_threadpool(chatgpt_service.create_chat_completion, payload)
            response_summary = _chat_response_summary(result, base_url=preview_base_url)
            response_summary["image_request"] = image_request
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model=str(payload.get("model") or ""),
                request_summary=request_summary,
                status_code=200,
                success=True,
                response_summary=response_summary,
            )
            return result
        except HTTPException as exc:
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model=str(payload.get("model") or ""),
                request_summary=request_summary,
                status_code=exc.status_code,
                success=False,
                error=_extract_error_message(exc),
            )
            raise
        except Exception as exc:
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model=str(payload.get("model") or ""),
                request_summary=request_summary,
                status_code=500,
                success=False,
                error=str(exc),
            )
            raise

    @router.post("/v1/responses")
    async def create_response(
        body: ResponseCreateRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        require_api_access(request, authorization)
        payload = body.model_dump(mode="python")
        request_id = uuid.uuid4().hex
        started_at = perf_counter()
        preview_base_url = resolve_image_base_url(request)
        response_image_request = has_response_image_generation_tool(payload)
        request_summary = {
            "prompt_preview": _truncate_text(extract_response_prompt(payload.get("input"))),
            "stream": bool(payload.get("stream")),
            "tool_count": len(payload.get("tools") or []) if isinstance(payload.get("tools"), list) else 0,
            "image_request": response_image_request,
        }
        if response_image_request:
            try:
                _append_image_options_summary(
                    request_summary,
                    extract_response_image_options(payload),
                    include_input_fidelity=True,
                )
            except HTTPException as exc:
                _write_request_log(
                    request,
                    request_id=request_id,
                    started_at=started_at,
                    endpoint=request.url.path,
                    model=str(payload.get("model") or ""),
                    request_summary=request_summary,
                    status_code=exc.status_code,
                    success=False,
                    error=_extract_error_message(exc),
                )
                raise

        if newapi_service.is_enabled():
            request_headers = dict(request.headers)
            try:
                if bool(payload.get("stream")):
                    result = await run_in_threadpool(newapi_service.stream_response, request_headers, payload)
                    _write_request_log(
                        request,
                        request_id=request_id,
                        started_at=started_at,
                        endpoint=request.url.path,
                        model=str(payload.get("model") or ""),
                        request_summary=request_summary,
                        status_code=200,
                        success=True,
                        response_summary={"stream": True, "image_request": response_image_request},
                    )
                    return result

                result = await run_in_threadpool(newapi_service.create_response, request_headers, payload)
                response_summary = _chat_response_summary(result, base_url=preview_base_url)
                response_summary["image_request"] = response_image_request
                _write_request_log(
                    request,
                    request_id=request_id,
                    started_at=started_at,
                    endpoint=request.url.path,
                    model=str(payload.get("model") or ""),
                    request_summary=request_summary,
                    status_code=200,
                    success=True,
                    response_summary=response_summary,
                )
                return result
            except NewAPIRequestError as exc:
                _write_request_log(
                    request,
                    request_id=request_id,
                    started_at=started_at,
                    endpoint=request.url.path,
                    model=str(payload.get("model") or ""),
                    request_summary=request_summary,
                    status_code=exc.status_code,
                    success=False,
                    error=exc.message,
                )
                _raise_newapi_http_error(exc)

        try:
            if bool(payload.get("stream")):
                result = StreamingResponse(
                    _logged_sse_json_stream(
                        chatgpt_service.stream_response(payload),
                        request,
                        request_id=request_id,
                        started_at=started_at,
                        endpoint=request.url.path,
                        model=str(payload.get("model") or ""),
                        request_summary=request_summary,
                        response_summary={"stream": True, "image_request": response_image_request},
                        base_url=preview_base_url,
                        image_request=response_image_request,
                    ),
                    media_type="text/event-stream",
                )
                return result

            result = await run_in_threadpool(chatgpt_service.create_response, payload)
            response_summary = _chat_response_summary(result, base_url=preview_base_url)
            response_summary["image_request"] = response_image_request
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model=str(payload.get("model") or ""),
                request_summary=request_summary,
                status_code=200,
                success=True,
                response_summary=response_summary,
            )
            return result
        except HTTPException as exc:
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model=str(payload.get("model") or ""),
                request_summary=request_summary,
                status_code=exc.status_code,
                success=False,
                error=_extract_error_message(exc),
            )
            raise
        except Exception as exc:
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model=str(payload.get("model") or ""),
                request_summary=request_summary,
                status_code=500,
                success=False,
                error=str(exc),
            )
            raise

    return router
