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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from crypto_smc.db.base import Base


class StrategyVersionRecord(Base):
    __tablename__ = "strategy_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(String(64), unique=True)
    parameter_checksum: Mapped[str] = mapped_column(String(64))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class AnalysisSnapshotRecord(Base):
    __tablename__ = "analysis_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "strategy_version_id",
            "input_signature",
            name="uq_analysis_snapshots_symbol_version_input",
        ),
        Index("ix_analysis_snapshots_symbol_analyzed", "symbol", "analyzed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        ForeignKey("instruments.symbol", ondelete="CASCADE"),
        index=True,
    )
    strategy_version_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="RESTRICT"),
        index=True,
    )
    input_signature: Mapped[str] = mapped_column(String(64))
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    input_cutoffs: Mapped[dict[str, Any]] = mapped_column(JSON)
    market_context: Mapped[dict[str, Any]] = mapped_column(JSON)
    analyses: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class SignalCandidateRecord(Base):
    __tablename__ = "signal_candidates"
    __table_args__ = (
        UniqueConstraint(
            "analysis_snapshot_id",
            "direction",
            name="uq_signal_candidates_snapshot_direction",
        ),
        Index(
            "ix_signal_candidates_status_created",
            "status",
            "created_at",
        ),
        Index(
            "ix_signal_candidates_symbol_direction",
            "symbol",
            "direction",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    analysis_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_snapshots.id", ondelete="CASCADE"),
        index=True,
    )
    strategy_version_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="RESTRICT"),
        index=True,
    )
    symbol: Mapped[str] = mapped_column(
        ForeignKey("instruments.symbol", ondelete="CASCADE"),
        index=True,
    )
    direction: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16))
    score: Mapped[int] = mapped_column(Integer)
    strength: Mapped[str] = mapped_column(String(16))
    entry_lower: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    entry_upper: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    planned_entry: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    take_profit_1: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    take_profit_2: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    gross_reward_to_risk: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    net_reward_to_risk: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    risk_amount: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    notional: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    recommended_leverage: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    estimated_margin: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    estimated_entry_fee: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    estimated_exit_fee: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    estimated_loss_at_stop: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    invalidation: Mapped[str | None] = mapped_column(String(256))
    score_components: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    evidence: Mapped[list[str]] = mapped_column(JSON)
    warnings: Mapped[list[str]] = mapped_column(JSON)
    suppression_reasons: Mapped[list[str]] = mapped_column(JSON)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
