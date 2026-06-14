import asyncio
import json
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from pydantic import ValidationError
from websockets.asyncio.client import ClientConnection, connect

from crypto_smc.observability.metrics import (
    MARKET_DATA_WS_EVENTS,
    MARKET_DATA_WS_FRESHNESS_SECONDS,
    MARKET_DATA_WS_QUEUE_DEPTH,
    MARKET_DATA_WS_RECONNECTS,
)
from crypto_smc.providers.bybit.schemas import BybitWebSocketKlineMessage
from crypto_smc.providers.models import Candle1m

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ClosedCandleEvent:
    candle: Candle1m
    received_at: datetime


@dataclass(frozen=True, slots=True)
class ShardDisconnectedEvent:
    symbols: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class ShardReconnectedEvent:
    symbols: tuple[str, ...]


type MarketStreamEvent = ClosedCandleEvent | ShardDisconnectedEvent | ShardReconnectedEvent


class BybitWebSocketError(RuntimeError):
    """Raised when a Bybit WebSocket command or payload is invalid."""


def shard_symbols(symbols: Sequence[str], shard_size: int) -> list[tuple[str, ...]]:
    normalized = sorted({symbol.upper() for symbol in symbols})
    return [
        tuple(normalized[index : index + shard_size])
        for index in range(0, len(normalized), shard_size)
    ]


def parse_closed_1m_candles(payload: object) -> list[Candle1m]:
    if not isinstance(payload, dict):
        return []
    topic = payload.get("topic")
    if not isinstance(topic, str) or not topic.startswith("kline.1."):
        return []

    try:
        message = BybitWebSocketKlineMessage.model_validate(payload)
    except ValidationError as exc:
        raise BybitWebSocketError("Invalid Bybit kline WebSocket payload") from exc

    symbol = message.topic.removeprefix("kline.1.").upper()
    return [
        Candle1m(
            symbol=symbol,
            open_time=datetime.fromtimestamp(item.start / 1000, tz=UTC),
            open_price=Decimal(item.open),
            high_price=Decimal(item.high),
            low_price=Decimal(item.low),
            close_price=Decimal(item.close),
            volume=Decimal(item.volume),
            turnover=Decimal(item.turnover),
        )
        for item in message.data
        if item.confirm and item.interval == "1"
    ]


