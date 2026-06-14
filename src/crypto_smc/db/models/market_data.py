from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from crypto_smc.db.base import Base


class Candle1mRecord(Base):
    __tablename__ = "candles_1m"
    __table_args__ = (
        Index("ix_candles_1m_open_time", "open_time"),
        {"postgresql_partition_by": "RANGE (open_time)"},
    )

    symbol: Mapped[str] = mapped_column(
        ForeignKey("instruments.symbol", ondelete="CASCADE"),
        primary_key=True,
    )
    open_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
    )
    open_price: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    high_price: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    low_price: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    close_price: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    volume: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    turnover: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    source: Mapped[str] = mapped_column(String(16), default="rest", server_default="rest")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class DataCheckpointRecord(Base):
    __tablename__ = "data_checkpoints"
    __table_args__ = (
        UniqueConstraint("symbol", "stream", name="uq_data_checkpoints_symbol_stream"),
        Index("ix_data_checkpoints_state", "state"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        ForeignKey("instruments.symbol", ondelete="CASCADE"),
        index=True,
    )
    stream: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(32))
    last_confirmed_open_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class DataGapRecord(Base):
    __tablename__ = "data_gaps"
    __table_args__ = (
        Index("ix_data_gaps_symbol_status", "symbol", "status"),
        Index("ix_data_gaps_detected_at", "detected_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        ForeignKey("instruments.symbol", ondelete="CASCADE"),
        index=True,
    )
    stream: Mapped[str] = mapped_column(String(32))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AggregatedCandleRecord(Base):
    __tablename__ = "candles_agg"
    __table_args__ = (Index("ix_candles_agg_timeframe_open_time", "timeframe", "open_time"),)

    symbol: Mapped[str] = mapped_column(
        ForeignKey("instruments.symbol", ondelete="CASCADE"),
        primary_key=True,
    )
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open_price: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    high_price: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    low_price: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    close_price: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    volume: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    turnover: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    source_candle_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class AggregationJobRecord(Base):
    __tablename__ = "aggregation_jobs"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "timeframe",
            "open_time",
            name="uq_aggregation_jobs_interval",
        ),
        Index("ix_aggregation_jobs_claim", "state", "priority", "available_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        ForeignKey("instruments.symbol", ondelete="CASCADE"),
        index=True,
    )
    timeframe: Mapped[str] = mapped_column(String(8))
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    priority: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class AggregationCursorRecord(Base):
    __tablename__ = "aggregation_cursors"

    symbol: Mapped[str] = mapped_column(
        ForeignKey("instruments.symbol", ondelete="CASCADE"),
        primary_key=True,
    )
    last_scanned_open_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
