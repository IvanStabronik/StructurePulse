from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from crypto_smc.db.base import Base


class EvaluationWindowRecord(Base):
    __tablename__ = "evaluation_windows"
    __table_args__ = (
        Index(
            "uq_evaluation_windows_active",
            "status",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_evaluation_windows_strategy_started", "strategy_version_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(96), unique=True)
    strategy_version_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="RESTRICT"),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    minimum_completed_signals: Mapped[int] = mapped_column(
        Integer,
        default=100,
        server_default="100",
    )
    minimum_profit_factor: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        default=Decimal("1.3"),
        server_default="1.3",
    )
    maximum_drawdown_fraction: Mapped[Decimal] = mapped_column(
        Numeric(10, 6),
        default=Decimal("0.15"),
        server_default="0.15",
    )
    maximum_symbol_share: Mapped[Decimal] = mapped_column(
        Numeric(10, 6),
        default=Decimal("0.35"),
        server_default="0.35",
    )
    reference_balance: Mapped[Decimal] = mapped_column(
        Numeric(20, 4),
        default=Decimal(10_000),
        server_default="10000",
    )
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
