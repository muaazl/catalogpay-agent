from functools import lru_cache
from typing import Annotated, Final

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    Path,
    Query,
    Response,
    UploadFile,
    status,
)
from google import genai
from google.genai import types
from sqlalchemy import func, select

from app.api.deps import AppSettings, DbSession, Matcher, PayPal
from app.models.order import Order, OrderStatus
from app.schemas.catalog import MatchPreviewItem
from app.schemas.order import (
    IDEMPOTENCY_KEY_PATTERN,
    PUBLIC_ORDER_ID_PATTERN,
    CreateOrderRequest,
    OrderListResponse,
    OrderOut,
    OrderSummaryOut,
)
from app.schemas.order_parser import (
    GeminiExtractionPayload,
    ParsedOrderPreviewResponse,
    ParserMode,
    ParseTextOrderRequest,
)
from app.services.gemini_parser import (
    ALLOWED_MEDIA_TYPES,
    MAX_ITEMS,
    EmptyMediaError,
    GeminiOrderParser,
    MediaTooLargeError,
    ParserNotConfiguredError,
    UnsupportedMediaTypeError,
    normalize_media_type,
    validate_media,
)
from app.services.order_preview import build_match_preview
from app.services.order_settlement import (
    InvalidOrderError,
    capture_order_and_disburse,
    create_order_from_items,
    load_order,
    retry_payouts,
)
from app.services.order_text_fallback import parse_text_deterministic_fallback

router = APIRouter(prefix="/orders", tags=["orders"])

FALLBACK_MODEL_NAME: Final = "deterministic-regex-v1"
PARSE_MEDIA_PATH: Final = "/orders/parse-media"


@lru_cache(maxsize=4)
def _gemini_client(api_key: str, timeout_ms: int) -> genai.Client:
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_ms))


def get_order_parser(settings: AppSettings) -> GeminiOrderParser:
    key = settings.gemini_api_key.strip()
    client = _gemini_client(key, int(settings.gemini_timeout_seconds * 1000)) if key else None
    return GeminiOrderParser(settings, client=client)


OrderParser = Annotated[GeminiOrderParser, Depends(get_order_parser)]


def _build_response(
    extraction: GeminiExtractionPayload,
    *,
    mode: ParserMode,
    model_used: str,
    matcher: Matcher,
    currency: str,
    top_k: int,
) -> ParsedOrderPreviewResponse:
    items = extraction.items[:MAX_ITEMS]
    overflow = [i.raw_line_text for i in extraction.items[MAX_ITEMS:]]
    preview = build_match_preview(
        matcher,
        [MatchPreviewItem(query=i.search_query, quantity=i.quantity) for i in items],
        currency=currency,
        top_k=top_k,
    )
    return ParsedOrderPreviewResponse(
        parser_mode=mode,
        model_used=model_used,
        customer_note=extraction.customer_note,
        extracted_items=items,
        unrecognized_fragments=[*extraction.unrecognized_fragments, *overflow],
        match_preview=preview,
    )


@router.post("/parse-text", response_model=ParsedOrderPreviewResponse)
async def parse_text(
    payload: ParseTextOrderRequest,
    parser: OrderParser,
    matcher: Matcher,
    settings: AppSettings,
) -> ParsedOrderPreviewResponse:
    mode: ParserMode
    if parser.is_configured:
        extraction, mode, model_used = await parser.parse_text(payload.text), "gemini", parser.model
    elif payload.allow_fallback:
        extraction = parse_text_deterministic_fallback(payload.text)
        mode, model_used = "deterministic_fallback", FALLBACK_MODEL_NAME
    else:
        raise ParserNotConfiguredError(
            "GEMINI_API_KEY is not configured and allow_fallback is false."
        )
    return _build_response(
        extraction,
        mode=mode,
        model_used=model_used,
        matcher=matcher,
        currency=settings.default_currency,
        top_k=payload.top_k,
    )


