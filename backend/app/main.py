"""FastAPI application entrypoint: ``uvicorn app.main:app --reload``."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deps import close_paypal_clients
from app.api.v1.orders import PARSE_MEDIA_PATH
from app.api.v1.router import api_router
from app.api.v1.webhooks import MAX_WEBHOOK_BYTES, PAYPAL_WEBHOOK_PATH
from app.core.config import Settings, get_settings
from app.core.errors import DomainError
from app.core.limits import BodySizeLimitMiddleware
from app.core.spa import mount_spa
from app.db.session import SessionLocal, init_db
from app.services.demo_orders import seed_demo_orders
from app.services.seed import seed_catalog

logger = logging.getLogger("catalogpay")

# Room for multipart boundaries, headers and the small form fields around the file.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        init_db()
        if settings.seed_on_startup:
            with SessionLocal() as session:
                report = seed_catalog(session)
                logger.info("Catalog seed complete: %s", report)
                if settings.seed_demo_orders and seed_demo_orders(session):
                    logger.info("Seeded demo order history")
        yield
        await close_paypal_clients()

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="Messy buyer orders -> SKU-verified PayPal Orders with multi-vendor payouts.",
        lifespan=lifespan,
    )
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_body_bytes=settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES,
        paths=frozenset({settings.api_v1_prefix + PARSE_MEDIA_PATH}),
    )
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_body_bytes=MAX_WEBHOOK_BYTES,
        paths=frozenset({settings.api_v1_prefix + PAYPAL_WEBHOOK_PATH}),
    )
    # Added last so it is outermost: even 413s from the size limiter carry CORS headers.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key"],
    )

    @app.exception_handler(DomainError)
    async def _domain_error(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.message, "error": exc.code}
        )

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    # Production: serve the built Command Center from the same origin (must be registered
    # after the API so /api routes always win over the SPA fallback).
    dist = settings.resolved_static_dist()
    if dist is not None:
        mount_spa(app, dist, api_prefix=settings.api_v1_prefix)
        logger.info("Serving frontend from %s", dist)
    return app


app = create_app()
