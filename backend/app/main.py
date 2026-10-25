"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import migrations_check
from app.api import (
    routes_auth,
    routes_control,
    routes_diagnostics,
    routes_executive,
    routes_production,
    routes_research,
    routes_youtube,
)
from app.api.ratelimit import RateLimiter
from app.config import get_settings
from app.research import ideas as ideas_svc
from app.research import notes as notes_svc
from app.research import seo as seo_svc
from app.research import thumbs as thumbs_svc
from app.security import redaction
from app.security.permissions import PermissionDenied
from app.services import approvals, board, control, evidence, finance, registry, reports, settings, tasks, videos
from app.services import gate as gate_svc
from app.storage import UnsafePath
from app.youtube import connection as conn_svc
from app.youtube import quota as quota_svc
from app.youtube import upload as upload_svc

log = logging.getLogger("api")


class SecurityMiddleware(BaseHTTPMiddleware):
    """Origin check for writes, rate limiting and security headers."""

    def __init__(self, app, allowed_origins: set[str], limiter: RateLimiter):
        super().__init__(app)
        self.allowed = allowed_origins
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next):
        ip = request.client.host if request.client else "?"
        if not self.limiter.allow(ip):
            return JSONResponse({"detail": "Too many requests."}, status_code=429, headers={"Retry-After": "30"})
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin is not None and origin not in self.allowed:
                return JSONResponse({"detail": "Cross-origin request refused."}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        return response


def create_app() -> FastAPI:
    s = get_settings()
    redaction.register_secrets(s.secret_values())
    redaction.install_log_redaction()
    registry.load()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        s.data_dir.mkdir(parents=True, exist_ok=True)
        migrations_check.assert_db_current()  # refuse to run against an outdated schema
        log.info("API ready (env=%s)", s.env)
        yield

    dev = s.env != "production"
    app = FastAPI(title="AI Media Company", version="0.1.0", lifespan=lifespan,
                  docs_url="/api/docs" if dev else None, redoc_url=None,
                  openapi_url="/api/openapi.json" if dev else None)
    origins = {s.frontend_origin, "http://localhost:3000", "http://127.0.0.1:3000",
               f"http://localhost:{s.port}", f"http://127.0.0.1:{s.port}"}
    app.add_middleware(SecurityMiddleware, allowed_origins=origins, limiter=RateLimiter(s.api_rate_limit_per_minute))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[h.strip() for h in s.allowed_hosts.split(",") if h.strip()])

    for r in (routes_auth.router, routes_executive.router, routes_control.router, routes_diagnostics.router, routes_production.router,
              routes_research.router, routes_youtube.router):
        app.include_router(r)

    @app.get("/api/health", tags=["system"])
    def health():
        return {"status": "ok"}

    # --- error mapping: domain errors become 4xx with a safe message; anything else is a generic 500 ---
    def err(status: int):
        async def handler(_: Request, exc: Exception):
            return JSONResponse({"detail": str(exc)}, status_code=status)

        return handler

    async def denied(_: Request, exc: PermissionDenied):
        return JSONResponse({"detail": "You do not have permission to do that."}, status_code=403)

    async def blocked(_: Request, exc: gate_svc.GateBlocked):
        return JSONResponse({"detail": str(exc), "gate": exc.result.as_dict()}, status_code=409)

    async def unsafe(_: Request, exc: UnsafePath):
        return JSONResponse({"detail": "Invalid file path."}, status_code=400)

    async def unexpected(_: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse({"detail": "Internal error. See the server log."}, status_code=500)

    app.add_exception_handler(PermissionDenied, denied)
    app.add_exception_handler(gate_svc.GateBlocked, blocked)
    app.add_exception_handler(UnsafePath, unsafe)
    for exc_type, code in (
        (board.BoardError, 409), (board.AcknowledgementRequired, 409), (tasks.TaskError, 409),
        (approvals.ApprovalError, 409), (settings.SettingError, 422), (videos.VideoError, 409),
        (reports.ReportError, 409), (evidence.EvidenceError, 422), (finance.BudgetExceeded, 402),
        (control.ExternalActionBlocked, 409), (ideas_svc.IdeaError, 409), (notes_svc.ResearchError, 422), (seo_svc.SeoError, 409),
        (thumbs_svc.ThumbError, 409),
        (conn_svc.YouTubeError, 409), (upload_svc.UploadError, 409), (quota_svc.QuotaExceeded, 409),
    ):
        app.add_exception_handler(exc_type, err(code))
    app.add_exception_handler(Exception, unexpected)
    app.add_exception_handler(HTTPException, _http_exc)
    return app


async def _http_exc(_: Request, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


def asgi() -> FastAPI:
    """ASGI factory: ``uvicorn app.main:asgi --factory``."""
    return create_app()
