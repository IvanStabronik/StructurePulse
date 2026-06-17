from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from crypto_smc.db.base import Base


class LiveExecutionRecord(Base):
    __tablename__ = "live_executions"
    __table_args__ = (
        Index("ix_live_executions_status_created", "status", "created_at"),
        Index("ix_live_executions_symbol_created", "symbol", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    symbol: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(32))
    order_budget_usdt: Mapped[Decimal] = mapped_column(Numeric(30, 12))
    entry_order_id: Mapped[str | None] = mapped_column(String(128))
    tp1_order_id: Mapped[str | None] = mapped_column(String(128))
    close_order_id: Mapped[str | None] = mapped_column(String(128))
    entry_order_link_id: Mapped[str | None] = mapped_column(String(128))
    tp1_order_link_id: Mapped[str | None] = mapped_column(String(128))
    close_order_link_id: Mapped[str | None] = mapped_column(String(128))
    entry_qty: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    remaining_qty: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    entry_price: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    current_stop: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    error: Mapped[str | None] = mapped_column(Text)
    entry_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tp1_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
