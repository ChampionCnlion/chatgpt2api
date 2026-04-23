from __future__ import annotations

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from api.support import raise_image_quota_error, require_api_access, resolve_image_base_url
from services.account_service import account_service
from services.chatgpt_service import ChatGPTService, ImageGenerationError
from services.newapi_service import NewAPIRequestError, NewAPIService
from utils.helper import is_image_chat_request, sse_json_stream


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


def create_router(chatgpt_service: ChatGPTService, newapi_service: NewAPIService) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/models")
    async def list_models(request: Request, authorization: str | None = Header(default=None)):
        require_api_access(request, authorization)
        if newapi_service.is_enabled():
            request_headers = dict(request.headers)
            try:
                return await run_in_threadpool(newapi_service.list_models, request_headers)
            except NewAPIRequestError as exc:
                _raise_newapi_http_error(exc)
        try:
            return await run_in_threadpool(chatgpt_service.list_models)
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc

    @router.post("/v1/images/generations")
    async def generate_images(
            body: ImageGenerationRequest,
            request: Request,
            authorization: str | None = Header(default=None),
    ):
        require_api_access(request, authorization)
        if newapi_service.is_enabled():
            payload = body.model_dump(mode="python")
            request_headers = dict(request.headers)
            try:
                if body.stream:
                    return await run_in_threadpool(newapi_service.stream_generate_images, request_headers, payload)
                return await run_in_threadpool(newapi_service.generate_images, request_headers, payload)
            except NewAPIRequestError as exc:
                _raise_newapi_http_error(exc)
        base_url = resolve_image_base_url(request)
        if body.stream:
            try:
                await run_in_threadpool(account_service.get_available_access_token)
            except RuntimeError as exc:
                raise_image_quota_error(exc)
            return StreamingResponse(
                sse_json_stream(
                    chatgpt_service.stream_image_generation(
                        body.prompt, body.model, body.n, body.response_format, base_url
                    )
                ),
                media_type="text/event-stream",
            )
        try:
            return await run_in_threadpool(
                chatgpt_service.generate_with_pool, body.prompt, body.model, body.n, body.response_format, base_url
            )
        except ImageGenerationError as exc:
            raise_image_quota_error(exc)

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
        if n < 1 or n > 4:
            raise HTTPException(status_code=400, detail={"error": "n must be between 1 and 4"})
        uploads = [*(image or []), *(image_list or [])]
        if not uploads:
            raise HTTPException(status_code=400, detail={"error": "image file is required"})
        images: list[tuple[bytes, str, str]] = []
        for upload in uploads:
            image_data = await upload.read()
            if not image_data:
                raise HTTPException(status_code=400, detail={"error": "image file is empty"})
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
                    return await run_in_threadpool(
                        newapi_service.stream_edit_images,
                        request_headers,
                        form_data=form_data,
                        files=upload_files,
                    )
                return await run_in_threadpool(
                    newapi_service.edit_images,
                    request_headers,
                    form_data=form_data,
                    files=upload_files,
                )
            except NewAPIRequestError as exc:
                _raise_newapi_http_error(exc)
        base_url = resolve_image_base_url(request)
        if stream:
            if not account_service.has_available_account():
                raise_image_quota_error(RuntimeError("no available image quota"))
            return StreamingResponse(
                sse_json_stream(chatgpt_service.stream_image_edit(prompt, images, model, n, response_format, base_url)),
                media_type="text/event-stream",
            )
        try:
            return await run_in_threadpool(
                chatgpt_service.edit_with_pool, prompt, images, model, n, response_format, base_url
            )
        except ImageGenerationError as exc:
            raise_image_quota_error(exc)

    @router.post("/v1/chat/completions")
    async def create_chat_completion(
        body: ChatCompletionRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        require_api_access(request, authorization)
        payload = body.model_dump(mode="python")
        if newapi_service.is_enabled():
            request_headers = dict(request.headers)
            try:
                if bool(payload.get("stream")):
                    return await run_in_threadpool(newapi_service.stream_chat_completion, request_headers, payload)
                return await run_in_threadpool(newapi_service.create_chat_completion, request_headers, payload)
            except NewAPIRequestError as exc:
                _raise_newapi_http_error(exc)
        if bool(payload.get("stream")):
            if is_image_chat_request(payload):
                try:
                    await run_in_threadpool(account_service.get_available_access_token)
                except RuntimeError as exc:
                    raise_image_quota_error(exc)
            return StreamingResponse(
                sse_json_stream(chatgpt_service.stream_chat_completion(payload)),
                media_type="text/event-stream",
            )
        return await run_in_threadpool(chatgpt_service.create_chat_completion, payload)

    @router.post("/v1/responses")
    async def create_response(
        body: ResponseCreateRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        require_api_access(request, authorization)
        payload = body.model_dump(mode="python")
        if newapi_service.is_enabled():
            request_headers = dict(request.headers)
            try:
                if bool(payload.get("stream")):
                    return await run_in_threadpool(newapi_service.stream_response, request_headers, payload)
                return await run_in_threadpool(newapi_service.create_response, request_headers, payload)
            except NewAPIRequestError as exc:
                _raise_newapi_http_error(exc)
        if bool(payload.get("stream")):
            return StreamingResponse(
                sse_json_stream(chatgpt_service.stream_response(payload)),
                media_type="text/event-stream",
            )
        return await run_in_threadpool(chatgpt_service.create_response, payload)

    return router
