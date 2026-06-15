import asyncio
import json
import random
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from pydantic import ValidationError
from websockets.asyncio.client import ClientConnection, connect

from crypto_smc.observability.metrics import (
    SIGNAL_TRADE_STREAM_EVENTS,
    SIGNAL_TRADE_STREAM_RECONNECTS,
)
from crypto_smc.providers.bybit.schemas import BybitWebSocketPublicTradeMessage
from crypto_smc.providers.bybit.websocket import BybitWebSocketError
from crypto_smc.providers.models import PublicTrade

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PublicTradeEvent:
    trade: PublicTrade
    received_at: datetime


@dataclass(frozen=True, slots=True)
class TradeStreamReadyEvent:
    symbol: str
    subscribed_at: datetime
    reconnected: bool


@dataclass(frozen=True, slots=True)
class TradeStreamDisconnectedEvent:
    symbol: str
    disconnected_at: datetime
    reason: str


type TradeStreamEvent = PublicTradeEvent | TradeStreamReadyEvent | TradeStreamDisconnectedEvent


def parse_public_trades(payload: object) -> tuple[PublicTrade, ...]:
    if not isinstance(payload, dict):
        return ()
    topic = payload.get("topic")
    if not isinstance(topic, str) or not topic.startswith("publicTrade."):
        return ()
    try:
        message = BybitWebSocketPublicTradeMessage.model_validate(payload)
    except ValidationError as exc:
        raise BybitWebSocketError("Invalid Bybit public trade payload") from exc
    symbol = message.topic.removeprefix("publicTrade.").upper()
    trades = tuple(
        PublicTrade(
            trade_id=item.i,
            symbol=item.s.upper(),
            price=Decimal(item.p),
            size=Decimal(item.v),
            side=item.S,
            executed_at=datetime.fromtimestamp(item.T / 1000, tz=UTC),
            sequence=item.seq,
            is_block_trade=item.BT,
            is_rpi_trade=item.RPI,
        )
        for item in message.data
        if item.s.upper() == symbol
    )
    return tuple(
        sorted(
            trades,
            key=lambda trade: (trade.executed_at, trade.sequence, trade.trade_id),
        )
    )


