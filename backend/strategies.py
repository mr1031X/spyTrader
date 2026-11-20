"""Three deterministic intraday strategies and market-regime classification."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from .indicators import IndicatorSnapshot, compute_indicators

Direction = Literal["call", "put"]


@dataclass(frozen=True)
class SignalCandidate:
    underlying: str
    direction: Direction
    strategy: str
    regime: str
    factors: dict[str, float]
    explanation: list[str]
    reference_price: float
    atr: float
    stronger_confirmation_required: bool = False


def classify_regime(indicators: IndicatorSnapshot, news_unstable: bool = False) -> str:
    if news_unstable:
        return "unstable/news-driven"
    atr_percent = indicators.atr / indicators.price * 100
    if atr_percent > 0.8 or indicators.volatility_ratio > 1.8:
        return "high volatility"
    if indicators.volatility_ratio < 0.55:
        return "low volatility"
    if indicators.price > indicators.vwap and indicators.ema21 > indicators.ema50:
        return "bullish trending"
    if indicators.price < indicators.vwap and indicators.ema21 < indicators.ema50:
        return "bearish trending"
    return "sideways"


def _momentum(indicators: IndicatorSnapshot, direction: Direction) -> float:
    signed = indicators.momentum if direction == "call" else -indicators.momentum
    hist = indicators.macd_histogram if direction == "call" else -indicators.macd_histogram
    return max(0.0, min(1.0, 0.5 + signed * 70 + (0.2 if hist > 0 else -0.2)))


def _trend(indicators: IndicatorSnapshot, direction: Direction) -> float:
    score = 0.0
    if direction == "call":
        score += 0.35 if indicators.price > indicators.vwap else 0
        score += 0.35 if indicators.ema9 > indicators.ema21 > indicators.ema50 else 0
        score += 0.30 if indicators.macd > indicators.macd_signal else 0
    else:
        score += 0.35 if indicators.price < indicators.vwap else 0
        score += 0.35 if indicators.ema9 < indicators.ema21 < indicators.ema50 else 0
        score += 0.30 if indicators.macd < indicators.macd_signal else 0
    return score


def momentum_breakout(underlying: str, one: pd.DataFrame, five: pd.DataFrame, opening_minutes: int) -> SignalCandidate | None:
    if len(one) < 55 or len(five) < 55:
        return None
    i1 = compute_indicators(one, opening_minutes)
    i5 = compute_indicators(five, opening_minutes)
    previous = one.iloc[-2]
    current = one.iloc[-1]
    direction: Direction | None = None
    reasons: list[str] = []
    if current["close"] > i1.opening_range_high and previous["close"] <= i1.opening_range_high:
        direction = "call"
        reasons.append("Closed above the opening-range high")
    elif current["close"] < i1.opening_range_low and previous["close"] >= i1.opening_range_low:
        direction = "put"
        reasons.append("Closed below the opening-range low")
    if direction is None or i1.relative_volume < 1.2:
        return None
    aligned = (i5.ema9 > i5.ema21 > i5.ema50 and i1.price > i1.vwap) if direction == "call" else (
        i5.ema9 < i5.ema21 < i5.ema50 and i1.price < i1.vwap)
    if not aligned:
        return None
    extension = abs(i1.price - i1.vwap) / i1.atr
    if extension > 2.5:
        return None
    confirmation = (current["close"] > current["open"] and previous["close"] >= previous["open"]) if direction == "call" else (
        current["close"] < current["open"] and previous["close"] <= previous["open"])
    if not confirmation:
        return None
    reasons.extend(["EMA stack and VWAP confirm direction", f"Relative volume {i1.relative_volume:.2f}×", "Entry is not overextended"])
    return SignalCandidate(
        underlying, direction, "momentum_breakout", classify_regime(i5),
        {"momentum": _momentum(i1, direction), "trend": _trend(i5, direction),
         "volume": min(i1.relative_volume / 2.5, 1.0), "volatility": max(0, 1 - abs(i1.volatility_ratio - 1.2) / 1.8)},
        reasons, i1.price, i1.atr,
    )


def trend_continuation(underlying: str, one: pd.DataFrame, five: pd.DataFrame, opening_minutes: int) -> SignalCandidate | None:
    if len(one) < 55 or len(five) < 55:
        return None
    i1 = compute_indicators(one, opening_minutes)
    i5 = compute_indicators(five, opening_minutes)
    recent = five.iloc[-6:-1]
    direction: Direction | None = None
    if i5.ema9 > i5.ema21 > i5.ema50 and i5.price > i5.vwap:
        direction = "call"
        structure = recent["high"].iloc[-1] > recent["high"].iloc[0] and recent["low"].iloc[-1] > recent["low"].iloc[0]
        renewed = one["close"].iloc[-1] > one["high"].iloc[-2] and i1.macd_histogram > 0
    elif i5.ema9 < i5.ema21 < i5.ema50 and i5.price < i5.vwap:
        direction = "put"
        structure = recent["high"].iloc[-1] < recent["high"].iloc[0] and recent["low"].iloc[-1] < recent["low"].iloc[0]
        renewed = one["close"].iloc[-1] < one["low"].iloc[-2] and i1.macd_histogram < 0
    else:
        return None
    support = i1.ema9 if direction == "call" else i1.ema9
    pullback = abs(i1.price - support) <= i1.atr * 0.8 or abs(i1.price - i1.vwap) <= i1.atr * 0.6
    if not (structure and pullback and renewed and i1.relative_volume >= 0.8):
        return None
    return SignalCandidate(
        underlying, direction, "trend_continuation", classify_regime(i5),
        {"momentum": _momentum(i1, direction), "trend": _trend(i5, direction),
         "volume": min(i1.relative_volume / 2.0, 1.0), "volatility": max(0, 1 - abs(i1.volatility_ratio - 1.0) / 2)},
        ["Established EMA trend and directional price structure", "Pullback held near dynamic support", "Renewed momentum confirmed on a closed candle"],
        i1.price, i1.atr,
    )


def vwap_reversal(underlying: str, one: pd.DataFrame, five: pd.DataFrame, opening_minutes: int) -> SignalCandidate | None:
    if len(one) < 60 or len(five) < 55:
        return None
    i1 = compute_indicators(one, opening_minutes)
    i5 = compute_indicators(five, opening_minutes)
    previous_vwap = session_vwap_at(one, -2)
    previous_close = float(one["close"].iloc[-2])
    direction: Direction | None = None
    if previous_close < previous_vwap and i1.price > i1.vwap:
        direction = "call"
        momentum_changed = i1.macd_histogram > 0 and i1.rsi > 50
    elif previous_close > previous_vwap and i1.price < i1.vwap:
        direction = "put"
        momentum_changed = i1.macd_histogram < 0 and i1.rsi < 50
    else:
        return None
    failed_breakout = abs(previous_close - previous_vwap) <= i1.atr * 0.5
    if not (momentum_changed and failed_breakout and i1.relative_volume >= 1.3):
        return None
    return SignalCandidate(
        underlying, direction, "vwap_reversal", classify_regime(i5),
        {"momentum": _momentum(i1, direction), "trend": _trend(i5, direction),
         "volume": min(i1.relative_volume / 2.5, 1.0), "volatility": max(0, 1 - abs(i1.volatility_ratio - 1.3) / 1.7)},
        ["VWAP reclaim/rejection confirmed at candle close", "Prior break failed near VWAP", "Momentum and volume confirm reversal"],
        i1.price, i1.atr, stronger_confirmation_required=True,
    )


def session_vwap_at(frame: pd.DataFrame, index: int) -> float:
    from .indicators import session_vwap
    return float(session_vwap(frame).iloc[index])


class StrategyEngine:
    STRATEGIES = {
        "momentum_breakout": momentum_breakout,
        "trend_continuation": trend_continuation,
        "vwap_reversal": vwap_reversal,
    }

    def evaluate(self, underlying: str, one: pd.DataFrame, five: pd.DataFrame, fifteen: pd.DataFrame,
                 enabled: list[str], opening_minutes: int = 15, news_unstable: bool = False) -> list[SignalCandidate]:
        candidates = []
        for name in enabled:
            function = self.STRATEGIES.get(name)
            if not function:
                continue
            candidate = function(underlying, one, five, opening_minutes)
            if candidate is None:
                continue
            regime = classify_regime(compute_indicators(fifteen, opening_minutes), news_unstable)
            compatible = not (
                regime == "unstable/news-driven" or
                (name == "trend_continuation" and regime == "sideways") or
                (name == "momentum_breakout" and regime == "low volatility")
            )
            if compatible:
                candidates.append(SignalCandidate(**{**candidate.__dict__, "regime": regime}))
        return candidates
