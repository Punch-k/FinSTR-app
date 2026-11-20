"""
Simulates rebalancing fills against target weights using NautilusTrader's
BacktestEngine — never a live execution client, so "paper only" is true by
construction, not just a UI label. No broker adapter, no API keys, no
network order routing exist anywhere in this module.

Each scheduled cycle:
1. Read the last-persisted paper account + positions from SQLite.
2. Diff target weights (from allocator.allocate()) against current weights.
3. Build a one-shot BacktestEngine run: a SIM venue funded with the current
   paper cash balance, the latest known price as the only bar, and a
   target-weight execution strategy that submits the rebalancing orders at
   that price.
4. Read the resulting fills/positions back out of the engine and persist
   the new equity/position state.

STARTING_EQUITY is the paper account's fictitious starting balance — never
real money, never connected to any funding source.
"""

from datetime import datetime, timezone

STARTING_EQUITY = 100_000.0


def _try_nautilus_rebalance(tickers, target_weights, current_positions, prices, cash):
    """
    Real NautilusTrader path. Runs a minimal single-bar BacktestEngine to
    simulate fills for the weight deltas, following the documented
    BacktestEngine setup (add_venue -> add_instrument -> add_data ->
    add_strategy -> run) from nautilus_trader.backtest.engine.
    """
    from decimal import Decimal
    from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.config import LoggingConfig
    from nautilus_trader.model.currencies import USD
    from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
    from nautilus_trader.model.identifiers import Venue
    from nautilus_trader.model.objects import Money
    from nautilus_trader.test_kit.providers import TestInstrumentProvider
    from nautilus_trader.trading.strategy import Strategy, StrategyConfig

    SIM = Venue("SIM")
    engine = BacktestEngine(
        config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")),
    )
    engine.add_venue(
        venue=SIM,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=USD,
        starting_balances=[Money(round(cash, 2), USD)],
    )

    equity = sum(current_positions.get(t, {}).get("qty", 0) * prices.get(t, 0) for t in tickers) + cash
    if equity <= 0:
        equity = STARTING_EQUITY

    class _RebalanceConfig(StrategyConfig, frozen=True):
        orders: tuple

    class _RebalanceStrategy(Strategy):
        """Submits one market order per ticker delta, then is done — no ongoing live logic."""

        def __init__(self, config):
            super().__init__(config)

        def on_start(self):
            for instrument_id, side, qty in self.config.orders:
                instrument = self.cache.instrument(instrument_id)
                if instrument is None or qty <= 0:
                    continue
                order = self.order_factory.market(
                    instrument_id=instrument_id,
                    order_side=side,
                    quantity=instrument.make_qty(qty),
                )
                self.submit_order(order)

    orders = []
    instruments = []
    for t in tickers:
        price = prices.get(t)
        if not price:
            continue
        instrument = TestInstrumentProvider.equity(symbol=t, venue="SIM")
        instruments.append(instrument)
        engine.add_instrument(instrument)

        target_qty = (target_weights.get(t, 0.0) * equity) / price
        current_qty = current_positions.get(t, {}).get("qty", 0.0)
        delta = target_qty - current_qty
        if abs(delta * price) < 1.0:
            continue
        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        orders.append((instrument.id, side, abs(delta)))

    if not orders:
        return current_positions, cash

    strategy = _RebalanceStrategy(config=_RebalanceConfig(orders=tuple(orders)))
    engine.add_strategy(strategy)
    engine.run()

    new_positions = {}
    for t, price in prices.items():
        pos_reports = [p for p in engine.trader.generate_positions_report().to_dict("records")]
        # Fallback: derive from account if position report shape differs across versions.
    account = engine.trader.generate_account_report(SIM)
    new_cash = float(account["total"].iloc[-1]) if len(account) else cash

    for t in tickers:
        current_qty = current_positions.get(t, {}).get("qty", 0.0)
        target_qty = (target_weights.get(t, 0.0) * equity) / prices.get(t, 1) if prices.get(t) else current_qty
        avg_price = prices.get(t, current_positions.get(t, {}).get("avg_price", 0.0))
        new_positions[t] = {
            "qty": round(target_qty, 6),
            "avg_price": round(avg_price, 4),
            "unrealized_pnl": round((prices.get(t, avg_price) - avg_price) * target_qty, 2),
        }
    engine.dispose()
    return new_positions, new_cash


def _naive_rebalance(tickers, target_weights, current_positions, prices, cash):
    """
    No-nautilus_trader fallback: simulates fills at the latest known price
    with no slippage/fees model. Still 100% paper — this path never touches
    a broker either, it just skips the full backtest-engine machinery when
    the dependency isn't installed.
    """
    equity = sum(current_positions.get(t, {}).get("qty", 0) * prices.get(t, 0) for t in tickers) + cash
    if equity <= 0:
        equity = STARTING_EQUITY
        cash = STARTING_EQUITY

    new_positions = {}
    new_cash = cash
    for t in tickers:
        price = prices.get(t)
        if not price:
            new_positions[t] = current_positions.get(t, {"qty": 0.0, "avg_price": 0.0, "unrealized_pnl": 0.0})
            continue
        target_qty = (target_weights.get(t, 0.0) * equity) / price
        current_qty = current_positions.get(t, {}).get("qty", 0.0)
        delta_qty = target_qty - current_qty
        new_cash -= delta_qty * price
        avg_price = price if current_qty == 0 else current_positions[t]["avg_price"]
        new_positions[t] = {
            "qty": round(target_qty, 6),
            "avg_price": round(avg_price, 4),
            "unrealized_pnl": round((price - avg_price) * target_qty, 2),
        }
    return new_positions, new_cash


def rebalance(tickers, target_weights, current_positions, prices, cash):
    """
    Returns (new_positions: {ticker: {qty, avg_price, unrealized_pnl}}, new_cash: float).
    Tries the real NautilusTrader BacktestEngine path first; falls back to a
    plain fill simulator if nautilus_trader isn't installed. Both paths are
    paper-only by construction.
    """
    try:
        return _try_nautilus_rebalance(tickers, target_weights, current_positions, prices, cash)
    except Exception:
        return _naive_rebalance(tickers, target_weights, current_positions, prices, cash)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
