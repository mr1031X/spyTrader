"""Explainable intraday breakout, continuation, and VWAP-reversal strategies."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from .indicators import IndicatorSnapshot, compute_indicators, filter_regular_session

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
    trend: str = ""
    volatility: str = "normal"
    event_key: str | None = None


@dataclass
class StrategyEvaluation:
    strategy: str
    conditions: dict[str, bool] = field(default_factory=dict)
    rejected: list[str] = field(default_factory=list)
    candidate: SignalCandidate | None = None
    direction: Direction | None = None
    trend: str = "sideways"
    volatility: str = "normal"

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "conditions": self.conditions,
            "rejected": self.rejected,
            "direction": self.direction,
            "trend": self.trend,
            "volatility": self.volatility,
            "candidate": bool(self.candidate),
            "event_key": self.candidate.event_key if self.candidate else None,
        }


def classify_trend(indicators: IndicatorSnapshot) -> str:
    if indicators.price > indicators.vwap and indicators.ema21 > indicators.ema50:
        strong = indicators.ema9 > indicators.ema21 and indicators.macd > indicators.macd_signal
        return "strong bullish" if strong else "moderate bullish"
    if indicators.price < indicators.vwap and indicators.ema21 < indicators.ema50:
        strong = indicators.ema9 < indicators.ema21 and indicators.macd < indicators.macd_signal
        return "strong bearish" if strong else "moderate bearish"
    return "sideways"


def classify_volatility(indicators: IndicatorSnapshot, news_unstable: bool = False) -> str:
    atr_percent = indicators.atr / indicators.price * 100 if indicators.price else 0
    if news_unstable or atr_percent > 1.6 or indicators.volatility_ratio > 3.0:
        return "extreme"
    if atr_percent > 0.8 or indicators.volatility_ratio > 1.8:
        return "high"
    if indicators.volatility_ratio < 0.55:
        return "low"
    return "normal"


def classify_regime(indicators: IndicatorSnapshot, news_unstable: bool = False) -> str:
    if news_unstable:
        return "unstable/news-driven"
    return classify_trend(indicators)


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


def _volatility_score(indicators: IndicatorSnapshot, target: float) -> float:
    return max(0.0, 1 - abs(indicators.volatility_ratio - target) / 2.0)


def _event_key(frame: pd.DataFrame, setup: str) -> str:
    return f"{setup}:{pd.Timestamp(frame['timestamp'].iloc[-1]).isoformat()}"


def _breakout_detail(underlying: str, one: pd.DataFrame, five: pd.DataFrame,
                     opening_minutes: int) -> StrategyEvaluation:
    evaluation = StrategyEvaluation("momentum_breakout")
    i1 = compute_indicators(one, opening_minutes)
    i5 = compute_indicators(five, opening_minutes)
    evaluation.trend = classify_trend(i5)
    evaluation.volatility = classify_volatility(i5)
    previous = one.iloc[-2]
    current = one.iloc[-1]
    direction: Direction | None = None
    if current["close"] > i1.opening_range_high and previous["close"] <= i1.opening_range_high:
        direction = "call"
    elif current["close"] < i1.opening_range_low and previous["close"] >= i1.opening_range_low:
        direction = "put"
    evaluation.direction = direction
    evaluation.conditions["fresh_opening_range_cross"] = direction is not None
    evaluation.conditions["relative_volume"] = i1.relative_volume >= 1.2
    if direction is None:
        evaluation.rejected.append("BREAKOUT_NOT_CONFIRMED")
    if not evaluation.conditions["relative_volume"]:
        evaluation.rejected.append("INSUFFICIENT_VOLUME")
    if direction:
        ema_aligned = i5.ema9 > i5.ema21 > i5.ema50 if direction == "call" else i5.ema9 < i5.ema21 < i5.ema50
        vwap_aligned = i1.price > i1.vwap if direction == "call" else i1.price < i1.vwap
        extension = abs(i1.price - i1.vwap) / i1.atr
        confirmation = (current["close"] > current["open"] and previous["close"] >= previous["open"]) if direction == "call" else (
            current["close"] < current["open"] and previous["close"] <= previous["open"])
        evaluation.conditions.update({
            "ema_alignment": ema_aligned, "vwap_alignment": vwap_aligned,
            "not_overextended": extension <= 2.5, "candle_confirmation": confirmation,
            "atr_expansion": i1.volatility_ratio >= 0.75,
        })
        for condition, code in (
            ("ema_alignment", "EMA_MISALIGNMENT"), ("vwap_alignment", "VWAP_MISALIGNMENT"),
            ("not_overextended", "OVEREXTENDED_PRICE"), ("candle_confirmation", "BREAKOUT_NOT_CONFIRMED"),
            ("atr_expansion", "INSUFFICIENT_ATR_EXPANSION"),
        ):
            if not evaluation.conditions[condition]:
                evaluation.rejected.append(code)
    if evaluation.rejected or direction is None:
        return evaluation
    reasons = ["Closed through the opening-range boundary", "EMA stack and VWAP confirm direction",
               f"Relative volume {i1.relative_volume:.2f}×", "Closed-candle momentum and ATR confirm without overextension"]
    evaluation.candidate = SignalCandidate(
        underlying, direction, "momentum_breakout", evaluation.trend,
        {"momentum": _momentum(i1, direction), "trend": _trend(i5, direction),
         "volume": min(i1.relative_volume / 2.5, 1.0), "volatility": _volatility_score(i1, 1.2)},
        reasons, i1.price, i1.atr, trend=evaluation.trend, volatility=evaluation.volatility,
        event_key=_event_key(one, "opening-range"),
    )
    return evaluation


def momentum_breakout(underlying: str, one: pd.DataFrame, five: pd.DataFrame,
                      opening_minutes: int) -> SignalCandidate | None:
    return _breakout_detail(underlying, one, five, opening_minutes).candidate


def _continuation_detail(underlying: str, one: pd.DataFrame, five: pd.DataFrame,
                         opening_minutes: int) -> StrategyEvaluation:
    evaluation = StrategyEvaluation("trend_continuation")
    i1 = compute_indicators(one, opening_minutes)
    i5 = compute_indicators(five, opening_minutes)
    evaluation.trend = classify_trend(i5)
    evaluation.volatility = classify_volatility(i5)
    direction: Direction | None = None
    if i5.ema9 > i5.ema21 > i5.ema50:
        direction = "call"
    elif i5.ema9 < i5.ema21 < i5.ema50:
        direction = "put"
    evaluation.direction = direction
    evaluation.conditions["ema_alignment"] = direction is not None
    if direction is None:
        evaluation.rejected.extend(["EMA_MISALIGNMENT", "REGIME_MISMATCH"])
        return evaluation
    recent = five.iloc[-6:-1]
    current = one.iloc[-1]
    previous = one.iloc[-2]
    bullish = direction == "call"
    vwap_aligned = i1.price > i1.vwap if bullish else i1.price < i1.vwap
    structure = ((recent["high"].iloc[-1] > recent["high"].iloc[0] and recent["low"].iloc[-1] > recent["low"].iloc[0])
                 if bullish else
                 (recent["high"].iloc[-1] < recent["high"].iloc[0] and recent["low"].iloc[-1] < recent["low"].iloc[0]))
    renewed = ((current["close"] > previous["high"] and i1.macd_histogram > 0) if bullish else
               (current["close"] < previous["low"] and i1.macd_histogram < 0))
    dynamic_support = abs(i1.price - i1.ema9) <= i1.atr * 0.8 or abs(i1.price - i1.vwap) <= i1.atr * 0.6
    beyond_opening = ((current["close"] > i1.opening_range_high and recent["high"].max() > i1.opening_range_high)
                      if bullish else
                      (current["close"] < i1.opening_range_low and recent["low"].min() < i1.opening_range_low))
    retest = ((previous["low"] <= i1.opening_range_high + i1.atr * 0.3 and current["close"] > i1.opening_range_high)
              if bullish else
              (previous["high"] >= i1.opening_range_low - i1.atr * 0.3 and current["close"] < i1.opening_range_low))
    consolidation_range = float(recent["high"].iloc[-4:].max() - recent["low"].iloc[-4:].min())
    consolidation_break = consolidation_range <= i5.atr * 1.8 and (
        current["close"] > recent["high"].iloc[-4:].max() if bullish else current["close"] < recent["low"].iloc[-4:].min()
    )
    structure_break = current["close"] > recent["high"].max() if bullish else current["close"] < recent["low"].min()
    extension = abs(i1.price - i1.vwap) / i1.atr
    evaluation.conditions.update({
        "vwap_alignment": vwap_aligned, "directional_structure": structure,
        "renewed_momentum": renewed, "pullback_or_dynamic_support": dynamic_support,
        "confirmed_breakout": beyond_opening, "failed_retest": retest,
        "consolidation_continuation": consolidation_break, "subsequent_structure_break": structure_break,
        "relative_volume": i1.relative_volume >= 0.8, "not_overextended": extension <= 2.5,
    })
    if not vwap_aligned:
        evaluation.rejected.append("VWAP_MISALIGNMENT")
    if not structure:
        evaluation.rejected.append("TREND_STRUCTURE_MISSING")
    if not evaluation.conditions["relative_volume"]:
        evaluation.rejected.append("INSUFFICIENT_VOLUME")
    if not evaluation.conditions["not_overextended"]:
        evaluation.rejected.append("OVEREXTENDED_PRICE")
    trigger = renewed or consolidation_break or structure_break
    location = dynamic_support or beyond_opening or retest or structure_break
    evaluation.conditions["continuation_trigger"] = trigger
    evaluation.conditions["continuation_location"] = location
    if not trigger:
        evaluation.rejected.append("CONTINUATION_NOT_CONFIRMED")
    if not location:
        evaluation.rejected.append("NO_PULLBACK_OR_FAILED_RETEST")
    if evaluation.rejected:
        return evaluation
    setup = "consolidation" if consolidation_break else ("failed-retest" if retest else ("pullback" if dynamic_support else "structure-break"))
    evaluation.candidate = SignalCandidate(
        underlying, direction, "trend_continuation", evaluation.trend,
        {"momentum": _momentum(i1, direction), "trend": _trend(i5, direction),
         "volume": min(i1.relative_volume / 2.0, 1.0), "volatility": _volatility_score(i1, 1.0)},
        ["Established EMA trend and directional price structure", f"{setup.replace('-', ' ').title()} confirmed on closed candles",
         "VWAP, volume, momentum, and extension checks passed"],
        i1.price, i1.atr, trend=evaluation.trend, volatility=evaluation.volatility,
        event_key=_event_key(one, setup),
    )
    return evaluation


def trend_continuation(underlying: str, one: pd.DataFrame, five: pd.DataFrame,
                       opening_minutes: int) -> SignalCandidate | None:
    return _continuation_detail(underlying, one, five, opening_minutes).candidate


def _vwap_detail(underlying: str, one: pd.DataFrame, five: pd.DataFrame,
                 opening_minutes: int) -> StrategyEvaluation:
    evaluation = StrategyEvaluation("vwap_reversal")
    i1 = compute_indicators(one, opening_minutes)
    i5 = compute_indicators(five, opening_minutes)
    evaluation.trend = classify_trend(i5)
    evaluation.volatility = classify_volatility(i5)
    previous_vwap = session_vwap_at(one, -2)
    previous_close = float(one["close"].iloc[-2])
    direction: Direction | None = None
    if previous_close < previous_vwap and i1.price > i1.vwap:
        direction = "call"
    elif previous_close > previous_vwap and i1.price < i1.vwap:
        direction = "put"
    evaluation.direction = direction
    evaluation.conditions["vwap_cross"] = direction is not None
    if direction is None:
        evaluation.rejected.append("VWAP_MISALIGNMENT")
        return evaluation
    momentum_changed = (i1.macd_histogram > 0 and i1.rsi > 50) if direction == "call" else (i1.macd_histogram < 0 and i1.rsi < 50)
    failed_breakout = abs(previous_close - previous_vwap) <= i1.atr * 0.5
    evaluation.conditions.update({
        "momentum_reversal": momentum_changed, "failed_breakout": failed_breakout,
        "relative_volume": i1.relative_volume >= 1.3,
        "not_overextended": abs(i1.price - i1.vwap) / i1.atr <= 2.0,
    })
    for condition, code in (
        ("momentum_reversal", "MOMENTUM_NOT_CONFIRMED"), ("failed_breakout", "FAILED_RETEST_NOT_CONFIRMED"),
        ("relative_volume", "INSUFFICIENT_VOLUME"), ("not_overextended", "OVEREXTENDED_PRICE"),
    ):
        if not evaluation.conditions[condition]:
            evaluation.rejected.append(code)
    if evaluation.rejected:
        return evaluation
    evaluation.candidate = SignalCandidate(
        underlying, direction, "vwap_reversal", evaluation.trend,
        {"momentum": _momentum(i1, direction), "trend": _trend(i5, direction),
         "volume": min(i1.relative_volume / 2.5, 1.0), "volatility": _volatility_score(i1, 1.3)},
        ["VWAP reclaim/rejection confirmed at candle close", "Prior break failed near VWAP",
         "Momentum and volume confirm reversal"], i1.price, i1.atr, stronger_confirmation_required=True,
        trend=evaluation.trend, volatility=evaluation.volatility, event_key=_event_key(one, "vwap-reversal"),
    )
    return evaluation


def vwap_reversal(underlying: str, one: pd.DataFrame, five: pd.DataFrame,
                  opening_minutes: int) -> SignalCandidate | None:
    return _vwap_detail(underlying, one, five, opening_minutes).candidate


def session_vwap_at(frame: pd.DataFrame, index: int) -> float:
    from .indicators import session_vwap
    return float(session_vwap(frame).iloc[index])


class StrategyEngine:
    STRATEGIES = {
        "momentum_breakout": momentum_breakout,
        "trend_continuation": trend_continuation,
        "vwap_reversal": vwap_reversal,
    }
    DETAILED = {
        "momentum_breakout": _breakout_detail,
        "trend_continuation": _continuation_detail,
        "vwap_reversal": _vwap_detail,
    }

    def evaluate_detailed(self, underlying: str, one: pd.DataFrame, five: pd.DataFrame, fifteen: pd.DataFrame,
                          enabled: list[str], opening_minutes: int = 15,
                          news_unstable: bool = False) -> list[StrategyEvaluation]:
        one = filter_regular_session(one)
        five = filter_regular_session(five)
        fifteen = filter_regular_session(fifteen)
        results = []
        for name in enabled:
            function = self.DETAILED.get(name)
            if not function:
                continue
            if len(one) < 60 or len(five) < 55 or len(fifteen) < 55:
                results.append(StrategyEvaluation(name, rejected=["INSUFFICIENT_HISTORICAL_DATA"]))
                continue
            evaluation = function(underlying, one, five, opening_minutes)
            i15 = compute_indicators(fifteen, opening_minutes)
            trend = classify_trend(i15)
            volatility = classify_volatility(i15, news_unstable)
            evaluation.trend = trend
            evaluation.volatility = volatility
            if evaluation.candidate is not None:
                compatible = not (
                    volatility == "extreme" or
                    (name == "trend_continuation" and trend == "sideways") or
                    (name == "momentum_breakout" and volatility == "low")
                )
                if compatible:
                    candidate = evaluation.candidate
                    evaluation.candidate = SignalCandidate(**{
                        **candidate.__dict__, "regime": trend, "trend": trend, "volatility": volatility,
                    })
                else:
                    evaluation.rejected.append("UNSTABLE_MARKET" if volatility == "extreme" else "REGIME_MISMATCH")
                    evaluation.candidate = None
            results.append(evaluation)
        return results

    def evaluate(self, underlying: str, one: pd.DataFrame, five: pd.DataFrame, fifteen: pd.DataFrame,
                 enabled: list[str], opening_minutes: int = 15, news_unstable: bool = False) -> list[SignalCandidate]:
        return [item.candidate for item in self.evaluate_detailed(
            underlying, one, five, fifteen, enabled, opening_minutes, news_unstable,
        ) if item.candidate is not None]
