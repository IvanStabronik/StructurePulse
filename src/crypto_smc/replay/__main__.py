import argparse
import json
from pathlib import Path

from crypto_smc.replay.loader import load_replay_csv
from crypto_smc.replay.reporting import write_reports
from crypto_smc.replay.runner import ReplayConfig, run_replay
from crypto_smc.strategy import StrategyConfig
from crypto_smc.strategy.serialization import json_safe


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay StructurePulse strategy over closed 1m CSV candles",
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--history-candles", type=int, default=300)
    parser.add_argument("--minimum-history-candles", type=int, default=30)
    arguments = parser.parse_args()

    strategy_config = StrategyConfig()
    rows = load_replay_csv(arguments.input)
    result = run_replay(
        rows,
        strategy_config=strategy_config,
        replay_config=ReplayConfig(
            history_candles=arguments.history_candles,
            minimum_history_candles=arguments.minimum_history_candles,
        ),
    )
    write_reports(
        arguments.output_dir,
        config=strategy_config,
        candidates=result.candidates,
        outcomes=result.outcomes,
        summary=result.summary,
    )
    print(json.dumps(json_safe(result.summary), sort_keys=True))


if __name__ == "__main__":
    main()
