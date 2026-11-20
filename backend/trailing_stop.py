"""Application-managed option trailing stops with monotonic, persisted state."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any

from .config import BotSettings
from .database import PositionRecord


@dataclass(frozen=True)
class TrailingDecision:
    highest_bid: float
    stop_threshold: float
    trailing_active: bool
    should_exit: bool
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def protection_snapshot(settings: BotSettings) -> dict[str, Any]:
    """Settings captured at entry; dashboard edits never mutate open protection."""
    return {
        "initial_stop_loss_percent": settings.initial_stop_loss_percent,
        "trailing_stop_enabled": settings.trailing_stop_enabled,
        "trailing_activation_percent": settings.trailing_activation_percent,
        "trailing_distance_percent": settings.trailing_distance_percent,
        "fixed_profit_target_enabled": settings.fixed_profit_target_enabled,
        "fixed_profit_target_percent": settings.fixed_profit_target_percent,
    }


def initial_stop(entry_premium: float, stop_percent: float) -> float:
    return entry_premium * (1 - stop_percent / 100)


def evaluate_trailing(position: PositionRecord, valid_bid: float) -> TrailingDecision:
    if valid_bid <= 0:
        raise ValueError("A valid positive executable bid is required")
    protection = position.protection or {}
    stop_percent = float(protection.get("initial_stop_loss_percent", 10))
    activation_percent = float(protection.get("trailing_activation_percent", 20))
    distance_percent = float(protection.get("trailing_distance_percent", 10))
    trailing_enabled = bool(protection.get("trailing_stop_enabled", True))
    fixed_enabled = bool(protection.get("fixed_profit_target_enabled", False))
    fixed_percent = float(protection.get("fixed_profit_target_percent", 50))

    highest = max(float(position.highest_bid or position.entry_premium), valid_bid)
    threshold = max(float(position.stop_threshold), initial_stop(position.entry_premium, stop_percent))
    active = bool(position.trailing_active)
    if trailing_enabled and valid_bid >= position.entry_premium * (1 + activation_percent / 100):
        active = True
    if active:
        # max() is the central invariant: the threshold can never decrease.
        threshold = max(threshold, highest * (1 - distance_percent / 100))

    should_exit = valid_bid <= threshold + 1e-9
    reason = "trailing_stop" if active else "initial_stop"
    if fixed_enabled and valid_bid >= position.entry_premium * (1 + fixed_percent / 100):
        should_exit = True
        reason = "fixed_profit_target"
    return TrailingDecision(highest, threshold, active, should_exit, reason if should_exit else None)


def apply_decision(position: PositionRecord, decision: TrailingDecision) -> None:
    position.highest_bid = decision.highest_bid
    position.stop_threshold = decision.stop_threshold
    position.trailing_active = decision.trailing_active


def option_limit_price(value: float, side: str = "buy") -> float:
    """Round outward to a valid cent: buys up, sells down."""
    rounding = ROUND_UP if side == "buy" else ROUND_DOWN
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=rounding))
