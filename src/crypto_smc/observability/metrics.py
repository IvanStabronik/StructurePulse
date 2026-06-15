from prometheus_client import Counter, Gauge, Histogram

BYBIT_REQUESTS = Counter(
    "crypto_smc_bybit_requests_total",
    "Bybit REST requests",
    labelnames=("endpoint", "outcome"),
)

BYBIT_REQUEST_DURATION = Histogram(
    "crypto_smc_bybit_request_duration_seconds",
    "Bybit REST request duration",
    labelnames=("endpoint",),
)

BYBIT_RATE_LIMIT_EVENTS = Counter(
    "crypto_smc_bybit_rate_limit_events_total",
    "Bybit REST rate-limit responses and waits",
    labelnames=("reason",),
)

COINGECKO_REQUESTS = Counter(
    "crypto_smc_coingecko_requests_total",
    "CoinGecko REST requests",
    labelnames=("endpoint", "outcome"),
)

COINGECKO_REQUEST_DURATION = Histogram(
    "crypto_smc_coingecko_request_duration_seconds",
    "CoinGecko REST request duration",
    labelnames=("endpoint",),
)

UNIVERSE_REFRESHES = Counter(
    "crypto_smc_universe_refreshes_total",
    "Universe refresh attempts",
    labelnames=("outcome",),
)

MARKET_DATA_SYNC_RESULTS = Counter(
    "crypto_smc_market_data_sync_results_total",
    "Market-data symbol synchronization results",
    labelnames=("result",),
)

MARKET_DATA_UNRESOLVED_GAPS = Gauge(
    "crypto_smc_market_data_unresolved_gaps",
    "Current unresolved market-data gaps",
)

MARKET_DATA_WS_EVENTS = Counter(
    "crypto_smc_market_data_ws_events_total",
    "Bybit WebSocket market-data events",
    labelnames=("outcome",),
)

MARKET_DATA_WS_RECONNECTS = Counter(
    "crypto_smc_market_data_ws_reconnects_total",
    "Bybit WebSocket reconnect attempts",
    labelnames=("shard",),
)

MARKET_DATA_WS_QUEUE_DEPTH = Gauge(
    "crypto_smc_market_data_ws_queue_depth",
    "Buffered Bybit WebSocket market-data events",
)

MARKET_DATA_WS_FRESHNESS_SECONDS = Gauge(
    "crypto_smc_market_data_ws_freshness_seconds",
    "Delay between the end of a closed 1m candle and receipt",
    labelnames=("symbol",),
)

AGGREGATION_RESULTS = Counter(
    "crypto_smc_aggregation_results_total",
    "Aggregate rebuild outcomes",
    labelnames=("timeframe", "result"),
)

AGGREGATION_JOB_DURATION = Histogram(
    "crypto_smc_aggregation_job_duration_seconds",
    "Aggregate rebuild duration",
    labelnames=("timeframe",),
)

AGGREGATION_QUEUE_DEPTH = Gauge(
    "crypto_smc_aggregation_queue_depth",
    "Pending and processing aggregate rebuild jobs",
)

AGGREGATION_RECONCILIATIONS = Counter(
    "crypto_smc_aggregation_reconciliations_total",
    "Aggregate comparisons with Bybit REST",
    labelnames=("timeframe", "result"),
)

STRATEGY_ANALYSIS_RESULTS = Counter(
    "crypto_smc_strategy_analysis_results_total",
    "Strategy analysis outcomes",
    labelnames=("result",),
)

SIGNAL_TRADE_STREAM_EVENTS = Counter(
    "crypto_smc_signal_trade_stream_events_total",
    "Bybit public-trade events used for signal lifecycle tracking",
    labelnames=("outcome",),
)

SIGNAL_TRADE_STREAM_RECONNECTS = Counter(
    "crypto_smc_signal_trade_stream_reconnects_total",
    "Bybit public-trade reconnect attempts",
    labelnames=("symbol",),
)

SIGNAL_COVERAGE_RESULTS = Counter(
    "crypto_smc_signal_coverage_results_total",
    "Signal public-trade coverage establishment results",
    labelnames=("result",),
)

EVENT_LOOP_LAG_SECONDS = Gauge(
    "crypto_smc_event_loop_lag_seconds",
    "Observed event-loop scheduling delay",
)

EVENT_LOOP_LAG_WARNINGS = Counter(
    "crypto_smc_event_loop_lag_warnings_total",
    "Event-loop lag threshold crossings",
)

STRATEGY_PROCESS_ACTIVE_BATCHES = Gauge(
    "crypto_smc_strategy_process_active_batches",
    "Strategy batches currently using process-pool capacity",
)

STRATEGY_PROCESS_WAITING_BATCHES = Gauge(
    "crypto_smc_strategy_process_waiting_batches",
    "Strategy batches waiting for process-pool capacity",
)

STRATEGY_PROCESS_SATURATION_RATIO = Gauge(
    "crypto_smc_strategy_process_saturation_ratio",
    "Fraction of configured strategy process-pool batch capacity in use",
)

WORKER_READY = Gauge(
    "crypto_smc_worker_ready",
    "Whether the worker is ready to generate new signals",
)

WORKER_QUIESCING = Gauge(
    "crypto_smc_worker_quiescing",
    "Whether the worker is draining before shutdown",
)

MAINTENANCE_DELETED_ROWS = Counter(
    "crypto_smc_maintenance_deleted_rows_total",
    "Rows removed by bounded retention maintenance",
    labelnames=("table",),
)

MAINTENANCE_RUNS = Counter(
    "crypto_smc_maintenance_runs_total",
    "Database maintenance runs",
    labelnames=("outcome",),
)
