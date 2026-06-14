import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from websockets.asyncio.server import ServerConnection, serve

from crypto_smc.providers.bybit.websocket import (
    BybitKlineWebSocketManager,
    ClosedCandleEvent,
    ShardDisconnectedEvent,
    ShardReconnectedEvent,
    parse_closed_1m_candles,
    shard_symbols,
)


def kline_message(*, confirm: bool = True) -> dict[str, object]:
    return {
        "topic": "kline.1.BTCUSDT",
        "type": "snapshot",
        "ts": 1781450640100,
        "data": [
            {
                "start": 1781450580000,
                "end": 1781450639999,
                "interval": "1",
                "open": "104000",
                "close": "104010",
                "high": "104020",
                "low": "103990",
                "volume": "12.5",
                "turnover": "1300000",
                "confirm": confirm,
                "timestamp": 1781450639990,
            }
        ],
    }


def test_parse_closed_1m_candles_ignores_open_updates() -> None:
    assert parse_closed_1m_candles(kline_message(confirm=False)) == []


def test_parse_closed_1m_candles_normalizes_confirmed_payload() -> None:
    candles = parse_closed_1m_candles(kline_message())

    assert len(candles) == 1
    assert candles[0].symbol == "BTCUSDT"
    assert candles[0].open_time == datetime(2026, 6, 14, 15, 23, tzinfo=UTC)
    assert candles[0].close_price == 104_010


def test_shard_symbols_normalizes_deduplicates_and_splits() -> None:
    assert shard_symbols(["ethusdt", "BTCUSDT", "ETHUSDT"], 1) == [
        ("BTCUSDT",),
        ("ETHUSDT",),
    ]


@asynccontextmanager
async def websocket_server(
    handler: object,
) -> AsyncIterator[str]:
    async with serve(handler, "127.0.0.1", 0) as server:  # type: ignore[arg-type]
        socket = server.sockets[0]
        host, port = socket.getsockname()[:2]
        yield f"ws://{host}:{port}"


@pytest.mark.asyncio
async def test_manager_subscribes_and_emits_closed_candle() -> None:
    subscribed: list[str] = []
    release = asyncio.Event()

    async def handler(connection: ServerConnection) -> None:
        request = json.loads(await connection.recv())
        subscribed.extend(request["args"])
        await connection.send(json.dumps({"success": True, "op": "subscribe"}))
        await connection.send(json.dumps(kline_message()))
        await release.wait()

    async with websocket_server(handler) as url:
        manager = BybitKlineWebSocketManager(
            url=url,
            shard_size=15,
            queue_size=10,
            heartbeat_seconds=20,
            reconnect_base_seconds=0.001,
            reconnect_max_seconds=0.01,
            ready_timeout_seconds=1,
        )
        await manager.start(["BTCUSDT"])
        await manager.wait_until_ready()
        event = await asyncio.wait_for(manager.next_event(), timeout=1)
        release.set()
        await manager.stop()

    assert subscribed == ["kline.1.BTCUSDT"]
    assert isinstance(event, ClosedCandleEvent)
    assert event.candle.symbol == "BTCUSDT"


@pytest.mark.asyncio
async def test_manager_reports_disconnect_and_reconnect() -> None:
    connections = 0
    release = asyncio.Event()

    async def handler(connection: ServerConnection) -> None:
        nonlocal connections
        connections += 1
        await connection.recv()
        await connection.send(json.dumps({"success": True, "op": "subscribe"}))
        if connections == 1:
            await connection.close()
            return
        await release.wait()

    async with websocket_server(handler) as url:
        manager = BybitKlineWebSocketManager(
            url=url,
            shard_size=15,
            queue_size=10,
            heartbeat_seconds=20,
            reconnect_base_seconds=0.001,
            reconnect_max_seconds=0.01,
            ready_timeout_seconds=1,
        )
        await manager.start(["BTCUSDT"])
        await manager.wait_until_ready()
        disconnected = await asyncio.wait_for(manager.next_event(), timeout=1)
        reconnected = await asyncio.wait_for(manager.next_event(), timeout=1)
        release.set()
        await manager.stop()

    assert isinstance(disconnected, ShardDisconnectedEvent)
    assert isinstance(reconnected, ShardReconnectedEvent)
    assert connections >= 2
