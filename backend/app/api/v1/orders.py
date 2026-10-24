from functools import lru_cache
from typing import Annotated, Final

from fastapi import APIRouter, Depends, File, Form, UploadFile
from google import genai
from google.genai import types

from app.api.deps import AppSettings, Matcher
from app.schemas.catalog import MatchPreviewItem
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
)
from app.services.order_preview import build_match_preview
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
    # Read at most one byte past the limit: enough to detect "too large" without buffering more.
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise MediaTooLargeError(
            f"Uploaded file exceeds the {settings.max_upload_bytes} byte limit."
        )
    if not data:
        raise EmptyMediaError("Uploaded file is empty.")

    extraction = await parser.parse_media(data, mime_type, note)
    return _build_response(
        extraction,
        mode="gemini",
        model_used=parser.model,
        matcher=matcher,
        currency=settings.default_currency,
        top_k=top_k,
    )
