import csv
import json
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from crypto_smc.replay.models import (
    ReplayCandidate,
    ReplayOutcome,
    ReplaySummary,
)
from crypto_smc.strategy import StrategyConfig
from crypto_smc.strategy.serialization import json_safe


def build_summary(
    *,
    config: StrategyConfig,
    input_rows: int,
    symbol_count: int,
    candidates: tuple[ReplayCandidate, ...],
    outcomes: tuple[ReplayOutcome, ...],
) -> ReplaySummary:
    statuses = Counter(item.candidate.status for item in candidates)
    outcome_counts = Counter(item.status for item in outcomes)
    score_bands = Counter(
        _score_band(
            item.candidate.score,
            minimum=config.minimum_score,
            strong=config.strong_score,
        )
        for item in candidates
    )
    ordered_outcomes = sorted(
        outcomes,
        key=lambda item: (
            item.resolved_at or datetime.max.replace(tzinfo=UTC),
            item.candidate_sequence,
        ),
    )
    net_profit = sum((item.pnl for item in outcomes), Decimal(0))
    positives = sum((item.pnl for item in outcomes if item.pnl > 0), Decimal(0))
    negatives = abs(sum((item.pnl for item in outcomes if item.pnl < 0), Decimal(0)))
    profit_factor = positives / negatives if negatives > 0 else None
    balance = config.reference_balance
    peak = balance
    maximum_drawdown = Decimal(0)
    maximum_drawdown_fraction = Decimal(0)
    for item in ordered_outcomes:
        balance += item.pnl
        peak = max(peak, balance)
        drawdown = peak - balance
        maximum_drawdown = max(maximum_drawdown, drawdown)
        if peak > 0:
            maximum_drawdown_fraction = max(
                maximum_drawdown_fraction,
                drawdown / peak,
            )
    return ReplaySummary(
        strategy_version=config.version,
        input_rows=input_rows,
        symbols=symbol_count,
        candidate_count=len(candidates),
        accepted_count=statuses["accepted"],
        suppressed_count=statuses["suppressed"],
        outcome_counts=dict(sorted(outcome_counts.items())),
        score_bands=dict(sorted(score_bands.items())),
        net_profit=net_profit,
        profit_factor=profit_factor,
        maximum_drawdown=maximum_drawdown,
        maximum_drawdown_fraction=maximum_drawdown_fraction,
        ambiguity_count=sum(item.ambiguous for item in outcomes),
    )


def write_reports(
    output_directory: Path,
    *,
    config: StrategyConfig,
    candidates: tuple[ReplayCandidate, ...],
    outcomes: tuple[ReplayOutcome, ...],
    summary: ReplaySummary,
) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "strategy": config.parameter_snapshot(),
        "summary": json_safe(summary),
        "candidates": [
            {
                "sequence": item.sequence,
                "input_cutoffs": json_safe(item.input_cutoffs),
                **json_safe(asdict(item.candidate)),
            }
            for item in candidates
        ],
        "outcomes": [json_safe(item) for item in outcomes],
    }
    (output_directory / "report.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    _write_candidates_csv(output_directory / "candidates.csv", candidates)
    _write_outcomes_csv(output_directory / "outcomes.csv", outcomes)


def _write_candidates_csv(path: Path, candidates: tuple[ReplayCandidate, ...]) -> None:
    fields = (
        "sequence",
        "symbol",
        "direction",
        "status",
        "score",
        "strength",
        "analyzed_at",
        "expires_at",
        "entry_lower",
        "entry_upper",
        "planned_entry",
        "stop_loss",
        "take_profit_1",
        "take_profit_2",
        "net_reward_to_risk",
        "recommended_leverage",
        "suppression_reasons",
        "warnings",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in candidates:
            candidate = item.candidate
            plan = candidate.trade_plan
            writer.writerow(
                {
                    "sequence": item.sequence,
                    "symbol": candidate.symbol,
                    "direction": candidate.direction,
                    "status": candidate.status,
                    "score": candidate.score,
                    "strength": candidate.strength,
                    "analyzed_at": candidate.analyzed_at.isoformat(),
                    "expires_at": candidate.expires_at.isoformat(),
                    "entry_lower": plan.entry_lower if plan else "",
                    "entry_upper": plan.entry_upper if plan else "",
                    "planned_entry": plan.planned_entry if plan else "",
                    "stop_loss": plan.stop_loss if plan else "",
                    "take_profit_1": plan.take_profit_1 if plan else "",
                    "take_profit_2": plan.take_profit_2 if plan else "",
                    "net_reward_to_risk": plan.net_reward_to_risk if plan else "",
                    "recommended_leverage": plan.recommended_leverage if plan else "",
                    "suppression_reasons": "|".join(candidate.suppression_reasons),
                    "warnings": "|".join(candidate.warnings),
                }
            )


def _write_outcomes_csv(path: Path, outcomes: tuple[ReplayOutcome, ...]) -> None:
    fields = (
        tuple(asdict(outcomes[0]).keys())
        if outcomes
        else (
            "candidate_sequence",
            "symbol",
            "direction",
            "status",
            "entered_at",
            "resolved_at",
            "pnl",
            "r_multiple",
            "fees",
            "ambiguous",
        )
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for outcome in outcomes:
            writer.writerow(_csv_safe(asdict(outcome)))


def _csv_safe(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.isoformat() if isinstance(item, datetime) else item for key, item in value.items()
    }


def _score_band(score: int, *, minimum: int, strong: int) -> str:
    if score < minimum:
        return f"0-{minimum - 1}"
    if score < strong:
        return f"{minimum}-{strong - 1}"
    return f"{strong}-100"
