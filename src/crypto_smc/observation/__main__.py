import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import cast

from crypto_smc.config import get_settings
from crypto_smc.db.session import create_engine, create_session_factory
from crypto_smc.observation.repository import ObservationRepository
from crypto_smc.strategy import StrategyConfig
from crypto_smc.strategy.serialization import json_safe


def _write_report(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2),
        encoding="utf-8",
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="StructurePulse live observation")
    commands = root.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start", help="Start a strategy evaluation window")
    start.add_argument("--name", required=True)
    start.add_argument("--strategy-version", default=StrategyConfig().version)

    commands.add_parser("status", help="Show the active evaluation window")

    report = commands.add_parser("report", help="Build a live performance report")
    report.add_argument("--name")
    report.add_argument("--output")

    commands.add_parser("close", help="Close the active evaluation window")
    return root


async def run(args: argparse.Namespace) -> dict[str, object]:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    repository = ObservationRepository()
    try:
        if args.command == "start":
            result = await repository.start_window(
                session_factory,
                name=args.name,
                strategy_version=args.strategy_version,
            )
            return cast(dict[str, object], json_safe(asdict(result)))
        if args.command == "status":
            current = await repository.current_window(session_factory)
            return {"active": json_safe(asdict(current)) if current else None}
        if args.command == "close":
            result = await repository.close_window(session_factory)
            return cast(dict[str, object], json_safe(asdict(result)))
        report = await repository.report(
            session_factory,
            window_name=args.name,
        )
        payload = cast(dict[str, object], json_safe(asdict(report)))
        if args.output:
            await asyncio.to_thread(
                _write_report,
                Path(args.output),
                payload,
            )
        return payload
    finally:
        await engine.dispose()


def main() -> int:
    args = parser().parse_args()
    print(json.dumps(asyncio.run(run(args)), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