class BybitKlineWebSocketManager:
    def __init__(
        self,
        *,
        url: str,
        shard_size: int,
        queue_size: int,
        heartbeat_seconds: float,
        reconnect_base_seconds: float,
        reconnect_max_seconds: float,
        ready_timeout_seconds: float,
    ) -> None:
        self._url = url
        self._shard_size = shard_size
        self._heartbeat_seconds = heartbeat_seconds
        self._reconnect_base_seconds = reconnect_base_seconds
        self._reconnect_max_seconds = reconnect_max_seconds
        self._ready_timeout_seconds = ready_timeout_seconds
        self._events: asyncio.Queue[MarketStreamEvent] = asyncio.Queue(maxsize=queue_size)
        self._tasks: list[asyncio.Task[None]] = []
        self._ready_events: list[asyncio.Event] = []
        self._symbols: tuple[str, ...] = ()

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    async def start(self, symbols: Sequence[str]) -> None:
        await self.stop()
        while not self._events.empty():
            self._events.get_nowait()
        MARKET_DATA_WS_QUEUE_DEPTH.set(0)
        shards = shard_symbols(symbols, self._shard_size)
        self._symbols = tuple(symbol for shard in shards for symbol in shard)
        self._ready_events = [asyncio.Event() for _ in shards]
        self._tasks = [
            asyncio.create_task(
                self._run_shard(
                    shard_index=index,
                    symbols=shard,
                    ready_event=self._ready_events[index],
                ),
                name=f"bybit-kline-shard-{index}",
            )
            for index, shard in enumerate(shards)
        ]

    async def wait_until_ready(self) -> None:
        if not self._ready_events:
            raise BybitWebSocketError("Cannot start WebSocket manager without symbols")
        try:
            async with asyncio.timeout(self._ready_timeout_seconds):
                await asyncio.gather(*(event.wait() for event in self._ready_events))
        except TimeoutError as exc:
            raise BybitWebSocketError("Bybit WebSocket shards did not become ready") from exc

    async def next_event(self) -> MarketStreamEvent:
        event = await self._events.get()
        MARKET_DATA_WS_QUEUE_DEPTH.set(self._events.qsize())
        return event

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._ready_events = []
        self._symbols = ()

    async def _put_event(self, event: MarketStreamEvent) -> None:
        await self._events.put(event)
        MARKET_DATA_WS_QUEUE_DEPTH.set(self._events.qsize())

    async def _run_shard(
        self,
        *,
        shard_index: int,
        symbols: tuple[str, ...],
        ready_event: asyncio.Event,
    ) -> None:
        connected_once = False
        disconnect_notified = False
        attempt = 0

        while True:
            try:
                async with connect(
                    self._url,
                    open_timeout=10,
                    close_timeout=5,
                    ping_interval=None,
                    max_queue=128,
                    user_agent_header="StructurePulse/0.1.0",
                ) as websocket:
                    await self._subscribe(websocket, symbols)
                    if connected_once:
                        await self._put_event(ShardReconnectedEvent(symbols=symbols))
                    else:
                        connected_once = True
                        ready_event.set()
                    disconnect_notified = False
                    attempt = 0
                    await logger.ainfo(
                        "bybit_websocket_shard_connected",
                        shard=shard_index,
                        symbol_count=len(symbols),
                    )
                    await self._receive(websocket, symbols)
                    raise BybitWebSocketError("Bybit WebSocket connection closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if connected_once and not disconnect_notified:
                    await self._put_event(
                        ShardDisconnectedEvent(
                            symbols=symbols,
                            reason=f"{type(exc).__name__}: {exc}",
                        )
                    )
                    disconnect_notified = True
                MARKET_DATA_WS_RECONNECTS.labels(shard=str(shard_index)).inc()
                delay = self._reconnect_delay(attempt)
                attempt += 1
                await logger.awarning(
                    "bybit_websocket_shard_reconnecting",
                    shard=shard_index,
                    delay_seconds=delay,
                    error=f"{type(exc).__name__}: {exc}",
                )
                await asyncio.sleep(delay)

    async def _subscribe(
        self,
        websocket: ClientConnection,
        symbols: tuple[str, ...],
    ) -> None:
        request_id = f"kline-{id(websocket)}"
        await websocket.send(
            json.dumps(
                {
                    "req_id": request_id,
                    "op": "subscribe",
                    "args": [f"kline.1.{symbol}" for symbol in symbols],
                }
            )
        )
        async with asyncio.timeout(10):
            while True:
                payload = self._decode(await websocket.recv())
                if payload.get("op") == "subscribe":
                    if payload.get("success") is not True:
                        raise BybitWebSocketError(
                            f"Bybit subscription rejected: {payload.get('ret_msg')}"
                        )
                    return
                await self._handle_payload(payload)

    async def _receive(
        self,
        websocket: ClientConnection,
        symbols: tuple[str, ...],
    ) -> None:
        heartbeat = asyncio.create_task(
            self._heartbeat(websocket),
            name=f"bybit-heartbeat-{symbols[0]}",
        )
        try:
            async for raw_message in websocket:
                await self._handle_payload(self._decode(raw_message))
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _handle_payload(self, payload: dict[str, Any]) -> None:
        try:
            candles = parse_closed_1m_candles(payload)
        except BybitWebSocketError:
            MARKET_DATA_WS_EVENTS.labels(outcome="invalid").inc()
            await logger.aexception("bybit_websocket_payload_invalid")
            return

        received_at = datetime.now(UTC)
        for candle in candles:
            MARKET_DATA_WS_EVENTS.labels(outcome="closed_candle").inc()
            candle_end = candle.open_time + timedelta(minutes=1)
            MARKET_DATA_WS_FRESHNESS_SECONDS.labels(symbol=candle.symbol).set(
                max(0.0, (received_at - candle_end).total_seconds())
            )
            await self._put_event(ClosedCandleEvent(candle=candle, received_at=received_at))

    async def _heartbeat(self, websocket: ClientConnection) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            await websocket.send(json.dumps({"op": "ping"}))

    def _reconnect_delay(self, attempt: int) -> float:
        exponential = min(
            self._reconnect_max_seconds,
            self._reconnect_base_seconds * (2**attempt),
        )
        return float(exponential + random.uniform(0, exponential * 0.25))

    @staticmethod
    def _decode(raw_message: str | bytes) -> dict[str, Any]:
        try:
            payload = json.loads(raw_message)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BybitWebSocketError("Bybit returned invalid WebSocket JSON") from exc
        if not isinstance(payload, dict):
            raise BybitWebSocketError("Bybit WebSocket payload is not an object")
        return payload
