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
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from crypto_smc.db.base import Base


class UniverseSnapshotRecord(Base):
    __tablename__ = "universe_snapshots"
    __table_args__ = (
        Index(
            "uq_universe_snapshots_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    source_asset_count: Mapped[int] = mapped_column(Integer)
    selected_count: Mapped[int] = mapped_column(Integer)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UniverseMemberRecord(Base):
    __tablename__ = "universe_members"
    __table_args__ = (
        Index(
            "uq_universe_members_snapshot_provider",
            "snapshot_id",
            "provider_id",
            unique=True,
        ),
        Index("ix_universe_members_selected", "snapshot_id", "is_selected"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("universe_snapshots.id", ondelete="CASCADE"),
        index=True,
    )
    provider_id: Mapped[str] = mapped_column(String(128))
    asset_symbol: Mapped[str] = mapped_column(String(32))
    asset_name: Mapped[str] = mapped_column(String(128))
    market_cap_rank: Mapped[int] = mapped_column(Integer)
    market_cap_usd: Mapped[Decimal] = mapped_column(Numeric(30, 2))
    provider_volume_24h_usd: Mapped[Decimal] = mapped_column(Numeric(30, 2))
    instrument_symbol: Mapped[str | None] = mapped_column(
        ForeignKey("instruments.symbol", ondelete="SET NULL"),
    )
    exchange_turnover_24h_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    spread_bps: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    is_selected: Mapped[bool] = mapped_column(Boolean)
    exclusion_reason: Mapped[str | None] = mapped_column(String(64))
    decision_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
