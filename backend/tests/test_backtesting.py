import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.backtesting import Backtester
from backend.config import BotSettings
from backend.main import create_app


def synthetic_replay_inputs():
    """Deterministic accounting fixture; these are not real market observations."""
    rows = []
    price = 100.0
    for day in (datetime(2026, 1, 5), datetime(2026, 1, 6), datetime(2026, 1, 7)):
        start = datetime(day.year, day.month, day.day, 14, 30, tzinfo=timezone.utc)
        count = 64 if day.day == 7 else 390
        base = price
        for index in range(count):
            timestamp = start + timedelta(minutes=index)
            if day.day < 7:
                open_ = price
                close = price + 0.03
            elif index < 60:
                open_ = close = base
            else:
                open_ = base if index == 60 else price
                close = open_ + 0.06
            rows.append({
                "timestamp": timestamp.isoformat(), "symbol": "SPY", "open": open_,
                "high": max(open_, close) + 0.02, "low": min(open_, close) - 0.02,
                "close": close, "volume": 3000 if day.day == 7 and index == 60 else 1000,
            })
            price = close
    underlying = pd.DataFrame(rows)
    underlying["timestamp"] = pd.to_datetime(underlying["timestamp"], utc=True)
    entry_time = pd.Timestamp("2026-01-07T15:31:00Z")
    quotes = []
    for timestamp, bid, ask in (
        (entry_time, 2.00, 2.00),
        (entry_time + timedelta(minutes=1), 2.50, 2.52),
        (entry_time + timedelta(minutes=2), 2.20, 2.22),
    ):
        quotes.append({
            "timestamp": timestamp, "symbol": "SPY260109C00100000", "underlying": "SPY",
            "option_type": "call", "strike": price, "expiration": "2026-01-09", "delta": 0.55,
            "bid": bid, "ask": ask, "open_interest": 1000, "volume": 100,
        })
    return underlying, pd.DataFrame(quotes)


def test_replay_uses_calendar_dte_and_charges_each_fee_once():
    underlying, options = synthetic_replay_inputs()
    result = Backtester().run(
        underlying, options, BotSettings(), initial_equity=10_000,
        slippage_percent=0, fee_per_contract=0.65,
    )
    assert result["trade_count"] == 1
    trade = result["trades"][0]
    assert trade["strategy"] == "momentum_breakout"
    assert trade["exit_reason"] == "trailing_stop"
    assert trade["qty"] == 2
    assert trade["entry_price"] == 2.0
    assert trade["exit_price"] == 2.2
    assert trade["realized_pnl"] == pytest.approx(37.40)
    assert result["net_realized_pnl"] == pytest.approx(37.40)
    assert result["ending_equity_before_open_mark_value"] == pytest.approx(10_037.40)
    assert result["equity_curve"][-1] == pytest.approx(10_037.40)
    assert result["open_position"] is None


def test_replay_does_not_manufacture_a_terminal_fill():
    underlying, options = synthetic_replay_inputs()
    result = Backtester().run(
        underlying, options.iloc[:2], BotSettings(), initial_equity=10_000,
        slippage_percent=0, fee_per_contract=0.65,
    )
    assert result["trade_count"] == 0
    assert result["net_realized_pnl"] == 0
    assert result["open_position"]["qty"] == 2
    assert result["ending_equity_before_open_mark_value"] == pytest.approx(9_598.70)


def test_backtest_api_accepts_aligned_quote_csv_without_real_market_claims():
    underlying, options = synthetic_replay_inputs()
    api = TestClient(create_app())
    response = api.post(
        "/api/backtests",
        files={
            "underlying_file": ("underlying.csv", underlying.to_csv(index=False).encode(), "text/csv"),
            "options_file": ("options.csv", options.to_csv(index=False).encode(), "text/csv"),
        },
        data={"parameters": json.dumps({
            "name": "Synthetic replay accounting test", "initial_equity": 10_000,
            "slippage_percent": 0, "fee_per_contract": 0.65,
        })},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["trade_count"] == 1
    assert result["net_realized_pnl"] == pytest.approx(37.40)
    assert result["assumptions"]["fills"].endswith("not broker fills")
    stored = api.get("/api/backtests").json()
    assert stored[0]["name"] == "Synthetic replay accounting test"
    assert stored[0]["results"]["net_realized_pnl"] == pytest.approx(37.40)
