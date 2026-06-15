import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from websockets.asyncio.server import ServerConnection, serve

from crypto_smc.providers.bybit.trade_websocket import (
    BybitPublicTradeWebSocketManager,
    PublicTradeEvent,
    TradeStreamReadyEvent,
    parse_public_trades,
)
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


def trade_message() -> dict[str, object]:
    return {
        "topic": "publicTrade.BTCUSDT",
        "type": "snapshot",
        "ts": 1781450640100,
        "data": [
            {
                "T": 1781450640000,
                "s": "BTCUSDT",
                "S": "Buy",
                "v": "0.5",
                "p": "104000",
                "L": "PlusTick",
                "i": "trade-1",
                "BT": False,
                "RPI": False,
                "seq": 100,
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


def test_parse_public_trades_normalizes_trade_identity() -> None:
    trades = parse_public_trades(trade_message())

    assert len(trades) == 1
    assert trades[0].trade_id == "trade-1"
    assert trades[0].symbol == "BTCUSDT"
    assert trades[0].sequence == 100


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


@pytest.mark.asyncio
async def test_public_trade_manager_subscribes_buffers_and_emits() -> None:
    release = asyncio.Event()

    async def handler(connection: ServerConnection) -> None:
        request = json.loads(await connection.recv())
        assert request["args"] == ["publicTrade.BTCUSDT"]
        await connection.send(json.dumps({"success": True, "op": "subscribe"}))
        await connection.send(json.dumps(trade_message()))
        await release.wait()

    async with websocket_server(handler) as url:
        manager = BybitPublicTradeWebSocketManager(
            url=url,
            queue_size=10,
            buffer_size=1000,
            heartbeat_seconds=20,
            reconnect_base_seconds=0.001,
            reconnect_max_seconds=0.01,
            ready_timeout_seconds=1,
        )
        started_at = await manager.subscribe("btcusdt")
        trade_event = await asyncio.wait_for(manager.next_event(), timeout=1)
        ready = await asyncio.wait_for(manager.next_event(), timeout=1)
        buffered = manager.buffered_trades(
            "BTCUSDT",
            since=datetime(2026, 6, 14, tzinfo=UTC),
        )
        release.set()
        await manager.stop()

    assert started_at.tzinfo is not None
    assert isinstance(ready, TradeStreamReadyEvent)
    assert isinstance(trade_event, PublicTradeEvent)
    assert [trade.trade_id for trade in buffered] == ["trade-1"]


@pytest.mark.asyncio
async def test_public_trade_manager_waits_for_first_trade_before_ready() -> None:
    send_trade = asyncio.Event()
    release = asyncio.Event()

    async def handler(connection: ServerConnection) -> None:
        await connection.recv()
        await connection.send(json.dumps({"success": True, "op": "subscribe"}))
        await send_trade.wait()
        await connection.send(json.dumps(trade_message()))
        await release.wait()

    async with websocket_server(handler) as url:
        manager = BybitPublicTradeWebSocketManager(
            url=url,
            queue_size=10,
            buffer_size=1000,
            heartbeat_seconds=20,
            reconnect_base_seconds=0.001,
            reconnect_max_seconds=0.01,
            ready_timeout_seconds=1,
        )
        await manager.subscribe("BTCUSDT")
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(manager.next_event(), timeout=0.05)
        send_trade.set()
        trade_event = await asyncio.wait_for(manager.next_event(), timeout=1)
        ready = await asyncio.wait_for(manager.next_event(), timeout=1)
        release.set()
        await manager.stop()

    assert isinstance(trade_event, PublicTradeEvent)
    assert isinstance(ready, TradeStreamReadyEvent)
