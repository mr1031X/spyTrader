from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from backend.broker import AccountState, OptionContract
from backend.config import BotSettings, Environment
from backend.database import OpportunityDiagnosticRecord, SessionLocal, WeeklyState
from backend.diagnostics import record_scan
from backend.history import HistoricalBarCache
from backend.indicators import (
    IndicatorSnapshot, closed_candles, compute_indicators, filter_session_candles, resample_candles,
)
from backend.market_data import MarketDataError, MarketDataService, OptionSnapshot, Quote, TradierOptionsProvider
from backend.option_selector import OptionSelector
from backend.risk_manager import week_start
from backend.signal_engine import SignalEngine
from backend.strategies import SignalCandidate, StrategyEngine, classify_trend, classify_volatility, trend_continuation
from backend.trading_bot import ET, TradingBot


def make_bars(timestamps, start_price=100.0, step=0.0, volume=1000):
    prices = start_price + np.arange(len(timestamps)) * step
    return pd.DataFrame({
        "timestamp": pd.to_datetime(timestamps, utc=True), "open": prices,
        "high": prices + 0.08, "low": prices - 0.08, "close": prices,
        "volume": np.full(len(timestamps), volume),
    })


def flat_warmup_bars(now):
    current_day = now.astimezone(ET).date()
    days = []
    cursor = current_day
    while len(days) < 4:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    pieces = []
    for day in reversed(days):
        start = datetime.combine(day, time(9, 30), tzinfo=ET).astimezone(timezone.utc)
        timestamps = [start + timedelta(minutes=index) for index in range(390)]
        timestamps = [timestamp for timestamp in timestamps if timestamp < now]
        pieces.append(make_bars(timestamps))
    return pd.concat(pieces, ignore_index=True)


def test_fresh_startup_scans_both_symbols_before_options_ready(account, daily, session):
    now = datetime.combine(daily.day, time(15, 30), tzinfo=timezone.utc)
    weekly = session.scalar(select(WeeklyState))
    weekly.week_start = week_start(daily.day)
    session.commit()
    bars = flat_warmup_bars(now)

    class History:
        def __init__(self):
            self.symbols = []

        def get(self, symbol, requested_now):
            self.symbols.append(symbol)
            return bars.copy()

    history = History()
    bot = TradingBot(Environment(), SessionLocal)
    bot._broker = object()
    bot._market = SimpleNamespace()
    bot._execution = object()
    bot._history = history
    bot._entries_enabled = True
    bot._reconciled = True
    bot._readiness.broker_connected = True
    bot._readiness.order_execution = True
    bot._clock = {"next_close": datetime.combine(daily.day, time(21), tzinfo=timezone.utc).isoformat()}

    bot._scan_entries(BotSettings(), account, now)

    assert history.symbols == ["SPY", "QQQ"]
    assert bot._readiness.historical == {"SPY": True, "QQQ": True}
    assert not bot._readiness.option_quotes
    diagnostics = session.scalars(select(OpportunityDiagnosticRecord)).all()
    assert {item.underlying for item in diagnostics} == {"SPY", "QQQ"}
    assert all(item.last_scan["final_decision"] == "NO_VALID_SETUP" for item in diagnostics)


def test_session_cache_covers_monday_holiday_and_refreshes_incrementally():
    now = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    calendar = []
    cursor = now.date() - timedelta(days=28)
    while cursor <= now.date():
        if cursor.weekday() < 5 and cursor != date(2026, 11, 11):
            calendar.append({"date": cursor.isoformat(), "open": "09:30", "close": "16:00"})
        cursor += timedelta(days=1)

    class Market:
        def __init__(self):
            self.calls = []

        def stock_bars(self, symbol, timeframe, start, end):
            self.calls.append((start, end))
            return make_bars([start])

    market = Market()
    cache = HistoricalBarCache(market, lambda start, end: calendar, refresh_seconds=45)
    sessions = cache.sessions(now)
    assert [opened.astimezone(ET).date() for opened, _ in sessions] == [
        date(2026, 11, 6), date(2026, 11, 9), date(2026, 11, 10),
        date(2026, 11, 12), date(2026, 11, 13), date(2026, 11, 16),
    ]
    cache.get("SPY", now)
    cache.get("SPY", now + timedelta(seconds=20))
    assert len(market.calls) == 1
    cache.get("SPY", now + timedelta(seconds=60))
    assert len(market.calls) == 2
    assert market.calls[0][0] == sessions[0][0]


