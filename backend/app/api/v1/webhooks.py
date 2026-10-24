import json
import logging
from typing import Final

from fastapi import APIRouter, Request

from app.api.deps import DbSession, PayPal
from app.core.errors import DomainError
from app.schemas.order import WebhookAck
from app.services.order_settlement import handle_paypal_webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

PAYPAL_WEBHOOK_PATH: Final = "/webhooks/paypal"
MAX_WEBHOOK_BYTES: Final = 256 * 1024


class InvalidWebhookError(DomainError):
    status_code, code = 400, "invalid_webhook"


@router.post("/paypal", response_model=WebhookAck)
async def paypal_webhook(request: Request, db: DbSession, paypal: PayPal) -> WebhookAck:
    raw = await request.body()
    try:
        event = json.loads(raw)
    except ValueError as exc:
        raise InvalidWebhookError("Webhook body is not valid JSON.") from exc
    if not isinstance(event, dict):
        raise InvalidWebhookError("Webhook body must be a JSON object.")

    # Never act on an event before PayPal (or the simulator) confirms it is authentic.
    if not await paypal.verify_webhook_signature(headers=request.headers, event=event):
        logger.warning("Rejected PayPal webhook with invalid signature: %s", event.get("id"))
        raise InvalidWebhookError("Webhook signature verification failed.")

    status, detail = await handle_paypal_webhook(db, paypal, event)
    return WebhookAck(
        status=status,  # type: ignore[arg-type]
        event_id=str(event.get("id")),
        event_type=str(event.get("event_type")),
        detail=detail,
    )
