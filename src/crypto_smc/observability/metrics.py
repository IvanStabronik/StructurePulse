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
