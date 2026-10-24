"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models.catalog import ProductSKU, Vendor
from app.services.sku_matcher import HybridSKUMatcher

DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def get_matcher(db: DbSession, settings: AppSettings) -> HybridSKUMatcher:
    """Index the live active catalog. Building it for 25 SKUs takes a few milliseconds."""
    skus = db.scalars(
        select(ProductSKU)
        .join(ProductSKU.vendor)
        .where(ProductSKU.is_active.is_(True), Vendor.is_active.is_(True))
        .options(joinedload(ProductSKU.vendor))
        .order_by(ProductSKU.sku_code)
    ).all()
    return HybridSKUMatcher.from_skus(
        skus,
        match_threshold=settings.match_confidence_threshold,
        low_confidence_threshold=settings.low_confidence_threshold,
    )


Matcher = Annotated[HybridSKUMatcher, Depends(get_matcher)]