def test_regular_session_vwap_and_opening_range_exclude_premarket():
    previous = [datetime(2026, 11, 17, 14, 30, tzinfo=timezone.utc) + timedelta(minutes=i) for i in range(60)]
    premarket = [datetime(2026, 11, 18, 13, 30, tzinfo=timezone.utc) + timedelta(minutes=i) for i in range(60)]
    regular = [datetime(2026, 11, 18, 14, 30, tzinfo=timezone.utc) + timedelta(minutes=i) for i in range(20)]
    frame = pd.concat([
        make_bars(previous, 100), make_bars(premarket, 900), make_bars(regular, 101, 0.01),
    ], ignore_index=True)
    result = compute_indicators(frame, 15)
    assert result.opening_range_high < 102
    assert result.opening_range_low > 100
    assert result.vwap < 102


def test_incomplete_higher_timeframe_candle_is_excluded():
    start = datetime(2026, 11, 18, 14, 30, tzinfo=timezone.utc)
    frame = make_bars([start + timedelta(minutes=i) for i in range(11)])
    result = resample_candles(frame, 5, now=start + timedelta(minutes=10, seconds=30))
    assert list(result["timestamp"]) == [pd.Timestamp(start), pd.Timestamp(start + timedelta(minutes=5))]


def test_early_close_session_filter_uses_actual_calendar_close():
    opened = datetime(2026, 11, 27, 14, 30, tzinfo=timezone.utc)
    closed = datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)
    frame = make_bars([opened + timedelta(minutes=i * 30) for i in range(8)])
    result = filter_session_candles(frame, [(opened, closed)])
    assert len(result) == 7
    assert result["timestamp"].max() < pd.Timestamp(closed)


def indicator(price=100, vwap=101, ema9=98, ema21=99, ema50=100, volatility=2.0):
    return IndicatorSnapshot(
        price=price, vwap=vwap, ema9=ema9, ema21=ema21, ema50=ema50, rsi=30,
        macd=-1, macd_signal=-0.5, macd_histogram=-0.5, atr=1,
        relative_volume=1.5, opening_range_high=102, opening_range_low=99,
        support=97, resistance=103, momentum=-0.01, volatility_ratio=volatility,
    )


def test_high_volatility_bearish_trend_keeps_directional_regime_score():
    snapshot = indicator()
    assert classify_trend(snapshot) == "strong bearish"
    assert classify_volatility(snapshot) == "high"
    candidate = SignalCandidate(
        "SPY", "put", "trend_continuation", "strong bearish",
        {"momentum": 1, "trend": 1, "volume": 1, "volatility": 1}, ["test"], 100, 1,
        trend="strong bearish", volatility="high",
    )
    scored = SignalEngine().score(candidate, BotSettings())
    assert scored.contributions["regime"] == 15
    assert scored.score == 80 and scored.qualified


def test_strategy_rejections_are_structured():
    now = datetime(2026, 11, 18, 16, 0, tzinfo=timezone.utc)
    bars = flat_warmup_bars(now)
    one = closed_candles(bars, 1, now)
    evaluations = StrategyEngine().evaluate_detailed(
        "SPY", one, resample_candles(one, 5, now), resample_candles(one, 15, now),
        ["momentum_breakout", "trend_continuation", "vwap_reversal"], 15,
    )
    assert len(evaluations) == 3
    assert all(item.candidate is None and item.rejected for item in evaluations)
    assert "BREAKOUT_NOT_CONFIRMED" in evaluations[0].rejected


def continuation_frames(direction):
    bullish = direction == "call"
    five_start = datetime(2026, 11, 17, 14, 30, tzinfo=timezone.utc)
    five_prices = np.linspace(100, 110, 80) if bullish else np.linspace(110, 100, 80)
    five = pd.DataFrame({
        "timestamp": [five_start + timedelta(minutes=5 * i) for i in range(80)],
        "open": five_prices, "high": five_prices + 0.15, "low": five_prices - 0.15,
        "close": five_prices + (0.04 if bullish else -0.04), "volume": np.full(80, 1000),
    })
    one_start = datetime(2026, 11, 18, 14, 30, tzinfo=timezone.utc)
    base = np.linspace(109, 109.8, 80) if bullish else np.linspace(100.8, 100, 80)
    wave = np.sin(np.arange(80) / 3) * 0.12
    closes = base + wave
    one = pd.DataFrame({
        "timestamp": [one_start + timedelta(minutes=i) for i in range(80)],
        "open": closes - (0.03 if bullish else -0.03), "high": closes + 0.18,
        "low": closes - 0.18, "close": closes, "volume": np.full(80, 1000),
    })
    previous_high = one["high"].iloc[-2]
    previous_low = one["low"].iloc[-2]
    one.loc[one.index[-1], "close"] = previous_high + 0.05 if bullish else previous_low - 0.05
    one.loc[one.index[-1], "open"] = previous_high - 0.02 if bullish else previous_low + 0.02
    one.loc[one.index[-1], "high"] = one["close"].iloc[-1] + 0.03
    one.loc[one.index[-1], "low"] = one["close"].iloc[-1] - 0.20
    one.loc[one.index[-1], "volume"] = 2500
    return one, five


