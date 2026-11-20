from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.broker import OptionContract, OrderState
from backend.config import BotSettings, Environment
from backend.database import OrderRecord, PositionRecord, SessionLocal, WeeklyState
from backend.execution import ExecutionEngine
from backend.market_data import MarketDataError, OptionSnapshot, Quote
from backend.option_selector import SelectedOption
from backend.risk_manager import week_start
from backend.signal_engine import allocation_for_score
from backend.strategies import SignalCandidate, StrategyEvaluation
from backend.trading_bot import TradingBot

from .conftest import FakeBroker, FakeNotifier
from .test_operational_regressions import flat_warmup_bars


def _candidate(symbol, direction):
    bullish = direction == "call"
    return SignalCandidate(
        symbol, direction, "momentum_breakout", "strong bullish" if bullish else "strong bearish",
        {"momentum": 1, "trend": 1, "volume": 1, "volatility": 1}, ["test setup"],
        600 if symbol == "SPY" else 500, 2, event_key="readiness-test-event",
    )


def _contract(symbol, direction):
    expiration = datetime.now(timezone.utc).date() + timedelta(days=3)
    strike = 600 if symbol == "SPY" else 500
    code = "C" if direction == "call" else "P"
    occ = f"{symbol}{expiration:%y%m%d}{code}{int(strike * 1000):08d}"
    return OptionContract(occ, symbol, direction, strike, expiration, open_interest=1000)


def _snapshot(contract, now=None):
    return OptionSnapshot(
        contract, Quote(contract.symbol, 2.0, 2.04, now or datetime.now(timezone.utc), "alpaca_opra"),
        0.55 if contract.option_type == "call" else -0.55,
        0.04, -0.1, 0.3, 1000, 500,
    )


class PipelineBroker(FakeBroker):
    def __init__(self, contract):
        super().__init__()
        self.contract = contract

    def get_contracts(self, *args):
        return [self.contract]


class PipelineMarket:
    def __init__(self, settings, snapshot, failures=0):
        self.settings = settings
        self.options_provider = SimpleNamespace(name="alpaca_opra")
        self.snapshot = snapshot
        self.failures = failures
        self.calls = 0

    def option_snapshots(self, contracts):
        self.calls += 1
        if self.calls <= self.failures:
            raise MarketDataError("temporary OPRA interruption")
        return [self.snapshot]


def _pipeline_bot(monkeypatch, daily, symbol, direction, failures=0):
    settings = BotSettings(symbols=[symbol])
    now = datetime.combine(daily.day, time(15, 30), tzinfo=timezone.utc)
    with SessionLocal() as session:
        weekly = session.scalar(select(WeeklyState))
        weekly.week_start = week_start(daily.day)
        session.commit()
    contract = _contract(symbol, direction)
    broker = PipelineBroker(contract)
    market = PipelineMarket(settings, _snapshot(contract), failures)
    bot = TradingBot(Environment(), SessionLocal)
    bot.notifier = FakeNotifier()
    bot._broker = broker
    bot._market = market
    bot._execution = ExecutionEngine(broker, bot.notifier, SessionLocal)
    bot._history = SimpleNamespace(get=lambda requested_symbol, requested_now: flat_warmup_bars(requested_now))
    bot._entries_enabled = True
    bot._reconciled = True
    bot._readiness.broker_connected = True
    bot._readiness.order_execution = True
    bot._clock = {"next_close": datetime.combine(daily.day, time(21), tzinfo=timezone.utc).isoformat()}
    candidate = _candidate(symbol, direction)
    evaluation = StrategyEvaluation(
        candidate.strategy, direction=direction, candidate=candidate,
        trend=candidate.trend or candidate.regime, volatility="normal",
    )
    monkeypatch.setattr(bot.strategy, "evaluate_detailed", lambda *args, **kwargs: [evaluation])
    return bot, broker, settings, now


