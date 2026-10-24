"""FastAPI application entrypoint: ``uvicorn app.main:app --reload``."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.orders import PARSE_MEDIA_PATH
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.limits import BodySizeLimitMiddleware
from app.db.session import SessionLocal, init_db
from app.services.gemini_parser import OrderParserError
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
        yield

    app = FastAPI(
        title=settings.app_name,
        version="0.2.0",
        description="Messy buyer orders -> SKU-verified PayPal Orders with multi-vendor payouts.",
        lifespan=lifespan,
    )
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_body_bytes=settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES,
        paths=frozenset({settings.api_v1_prefix + PARSE_MEDIA_PATH}),
    )

    @app.exception_handler(OrderParserError)
    async def _order_parser_error(_request: Request, exc: OrderParserError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.message, "error": exc.code}
        )

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
