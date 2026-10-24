"""Contracts for the order parser (Gemini structured output + the API surface)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.schemas.catalog import MatchPreviewResponse


class ExtractedLineItem(BaseModel):
    """One purchasable line pulled out of the buyer's message. Never carries a price."""

    raw_line_text: str = Field(
        max_length=1000, description="The original fragment of buyer input for this line."
    )
    search_query: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "Product name plus size/variant only, without the order multiplier. "
            "'3 bags of 500g arabica' -> 'arabica 500g'."
        ),
    )
    quantity: int = Field(ge=1, le=10_000, description="How many units the buyer wants.")
    unit_hint: str | None = Field(
        default=None, max_length=100, description="Packaging word, e.g. 'bags', 'cases'."
    )
    notes: str | None = Field(default=None, max_length=500)


class GeminiExtractionPayload(BaseModel):
    """``response_schema`` for Gemini structured output."""

    customer_note: str | None = Field(default=None, max_length=2000)
    items: list[ExtractedLineItem]
    unrecognized_fragments: list[str] = Field(default_factory=list)


class ParseTextOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=5000)
    top_k: StrictInt = Field(default=3, ge=1, le=10)
    allow_fallback: bool = True


ParserMode = Literal["gemini", "deterministic_fallback"]


class ParsedOrderPreviewResponse(BaseModel):
    parser_mode: ParserMode
    model_used: str
    customer_note: str | None
    extracted_items: list[ExtractedLineItem]
    unrecognized_fragments: list[str]
    match_preview: MatchPreviewResponse
