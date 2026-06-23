import asyncio
from decimal import Decimal

from crypto_smc.config import get_settings
from crypto_smc.providers.bybit import BybitPrivateClient


async def main() -> None:
    settings = get_settings()
    print(f"Execution enabled: {settings.execution_enabled}")
    print(f"Execution mode: {settings.execution_mode}")
    print(f"Risk per trade: {settings.execution_risk_usdt} USDT")
    print(f"Minimum adaptive risk: {settings.execution_min_risk_usdt} USDT")
    print(f"Leverage: {settings.execution_leverage}x")
    print(f"Max effective leverage: {settings.execution_max_effective_leverage}x")
    print(f"Minimum live score: {settings.execution_min_signal_score}")
    print(f"Max notional/wallet: {settings.execution_max_notional_to_wallet_ratio}x")
    print(f"Execution allowlist: {sorted(settings.execution_symbol_allowlist) or 'all'}")
    print(f"Execution denylist: {sorted(settings.execution_symbol_denylist) or 'none'}")
    print(f"TP1 close fraction: {settings.execution_tp1_close_fraction}")
    print(f"Move stop to BE after TP1: {settings.execution_move_stop_to_be_after_tp1}")
    print(f"Max open positions: {settings.execution_max_open_positions}")
    print(f"Max trades per day: {settings.execution_max_trades_per_day}")
    print(f"Max daily loss: {settings.execution_max_daily_loss_usdt} USDT")
    print(f"Pending entry timeout: {settings.execution_pending_entry_timeout_seconds}s")

    if not settings.bybit_api_key or not settings.bybit_api_secret:
        print("Bybit credentials: missing")
        print("Add BYBIT_API_KEY and BYBIT_API_SECRET to .env to check the live account.")
        return

    print("Bybit credentials: configured")
    client = BybitPrivateClient(
        base_url=settings.bybit_base_url,
        api_key=settings.bybit_api_key,
        api_secret=settings.bybit_api_secret,
        timeout_seconds=settings.bybit_request_timeout_seconds,
        recv_window_ms=settings.bybit_recv_window_ms,
        max_requests_per_second=settings.bybit_max_requests_per_second,
        max_concurrency=settings.bybit_max_concurrency,
    )
    try:
        balance = await client.get_wallet_balance(
            account_type=settings.bybit_account_type,
            coin="USDT",
        )
    finally:
        await client.close()

    usdt = next((coin for coin in balance.coins if coin.coin == "USDT"), None)
    available = balance.total_available_balance
    wallet = usdt.wallet_balance if usdt is not None else balance.total_wallet_balance
    print(f"USDT available: {_format_money(available)}")
    print(f"USDT wallet: {_format_money(wallet)}")


def _format_money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))} USDT"


if __name__ == "__main__":
    asyncio.run(main())
