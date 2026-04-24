from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
import uuid

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from api.support import raise_image_quota_error, require_api_access, resolve_image_base_url
from services.account_service import account_service
from services.chatgpt_service import ChatGPTService, ImageGenerationError
from services.newapi_service import NewAPIRequestError, NewAPIService
from services.request_log_service import request_log_store
from utils.helper import extract_chat_prompt, extract_response_prompt, is_image_chat_request, sse_json_stream


class ImageGenerationRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str = "gpt-image-2"
    n: int = Field(default=1, ge=1, le=4)
    response_format: str = "b64_json"
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


class ResponseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    input: object | None = None
    tools: list[dict[str, object]] | None = None
    tool_choice: object | None = None
    stream: bool | None = None


def _raise_newapi_http_error(exc: NewAPIRequestError) -> None:
    raise HTTPException(status_code=exc.status_code, detail={"error": exc.message}) from exc


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _image_response_summary(result: object) -> dict[str, object]:
    if not isinstance(result, dict):
        return {}
    data = result.get("data")
    image_count = len(data) if isinstance(data, list) else 0
    created = result.get("created")
    return {
        "image_count": image_count,
        "created": int(created) if str(created or "").strip() else None,
    }


def _chat_response_summary(result: object) -> dict[str, object]:
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
        request_summary = {
            "prompt_preview": _truncate_text(body.prompt),
            "n": body.n,
            "response_format": body.response_format,
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
                    response_summary=_image_response_summary(result),
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

        base_url = resolve_image_base_url(request)
        try:
            if body.stream:
                await run_in_threadpool(account_service.get_available_access_token)
                result = StreamingResponse(
                    sse_json_stream(
                        chatgpt_service.stream_image_generation(
                            body.prompt,
                            body.model,
                            body.n,
                            body.response_format,
                            base_url,
                        )
                    ),
                    media_type="text/event-stream",
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
                    response_summary={"stream": True},
                )
                return result

            result = await run_in_threadpool(
                chatgpt_service.generate_with_pool,
                body.prompt,
                body.model,
                body.n,
                body.response_format,
                base_url,
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
                response_summary=_image_response_summary(result),
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
        stream: bool | None = Form(default=None),
    ):
        require_api_access(request, authorization)
        request_id = uuid.uuid4().hex
        started_at = perf_counter()
        uploads = [*(image or []), *(image_list or [])]
        request_summary = {
            "prompt_preview": _truncate_text(prompt),
            "n": n,
            "response_format": response_format,
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
                    response_summary=_image_response_summary(result),
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

        base_url = resolve_image_base_url(request)
        try:
            if stream:
                if not account_service.has_available_account():
                    raise_image_quota_error(RuntimeError("no available image quota"))
                result = StreamingResponse(
                    sse_json_stream(
                        chatgpt_service.stream_image_edit(prompt, images, model, n, response_format, base_url)
                    ),
                    media_type="text/event-stream",
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
                chatgpt_service.edit_with_pool,
                prompt,
                images,
                model,
                n,
                response_format,
                base_url,
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
                response_summary=_image_response_summary(result),
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
        request_summary = {
            "prompt_preview": _truncate_text(extract_chat_prompt(payload)),
            "stream": bool(payload.get("stream")),
            "image_request": image_request,
            "message_count": len(payload.get("messages") or []) if isinstance(payload.get("messages"), list) else 0,
        }

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
                response_summary = _chat_response_summary(result)
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
                    sse_json_stream(chatgpt_service.stream_chat_completion(payload)),
                    media_type="text/event-stream",
                )
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

            result = await run_in_threadpool(chatgpt_service.create_chat_completion, payload)
            response_summary = _chat_response_summary(result)
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
        request_summary = {
            "prompt_preview": _truncate_text(extract_response_prompt(payload.get("input"))),
            "stream": bool(payload.get("stream")),
            "tool_count": len(payload.get("tools") or []) if isinstance(payload.get("tools"), list) else 0,
        }

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
                        response_summary={"stream": True},
                    )
                    return result

                result = await run_in_threadpool(newapi_service.create_response, request_headers, payload)
                _write_request_log(
                    request,
                    request_id=request_id,
                    started_at=started_at,
                    endpoint=request.url.path,
                    model=str(payload.get("model") or ""),
                    request_summary=request_summary,
                    status_code=200,
                    success=True,
                    response_summary=_chat_response_summary(result),
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
                    sse_json_stream(chatgpt_service.stream_response(payload)),
                    media_type="text/event-stream",
                )
                _write_request_log(
                    request,
                    request_id=request_id,
                    started_at=started_at,
                    endpoint=request.url.path,
                    model=str(payload.get("model") or ""),
                    request_summary=request_summary,
                    status_code=200,
                    success=True,
                    response_summary={"stream": True},
                )
                return result

            result = await run_in_threadpool(chatgpt_service.create_response, payload)
            _write_request_log(
                request,
                request_id=request_id,
                started_at=started_at,
                endpoint=request.url.path,
                model=str(payload.get("model") or ""),
                request_summary=request_summary,
                status_code=200,
                success=True,
                response_summary=_chat_response_summary(result),
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
