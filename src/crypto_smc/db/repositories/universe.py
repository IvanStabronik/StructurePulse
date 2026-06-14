from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.models import UniverseMemberRecord, UniverseSnapshotRecord
from crypto_smc.universe import UniverseDecision

UNIVERSE_REFRESH_LOCK_ID = 7_361_817_235


class UniverseRepository:
    @asynccontextmanager
    async def refresh_lock(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[bool]:
        async with session_factory() as session:
            lock_acquired = bool(
                await session.scalar(select(func.pg_try_advisory_lock(UNIVERSE_REFRESH_LOCK_ID)))
            )
            try:
                yield lock_acquired
            finally:
                if lock_acquired:
                    await session.scalar(select(func.pg_advisory_unlock(UNIVERSE_REFRESH_LOCK_ID)))

    async def save_snapshot(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        decisions: list[UniverseDecision],
        source: str,
        configuration: dict[str, Any],
        source_updated_at: datetime | None,
    ) -> int:
        async with session_factory() as session, session.begin():
            await session.execute(
                update(UniverseSnapshotRecord)
                .where(UniverseSnapshotRecord.is_active.is_(True))
                .values(is_active=False, status="superseded")
            )

            snapshot = UniverseSnapshotRecord(
                source=source,
                status="active",
                is_active=True,
                source_asset_count=len(decisions),
                selected_count=sum(decision.is_selected for decision in decisions),
                configuration=configuration,
                source_updated_at=source_updated_at,
                activated_at=datetime.now(UTC),
            )
            session.add(snapshot)
            await session.flush()

            session.add_all(
                [
                    UniverseMemberRecord(
                        snapshot_id=snapshot.id,
                        provider_id=decision.asset.provider_id,
                        asset_symbol=decision.asset.symbol,
                        asset_name=decision.asset.name,
                        market_cap_rank=decision.asset.market_cap_rank,
                        market_cap_usd=decision.asset.market_cap_usd,
                        provider_volume_24h_usd=decision.asset.total_volume_usd,
                        instrument_symbol=decision.instrument_symbol,
                        exchange_turnover_24h_usdt=decision.exchange_turnover_24h_usdt,
                        spread_bps=decision.spread_bps,
                        is_selected=decision.is_selected,
                        exclusion_reason=(
                            decision.exclusion_reason.value
                            if decision.exclusion_reason is not None
                            else None
                        ),
                        decision_detail=decision.detail,
                    )
                    for decision in decisions
                ]
            )

        return snapshot.id

    async def get_current(
        self,
        session: AsyncSession,
    ) -> tuple[UniverseSnapshotRecord, list[UniverseMemberRecord]] | None:
        snapshot = await session.scalar(
            select(UniverseSnapshotRecord)
            .where(UniverseSnapshotRecord.is_active.is_(True))
            .order_by(UniverseSnapshotRecord.id.desc())
            .limit(1)
        )
        if snapshot is None:
            return None

        members = list(
            (
                await session.scalars(
                    select(UniverseMemberRecord)
                    .where(UniverseMemberRecord.snapshot_id == snapshot.id)
                    .order_by(UniverseMemberRecord.market_cap_rank)
                )
            ).all()
        )
        return snapshot, members
