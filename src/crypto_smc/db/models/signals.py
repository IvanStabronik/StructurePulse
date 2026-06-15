from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from crypto_smc.db.base import Base


class SignalRecord(Base):
    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signals_status_created", "status", "created_at"),
        Index("ix_signals_symbol_created", "symbol", "created_at"),
        Index(
            "uq_signals_one_active_per_symbol",
            "symbol",
            unique=True,
            postgresql_where=text("status IN ('preparing', 'active', 'entered', 'tp1_reached')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("signal_candidates.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    symbol: Mapped[str] = mapped_column(
        ForeignKey("instruments.symbol", ondelete="CASCADE"),
        index=True,
    )
    direction: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(32))
    suppression_reason: Mapped[str | None] = mapped_column(String(64))
    entry_lower: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    entry_upper: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    planned_entry: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    take_profit_1: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    take_profit_2: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    risk_amount: Mapped[Decimal] = mapped_column(Numeric(30, 12))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class SignalEventRecord(Base):
    __tablename__ = "signal_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_signal_events_idempotency_key"),
        Index("ix_signal_events_signal_time", "signal_id", "event_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"),
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64))
    status_from: Mapped[str | None] = mapped_column(String(32))
    status_to: Mapped[str] = mapped_column(String(32))
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_event_id: Mapped[str | None] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class VirtualTradeRecord(Base):
    __tablename__ = "virtual_trades"
    __table_args__ = (Index("ix_virtual_trades_status_updated", "status", "updated_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32))
    planned_entry: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    current_stop: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    take_profit_1: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    take_profit_2: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    remaining_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    realized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(38, 18),
        default=Decimal(0),
        server_default="0",
    )
    fees: Mapped[Decimal] = mapped_column(
        Numeric(38, 18),
        default=Decimal(0),
        server_default="0",
    )
    estimated_funding: Mapped[Decimal] = mapped_column(
        Numeric(38, 18),
        default=Decimal(0),
        server_default="0",
    )
    r_multiple: Mapped[Decimal] = mapped_column(
        Numeric(30, 12),
        default=Decimal(0),
        server_default="0",
    )
    ambiguous: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    resolution_note: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
