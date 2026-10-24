"""FastAPI application entrypoint: ``uvicorn app.main:app --reload``."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.db.session import SessionLocal, init_db
from app.services.seed import seed_catalog

logger = logging.getLogger("catalogpay")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        init_db()
        if settings.seed_on_startup:
            with SessionLocal() as session:
                report = seed_catalog(session)
            logger.info("Catalog seed complete: %s", report)
        yield

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Messy buyer orders -> SKU-verified PayPal Orders with multi-vendor payouts.",
        lifespan=lifespan,
    )
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
