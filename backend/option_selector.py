"""Liquid 1–3 DTE option selection with per-contract rejection diagnostics."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .broker import AlpacaBroker
from .config import BotSettings
from .market_data import MarketDataService, OptionSnapshot
from .strategies import SignalCandidate
from .trailing_stop import option_limit_price

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class SelectedOption:
    snapshot: OptionSnapshot
    quality_score: float
    liquidity_score: float
    reward_risk_score: float
    ranking_explanation: dict[str, Any]


@dataclass
class SelectionResult:
    selected: SelectedOption | None = None
    stats: dict[str, int] = field(default_factory=dict)
    rejections: list[dict[str, str]] = field(default_factory=list)

    @property
    def rejection_codes(self) -> list[str]:
        return sorted({item["reason"] for item in self.rejections})

    def to_dict(self) -> dict[str, Any]:
        return {
            "stats": self.stats,
            "rejections": self.rejections[-100:],
            "rejection_codes": self.rejection_codes,
            "selected": self.selected.snapshot.contract.symbol if self.selected else None,
            "ranking": self.selected.ranking_explanation if self.selected else None,
        }


class OptionSelector:
    def __init__(self, broker: AlpacaBroker, market_data: MarketDataService):
        self.broker = broker
        self.market_data = market_data
        self.last_result = SelectionResult()

    def select(self, candidate: SignalCandidate, settings: BotSettings, equity: float) -> SelectedOption | None:
        return self.select_detailed(candidate, settings, equity).selected

    def select_detailed(self, candidate: SignalCandidate, settings: BotSettings, equity: float) -> SelectionResult:
        contracts = self.broker.get_contracts(candidate.underlying, candidate.direction, 1, 3)
        underlying = candidate.reference_price
        today = datetime.now(ET).date()
        in_dte = [contract for contract in contracts if 1 <= (contract.expiration - today).days <= 3]
        result = SelectionResult(stats={
            "discovered": len(contracts), "considered": 0, "valid_quotes": 0,
            "within_dte": len(in_dte), "within_delta": 0, "liquidity_passed": 0,
            "spread_passed": 0, "affordable": 0, "eligible": 0, "rejected": 0,
        })
        for contract in contracts:
            if contract not in in_dte:
                result.rejections.append({"symbol": contract.symbol, "reason": "DTE_OUT_OF_RANGE"})
        nearby = [contract for contract in in_dte if contract.tradable and underlying * 0.88 <= contract.strike <= underlying * 1.12]
        for contract in in_dte:
            if contract not in nearby:
                result.rejections.append({"symbol": contract.symbol, "reason": "STRIKE_OR_TRADABILITY_REJECTED"})
        nearby.sort(key=lambda contract: (abs(contract.strike - underlying), contract.expiration))
        considered = nearby[:50]
        result.stats["considered"] = len(considered)
        for contract in nearby[50:]:
            result.rejections.append({"symbol": contract.symbol, "reason": "CHAIN_EVALUATION_LIMIT"})
        snapshots = self.market_data.option_snapshots(considered)
        by_symbol = {snapshot.contract.symbol: snapshot for snapshot in snapshots}
        ranked: list[SelectedOption] = []
        for contract in considered:
            snapshot = by_symbol.get(contract.symbol)
            if snapshot is None:
                result.rejections.append({"symbol": contract.symbol, "reason": "OPTION_QUOTE_UNAVAILABLE"})
                continue
            selected, reason = self._evaluate_detailed(snapshot, candidate, settings, equity, result.stats)
            if selected:
                ranked.append(selected)
            elif reason:
                result.rejections.append({"symbol": contract.symbol, "reason": reason})
        result.stats["eligible"] = len(ranked)
        result.stats["rejected"] = len(contracts) - len(ranked)
        if ranked:
            ranked.sort(key=lambda item: item.quality_score, reverse=True)
            result.selected = ranked[0]
        self.last_result = result
        return result

    def _evaluate(self, snapshot: OptionSnapshot, candidate: SignalCandidate, settings: BotSettings,
                  equity: float) -> SelectedOption | None:
        selected, _ = self._evaluate_detailed(snapshot, candidate, settings, equity, {})
        return selected

    def _evaluate_detailed(self, snapshot: OptionSnapshot, candidate: SignalCandidate, settings: BotSettings,
                           equity: float, stats: dict[str, int]) -> tuple[SelectedOption | None, str | None]:
        quote = snapshot.quote
        quote_error = quote.validation_error(max_age_seconds=settings.max_quote_age_seconds, max_spread_percent=None)
        if quote_error:
            reason = "STALE_OPTION_QUOTE" if "stale" in quote_error.lower() or "timestamp" in quote_error.lower() else "INVALID_OPTION_QUOTE"
            return None, reason
        stats["valid_quotes"] = stats.get("valid_quotes", 0) + 1
        if snapshot.delta is None:
            return None, "DELTA_UNAVAILABLE"
        if not settings.min_absolute_delta <= abs(snapshot.delta) <= settings.max_absolute_delta:
            return None, "DELTA_OUT_OF_RANGE"
        stats["within_delta"] = stats.get("within_delta", 0) + 1
        if snapshot.open_interest is not None and snapshot.open_interest < settings.min_open_interest:
            return None, "INSUFFICIENT_OPEN_INTEREST"
        if snapshot.volume is not None and snapshot.volume < settings.min_contract_volume:
            return None, "INSUFFICIENT_CONTRACT_VOLUME"
        stats["liquidity_passed"] = stats.get("liquidity_passed", 0) + 1
        if quote.spread_percent > settings.max_spread_percent:
            return None, "SPREAD_TOO_WIDE"
        stats["spread_passed"] = stats.get("spread_passed", 0) + 1
        intended_limit = option_limit_price(min(quote.ask, quote.midpoint + (quote.ask - quote.bid) * 0.75))
        estimated_slippage = (intended_limit - quote.midpoint) / quote.midpoint * 100
        if estimated_slippage > settings.max_slippage_percent:
            return None, "ESTIMATED_SLIPPAGE_TOO_HIGH"
        spread_penalty = min(quote.spread_percent / settings.max_spread_percent, 1)
        delta_fit = 1 - min(abs(abs(snapshot.delta) - 0.55) / 0.55, 1)
        oi_score = min((snapshot.open_interest or 0) / 2000, 1) if snapshot.open_interest is not None else 0.5
        volume_score = min((snapshot.volume or 0) / 500, 1) if snapshot.volume is not None else 0.5
        liquidity = max(0, min(1, 0.45 * (1 - spread_penalty) + 0.30 * oi_score + 0.25 * volume_score))
        premium = quote.ask * snapshot.contract.multiplier
        if premium <= 0 or premium > equity * settings.max_allocation_percent / 100:
            return None, "PREMIUM_TOO_EXPENSIVE"
        stats["affordable"] = stats.get("affordable", 0) + 1
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
        }), None
