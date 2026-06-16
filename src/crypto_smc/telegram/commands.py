from dataclasses import dataclass
from datetime import time
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_smc.db.models import (
    DataCheckpointRecord,
    NotificationDeliveryRecord,
    SignalCandidateRecord,
    SignalRecord,
    UniverseMemberRecord,
    UniverseSnapshotRecord,
    VirtualTradeRecord,
)
from crypto_smc.db.repositories.notifications import NotificationRepository
from crypto_smc.telegram.localization import text
from crypto_smc.telegram.rendering import render_settings

ACTIVE_STATUSES = ("preparing", "active", "entered", "tp1_reached")
TERMINAL_TRADE_STATUSES = (
    "stopped",
    "stopped_at_breakeven",
    "tp2_completed",
    "ambiguous",
)


@dataclass(frozen=True, slots=True)
class ActiveSignal:
    symbol: str
    direction: str
    status: str
    score: int
    entry_lower: Decimal
    entry_upper: Decimal
    stop_loss: Decimal
    take_profit_1: Decimal
    take_profit_2: Decimal


@dataclass(frozen=True, slots=True)
class CoinAnalysis:
    symbol: str
    direction: str
    status: str
    score: int
    strength: str
    evidence: tuple[str, ...]
    suppression_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PerformanceStats:
    completed: int
    wins: int
    ambiguous: int
    pnl: Decimal
    average_r: Decimal


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    market_ready: int
    market_degraded: int
    notification_pending: int
    notification_failed: int
    delivery_unknown: int


