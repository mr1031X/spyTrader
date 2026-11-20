"""Liquid 1–3 DTE option selection using actual quotes and available Greeks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .broker import AlpacaBroker, OptionContract
from .config import BotSettings
from .market_data import MarketDataError, MarketDataService, OptionSnapshot
from .strategies import SignalCandidate
from .trailing_stop import option_limit_price


@dataclass(frozen=True)
class SelectedOption:
    snapshot: OptionSnapshot
    quality_score: float
    liquidity_score: float
    reward_risk_score: float
    ranking_explanation: dict[str, Any]


class OptionSelector:
    def __init__(self, broker: AlpacaBroker, market_data: MarketDataService):
        self.broker = broker
        self.market_data = market_data

    def select(self, candidate: SignalCandidate, settings: BotSettings, equity: float) -> SelectedOption | None:
        contracts = self.broker.get_contracts(candidate.underlying, candidate.direction, 1, 3)
        underlying = candidate.reference_price
        # Prefilter only by strike and DTE; all pricing/Greeks below come from real snapshots.
        nearby = [c for c in contracts if c.tradable and underlying * 0.88 <= c.strike <= underlying * 1.12]
        nearby.sort(key=lambda c: (abs(c.strike - underlying), c.expiration))
        snapshots = self.market_data.option_snapshots(nearby[:50])
        ranked: list[SelectedOption] = []
        for snapshot in snapshots:
            selected = self._evaluate(snapshot, candidate, settings, equity)
            if selected:
                ranked.append(selected)
        if not ranked:
            return None
        ranked.sort(key=lambda item: item.quality_score, reverse=True)
        return ranked[0]

    def _evaluate(self, snapshot: OptionSnapshot, candidate: SignalCandidate, settings: BotSettings,
                  equity: float) -> SelectedOption | None:
        quote = snapshot.quote
        if quote.validation_error(max_age_seconds=settings.max_quote_age_seconds,
                                  max_spread_percent=settings.max_spread_percent):
            return None
        if snapshot.delta is None or not settings.min_absolute_delta <= abs(snapshot.delta) <= settings.max_absolute_delta:
            return None
        if snapshot.open_interest is not None and snapshot.open_interest < settings.min_open_interest:
            return None
        if snapshot.volume is not None and snapshot.volume < settings.min_contract_volume:
            return None
        intended_limit = option_limit_price(min(quote.ask, quote.midpoint + (quote.ask - quote.bid) * 0.75))
        estimated_slippage = (intended_limit - quote.midpoint) / quote.midpoint * 100
        if estimated_slippage > settings.max_slippage_percent:
            return None
        spread_penalty = min(quote.spread_percent / settings.max_spread_percent, 1)
        delta_fit = 1 - min(abs(abs(snapshot.delta) - 0.55) / 0.55, 1)
        oi_score = min((snapshot.open_interest or 0) / 2000, 1) if snapshot.open_interest is not None else 0.5
        volume_score = min((snapshot.volume or 0) / 500, 1) if snapshot.volume is not None else 0.5
        liquidity = max(0, min(1, 0.45 * (1 - spread_penalty) + 0.30 * oi_score + 0.25 * volume_score))
        premium = quote.ask * snapshot.contract.multiplier
        if premium <= 0 or premium > equity * settings.max_allocation_percent / 100:
            return None
        intended_risk = premium * settings.initial_stop_loss_percent / 100
        expected_move = candidate.atr * abs(snapshot.delta) * snapshot.contract.multiplier * 1.5
        raw_rr = expected_move / intended_risk if intended_risk > 0 else 0
        reward_risk = max(0, min(raw_rr / 3, 1))
        gamma_score = min(max(snapshot.gamma, 0) / 0.05, 1) if snapshot.gamma is not None else 0.5
        theta_ratio = abs(snapshot.theta) / quote.midpoint if snapshot.theta is not None else None
        theta_score = max(0, 1 - theta_ratio / 0.25) if theta_ratio is not None else 0.5
        iv_score = max(0, 1 - max(snapshot.implied_volatility - 0.75, 0) / 1.25) if snapshot.implied_volatility is not None else 0.5
        quality = (0.30 * liquidity + 0.20 * delta_fit + 0.15 * reward_risk +
                   0.10 * gamma_score + 0.15 * theta_score + 0.10 * iv_score)
        return SelectedOption(snapshot, quality, liquidity, reward_risk, {
            "spread_percent": quote.spread_percent, "delta_fit": delta_fit,
            "delta": snapshot.delta, "gamma": snapshot.gamma, "theta": snapshot.theta,
            "implied_volatility": snapshot.implied_volatility,
            "open_interest": snapshot.open_interest, "volume": snapshot.volume,
            "premium": premium, "intended_limit": intended_limit,
            "estimated_slippage_percent": estimated_slippage,
            "estimated_reward_risk_ratio": raw_rr, "selection_score": quality,
        })
