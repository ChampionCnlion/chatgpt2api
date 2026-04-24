from __future__ import annotations

from fastapi import APIRouter, Header, Query, Request

from api.support import require_admin_access
from services.request_log_service import request_log_store


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/request-logs")
    async def get_request_logs(
        request: Request,
        authorization: str | None = Header(default=None),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
    ):
        require_admin_access(request, authorization)
        result = request_log_store.list(page=page, page_size=page_size)
        return {
            "items": result.items,
            "total": result.total,
            "page": result.page,
            "page_size": result.page_size,
        }

    return router
