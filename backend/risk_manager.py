"""Independent deterministic pre-trade and account-level risk controls."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import floor
from typing import Any
from zoneinfo import ZoneInfo

from .broker import AccountState
from .config import BotSettings
from .database import DailyState, WeeklyState
from .signal_engine import allocation_for_score

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class PositionSize:
    quantity: int
    allocation_percent: float
    capital_allocated: float
    intended_stop_loss: float
    maximum_premium_loss: float


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reasons: list[str]


class RiskManager:
    def check_entry(self, settings: BotSettings, account: AccountState, daily: DailyState,
                    weekly: WeeklyState, open_positions: int, pending_entries: int,
                    entries_requested: bool, broker_reconciled: bool, market_open: bool,
                    data_ready: bool, now: datetime | None = None) -> RiskDecision:
        reasons = []
        if not entries_requested:
            reasons.append("Automated entries are paused")
        if daily.circuit_breaker or weekly.circuit_breaker:
            reasons.append("A loss circuit breaker is active")
        if daily.goal_reached and settings.pause_after_daily_goal:
            reasons.append("Daily profit goal pause is active")
        if daily.consecutive_losses >= settings.max_consecutive_losses:
            reasons.append("Maximum consecutive losses reached")
        if daily.completed_trades >= settings.max_trades_per_day:
            reasons.append("Daily completed-trade limit reached")
        if open_positions + pending_entries >= settings.max_open_positions:
            reasons.append("Maximum open position or entry capacity reached")
        if not broker_reconciled:
            reasons.append("Broker state is not reconciled")
        if not market_open:
            reasons.append("Market is closed")
        if not data_ready:
            reasons.append("Real-time execution-quality data is unavailable")
        if account.blocked or account.trade_blocked or account.options_level < 1:
            reasons.append("Account or options permission check failed")
        if now and market_open and not self.entry_time_allowed(settings, now):
            reasons.append("Past the new-entry cutoff")
        return RiskDecision(not reasons, reasons)

    def position_size(self, score: float, premium: float, multiplier: int, equity: float,
                      buying_power: float, settings: BotSettings) -> PositionSize:
        allocation = allocation_for_score(score, settings)
        allocation = min(allocation, settings.max_allocation_percent)
        budget = min(equity * allocation / 100, buying_power)
        contract_cost = premium * multiplier
        quantity = floor(budget / contract_cost) if contract_cost > 0 else 0
        allocated = quantity * contract_cost
        return PositionSize(
            quantity=quantity, allocation_percent=allocation, capital_allocated=allocated,
            intended_stop_loss=allocated * settings.initial_stop_loss_percent / 100,
            maximum_premium_loss=allocated,
        )

    def circuit_breakers(self, settings: BotSettings, daily: DailyState, weekly: WeeklyState,
                         equity: float, week_realized_pnl: float) -> tuple[bool, bool]:
        daily_pnl = equity - daily.start_equity
        daily_trigger = daily_pnl <= -daily.start_equity * settings.daily_loss_limit_percent / 100
        weekly_pnl = week_realized_pnl + (equity - daily.start_equity)
        weekly_trigger = weekly_pnl <= -weekly.start_equity * settings.weekly_loss_limit_percent / 100
        return daily_trigger or daily.circuit_breaker, weekly_trigger or weekly.circuit_breaker

    @staticmethod
    def session_deadlines(settings: BotSettings, session_close: datetime) -> dict[str, datetime]:
        close_et = session_close.astimezone(ET)
        regular_close = close_et.replace(hour=16, minute=0, second=0, microsecond=0)
        cutoff = _parse_et(settings.new_entry_cutoff_et, close_et)
        liquidation = _parse_et(settings.liquidation_start_et, close_et)
        target = _parse_et(settings.target_flat_time_et, close_et)
        if close_et < regular_close:
            # Preserve the normal-session offsets on early-close days.
            cutoff = close_et - (regular_close - cutoff)
            liquidation = close_et - (regular_close - liquidation)
            target = close_et - (regular_close - target)
        return {"entry_cutoff": cutoff, "liquidation_start": liquidation, "target_flat": target}

    @staticmethod
    def entry_time_allowed(settings: BotSettings, now: datetime, session_close: datetime | None = None) -> bool:
        now_et = now.astimezone(ET)
        if session_close:
            cutoff = RiskManager.session_deadlines(settings, session_close)["entry_cutoff"]
        else:
            cutoff = _parse_et(settings.new_entry_cutoff_et, now_et)
        return now_et < cutoff


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _parse_et(value: str, day: datetime) -> datetime:
    hour, minute = map(int, value.split(":"))
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)