class TelegramQueryRepository:
    async def active_signals(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> tuple[ActiveSignal, ...]:
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(SignalRecord, SignalCandidateRecord.score)
                    .join(
                        SignalCandidateRecord,
                        SignalCandidateRecord.id == SignalRecord.candidate_id,
                    )
                    .where(SignalRecord.status.in_(ACTIVE_STATUSES))
                    .order_by(SignalRecord.created_at.desc())
                )
            ).all()
        return tuple(
            ActiveSignal(
                symbol=signal.symbol,
                direction=signal.direction,
                status=signal.status,
                score=score,
                entry_lower=signal.entry_lower,
                entry_upper=signal.entry_upper,
                stop_loss=signal.stop_loss,
                take_profit_1=signal.take_profit_1,
                take_profit_2=signal.take_profit_2,
            )
            for signal, score in rows
        )

    async def latest_coin(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        symbol: str,
    ) -> CoinAnalysis | None:
        async with session_factory() as session:
            candidate = await session.scalar(
                select(SignalCandidateRecord)
                .where(SignalCandidateRecord.symbol == symbol.upper())
                .order_by(
                    SignalCandidateRecord.analyzed_at.desc(),
                    SignalCandidateRecord.id.desc(),
                )
                .limit(1)
            )
        if candidate is None:
            return None
        return CoinAnalysis(
            symbol=candidate.symbol,
            direction=candidate.direction,
            status=candidate.status,
            score=candidate.score,
            strength=candidate.strength,
            evidence=tuple(candidate.evidence),
            suppression_reasons=tuple(candidate.suppression_reasons),
        )

    async def stats(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> PerformanceStats:
        async with session_factory() as session:
            row = (
                await session.execute(
                    select(
                        func.count(VirtualTradeRecord.id),
                        func.coalesce(
                            func.sum(
                                case(
                                    (VirtualTradeRecord.realized_pnl > 0, 1),
                                    else_=0,
                                )
                            ),
                            0,
                        ),
                        func.coalesce(
                            func.sum(
                                case(
                                    (VirtualTradeRecord.ambiguous.is_(True), 1),
                                    else_=0,
                                )
                            ),
                            0,
                        ),
                        func.coalesce(func.sum(VirtualTradeRecord.realized_pnl), 0),
                        func.coalesce(func.avg(VirtualTradeRecord.r_multiple), 0),
                    ).where(VirtualTradeRecord.status.in_(TERMINAL_TRADE_STATUSES))
                )
            ).one()
        return PerformanceStats(
            completed=int(row[0]),
            wins=int(row[1]),
            ambiguous=int(row[2]),
            pnl=Decimal(row[3]),
            average_r=Decimal(row[4]),
        )

    async def status(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> ServiceStatus:
        async with session_factory() as session:
            market_rows = (
                await session.execute(
                    select(DataCheckpointRecord.state, func.count())
                    .select_from(UniverseMemberRecord)
                    .join(
                        UniverseSnapshotRecord,
                        UniverseSnapshotRecord.id == UniverseMemberRecord.snapshot_id,
                    )
                    .outerjoin(
                        DataCheckpointRecord,
                        (DataCheckpointRecord.symbol == UniverseMemberRecord.instrument_symbol)
                        & (DataCheckpointRecord.stream == "kline_1m"),
                    )
                    .where(UniverseSnapshotRecord.is_active.is_(True))
                    .where(UniverseMemberRecord.is_selected.is_(True))
                    .group_by(DataCheckpointRecord.state)
                )
            ).all()
            delivery_rows = (
                await session.execute(
                    select(NotificationDeliveryRecord.status, func.count()).group_by(
                        NotificationDeliveryRecord.status
                    )
                )
            ).all()
        market = {state: count for state, count in market_rows}
        deliveries = {status: count for status, count in delivery_rows}
        return ServiceStatus(
            market_ready=market.get("ready", 0),
            market_degraded=sum(count for state, count in market.items() if state != "ready"),
            notification_pending=deliveries.get("pending", 0) + deliveries.get("sending", 0),
            notification_failed=deliveries.get("failed", 0),
            delivery_unknown=deliveries.get("delivery_unknown", 0),
        )


class TelegramCommandService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings_repository: NotificationRepository | None = None,
        query_repository: TelegramQueryRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings_repository or NotificationRepository()
        self._queries = query_repository or TelegramQueryRepository()

    async def handle(self, user_id: int, command_text: str) -> str:
        settings = await self._settings.get_user(self._session_factory, user_id)
        if settings is None:
            return ""
        parts = command_text.strip().split()
        command = parts[0].split("@", 1)[0].lower() if parts else ""
        args = parts[1:]
        language = settings.language

        if command in {"/start", "/help"}:
            return text(language, "help")
        if command == "/signals":
            signals = await self._queries.active_signals(self._session_factory)
            if not signals:
                return text(language, "no_signals")
            return "\n\n".join(_render_active(item, language) for item in signals)
        if command == "/coin":
            if not args:
                return text(language, "coin_missing")
            symbol = _normalize_symbol(args[0])
            analysis = await self._queries.latest_coin(
                self._session_factory,
                symbol,
            )
            if analysis is None:
                return text(language, "coin_not_found", symbol=symbol)
            return _render_coin(analysis, language)
        if command == "/settings":
            return render_settings(settings, language)
        if command == "/status":
            return _render_status(
                await self._queries.status(self._session_factory),
                language,
            )
        if command == "/stats":
            return _render_stats(
                await self._queries.stats(self._session_factory),
                language,
            )
        if command == "/language":
            if len(args) != 1 or args[0].lower() not in {"ru", "en"}:
                return text(language, "invalid_value")
            updated = await self._settings.update_user(
                self._session_factory,
                user_id,
                language=args[0].lower(),
            )
            return render_settings(updated, updated.language)
        if command == "/threshold":
            if len(args) != 1 or not args[0].isdigit():
                return text(language, "invalid_value")
            threshold = int(args[0])
            if not 0 <= threshold <= 100:
                return text(language, "invalid_value")
            updated = await self._settings.update_user(
                self._session_factory,
                user_id,
                minimum_score=threshold,
            )
            return render_settings(updated, language)
        if command == "/schedule":
            parsed = _parse_schedule(args, settings.schedule_timezone)
            if parsed is None:
                return text(language, "invalid_value")
            start, end, timezone = parsed
            updated = await self._settings.update_user(
                self._session_factory,
                user_id,
                schedule_start=start,
                schedule_end=end,
                schedule_timezone=timezone,
            )
            return render_settings(updated, language)
        if command == "/risk":
            parsed_risk = _parse_risk(args)
            if parsed_risk is None:
                return text(language, "invalid_value")
            risk, balance = parsed_risk
            updated = await self._settings.update_user(
                self._session_factory,
                user_id,
                risk_percent=risk,
                reference_balance=balance,
            )
            return render_settings(updated, language)
        if command in {"/pause", "/resume"}:
            paused = command == "/pause"
            await self._settings.update_user(
                self._session_factory,
                user_id,
                paused=paused,
            )
            return text(language, "paused" if paused else "resumed")
        return text(language, "help")


def _parse_schedule(
    args: list[str],
    default_timezone: str,
) -> tuple[time, time, str] | None:
    if len(args) not in {2, 3}:
        return None
    try:
        start = time.fromisoformat(args[0])
        end = time.fromisoformat(args[1])
    except ValueError:
        return None
    timezone = args[2] if len(args) == 3 else default_timezone
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        return None
    return start, end, timezone


def _parse_risk(args: list[str]) -> tuple[Decimal, Decimal] | None:
    if len(args) != 2:
        return None
    try:
        risk = Decimal(args[0])
        balance = Decimal(args[1])
    except InvalidOperation:
        return None
    if risk <= 0 or risk > 10 or balance <= 0:
        return None
    return risk, balance


def _normalize_symbol(value: str) -> str:
    symbol = value.strip().upper()
    return symbol if symbol.endswith("USDT") else f"{symbol}USDT"


def _render_active(signal: ActiveSignal, language: str) -> str:
    title = "Активный сигнал" if language == "ru" else "Active signal"
    labels = (
        ("Статус", "оценка", "Вход", "Стоп", "Цели")
        if language == "ru"
        else ("Status", "score", "Entry", "Stop", "Targets")
    )
    return "\n".join(
        (
            f"{title}: {signal.symbol} {signal.direction.upper()}",
            f"{labels[0]}: {signal.status}, {labels[1]}: {signal.score}",
            f"{labels[2]}: {signal.entry_lower}-{signal.entry_upper}",
            f"{labels[3]}: {signal.stop_loss}",
            f"{labels[4]} TP1/TP2: {signal.take_profit_1}/{signal.take_profit_2}",
        )
    )


def _render_coin(analysis: CoinAnalysis, language: str) -> str:
    title = "Последний анализ" if language == "ru" else "Latest analysis"
    labels = (
        ("Статус", "Оценка", "Подтверждения", "Причины")
        if language == "ru"
        else ("Status", "Score", "Evidence", "Reasons")
    )
    reasons = ", ".join(analysis.suppression_reasons) or "-"
    evidence = ", ".join(analysis.evidence[:5]) or "-"
    return "\n".join(
        (
            f"{title}: {analysis.symbol} {analysis.direction.upper()}",
            f"{labels[0]}: {analysis.status}",
            f"{labels[1]}: {analysis.score}/100 ({analysis.strength})",
            f"{labels[2]}: {evidence}",
            f"{labels[3]}: {reasons}",
        )
    )


def _render_stats(stats: PerformanceStats, language: str) -> str:
    title = "Статистика" if language == "ru" else "Statistics"
    labels = (
        ("Завершено", "Победы", "Неоднозначные", "PnL", "Средний R")
        if language == "ru"
        else ("Completed", "Wins", "Ambiguous", "PnL", "Average R")
    )
    return "\n".join(
        (
            title,
            f"{labels[0]}: {stats.completed}",
            f"{labels[1]}: {stats.wins}",
            f"{labels[2]}: {stats.ambiguous}",
            f"{labels[3]}: {stats.pnl:.4f} USDT",
            f"{labels[4]}: {stats.average_r:.4f}",
        )
    )


def _render_status(status: ServiceStatus, language: str) -> str:
    if language == "ru":
        return "\n".join(
            (
                "Статус системы",
                f"Рынок готов: {status.market_ready}",
                f"Проблемные: {status.market_degraded}",
                f"Ожидают отправки: {status.notification_pending}",
                f"Ошибки: {status.notification_failed}",
                f"Исход неизвестен: {status.delivery_unknown}",
            )
        )
    return "\n".join(
        (
            "System status",
            f"Market ready: {status.market_ready}",
            f"Market degraded: {status.market_degraded}",
            f"Notifications pending: {status.notification_pending}",
            f"Notifications failed: {status.notification_failed}",
            f"Delivery unknown: {status.delivery_unknown}",
        )
    )
