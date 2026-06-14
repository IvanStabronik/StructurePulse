from prometheus_client import Counter, Histogram

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
