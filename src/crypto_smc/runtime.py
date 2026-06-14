import asyncio
import signal
from collections.abc import Awaitable, Callable

import structlog

logger = structlog.get_logger(__name__)


async def run_periodic(
    job: Callable[[], Awaitable[object]],
    *,
    interval_seconds: float,
    service_name: str,
) -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        stop_event.set()

    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, request_stop)
        except NotImplementedError:
            signal.signal(signal_name, lambda *_: loop.call_soon_threadsafe(request_stop))

    await logger.ainfo("service_started", service=service_name)
    try:
        while not stop_event.is_set():
            try:
                await job()
            except Exception:
                await logger.aexception("periodic_job_failed", service=service_name)

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue
    finally:
        await logger.ainfo("service_stopped", service=service_name)


async def run_until_stopped(
    job: Callable[[], Awaitable[None]],
    *,
    service_name: str,
) -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        stop_event.set()

    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, request_stop)
        except NotImplementedError:
            signal.signal(signal_name, lambda *_: loop.call_soon_threadsafe(request_stop))

    service_task: asyncio.Future[None] = asyncio.ensure_future(job())
    stop_task = asyncio.create_task(stop_event.wait(), name=f"{service_name}-stop")
    await logger.ainfo("service_started", service=service_name)
    try:
        completed, _ = await asyncio.wait(
            (service_task, stop_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if service_task in completed:
            await service_task
    finally:
        stop_task.cancel()
        if not service_task.done():
            service_task.cancel()
        await asyncio.gather(service_task, stop_task, return_exceptions=True)
        await logger.ainfo("service_stopped", service=service_name)
