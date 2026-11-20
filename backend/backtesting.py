"""Historical quote replay using observed bids/asks, never theoretical fills.

Input CSVs:
- underlying: timestamp,symbol,open,high,low,close,volume
- options: timestamp,symbol,underlying,option_type,strike,expiration,delta,bid,ask,open_interest,volume
"""
from __future__ import annotations

import io
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from .config import BotSettings
from .indicators import closed_candles, resample_candles
from .risk_manager import RiskManager
from .strategies import StrategyEngine
from .signal_engine import SignalEngine
from .trailing_stop import initial_stop


class BacktestError(ValueError):
    pass


def parse_csv(content: bytes, kind: str) -> pd.DataFrame:
    try:
        frame = pd.read_csv(io.BytesIO(content))
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    except Exception as exc:
        raise BacktestError(f"Invalid {kind} CSV: {exc}") from exc
    required = {"timestamp", "symbol", "open", "high", "low", "close", "volume"} if kind == "underlying" else {
        "timestamp", "symbol", "underlying", "option_type", "strike", "expiration", "delta", "bid", "ask",
        "open_interest", "volume",
    }
    missing = required - set(frame.columns)
    if missing:
        raise BacktestError(f"Missing {kind} columns: {', '.join(sorted(missing))}")
    return frame.sort_values("timestamp").reset_index(drop=True)


