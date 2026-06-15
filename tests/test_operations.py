import asyncio
import json
from datetime import UTC, datetime

import pytest

from crypto_smc.observability.runtime import WorkerRuntimeState
from crypto_smc.observability.worker_health import WorkerHealthServer
from crypto_smc.runtime import run_until_stopped
from crypto_smc.services.maintenance import MaintenanceService
from crypto_smc.services.operational_warnings import OperationalWarningService
from crypto_smc.telegram.rendering import render_notification


class FakeEngine:
    pass


class FakeMaintenanceRepository:
    async def delete_expired_candles(self, _: object, **__: object) -> dict[str, int]:
        return {"candles_1m": 3, "candles_agg": 2}


class FakeWarningRepository:
    def __init__(self) -> None:
        self.keys: list[str] = []

    async def enqueue_operational_event(
        self,
        _: object,
        *,
        idempotency_key: str,
        **__: object,
    ) -> bool:
        if idempotency_key in self.keys:
            return False
        self.keys.append(idempotency_key)
        return True


async def request(port: int, path: str) -> tuple[int, dict[str, object]]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
    await writer.drain()
    status_line = await reader.readline()
    headers: dict[str, str] = {}
    while line := await reader.readline():
        if line == b"\r\n":
            break
        name, value = line.decode().split(":", 1)
        headers[name.lower()] = value.strip()
    body = await reader.readexactly(int(headers["content-length"]))
    writer.close()
    await writer.wait_closed()
    return int(status_line.split()[1]), json.loads(body)


@pytest.mark.asyncio
async def test_worker_readiness_fails_closed_and_quiesces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def healthy_database(_: object) -> bool:
        return True

    async def current_schema(_: object, *, required_revision: str) -> bool:
        return required_revision == "0009"

    monkeypatch.setattr(
        "crypto_smc.observability.worker_health.database_is_ready",
        healthy_database,
    )
    monkeypatch.setattr(
        "crypto_smc.observability.worker_health.database_schema_is_ready",
        current_schema,
    )
    market_ready = asyncio.Event()
    runtime = WorkerRuntimeState(market_data_ready=market_ready)
    server = WorkerHealthServer(
        engine=FakeEngine(),  # type: ignore[arg-type]
        runtime=runtime,
        port=0,
        required_database_revision="0009",
        dependency_timeout_seconds=1,
    )
    await server.start()
    try:
        status, payload = await request(server.bound_port, "/health/ready")
        assert status == 503
        assert payload["dependencies"]["market_data"] is False  # type: ignore[index]

        market_ready.set()
        status, payload = await request(server.bound_port, "/health/ready")
        assert status == 200
        assert payload["status"] == "ready"

        runtime.begin_quiescence()
        status, payload = await request(server.bound_port, "/health/ready")
        assert status == 503
        assert payload["dependencies"]["quiescing"] is True  # type: ignore[index]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_runtime_calls_quiesce_when_service_finishes() -> None:
    quiesced = False

    async def job() -> None:
        return None

    def quiesce() -> None:
        nonlocal quiesced
        quiesced = True

    await run_until_stopped(
        job,
        service_name="test",
        quiesce=quiesce,
        shutdown_timeout_seconds=1,
    )

    assert quiesced is True


@pytest.mark.asyncio
async def test_maintenance_service_reports_bounded_deletes() -> None:
    service = MaintenanceService(
        session_factory=object(),  # type: ignore[arg-type]
        interval_seconds=60,
        candle_1m_retention_days=180,
        candle_agg_retention_days=730,
        batch_size=5000,
        repository=FakeMaintenanceRepository(),  # type: ignore[arg-type]
    )

    assert await service.run_once() == {"candles_1m": 3, "candles_agg": 2}


@pytest.mark.asyncio
async def test_operational_warnings_are_bucketed_and_recover_once() -> None:
    market_ready = asyncio.Event()
    runtime = WorkerRuntimeState(market_data_ready=market_ready)
    repository = FakeWarningRepository()
    service = OperationalWarningService(
        session_factory=object(),  # type: ignore[arg-type]
        runtime=runtime,
        interval_seconds=30,
        warning_delay_seconds=0,
        cooldown_seconds=1800,
        repository=repository,  # type: ignore[arg-type]
    )
    now = datetime(2026, 6, 15, 10, tzinfo=UTC)

    assert await service.check_once(now=now) is False
    assert await service.check_once(now=now) is True
    assert await service.check_once(now=now) is False

    market_ready.set()
    assert await service.check_once(now=now) is True
    assert await service.check_once(now=now) is False
    assert len(repository.keys) == 2


def test_service_warning_is_localized() -> None:
    message = render_notification(
        "service_warning",
        {
            "service": "market_data",
            "status": "degraded",
            "reason": "market_data_not_ready",
        },
        "ru",
    )

    assert "СЕРВИС ДЕГРАДИРОВАН" in message
    assert "деградирован" in message
