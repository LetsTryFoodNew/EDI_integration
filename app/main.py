from __future__ import annotations

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.health import router as health_router
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ────────────────────────────────────────────────────────────────────
app.include_router(health_router)

# Phase 1+ routes (registered here as they are built)
# from app.api.routes import pos, master_data, exceptions, dashboard, auth, b1_logs
# app.include_router(pos.router, prefix="/api")
# ...
