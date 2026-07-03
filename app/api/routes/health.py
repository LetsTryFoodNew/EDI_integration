from __future__ import annotations

import redis.asyncio as aioredis
from fastapi import APIRouter

from app.config import get_settings
from app.db import check_db

router = APIRouter(tags=["health"])
settings = get_settings()


async def _check_redis() -> str:
    try:
        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
        await r.ping()
        await r.aclose()
        return "ok"
    except Exception as exc:
        return str(exc)


@router.get("/health")
async def health() -> dict[str, str]:
    db_status = await check_db()
    redis_status = await _check_redis()
    return {
        "status": "ok" if db_status == "ok" and redis_status == "ok" else "degraded",
        "db": db_status,
        "redis": redis_status,
    }
