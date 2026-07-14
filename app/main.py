from __future__ import annotations

from pathlib import Path

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.middleware import AuditMiddleware
from app.api.routes.auth import router as auth_router
from app.api.routes.b1_logs import router as b1_logs_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.exceptions import router as exceptions_router
from app.api.routes.health import router as health_router
from app.api.routes.inbox import router as inbox_router
from app.api.routes.master_data import router as master_data_router
from app.api.routes.pos import router as pos_router
from app.api.routes.webhooks import router as webhooks_router
from app.config import get_settings
from app.logging_config import configure_logging

settings = get_settings()

configure_logging(settings.environment)

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=0.1,
    )

app = FastAPI(
    title="EDI Middleware — Let's Try Foods",
    description=(
        "Custom EDI middleware that receives Purchase Orders from ~15 retail platforms "
        "(Blinkit, Zepto, Swiggy, BigBasket, Amazon, etc.), normalizes them to a "
        "canonical EDI 850 schema, and pushes Sales Orders into SAP Business One."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(AuditMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ────────────────────────────────────────────────────────────────────
app.include_router(health_router)
app.include_router(webhooks_router)
app.include_router(auth_router)
app.include_router(pos_router)
app.include_router(dashboard_router)
app.include_router(master_data_router)
app.include_router(b1_logs_router)
app.include_router(exceptions_router)
app.include_router(inbox_router)

# ── SPA static files (production) ────────────────────────────────────────────
_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="spa")
