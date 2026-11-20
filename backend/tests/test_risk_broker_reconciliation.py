from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from backend.broker import AlpacaBroker, BrokerConfigurationError, BrokerPosition, parse_occ_symbol
from backend.config import BotSettings, Environment
from backend.database import DailyState, PositionRecord, SessionLocal, WeeklyState
from backend.execution import ExecutionEngine
from backend.risk_manager import RiskManager, week_start
from backend.trailing_stop import protection_snapshot
from backend.trading_bot import TradingBot

from .conftest import FakeNotifier


def test_position_sizing_whole_contracts_and_loss_distinction(account):
    settings = BotSettings()
    risk = RiskManager()
    low = risk.position_size(85, 4, 100, 10_000, 10_000, settings)
    assert low.quantity == 1 and low.capital_allocated == 400
    assert low.intended_stop_loss == 40 and low.maximum_premium_loss == 400
    high = risk.position_size(96, 4, 100, 10_000, 10_000, settings)
    assert high.quantity == 3 and high.capital_allocated == 1200
    capped = risk.position_size(96, 4, 100, 10_000, 500, settings)
    assert capped.quantity == 1


def test_daily_trade_position_and_consecutive_loss_limits(account, daily, session):
    settings = BotSettings()
    weekly = session.scalar(select(WeeklyState))
    risk = RiskManager()
    daily.completed_trades = 3
    assert not risk.check_entry(settings, account, daily, weekly, 0, 0, True, True, True, True).allowed
    daily.completed_trades = 0
    assert not risk.check_entry(settings, account, daily, weekly, 1, 0, True, True, True, True).allowed
    daily.consecutive_losses = 2
    assert not risk.check_entry(settings, account, daily, weekly, 0, 0, True, True, True, True).allowed


def test_daily_profit_goal_pauses_entries(account, daily, session):
    settings = BotSettings()
    weekly = session.scalar(select(WeeklyState))
    daily.goal_reached = True
    result = RiskManager().check_entry(settings, account, daily, weekly, 0, 0, True, True, True, True)
    assert not result.allowed and any("goal" in reason for reason in result.reasons)
    settings.pause_after_daily_goal = False
    assert RiskManager().check_entry(settings, account, daily, weekly, 0, 0, True, True, True, True).allowed


def test_daily_and_weekly_circuit_breakers(account, daily, session):
    settings = BotSettings()
    weekly = session.scalar(select(WeeklyState))
    risk = RiskManager()
    daily_trigger, weekly_trigger = risk.circuit_breakers(settings, daily, weekly, 9399, 0)
    assert daily_trigger
    daily.start_equity = 10_000
    weekly.start_equity = 10_000
    daily_trigger, weekly_trigger = risk.circuit_breakers(settings, daily, weekly, 9400, -600)
    assert weekly_trigger


def test_early_close_deadlines_adjust():
    settings = BotSettings()
    close = datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)  # 1pm ET
    result = RiskManager.session_deadlines(settings, close)
    assert result["entry_cutoff"].hour == 12 and result["entry_cutoff"].minute == 0
    assert result["liquidation_start"].hour == 12 and result["liquidation_start"].minute == 40
    assert result["target_flat"].hour == 12 and result["target_flat"].minute == 45


def test_tick_publishes_broker_clock(monkeypatch, account, daily):
    clock = {
        "timestamp": "2026-11-18T12:00:00-05:00",
        "is_open": True,
        "next_open": "2026-11-19T09:30:00-05:00",
        "next_close": "2026-11-18T16:00:00-05:00",
    }

    class Broker:
        def get_account(self):
            return account

        def get_clock(self):
            return clock

    class Execution:
        def sync_orders(self):
            pass

    class Provider:
        name = "alpaca_opra"

    class Market:
        options_provider = Provider()

    env = Environment(alpaca_paper_api_key="paper", alpaca_paper_secret_key="secret")
    bot = TradingBot(env, SessionLocal)
    bot._broker = Broker()
    bot._market = Market()
    bot._execution = Execution()
    bot._reconciled = True
    bot._last_reconcile = float("inf")
    bot._last_snapshot = float("inf")
    monkeypatch.setattr(bot, "_ensure_clients", lambda settings: None)
    monkeypatch.setattr(bot, "_manage_positions", lambda settings: None)
    monkeypatch.setattr(bot, "_check_circuits", lambda settings, equity, now: None)
    monkeypatch.setattr(bot, "_handle_eod", lambda settings, clock, now: None)

    bot._tick()

    assert bot.snapshot()["clock"] == clock


def test_occ_parser():
    assert parse_occ_symbol("SPY260120C00670000") == ("SPY", "call", 670.0, date(2026, 1, 20))
    assert parse_occ_symbol("QQQ260121P00500000")[2] == 500.0


def test_missing_credentials_and_live_paper_isolation(monkeypatch):
    env = Environment(alpaca_paper_api_key="", alpaca_paper_secret_key="")
    with pytest.raises(BrokerConfigurationError):
        AlpacaBroker("paper", env)
    captured = []
    class Client:
        def __init__(self, api_key, secret_key, paper):
            captured.append((api_key, paper))
    monkeypatch.setattr("backend.broker.TradingClient", Client)
    AlpacaBroker("paper", Environment(alpaca_paper_api_key="paper", alpaca_paper_secret_key="secret"))
    AlpacaBroker("live", Environment(alpaca_live_api_key="live", alpaca_live_secret_key="secret", live_trading_enabled=True))
    assert captured == [("paper", True), ("live", False)]


def test_restart_reconciliation_recovers_broker_position(session, daily, fake_broker):
    symbol = "SPY260120C00670000"
    fake_broker.positions = [BrokerPosition(symbol, 2, 4.0, 900, {})]
    engine = ExecutionEngine(fake_broker, FakeNotifier(), SessionLocal)
    result = engine.reconcile(BotSettings())
    assert result["recovered"] == [symbol]
    position = session.scalar(select(PositionRecord).where(PositionRecord.contract_symbol == symbol))
    assert position.recovered and position.qty == 2 and position.stop_threshold == 3.6


def test_active_circuit_breaker_does_not_liquidate_repeatedly(session, daily):
    now = datetime.now(timezone.utc)
    weekly = session.scalar(select(WeeklyState))
    weekly.week_start = week_start(now.astimezone(ZoneInfo("America/New_York")).date())
    session.commit()

    class FakeExecution:
        def __init__(self):
            self.liquidations = 0
            self.cancellations = 0

        def cancel_opening_orders(self):
            self.cancellations += 1

        def emergency_liquidate(self):
            self.liquidations += 1
            return {"submitted_orders": [], "position_count": 0}

    execution = FakeExecution()
    bot = TradingBot(Environment(), SessionLocal)
    bot._execution = execution
    bot.notifier = FakeNotifier()
    bot._check_circuits(BotSettings(), 9_000, now)
    bot._check_circuits(BotSettings(), 9_000, now)
    assert execution.liquidations == 1
    assert execution.cancellations == 1
    assert bot.snapshot()["state"] == "circuit_breaker"
