from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from crypto_smc.observation.models import EvaluationReport
from crypto_smc.strategy.serialization import parameter_checksum


@dataclass(frozen=True, slots=True)
class MetricComparison:
    live: Decimal | None
    replay: Decimal | None
    absolute_delta: Decimal | None
    relative_delta: Decimal | None


@dataclass(frozen=True, slots=True)
class ObservationComparison:
    status: str
    strategy_version: str
    strategy_parameter_checksum: str
    live_duration_hours: Decimal
    live_completed: int
    replay_completed: int
    metrics: dict[str, MetricComparison]
    score_band_shares: dict[str, MetricComparison]
    notes: tuple[str, ...]


def compare_live_to_replay(
    live: EvaluationReport,
    replay_payload: dict[str, Any],
) -> ObservationComparison:
    strategy = _mapping(replay_payload.get("strategy"), "strategy")
    summary = _mapping(replay_payload.get("summary"), "summary")
    replay_version = _string(summary.get("strategy_version"), "summary.strategy_version")
    replay_checksum = replay_payload.get("strategy_parameter_checksum")
    if replay_checksum is None:
        replay_checksum = parameter_checksum(strategy)
    replay_checksum = _string(replay_checksum, "strategy_parameter_checksum")

    if replay_version != live.window.strategy_version:
        raise ValueError(
            f"Strategy version mismatch: live={live.window.strategy_version}, "
            f"replay={replay_version}"
        )
    if replay_checksum != live.window.strategy_parameter_checksum:
        raise ValueError("Strategy parameter checksum mismatch")

    replay_candidates = tuple(
        _mapping(item, "candidates[]")
        for item in _sequence(replay_payload.get("candidates"), "candidates")
    )
    replay_outcomes = tuple(
        _mapping(item, "outcomes[]")
        for item in _sequence(replay_payload.get("outcomes"), "outcomes")
        if _mapping(item, "outcomes[]").get("status") != "open"
    )
    replay_input_rows = _int(summary.get("input_rows"), "summary.input_rows")
    replay_accepted = _int(summary.get("accepted_count"), "summary.accepted_count")
    replay_candidate_count = _int(
        summary.get("candidate_count"),
        "summary.candidate_count",
    )
    replay_symbol_days = Decimal(replay_input_rows) / Decimal(1440)
    replay_scores = tuple(
        _decimal(item.get("score"), "candidates[].score") for item in replay_candidates
    )
    replay_completed = len(replay_outcomes)
    replay_entered = sum(item.get("entered_at") is not None for item in replay_outcomes)
    replay_wins = sum(_decimal(item.get("pnl"), "outcomes[].pnl") > 0 for item in replay_outcomes)
    replay_ambiguous = sum(bool(item.get("ambiguous")) for item in replay_outcomes)
    replay_average_r = (
        sum(
            (_decimal(item.get("r_multiple"), "outcomes[].r_multiple") for item in replay_outcomes),
            Decimal(0),
        )
        / Decimal(replay_completed)
        if replay_completed
        else Decimal(0)
    )

    live_candidate_count = live.candidates.total
    live_completed = live.overall.completed
    metrics = {
        "accepted_per_symbol_day": _compare(
            live.candidates.accepted_per_symbol_day,
            Decimal(replay_accepted) / replay_symbol_days if replay_symbol_days > 0 else Decimal(0),
        ),
        "acceptance_rate": _compare(
            _ratio(live.candidates.accepted, live_candidate_count),
            _ratio(replay_accepted, replay_candidate_count),
        ),
        "average_score": _compare(
            live.candidates.average_score,
            (
                sum(replay_scores, Decimal(0)) / Decimal(len(replay_scores))
                if replay_scores
                else Decimal(0)
            ),
        ),
        "entry_rate": _compare(
            _ratio(live.overall.entered, live_completed),
            _ratio(replay_entered, replay_completed),
        ),
        "win_rate": _compare(
            _ratio(live.overall.wins, live_completed),
            _ratio(replay_wins, replay_completed),
        ),
        "ambiguity_rate": _compare(
            _ratio(live.overall.ambiguous, live_completed),
            _ratio(replay_ambiguous, replay_completed),
        ),
        "average_r": _compare(live.overall.average_r, replay_average_r),
        "profit_factor": _compare(
            live.overall.profit_factor,
            _optional_decimal(summary.get("profit_factor")),
        ),
        "maximum_drawdown_fraction": _compare(
            live.maximum_drawdown_fraction,
            _decimal(
                summary.get("maximum_drawdown_fraction"),
                "summary.maximum_drawdown_fraction",
            ),
        ),
    }
    replay_score_bands = _count_scores(replay_scores)
    score_band_shares = {
        band: _compare(
            _ratio(live.candidates.score_bands.get(band, 0), live_candidate_count),
            _ratio(replay_score_bands.get(band, 0), replay_candidate_count),
        )
        for band in sorted(set(live.candidates.score_bands) | set(replay_score_bands))
    }
    notes: list[str] = []
    if live.candidates.duration_hours < 24:
        notes.append("live_duration_below_24_hours")
    if live_completed < 30:
        notes.append("live_completed_sample_below_30")
    if replay_input_rows == 0:
        notes.append("replay_input_rows_zero")
    if replay_completed < 30:
        notes.append("replay_completed_sample_below_30")
    if live.unresolved_data_gaps or live.coverage_failures:
        notes.append("live_data_quality_defects_present")
    return ObservationComparison(
        status="preliminary" if notes else "comparable",
        strategy_version=live.window.strategy_version,
        strategy_parameter_checksum=live.window.strategy_parameter_checksum,
        live_duration_hours=live.candidates.duration_hours,
        live_completed=live_completed,
        replay_completed=replay_completed,
        metrics=metrics,
        score_band_shares=score_band_shares,
        notes=tuple(notes),
    )


def _compare(live: Decimal | None, replay: Decimal | None) -> MetricComparison:
    if live is None or replay is None:
        return MetricComparison(live, replay, None, None)
    delta = live - replay
    return MetricComparison(
        live=live,
        replay=replay,
        absolute_delta=delta,
        relative_delta=delta / abs(replay) if replay != 0 else None,
    )


def _ratio(numerator: int, denominator: int) -> Decimal:
    return Decimal(numerator) / Decimal(denominator) if denominator else Decimal(0)


def _count_scores(scores: tuple[Decimal, ...]) -> dict[str, int]:
    counts = {"0-69": 0, "70-84": 0, "85-100": 0}
    for score in scores:
        if score < 70:
            counts["0-69"] += 1
        elif score < 85:
            counts["70-84"] += 1
        else:
            counts["85-100"] += 1
    return counts


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _sequence(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _decimal(value: object, name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{name} must be numeric") from exc


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else _decimal(value, "optional metric")
