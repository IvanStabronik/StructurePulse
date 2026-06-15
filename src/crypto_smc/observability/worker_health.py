import asyncio
import json
from collections.abc import Awaitable
from http import HTTPStatus

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.ext.asyncio import AsyncEngine

from crypto_smc.db.session import database_is_ready, database_schema_is_ready
from crypto_smc.observability.runtime import WorkerRuntimeState


class WorkerHealthServer:
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        runtime: WorkerRuntimeState,
        port: int,
        required_database_revision: str,
        dependency_timeout_seconds: float,
    ) -> None:
        self._engine = engine
        self._runtime = runtime
        self._port = port
        self._required_database_revision = required_database_revision
        self._dependency_timeout_seconds = dependency_timeout_seconds
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_connection,
            host="0.0.0.0",
            port=self._port,
        )

    @property
    def bound_port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("Worker health server is not running")
        return int(self._server.sockets[0].getsockname()[1])

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request_line = await asyncio.wait_for(
                reader.readline(),
                timeout=self._dependency_timeout_seconds,
            )
            path = _request_path(request_line)
            if path == "/health/live":
                await self._respond_json(writer, HTTPStatus.OK, {"status": "alive"})
            elif path == "/health/ready":
                await self._ready(writer)
            elif path == "/metrics":
                await self._respond(
                    writer,
                    HTTPStatus.OK,
                    generate_latest(),
                    CONTENT_TYPE_LATEST,
                )
            else:
                await self._respond_json(
                    writer,
                    HTTPStatus.NOT_FOUND,
                    {"detail": "not_found"},
                )
        except (TimeoutError, ValueError):
            await self._respond_json(
                writer,
                HTTPStatus.BAD_REQUEST,
                {"detail": "invalid_request"},
            )
        finally:
            writer.close()
            await writer.wait_closed()

    async def _ready(self, writer: asyncio.StreamWriter) -> None:
        database_ready, schema_ready = await asyncio.gather(
            self._bounded_check(database_is_ready(self._engine)),
            self._bounded_check(
                database_schema_is_ready(
                    self._engine,
                    required_revision=self._required_database_revision,
                )
            ),
        )
        self._runtime.update_ready_metric()
        dependencies = {
            "database": database_ready,
            "schema": schema_ready,
            "market_data": self._runtime.market_data_ready.is_set(),
            "quiescing": self._runtime.quiescing,
        }
        ready = (
            database_ready
            and schema_ready
            and dependencies["market_data"]
            and not self._runtime.quiescing
        )
        await self._respond_json(
            writer,
            HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
            {"status": "ready" if ready else "not_ready", "dependencies": dependencies},
        )

    async def _bounded_check(self, check: Awaitable[bool]) -> bool:
        try:
            return await asyncio.wait_for(
                check,
                timeout=self._dependency_timeout_seconds,
            )
        except Exception:
            return False

    @staticmethod
    async def _respond_json(
        writer: asyncio.StreamWriter,
        status: HTTPStatus,
        payload: dict[str, object],
    ) -> None:
        await WorkerHealthServer._respond(
            writer,
            status,
            json.dumps(payload, separators=(",", ":")).encode(),
            "application/json",
        )

    @staticmethod
    async def _respond(
        writer: asyncio.StreamWriter,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
    ) -> None:
        writer.write(
            (
                f"HTTP/1.1 {status.value} {status.phrase}\r\n"
                f"Content-Type: {content_type}\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode()
            + body
        )
        await writer.drain()


def _request_path(request_line: bytes) -> str:
    parts = request_line.decode("ascii").strip().split()
    if len(parts) != 3 or parts[0] != "GET":
        raise ValueError("Unsupported request")
    return parts[1].split("?", 1)[0]