@pytest.mark.parametrize("symbol,direction", [("SPY", "call"), ("QQQ", "put")])
def test_qualifying_signal_completes_mocked_paper_entry_fill_and_protective_exit(
    monkeypatch, account, daily, symbol, direction,
):
    bot, broker, settings, now = _pipeline_bot(monkeypatch, daily, symbol, direction)

    bot._scan_entries(settings, account, now)

    assert len(broker.submitted) == 1
    submitted = broker.submitted[0]
    assert submitted.side == "buy"
    assert submitted.status == "accepted"
    assert submitted.qty >= 1
    assert submitted.limit_price == 2.04
    with SessionLocal() as session:
        stored = session.scalar(select(OrderRecord))
        assert stored.mode == "paper"
        assert stored.position_intent == "buy_to_open"
        assert stored.client_order_id == submitted.client_order_id
        assert stored.broker_order_id == submitted.broker_order_id

    filled = replace(
        submitted, status="filled", filled_qty=submitted.qty, filled_avg_price=submitted.limit_price,
    )
    broker.orders[0] = filled
    bot._execution.sync_orders(settings)
    with SessionLocal() as session:
        position = session.scalar(select(PositionRecord))
        assert position.qty == submitted.qty
        assert position.entry_premium == submitted.limit_price
        assert position.stop_threshold == pytest.approx(submitted.limit_price * 0.9)

    exit_quote = Quote(position.contract_symbol, 1.5, 2.0, datetime.now(timezone.utc), "alpaca_opra")
    assert exit_quote.spread_percent > settings.max_spread_percent
    bot._execution.request_exit(position.id, "initial_stop", exit_quote, settings)
    with SessionLocal() as session:
        position = session.get(PositionRecord, position.id)
        assert position.status == "closed"
        assert position.qty == 0
        sells = [order for order in broker.submitted if order.side == "sell"]
        assert len(sells) == 1 and sells[0].qty == submitted.qty


def test_transient_option_data_failure_does_not_suppress_same_setup_retry(
    monkeypatch, account, daily,
):
    bot, broker, settings, now = _pipeline_bot(monkeypatch, daily, "SPY", "call", failures=1)

    bot._scan_entries(settings, account, now)
    assert not broker.submitted
    bot._scan_entries(settings, account, now)

    assert len(broker.submitted) == 1


def test_allocation_gate_uses_configured_qualifying_threshold():
    settings = BotSettings(min_signal_score=75)
    assert allocation_for_score(78, settings) == settings.min_allocation_percent
    settings = BotSettings(min_signal_score=85)
    assert allocation_for_score(82, settings) == 0


def test_snapshot_has_no_successful_tick_heartbeat_before_a_tick():
    bot = TradingBot(Environment(), SessionLocal)
    status = bot.snapshot()
    assert status["last_tick_at"] is None
    assert status["last_scan_at"] is None


def test_preflight_checks_near_money_tradable_contract(monkeypatch, account):
    far = OptionContract("SPY261123C00400000", "SPY", "call", 400, datetime.now(timezone.utc).date() + timedelta(days=3))
    near = OptionContract("SPY261123C00600000", "SPY", "call", 600, far.expiration)

    class Broker:
        def get_account(self):
            return account

        def verify_options_permission(self):
            pass

        def get_clock(self):
            return {"is_open": False}

        def get_contracts(self, *args):
            return [far, near]

    class Market:
        def __init__(self, settings, env):
            pass

        def stock_quote(self, symbol):
            return Quote(symbol, 599, 601, datetime.now(timezone.utc), "alpaca_opra")

        def option_snapshot(self, contract):
            assert contract == near
            return _snapshot(contract)

    env = Environment(alpaca_paper_api_key="paper", alpaca_paper_secret_key="secret")
    monkeypatch.setattr("backend.trading_bot.AlpacaBroker", lambda mode, env: Broker())
    monkeypatch.setattr("backend.trading_bot.MarketDataService", Market)
    result = TradingBot(env, SessionLocal).preflight("paper")
    assert result["ready"]


def test_alpaca_paper_limit_order_construction_and_acknowledgement(monkeypatch):
    from backend.broker import AlpacaBroker

    captured = {}

    class TradingClient:
        def __init__(self, api_key, secret_key, paper):
            captured["client"] = {"api_key": api_key, "secret_key": secret_key, "paper": paper}

        def submit_order(self, request):
            captured["request"] = request
            return SimpleNamespace(
                id="paper-broker-id", client_order_id=request.client_order_id, symbol=request.symbol,
                side=SimpleNamespace(value="buy"), qty=str(request.qty),
                status=SimpleNamespace(value="accepted"), filled_qty="0", filled_avg_price=None,
                limit_price=str(request.limit_price), model_dump=lambda: {},
            )

    monkeypatch.setattr("backend.broker.TradingClient", TradingClient)
    broker = AlpacaBroker("paper", Environment(
        alpaca_paper_api_key="paper-key", alpaca_paper_secret_key="paper-secret",
    ))
    result = broker.submit_limit_order(
        "SPY261123C00600000", 2, "buy", 2.04, "paper-client-id", "buy_to_open",
    )

    assert captured["client"] == {"api_key": "paper-key", "secret_key": "paper-secret", "paper": True}
    request = captured["request"]
    assert request.side.value == "buy"
    assert request.time_in_force.value == "day"
    assert request.position_intent.value == "buy_to_open"
    assert request.qty == 2 and request.limit_price == 2.04
    assert result.broker_order_id == "paper-broker-id"
    assert result.client_order_id == "paper-client-id"
    assert result.status == "accepted"
