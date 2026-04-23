from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from api.support import clear_admin_session_cookie, require_admin_access, set_admin_session_cookie
from services.config import config
from services.proxy_service import test_proxy


class LoginRequest(BaseModel):
    password: str = Field(default="", min_length=1)


class SettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")


class ProxyTestRequest(BaseModel):
    url: str = ""


def create_router(app_version: str) -> APIRouter:
    router = APIRouter()

    @router.post("/auth/login")
    async def login(body: LoginRequest, request: Request, response: Response):
        if str(body.password or "").strip() != config.admin_password:
            raise HTTPException(status_code=401, detail={"error": "password is invalid"})
        set_admin_session_cookie(response, request)
        return {"ok": True, "version": app_version}

    @router.post("/auth/logout")
    async def logout(response: Response):
        clear_admin_session_cookie(response)
        return {"ok": True}

    @router.get("/version")
    async def get_version():
        return {"version": app_version}

    @router.get("/api/settings")
    async def get_settings(request: Request, authorization: str | None = Header(default=None)):
        require_admin_access(request, authorization)
        return {"config": config.get()}

    @router.post("/api/settings")
    async def save_settings(
        body: SettingsUpdateRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        require_admin_access(request, authorization)
        return {"config": config.update(body.model_dump(mode="python"))}

    @router.post("/api/proxy/test")
    async def test_proxy_endpoint(
        body: ProxyTestRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        require_admin_access(request, authorization)
        candidate = (body.url or "").strip() or config.get_proxy_settings()
        if not candidate:
            raise HTTPException(status_code=400, detail={"error": "proxy url is required"})
        return {"result": await run_in_threadpool(test_proxy, candidate)}

    return router
