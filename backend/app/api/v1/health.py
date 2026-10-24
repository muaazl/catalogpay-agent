from fastapi import APIRouter, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import AppSettings, DbSession
from app.models.catalog import ProductSKU
from app.schemas.catalog import DatabaseHealth, HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(db: DbSession, settings: AppSettings, response: Response) -> HealthResponse:
    dialect = db.get_bind().dialect.name
    try:
        db.execute(text("SELECT 1"))
        active = db.scalar(
            select(func.count()).select_from(ProductSKU).where(ProductSKU.is_active.is_(True))
        )
        database = DatabaseHealth(connected=True, dialect=dialect)
        overall, active_count = "ok", int(active or 0)
    except SQLAlchemyError as exc:
        database = DatabaseHealth(connected=False, dialect=dialect, error=type(exc).__name__)
        overall, active_count = "degraded", 0
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status=overall,
        app=settings.app_name,
        environment=settings.environment,
        database=database,
        active_sku_count=active_count,
    )
