from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from crypto_smc.db.models import InstrumentRecord
from crypto_smc.providers.models import Instrument


class InstrumentRepository:
    async def replace_active_set(
        self,
        session: AsyncSession,
        instruments: list[Instrument],
    ) -> None:
        symbols = [instrument.symbol for instrument in instruments]
        if symbols:
            await session.execute(
                update(InstrumentRecord)
                .where(InstrumentRecord.symbol.not_in(symbols))
                .values(is_active=False, updated_at=datetime.now(UTC))
            )
        else:
            await session.execute(
                update(InstrumentRecord).values(is_active=False, updated_at=datetime.now(UTC))
            )

        for instrument in instruments:
            values = {
                "symbol": instrument.symbol,
                "base_coin": instrument.base_coin,
                "quote_coin": instrument.quote_coin,
                "settle_coin": instrument.settle_coin,
                "contract_type": instrument.contract_type,
                "status": instrument.status,
                "launch_time": instrument.launch_time,
                "tick_size": instrument.tick_size,
                "min_price": instrument.min_price,
                "max_price": instrument.max_price,
                "quantity_step": instrument.quantity_step,
                "min_order_quantity": instrument.min_order_quantity,
                "max_order_quantity": instrument.max_order_quantity,
                "max_market_order_quantity": instrument.max_market_order_quantity,
                "min_notional_value": instrument.min_notional_value,
                "min_leverage": instrument.min_leverage,
                "max_leverage": instrument.max_leverage,
                "leverage_step": instrument.leverage_step,
                "funding_interval_minutes": instrument.funding_interval_minutes,
                "is_active": True,
                "updated_at": datetime.now(UTC),
            }
            statement = insert(InstrumentRecord).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=[InstrumentRecord.symbol],
                set_={key: value for key, value in values.items() if key != "symbol"},
            )
            await session.execute(statement)
