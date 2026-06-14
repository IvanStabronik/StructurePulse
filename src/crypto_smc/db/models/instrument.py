from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from crypto_smc.db.base import Base


class InstrumentRecord(Base):
    __tablename__ = "instruments"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    base_coin: Mapped[str] = mapped_column(String(32), index=True)
    quote_coin: Mapped[str] = mapped_column(String(16))
    settle_coin: Mapped[str] = mapped_column(String(16))
    contract_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    launch_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tick_size: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    min_price: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    max_price: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    quantity_step: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    min_order_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    max_order_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    max_market_order_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    min_notional_value: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    min_leverage: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    max_leverage: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    leverage_step: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    funding_interval_minutes: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
