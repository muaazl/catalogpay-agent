"""Catalog ORM models. All money is integer cents; all rates are integer basis points."""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Vendor(Base):
    __tablename__ = "vendors"
    __table_args__ = (
        CheckConstraint(
            "commission_rate_bps >= 0 AND commission_rate_bps <= 10000",
            name="ck_vendor_commission_bps_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    paypal_merchant_email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    commission_rate_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    skus: Mapped[list["ProductSKU"]] = relationship(back_populates="vendor")

    def __repr__(self) -> str:
        return f"Vendor(id={self.id!r}, name={self.name!r})"


class ProductSKU(Base):
    __tablename__ = "product_skus"
    __table_args__ = (
        CheckConstraint("unit_price_cents >= 0", name="ck_sku_price_non_negative"),
        CheckConstraint("stock_quantity >= 0", name="ck_sku_stock_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sku_code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    unit_of_measure: Mapped[str] = mapped_column(String(64), nullable=False, default="unit")
    aliases_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    unit_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    vendor: Mapped[Vendor] = relationship(back_populates="skus")

    def __repr__(self) -> str:
        return f"ProductSKU(sku_code={self.sku_code!r}, name={self.name!r})"
