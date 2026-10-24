from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from app.api.deps import AppSettings, DbSession, Matcher
from app.models.catalog import ProductSKU, Vendor
from app.schemas.catalog import MatchPreviewRequest, MatchPreviewResponse, SKUListResponse, SKUOut
from app.services.order_preview import build_match_preview

router = APIRouter(prefix="/catalog", tags=["catalog"])

LIKE_ESCAPE = "\\"


def contains_pattern(term: str) -> str:
    """Build a ``%term%`` LIKE pattern where the user's own ``\\``, ``%`` and ``_`` are literal."""
    escaped = (
        term.strip()
        .replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"


@router.get("/skus", response_model=SKUListResponse)
def list_skus(
    db: DbSession,
    vendor_id: Annotated[int | None, Query(ge=1)] = None,
    vendor: Annotated[
        str | None, Query(max_length=200, description="Vendor name or PayPal email (substring)")
    ] = None,
    category: Annotated[str | None, Query(max_length=100)] = None,
    q: Annotated[str | None, Query(max_length=200, description="Substring search")] = None,
    search: Annotated[str | None, Query(max_length=200, description="Alias of `q`")] = None,
    include_inactive: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SKUListResponse:
    stmt = select(ProductSKU).join(ProductSKU.vendor)
    if not include_inactive:
        stmt = stmt.where(ProductSKU.is_active.is_(True), Vendor.is_active.is_(True))
    if vendor_id is not None:
        stmt = stmt.where(ProductSKU.vendor_id == vendor_id)
    if vendor and vendor.strip():
        pattern = contains_pattern(vendor)
        stmt = stmt.where(
            or_(
                Vendor.name.ilike(pattern, escape=LIKE_ESCAPE),
                Vendor.paypal_merchant_email.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    if category and category.strip():
        stmt = stmt.where(func.lower(ProductSKU.category) == category.strip().lower())
    term = q if q and q.strip() else search
    if term and term.strip():
        pattern = contains_pattern(term)
        stmt = stmt.where(
            or_(
                ProductSKU.name.ilike(pattern, escape=LIKE_ESCAPE),
                ProductSKU.sku_code.ilike(pattern, escape=LIKE_ESCAPE),
                ProductSKU.description.ilike(pattern, escape=LIKE_ESCAPE),
                ProductSKU.category.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.options(joinedload(ProductSKU.vendor))
        .order_by(ProductSKU.sku_code)
        .limit(limit)
        .offset(offset)
    ).all()
    return SKUListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[SKUOut.model_validate(r) for r in rows],
    )


@router.post("/match-preview", response_model=MatchPreviewResponse)
def match_preview(
    payload: MatchPreviewRequest, matcher: Matcher, settings: AppSettings
) -> MatchPreviewResponse:
    return build_match_preview(
        matcher, payload.items, currency=settings.default_currency, top_k=payload.top_k
    )
