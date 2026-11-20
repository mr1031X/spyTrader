from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.live import OptionDataStream, StockDataStream

from backend.broker import AlpacaBroker, OptionContract
from backend.config import BotSettings, ScoreWeights
from backend.indicators import closed_candles, compute_indicators
from backend.market_data import AlpacaMarketData, OptionSnapshot, Quote, QuoteCache
from backend.option_selector import OptionSelector
from backend.signal_engine import SignalEngine, allocation_for_score
from backend.strategies import SignalCandidate, classify_regime
from backend.indicators import IndicatorSnapshot


def test_stale_quote_rejected():
    quote = Quote("SPY", 100, 100.1, datetime.now(timezone.utc) - timedelta(seconds=5), "test")
    assert "stale" in quote.validation_error(max_age_seconds=3)


def test_crossed_zero_and_wide_quotes_rejected():
    now = datetime.now(timezone.utc)
    assert "positive" in Quote("SPY", 0, 1, now, "test").validation_error()
    assert "Crossed" in Quote("SPY", 2, 1, now, "test").validation_error()
    assert "Spread" in Quote("SPY", 1, 2, now, "test").validation_error(max_spread_percent=8)


def test_valid_quote_midpoint():
    quote = Quote("SPY", 100, 100.2, datetime.now(timezone.utc), "test")
    assert quote.validation_error() is None
    assert quote.midpoint == pytest.approx(100.1)


@pytest.mark.asyncio
async def test_alpaca_streams_accept_quote_cache_handler():
    cache = QuoteCache()
    stock_stream = StockDataStream("test-key", "test-secret", feed=DataFeed.IEX)
    option_stream = OptionDataStream("test-key", "test-secret", feed=OptionsFeed.OPRA)

    stock_stream.subscribe_quotes(cache.update, "SPY")
    option_stream.subscribe_quotes(cache.update, "SPY261119C00600000")

    now = datetime.now(timezone.utc)
    await cache.update(SimpleNamespace(
        symbol="SPY", bid_price=100, ask_price=100.2, timestamp=now, bid_size=5, ask_size=6,
    ))
    quote = cache.get("SPY")
    assert quote is not None
    assert quote.ask == pytest.approx(100.2)
    assert cache.connected


def test_contract_discovery_preserves_real_open_interest():
    broker = object.__new__(AlpacaBroker)
    item = SimpleNamespace(
        symbol="SPY261119C00600000", strike_price="600", expiration_date=date(2026, 11, 19),
        multiplier="100", tradable=True, open_interest="1234",
    )
    broker.client = SimpleNamespace(
        get_option_contracts=lambda request: SimpleNamespace(option_contracts=[item])
    )
    contracts = broker.get_contracts("SPY", "call", 1, 3, today=date(2026, 11, 18))
    assert len(contracts) == 1
    assert contracts[0].open_interest == 1234


def test_option_snapshot_supports_sdk_without_daily_bar():
    now = datetime.now(timezone.utc)
    contract = OptionContract(
        symbol="SPY261119C00600000", underlying="SPY", option_type="call", strike=600,
        expiration=date(2026, 11, 19), open_interest=456,
    )
    item = SimpleNamespace(
        latest_quote=SimpleNamespace(
            bid_price=2.0, ask_price=2.1, timestamp=now, bid_size=5, ask_size=6,
        ),
        greeks=SimpleNamespace(delta=0.55, gamma=0.04, theta=-0.1),
        implied_volatility=0.3,
    )
    provider = object.__new__(AlpacaMarketData)
    provider._snapshots = lambda symbols: {contract.symbol: item}
    provider._daily_volumes = lambda symbols: {contract.symbol: 321}
    snapshot = provider.option_snapshot(contract)
    assert snapshot.quote.bid == 2.0
    assert snapshot.delta == pytest.approx(0.55)
    assert snapshot.open_interest == 456
    assert snapshot.volume == 321


def test_incomplete_candle_excluded():
    now = datetime.now(timezone.utc)
    frame = pd.DataFrame({"timestamp": [now - timedelta(minutes=2), now],
                          "open": [1, 2], "high": [2, 3], "low": [0.5, 1], "close": [1.5, 2], "volume": [10, 20]})
    assert len(closed_candles(frame, 1, now)) == 1


def test_indicator_snapshot_from_closed_candles():
    count = 80
    start = datetime(2026, 11, 18, 14, 30, tzinfo=timezone.utc)
    now = start + timedelta(minutes=count + 1)
    prices = np.linspace(100, 110, count)
    frame = pd.DataFrame({"timestamp": [start + timedelta(minutes=i) for i in range(count)],
                          "open": prices, "high": prices + 1, "low": prices - 1,
                          "close": prices + 0.2, "volume": np.linspace(1000, 3000, count)})
    result = compute_indicators(closed_candles(frame, now=now))
    assert result.ema9 > result.ema21 > result.ema50
    assert result.atr > 0 and result.relative_volume > 0


def candidate():
    return SignalCandidate("SPY", "call", "momentum_breakout", "bullish trending",
                           {"momentum": 1, "trend": 1, "volume": 1, "volatility": 1}, ["test"], 600, 2)


def test_option_selection_uses_equity_for_affordability():
    now = datetime.now(timezone.utc)
    contract = OptionContract(
        symbol="SPY261119C00600000", underlying="SPY", option_type="call", strike=600,
        expiration=date.today() + timedelta(days=2), open_interest=1000,
    )
    snapshot = OptionSnapshot(
        contract=contract,
        quote=Quote(contract.symbol, 2.0, 2.04, now, "test", 10, 10),
        delta=0.55, gamma=0.04, theta=-0.1, implied_volatility=0.3, open_interest=1000, volume=500,
    )
    broker = SimpleNamespace(get_contracts=lambda *args: [contract])
    market = SimpleNamespace(option_snapshots=lambda contracts: [snapshot])
    selector = OptionSelector(broker, market)

    selected = selector.select(candidate(), BotSettings(), 10_000)

    assert selected is not None
    assert selected.snapshot == snapshot
    assert selector.select(candidate(), BotSettings(), 100) is None


def test_signal_score_and_qualification():
    settings = BotSettings()
    result = SignalEngine().score(candidate(), settings, 1, 1)
    assert result.score == 100 and result.qualified
    assert sum(result.contributions.values()) == pytest.approx(100)


def test_low_score_has_explicit_rejection():
    settings = BotSettings()
    result = SignalEngine().score(candidate(), settings, 0, 0)
    assert result.score == 80 and result.qualified  # technical factors alone reach threshold
    settings.score_weights = ScoreWeights(momentum=0, trend=20, volume=15, regime=15,
                                          volatility=10, liquidity=20, reward_risk=20)
    result = SignalEngine().score(candidate(), settings, 0, 0)
    assert result.score == 60 and not result.qualified
    assert "threshold" in result.rejection_reason


def test_allocation_tiers():
    settings = BotSettings()
    assert allocation_for_score(79.9, settings) == 0
    assert allocation_for_score(80, settings) == 5
    assert allocation_for_score(90, settings) == 10
    assert allocation_for_score(95, settings) == 15


def test_invalid_weights_rejected():
    with pytest.raises(ValueError):
        ScoreWeights(momentum=90)
