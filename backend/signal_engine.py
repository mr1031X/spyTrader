"""Explainable deterministic 0–100 setup and execution-quality scoring."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import BotSettings
from .strategies import SignalCandidate


@dataclass(frozen=True)
class ScoredSignal:
    candidate: SignalCandidate
    score: float
    qualified: bool
    factor_scores: dict[str, float]
    contributions: dict[str, float]
    explanation: list[str]
    rejection_reason: str | None = None
    technical_score: float = 0.0
    option_quality_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "underlying": self.candidate.underlying, "direction": self.candidate.direction,
            "strategy": self.candidate.strategy, "regime": self.candidate.regime,
            "trend": self.candidate.trend, "volatility": self.candidate.volatility,
            "score": self.score, "technical_score": self.technical_score,
            "option_quality_score": self.option_quality_score, "qualified": self.qualified,
            "factors": self.factor_scores, "contributions": self.contributions,
            "explanation": self.explanation, "rejection_reason": self.rejection_reason,
        }


class SignalEngine:
    def score(self, candidate: SignalCandidate, settings: BotSettings,
              option_liquidity: float = 0.0, reward_risk: float = 0.0) -> ScoredSignal:
        regime_scores = {
            "strong bullish": 1.0 if candidate.direction == "call" else 0.25,
            "moderate bullish": 0.85 if candidate.direction == "call" else 0.35,
            "strong bearish": 1.0 if candidate.direction == "put" else 0.25,
            "moderate bearish": 0.85 if candidate.direction == "put" else 0.35,
            "bullish trending": 1.0 if candidate.direction == "call" else 0.35,
            "bearish trending": 1.0 if candidate.direction == "put" else 0.35,
            "sideways": 0.75 if candidate.strategy == "vwap_reversal" else 0.25,
            "high volatility": 0.75,
            "low volatility": 0.35,
            "unstable/news-driven": 0.0,
        }
        factors = {
            "momentum": candidate.factors.get("momentum", 0.0),
            "trend": candidate.factors.get("trend", 0.0),
            "volume": candidate.factors.get("volume", 0.0),
            "regime": regime_scores.get(candidate.regime, 0.0),
            "volatility": candidate.factors.get("volatility", 0.0),
            "liquidity": max(0.0, min(1.0, option_liquidity)),
            "reward_risk": max(0.0, min(1.0, reward_risk)),
        }
        weights = settings.score_weights.model_dump()
        contributions = {name: round(factors[name] * weights[name], 3) for name in factors}
        score = round(sum(contributions.values()), 2)
        technical_score = round(sum(contributions[name] for name in ("momentum", "trend", "volume", "regime", "volatility")), 2)
        option_quality_score = round(contributions["liquidity"] + contributions["reward_risk"], 2)
        rejection = None
        if candidate.stronger_confirmation_required and (
            factors["momentum"] < 0.7 or factors["volume"] < 0.65
        ):
            rejection = "Countertrend setup lacks stronger momentum/volume confirmation"
        elif score < settings.min_signal_score:
            rejection = f"Score {score:.1f} is below the {settings.min_signal_score:.1f} threshold"
        return ScoredSignal(candidate, score, rejection is None, factors, contributions,
                            candidate.explanation, rejection, technical_score, option_quality_score)

    def score_underlying(self, candidate: SignalCandidate, settings: BotSettings) -> ScoredSignal:
        return self.score(candidate, settings)

    def score_technical(self, candidate: SignalCandidate, settings: BotSettings) -> ScoredSignal:
        base = self.score(candidate, settings)
        technical_weight = sum(settings.score_weights.model_dump()[name] for name in (
            "momentum", "trend", "volume", "regime", "volatility",
        ))
        score = round(base.technical_score / technical_weight * 100, 2) if technical_weight else 0.0
        rejection = None
        if candidate.stronger_confirmation_required and (
            base.factor_scores["momentum"] < 0.7 or base.factor_scores["volume"] < 0.65
        ):
            rejection = "Countertrend setup lacks stronger momentum/volume confirmation"
        elif score < settings.min_signal_score:
            rejection = f"Technical score {score:.1f} is below the {settings.min_signal_score:.1f} threshold"
        return ScoredSignal(candidate, score, rejection is None, base.factor_scores, base.contributions,
                            base.explanation, rejection, base.technical_score, 0.0)


def allocation_for_score(score: float, settings: BotSettings) -> float:
    if score < 80:
        return 0.0
    if score < 90:
        return settings.min_allocation_percent
    if score < 95:
        return min(settings.standard_allocation_percent, settings.max_allocation_percent)
    return settings.max_allocation_percent