class Backtester:
    def run(self, underlying: pd.DataFrame, options: pd.DataFrame, settings: BotSettings,
            initial_equity: float = 10_000, slippage_percent: float = 0.5,
            fee_per_contract: float = 0) -> dict[str, Any]:
        if underlying.empty or options.empty:
            raise BacktestError("Both historical underlying bars and observed option quotes are required")
        equity = initial_equity
        trades = []
        position = None
        pending_indices: dict[str, list[int]] = {}
        for symbol in underlying["symbol"].unique():
            rows = underlying.loc[underlying["symbol"] == symbol].reset_index(drop=True)
            pending_indices[symbol] = [0]
        timeline = sorted(underlying["timestamp"].unique())
        strategy = StrategyEngine()
        scorer = SignalEngine()
        risk = RiskManager()
        for timestamp in timeline:
            ts = pd.Timestamp(timestamp)
            quotes = options.loc[options["timestamp"] == ts]
            if position is not None:
                matching = quotes.loc[quotes["symbol"] == position["symbol"]]
                if matching.empty:
                    continue  # No observed executable quote; do not manufacture a fill.
                quote = matching.iloc[-1]
                bid, ask = float(quote["bid"]), float(quote["ask"])
                if bid <= 0 or ask <= 0 or bid > ask:
                    continue
                position["highest"] = max(position["highest"], bid)
                active = position["active"] or (
                    settings.trailing_stop_enabled and
                    bid >= position["entry"] * (1 + settings.trailing_activation_percent / 100)
                )
                position["active"] = active
                stop = max(position["stop"], position["highest"] * (1 - settings.trailing_distance_percent / 100)) if active else position["stop"]
                position["stop"] = stop
                fixed_target = settings.fixed_profit_target_enabled and bid >= position["entry"] * (
                    1 + settings.fixed_profit_target_percent / 100
                )
                if bid <= stop or fixed_target:
                    fill = bid * (1 - slippage_percent / 100)
                    proceeds = fill * 100 * position["qty"]
                    pnl = proceeds - position["cost"] - fee_per_contract * position["qty"]
                    equity += proceeds - fee_per_contract * position["qty"]
                    trades.append({"symbol": position["symbol"], "strategy": position["strategy"],
                                   "entry_time": position["entry_time"], "exit_time": ts.isoformat(),
                                   "entry_price": position["entry"], "exit_price": fill, "qty": position["qty"],
                                   "realized_pnl": pnl, "return_percent": (fill / position["entry"] - 1) * 100,
                                   "exit_reason": "fixed_profit_target" if fixed_target else ("trailing_stop" if active else "initial_stop")})
                    position = None
                continue
            if position is not None:
                continue
            for symbol in underlying["symbol"].unique():
                rows = underlying.loc[(underlying["symbol"] == symbol) & (underlying["timestamp"] <= ts)]
                one = closed_candles(rows.tail(1600), now=ts.to_pydatetime())
                if len(one) < 60:
                    continue
                five = resample_candles(one, 5)
                fifteen = resample_candles(one, 15)
                if len(five) < 55 or len(fifteen) < 55:
                    continue
                candidates = strategy.evaluate(symbol, one, five, fifteen, settings.enabled_strategies,
                                               settings.opening_range_minutes)
                for candidate in candidates:
                    eligible = quotes.loc[
                        (quotes["underlying"] == symbol) & (quotes["option_type"] == candidate.direction) &
                        (quotes["delta"].abs().between(settings.min_absolute_delta, settings.max_absolute_delta)) &
                        (quotes["bid"] > 0) & (quotes["ask"] >= quotes["bid"])
                    ].copy()
                    if eligible.empty:
                        continue
                    expiration = pd.to_datetime(eligible["expiration"], utc=True)
                    dte = (expiration.dt.normalize() - ts.normalize()).dt.days
                    eligible = eligible.loc[dte.between(1, 3)]
                    if eligible.empty:
                        continue
                    eligible["spread_pct"] = (eligible["ask"] - eligible["bid"]) / ((eligible["ask"] + eligible["bid"]) / 2) * 100
                    eligible = eligible.loc[eligible["spread_pct"] <= settings.max_spread_percent]
                    eligible = eligible.loc[eligible["open_interest"] >= settings.min_open_interest]
                    eligible = eligible.loc[eligible["volume"] >= settings.min_contract_volume]
                    if eligible.empty:
                        continue
                    quote = eligible.sort_values(["spread_pct", "open_interest"], ascending=[True, False]).iloc[0]
                    liquidity = max(0, 1 - float(quote["spread_pct"]) / settings.max_spread_percent)
                    scored = scorer.score(candidate, settings, liquidity, 0.6)
                    if not scored.qualified:
                        continue
                    entry = float(quote["ask"]) * (1 + slippage_percent / 100)
                    size = risk.position_size(scored.score, entry, 100, equity, equity, settings)
                    if size.quantity < 1:
                        continue
                    cost = entry * 100 * size.quantity
                    equity -= cost + fee_per_contract * size.quantity
                    position = {"symbol": quote["symbol"], "strategy": candidate.strategy, "entry": entry,
                                "qty": size.quantity, "cost": cost + fee_per_contract * size.quantity,
                                "highest": float(quote["bid"]), "stop": initial_stop(entry, settings.initial_stop_loss_percent),
                                "active": False, "entry_time": ts.isoformat()}
                    break
                if position is not None:
                    break
        if position is not None:
            # Open positions are reported, not closed with a fabricated terminal quote.
            open_position = {k: v for k, v in position.items() if k not in {"active"}}
        else:
            open_position = None
        pnls = np.array([trade["realized_pnl"] for trade in trades])
        gains = pnls[pnls > 0]
        losses = pnls[pnls < 0]
        curve = [initial_equity]
        running = initial_equity
        for pnl in pnls:
            running += float(pnl)
            curve.append(running)
        peaks = np.maximum.accumulate(curve)
        drawdown = float(np.max((np.array(peaks) - np.array(curve)) / np.array(peaks)) * 100) if curve else 0
        return {
            "initial_equity": initial_equity,
            "ending_equity_before_open_mark_value": equity,
            "net_realized_pnl": float(pnls.sum()) if len(pnls) else 0,
            "trade_count": len(trades), "win_rate": float((pnls > 0).mean() * 100) if len(pnls) else 0,
            "average_gain": float(gains.mean()) if len(gains) else 0,
            "average_loss": float(losses.mean()) if len(losses) else 0,
            "profit_factor": float(gains.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else None,
            "maximum_drawdown_percent": drawdown, "trades": trades, "equity_curve": curve,
            "open_position": open_position,
            "assumptions": {"slippage_percent": slippage_percent, "fee_per_contract": fee_per_contract,
                            "fills": "Simulated against supplied observed historical bid/ask quotes; not broker fills"},
        }
