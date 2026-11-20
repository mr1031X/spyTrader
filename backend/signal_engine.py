"""Explainable deterministic 0–100 signal scoring; not a win probability."""
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "underlying": self.candidate.underlying, "direction": self.candidate.direction,
            "strategy": self.candidate.strategy, "regime": self.candidate.regime,
            "score": self.score, "qualified": self.qualified, "factors": self.factor_scores,
            "contributions": self.contributions, "explanation": self.explanation,
            "rejection_reason": self.rejection_reason,
        }


class SignalEngine:
    def score(self, candidate: SignalCandidate, settings: BotSettings,
              option_liquidity: float = 0.0, reward_risk: float = 0.0) -> ScoredSignal:
        regime_scores = {
            "bullish trending": 1.0 if candidate.direction == "call" else 0.35,
            "bearish trending": 1.0 if candidate.direction == "put" else 0.35,
            "sideways": 0.75 if candidate.strategy == "vwap_reversal" else 0.25,
            "high volatility": 0.45,
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
        rejection = None
        if candidate.stronger_confirmation_required and (
            factors["momentum"] < 0.7 or factors["volume"] < 0.65
        ):
            rejection = "Countertrend setup lacks stronger momentum/volume confirmation"
        elif score < settings.min_signal_score:
            rejection = f"Score {score:.1f} is below the {settings.min_signal_score:.1f} threshold"
        return ScoredSignal(candidate, score, rejection is None, factors, contributions,
                            candidate.explanation, rejection)


def allocation_for_score(score: float, settings: BotSettings) -> float:
    if score < 80:
        return 0.0
    if score < 90:
        return settings.min_allocation_percent
    if score < 95:
        return min(settings.standard_allocation_percent, settings.max_allocation_percent)
    return settings.max_allocation_percent