@pytest.mark.parametrize("direction", ["call", "put"])
def test_directional_continuation_after_opening_range(direction):
    one, five = continuation_frames(direction)
    candidate = trend_continuation("SPY", one, five, 15)
    assert candidate is not None
    assert candidate.direction == direction
    assert candidate.strategy == "trend_continuation"


def option_candidate():
    return SignalCandidate(
        "SPY", "call", "momentum_breakout", "strong bullish",
        {"momentum": 1, "trend": 1, "volume": 1, "volatility": 1}, ["test"], 600, 2,
    )


def test_contract_diagnostics_allow_missing_optional_liquidity_fields():
    contract = OptionContract(
        "SPY261121C00600000", "SPY", "call", 600, date.today() + timedelta(days=2),
    )
    snapshot = OptionSnapshot(
        contract, Quote(contract.symbol, 2, 2.04, datetime.now(timezone.utc), "test"),
        0.55, 0.04, -0.1, 0.3, None, None,
    )
    selector = OptionSelector(
        SimpleNamespace(get_contracts=lambda *args: [contract]),
        SimpleNamespace(option_snapshots=lambda contracts: [snapshot]),
    )
    result = selector.select_detailed(option_candidate(), BotSettings(), 10_000)
    assert result.selected is not None
    assert result.stats["eligible"] == 1 and result.stats["valid_quotes"] == 1
    assert result.stats["within_delta"] == 1 and result.stats["affordable"] == 1


def test_contract_diagnostics_report_stale_quote_and_delta_rejection():
    contract = OptionContract(
        "SPY261121C00600000", "SPY", "call", 600, date.today() + timedelta(days=2),
    )
    stale = OptionSnapshot(
        contract, Quote(contract.symbol, 2, 2.04, datetime.now(timezone.utc) - timedelta(seconds=30), "test"),
        0.55, None, None, None, 1000, 100,
    )
    selector = OptionSelector(
        SimpleNamespace(get_contracts=lambda *args: [contract]),
        SimpleNamespace(option_snapshots=lambda contracts: [stale]),
    )
    result = selector.select_detailed(option_candidate(), BotSettings(), 10_000)
    assert result.selected is None
    assert "STALE_OPTION_QUOTE" in result.rejection_codes


def test_tradier_uses_provider_bid_ask_timestamps(monkeypatch):
    provider_time = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=2)
    payload = {"quotes": {"quote": {
        "bid": 2, "ask": 2.1, "biddate": int(provider_time.timestamp() * 1000),
        "askdate": int((provider_time + timedelta(seconds=1)).timestamp() * 1000),
        "greeks": {"delta": 0.55}, "open_interest": 100, "volume": 20,
    }}}
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
    monkeypatch.setattr("backend.market_data.httpx.get", lambda *args, **kwargs: response)
    provider = TradierOptionsProvider(Environment(tradier_api_key="test"))
    contract = OptionContract("SPY261121C00600000", "SPY", "call", 600, date(2026, 11, 21))
    snapshot = provider.option_snapshot(contract)
    assert snapshot.quote.timestamp == provider_time
    assert snapshot.quote.received_at > snapshot.quote.timestamp

    payload["quotes"]["quote"]["biddate"] = 0
    with pytest.raises(MarketDataError, match="timestamps"):
        provider.option_snapshot(contract)


def test_tradier_provider_does_not_initialize_alpaca_opra(monkeypatch):
    monkeypatch.setattr("backend.market_data.StockHistoricalDataClient", lambda **kwargs: object())
    def forbidden(**kwargs):
        raise AssertionError("OPRA client should not be initialized for Tradier")
    monkeypatch.setattr("backend.market_data.OptionHistoricalDataClient", forbidden)
    env = Environment(
        alpaca_paper_api_key="paper", alpaca_paper_secret_key="secret",
        alpaca_options_feed="indicative", tradier_api_key="test",
    )
    service = MarketDataService(BotSettings(options_provider="tradier"), env)
    assert service.alpaca.options is None
    assert service.options_provider.name == "tradier_production"


def test_diagnostic_recent_scan_history_is_bounded(session):
    now = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)
    for index in range(30):
        record_scan(session, "paper", "SPY", now, {"final_decision": "NO_VALID_SETUP", "index": index})
    item = session.scalar(select(OpportunityDiagnosticRecord))
    assert item.scans == 30
    assert len(item.recent_scans) == 25
    assert item.recent_scans[-1]["index"] == 29
