from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.common.errors import AppError
from app.common.logging import CorrelationIdMiddleware, SecurityHeadersMiddleware, configure_logging
from app.config import get_settings
from app.farmos.idempotency import IdempotencyMiddleware

settings = get_settings()
configure_logging(settings.log_level)

if settings.is_production and settings.auth_dev_mode:
    raise RuntimeError("AUTH_DEV_MODE must never be enabled in production")

app = FastAPI(
    title="Origami Server API",
    version="0.1.0",
    description=(
        "Multi-tenant control plane and secure FarmOS API platform. "
        "See /docs for the OpenAPI schema, grouped by tag."
    ),
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(IdempotencyMiddleware)
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.get("/healthz", tags=["Health"])
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/health", tags=["Health"])
def health() -> dict:
    """The FarmOS tablet app's own liveness ping — deliberately at the
    server root, not under /api/v1 (that prefixed path is the animal-health
    module's own route group, a different thing entirely). Unauthenticated
    and as cheap as /healthz on purpose: the app calls this just to decide
    whether it's online, and anything under 500 counts as reachable.
    """
    return {"status": "ok"}


@app.get("/readyz", tags=["Health"])
def readyz() -> dict:
    from sqlalchemy import text

    from app.common.db import control_engine, tenant_shared_engine

    checks = {}
    for name, engine in (("control_db", control_engine), ("tenant_db", tenant_shared_engine)):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            checks[name] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks[name] = f"error: {exc}"
    healthy = all(v == "ok" for v in checks.values())
    return {"status": "ok" if healthy else "degraded", "checks": checks}


from app.auth.routes import router as auth_router  # noqa: E402
from app.backups.routes import router as backups_router  # noqa: E402
from app.devices.routes import router as devices_router  # noqa: E402
from app.farmos.routes_auth import router as farmos_auth_router  # noqa: E402
from app.farmos.routes_employees import router as farmos_employees_router  # noqa: E402
from app.farmos.routes_farms import router as farmos_farms_router  # noqa: E402
from app.files.routes import router as files_router  # noqa: E402
from app.platform.routes import router as platform_router  # noqa: E402
from app.support.routes import router as support_router  # noqa: E402
from app.sync.routes import router as sync_router  # noqa: E402

app.include_router(auth_router, prefix="/api/v1/auth", tags=["Auth"])
# The FarmOS tablet contract (docs/FARMOS_API.md) — every path relative to
# /api/v1, matched exactly against the reference backend's OpenAPI schema.
app.include_router(farmos_auth_router, prefix="/api/v1", tags=["FarmOS: Auth"])
app.include_router(farmos_employees_router, prefix="/api/v1", tags=["FarmOS: Employees"])
app.include_router(farmos_farms_router, prefix="/api/v1", tags=["FarmOS: Farm"])
app.include_router(sync_router, prefix="/api/v1/sync", tags=["Sync"])
app.include_router(files_router, prefix="/api/v1/files", tags=["Files"])
app.include_router(devices_router, prefix="/api/v1", tags=["Devices"])
app.include_router(platform_router, prefix="/platform/v1", tags=["Platform"])
app.include_router(support_router, prefix="/platform/v1", tags=["Platform Support"])
app.include_router(backups_router, prefix="/platform/v1", tags=["Platform Backups"])