class BybitPublicTradeWebSocketManager:
    def __init__(
        self,
        *,
        url: str,
        queue_size: int,
        buffer_size: int,
        heartbeat_seconds: float,
        reconnect_base_seconds: float,
        reconnect_max_seconds: float,
        ready_timeout_seconds: float,
    ) -> None:
        self._url = url
        self._heartbeat_seconds = heartbeat_seconds
        self._reconnect_base_seconds = reconnect_base_seconds
        self._reconnect_max_seconds = reconnect_max_seconds
        self._ready_timeout_seconds = ready_timeout_seconds
        self._events: asyncio.Queue[TradeStreamEvent] = asyncio.Queue(maxsize=queue_size)
        self._buffer_size = buffer_size
        self._buffers: dict[str, deque[PublicTrade]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._ready: dict[str, asyncio.Event] = {}

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._tasks))

    async def subscribe(self, symbol: str) -> datetime:
        normalized = symbol.upper()
        if normalized in self._tasks:
            return datetime.now(UTC)
        started_at = datetime.now(UTC)
        self._buffers[normalized] = deque(maxlen=self._buffer_size)
        self._ready[normalized] = asyncio.Event()
        self._tasks[normalized] = asyncio.create_task(
            self._run_symbol(normalized),
            name=f"bybit-public-trades-{normalized}",
        )
        return started_at

    async def wait_until_ready(self, symbol: str) -> None:
        normalized = symbol.upper()
        ready = self._ready.get(normalized)
        if ready is None:
            raise BybitWebSocketError(f"Symbol is not subscribed: {normalized}")
        try:
            async with asyncio.timeout(self._ready_timeout_seconds):
                await ready.wait()
        except TimeoutError as exc:
            raise BybitWebSocketError(
                f"Bybit public trade stream did not become ready: {normalized}"
            ) from exc

    def buffered_trades(
        self,
        symbol: str,
        *,
        since: datetime,
    ) -> tuple[PublicTrade, ...]:
        return tuple(
            trade for trade in self._buffers.get(symbol.upper(), ()) if trade.executed_at >= since
        )

    async def next_event(self) -> TradeStreamEvent:
        return await self._events.get()

    async def unsubscribe(self, symbol: str) -> None:
        normalized = symbol.upper()
        task = self._tasks.pop(normalized, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._ready.pop(normalized, None)
        self._buffers.pop(normalized, None)

    async def stop(self) -> None:
        for symbol in tuple(self._tasks):
            await self.unsubscribe(symbol)
        while not self._events.empty():
            self._events.get_nowait()

    async def _run_symbol(self, symbol: str) -> None:
        connected_once = False
        attempt = 0
        while True:
            subscribed_at = datetime.now(UTC)
            try:
                is_reconnect = connected_once
                async with connect(
                    self._url,
                    open_timeout=10,
                    close_timeout=5,
                    ping_interval=None,
                    max_queue=256,
                    user_agent_header="StructurePulse/0.1.0",
                ) as websocket:
                    first_trade_buffered = await self._subscribe(websocket, symbol)
                    if first_trade_buffered:
                        await self._mark_ready(
                            symbol,
                            subscribed_at=subscribed_at,
                            reconnected=is_reconnect,
                        )
                    connected_once = True
                    attempt = 0
                    await self._receive(
                        websocket,
                        symbol,
                        subscribed_at=subscribed_at,
                        reconnected=is_reconnect,
                        ready_emitted=first_trade_buffered,
                    )
                    raise BybitWebSocketError("Bybit public trade stream closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._ready[symbol].clear()
                SIGNAL_TRADE_STREAM_RECONNECTS.labels(symbol=symbol).inc()
                await self._events.put(
                    TradeStreamDisconnectedEvent(
                        symbol=symbol,
                        disconnected_at=datetime.now(UTC),
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                )
                delay = self._reconnect_delay(attempt)
                attempt += 1
                await logger.awarning(
                    "bybit_public_trade_stream_reconnecting",
                    symbol=symbol,
                    delay_seconds=delay,
                    error=f"{type(exc).__name__}: {exc}",
                )
                await asyncio.sleep(delay)

    async def _subscribe(self, websocket: ClientConnection, symbol: str) -> bool:
        await websocket.send(
            json.dumps(
                {
                    "req_id": f"public-trade-{symbol}-{id(websocket)}",
                    "op": "subscribe",
                    "args": [f"publicTrade.{symbol}"],
                }
            )
        )
        first_trade_buffered = False
        async with asyncio.timeout(10):
            while True:
                payload = self._decode(await websocket.recv())
                if payload.get("op") == "subscribe":
                    if payload.get("success") is not True:
                        raise BybitWebSocketError(
                            f"Bybit public trade subscription rejected: {payload.get('ret_msg')}"
                        )
                    return first_trade_buffered
                first_trade_buffered = (
                    await self._handle_payload(payload, symbol) or first_trade_buffered
                )

    async def _receive(
        self,
        websocket: ClientConnection,
        symbol: str,
        *,
        subscribed_at: datetime,
        reconnected: bool,
        ready_emitted: bool,
    ) -> None:
        heartbeat = asyncio.create_task(
            self._heartbeat(websocket),
            name=f"bybit-public-trade-heartbeat-{symbol}",
        )
        try:
            async for raw_message in websocket:
                has_trades = await self._handle_payload(
                    self._decode(raw_message),
                    symbol,
                )
                if has_trades and not ready_emitted:
                    await self._mark_ready(
                        symbol,
                        subscribed_at=subscribed_at,
                        reconnected=reconnected,
                    )
                    ready_emitted = True
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _handle_payload(
        self,
        payload: dict[str, Any],
        symbol: str,
    ) -> bool:
        try:
            trades = parse_public_trades(payload)
        except BybitWebSocketError:
            SIGNAL_TRADE_STREAM_EVENTS.labels(outcome="invalid").inc()
            await logger.aexception(
                "bybit_public_trade_payload_invalid",
                symbol=symbol,
            )
            return False
        received_at = datetime.now(UTC)
        for trade in trades:
            self._buffers[symbol].append(trade)
            SIGNAL_TRADE_STREAM_EVENTS.labels(outcome="trade").inc()
            await self._events.put(PublicTradeEvent(trade=trade, received_at=received_at))
        return bool(trades)

    async def _mark_ready(
        self,
        symbol: str,
        *,
        subscribed_at: datetime,
        reconnected: bool,
    ) -> None:
        self._ready[symbol].set()
        await self._events.put(
            TradeStreamReadyEvent(
                symbol=symbol,
                subscribed_at=subscribed_at,
                reconnected=reconnected,
            )
        )
        await logger.ainfo(
            "bybit_public_trade_stream_connected",
            symbol=symbol,
        )

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
            raise BybitWebSocketError("Bybit public trade payload is not an object")
        return payload