@router.post("/parse-media", response_model=ParsedOrderPreviewResponse)
async def parse_media(
    parser: OrderParser,
    matcher: Matcher,
    settings: AppSettings,
    file: Annotated[UploadFile, File(description="Order photo, PDF, or voice note")],
    note: Annotated[str, Form(max_length=1000)] = "",
    top_k: Annotated[int, Form(ge=1, le=10)] = 3,
) -> ParsedOrderPreviewResponse:
    try:
        mime_type = normalize_media_type(file.content_type)
        if mime_type not in ALLOWED_MEDIA_TYPES:
            raise UnsupportedMediaTypeError(
                f"Unsupported media type '{mime_type or 'unknown'}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_MEDIA_TYPES))}."
            )
        if not parser.is_configured:
            raise ParserNotConfiguredError(
                "GEMINI_API_KEY is required for image, PDF and audio order parsing."
            )
        # Read at most one byte past the limit: enough to detect "too large" without buffering.
        data = await file.read(settings.max_upload_bytes + 1)
    finally:
        await file.close()
    if len(data) > settings.max_upload_bytes:
        raise MediaTooLargeError(
            f"Uploaded file exceeds the {settings.max_upload_bytes} byte limit."
        )
    if not data:
        raise EmptyMediaError("Uploaded file is empty.")
    # Size + magic-byte check against the declared type before any bytes reach Gemini.
    mime_type = validate_media(data, mime_type, settings.max_upload_bytes)

    extraction = await parser.parse_media(data, mime_type, note)
    return _build_response(
        extraction,
        mode="gemini",
        model_used=parser.model,
        matcher=matcher,
        currency=settings.default_currency,
        top_k=top_k,
    )


# --------------------------------------------------------------------------------------------
# Order ledger & settlement
# --------------------------------------------------------------------------------------------

OrderIdPath = Annotated[str, Path(pattern=PUBLIC_ORDER_ID_PATTERN)]


@router.post(
    "",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"description": "Idempotent replay of an existing order"}},
)
async def create_order(
    payload: CreateOrderRequest,
    response: Response,
    db: DbSession,
    paypal: PayPal,
    settings: AppSettings,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", pattern=IDEMPOTENCY_KEY_PATTERN)
    ] = None,
) -> OrderOut:
    if idempotency_key and payload.idempotency_key and idempotency_key != payload.idempotency_key:
        raise InvalidOrderError("Idempotency-Key header and body idempotency_key differ.")
    key = idempotency_key or payload.idempotency_key
    if not key:
        raise InvalidOrderError("An Idempotency-Key header (or body idempotency_key) is required.")
    order, created = await create_order_from_items(
        db,
        paypal,
        items=payload.items,
        idempotency_key=key,
        customer_note=payload.customer_note,
        parser_mode=payload.parser_mode,
        currency=settings.default_currency,
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return OrderOut.model_validate(order)


@router.get("", response_model=OrderListResponse)
def list_orders(
    db: DbSession,
    status_filter: Annotated[OrderStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OrderListResponse:
    stmt = select(Order)
    if status_filter is not None:
        stmt = stmt.where(Order.status == status_filter)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Order.created_at.desc(), Order.id.desc()).limit(limit).offset(offset)
    ).all()
    return OrderListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[OrderSummaryOut.model_validate(r) for r in rows],
    )


@router.get("/{public_order_id}", response_model=OrderOut)
def get_order(public_order_id: OrderIdPath, db: DbSession) -> OrderOut:
    return OrderOut.model_validate(load_order(db, public_order_id))


@router.post("/{public_order_id}/capture", response_model=OrderOut)
async def capture_order(public_order_id: OrderIdPath, db: DbSession, paypal: PayPal) -> OrderOut:
    return OrderOut.model_validate(await capture_order_and_disburse(db, paypal, public_order_id))


@router.post("/{public_order_id}/retry-payouts", response_model=OrderOut)
async def retry_order_payouts(
    public_order_id: OrderIdPath, db: DbSession, paypal: PayPal
) -> OrderOut:
    return OrderOut.model_validate(await retry_payouts(db, paypal, public_order_id))
