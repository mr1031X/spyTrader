from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from backend.config import BotSettings
from backend.indicators import IndicatorSnapshot
from backend.strategies import (
    StrategyEngine, _breakout_detail, _confirmed_continuation_detail,
)


def snapshot(*, price, vwap, ema9, ema21, ema50, macd_histogram=0.5, relative_volume=1.5):
    return IndicatorSnapshot(
        price=price, vwap=vwap, ema9=ema9, ema21=ema21, ema50=ema50,
        rsi=60 if macd_histogram > 0 else 40, macd=1 if macd_histogram > 0 else -1,
        macd_signal=0.5 if macd_histogram > 0 else -0.5, macd_histogram=macd_histogram,
        atr=1, relative_volume=relative_volume, opening_range_high=101.5, opening_range_low=99,
        support=97, resistance=103, momentum=0.01 if macd_histogram > 0 else -0.01,
        volatility_ratio=1.2,
    )


def test_experiment_flags_default_disabled_and_new_strategy_is_opt_in():
    settings = BotSettings()
    assert not settings.experiment_breakout_3_atr_enabled
    assert not settings.experiment_confirmed_continuation_enabled
    empty = pd.DataFrame()
    engine = StrategyEngine()
    assert engine.evaluate_detailed("SPY", empty, empty, empty, []) == []
    evaluations = engine.evaluate_detailed(
        "SPY", empty, empty, empty, [], experiment_confirmed_continuation=True,
    )
    assert [item.strategy for item in evaluations] == ["confirmed_trend_continuation"]
    assert evaluations[0].rejected == ["INSUFFICIENT_HISTORICAL_DATA"]


def test_breakout_experiment_changes_only_extension_ceiling(monkeypatch):
    start = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)
    one = pd.DataFrame({
        "timestamp": [start, start + timedelta(minutes=1)],
        "open": [101.3, 102.5], "high": [101.5, 102.8], "low": [101.2, 102.4],
        "close": [101.4, 102.7], "volume": [1000, 1500],
    })
    five = one.copy()
    i1 = snapshot(price=102.7, vwap=100, ema9=102, ema21=101.5, ema50=101)
    i5 = snapshot(price=102.6, vwap=100, ema9=102, ema21=101.5, ema50=101)
    monkeypatch.setattr("backend.strategies.compute_indicators",
                        lambda frame, opening_minutes: i1 if frame is one else i5)

    baseline = _breakout_detail("SPY", one, five, 15)
    experiment = _breakout_detail("SPY", one, five, 15, extension_limit=3.0)

    assert baseline.candidate is None
    assert baseline.rejected == ["OVEREXTENDED_PRICE"]
    assert experiment.candidate is not None
    assert experiment.candidate.strategy == "momentum_breakout"


def continuation_inputs(direction):
    bullish = direction == "call"
    start = datetime(2026, 11, 20, 14, 30, tzinfo=timezone.utc)
    boundary = 105 if bullish else 95
    five = pd.DataFrame({
        "timestamp": [start + timedelta(minutes=5 * index) for index in range(6)],
        "open": [104.7 if bullish else 95.3] * 6,
        "high": [boundary] * 6,
        "low": [boundary - 0.5 if bullish else boundary] * 6,
        "close": [104.8 if bullish else 95.2] * 6,
        "volume": [1000] * 6,
    })
    previous_close = 104.9 if bullish else 95.1
    current_close = 105.2 if bullish else 94.8
    one = pd.DataFrame({
        "timestamp": [start + timedelta(minutes=34), start + timedelta(minutes=35)],
        "open": [previous_close - 0.05 if bullish else previous_close + 0.05,
                 current_close - 0.1 if bullish else current_close + 0.1],
        "high": [previous_close + 0.1, current_close + 0.1],
        "low": [previous_close - 0.1, current_close - 0.1],
        "close": [previous_close, current_close], "volume": [1000, 1500],
    })
    i1 = snapshot(
        price=current_close, vwap=104 if bullish else 96,
        ema9=105 if bullish else 95, ema21=104.5 if bullish else 95.5,
        ema50=104 if bullish else 96, macd_histogram=0.5 if bullish else -0.5,
    )
    i5 = snapshot(
        price=105 if bullish else 95, vwap=104 if bullish else 96,
        ema9=105 if bullish else 95, ema21=104.5 if bullish else 95.5,
        ema50=104 if bullish else 96, macd_histogram=0.5 if bullish else -0.5,
    )
    return one, five, i1, i5


@pytest.mark.parametrize("direction", ["call", "put"])
def test_confirmed_continuation_uses_completed_consolidation_and_fresh_trigger(monkeypatch, direction):
    one, five, i1, i5 = continuation_inputs(direction)
    monkeypatch.setattr("backend.strategies.compute_indicators",
                        lambda frame, opening_minutes: i1 if frame is one else i5)
    evaluation = _confirmed_continuation_detail("SPY", one, five, 15)
    assert evaluation.candidate is not None
    assert evaluation.candidate.direction == direction
    assert evaluation.candidate.strategy == "confirmed_trend_continuation"
    assert all(evaluation.conditions.values())

    changed_latest = five.copy()
    changed_latest.loc[changed_latest.index[-1], ["high", "low", "close"]] = [1000, 0, 500]
    repeated = _confirmed_continuation_detail("SPY", one, changed_latest, 15)
    assert repeated.candidate is not None


@pytest.mark.parametrize("direction", ["call", "put"])
def test_confirmed_continuation_rejects_already_triggered_entry(monkeypatch, direction):
    one, five, i1, i5 = continuation_inputs(direction)
    one.loc[one.index[-2], "close"] = 105.1 if direction == "call" else 94.9
    monkeypatch.setattr("backend.strategies.compute_indicators",
                        lambda frame, opening_minutes: i1 if frame is one else i5)
    evaluation = _confirmed_continuation_detail("SPY", one, five, 15)
    assert evaluation.candidate is None
    assert "CONTINUATION_NOT_CONFIRMED" in evaluation.rejected
