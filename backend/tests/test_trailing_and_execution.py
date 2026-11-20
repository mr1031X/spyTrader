from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.broker import BrokerError, BrokerPosition, OrderState
from backend.config import BotSettings
from backend.database import DailyState, FillRecord, OrderRecord, PositionRecord, SessionLocal, TradeRecord
from backend.execution import ExecutionEngine
from backend.market_data import Quote
from backend.trailing_stop import apply_decision, evaluate_trailing, initial_stop, option_limit_price, protection_snapshot

from .conftest import FakeNotifier


def make_position(session, daily):
    settings = BotSettings()
    position = PositionRecord(
        mode="paper", contract_symbol="SPY260120C00670000", underlying="SPY", option_type="call",
        strike=670, expiration=date(2026, 1, 20), qty=1, initial_qty=1, entry_premium=4.0,
        highest_bid=4.0, stop_threshold=3.6, strategy="momentum_breakout", protection=protection_snapshot(settings),
    )
    session.add(position)
    session.commit()
    return position


def quote(price):
    return Quote("SPY260120C00670000", price, price + 0.05, datetime.now(timezone.utc), "test", 10, 10)


def test_required_trailing_sequence_and_realized_fill(session, daily, fake_broker):
    position = make_position(session, daily)
    expected = [
        (3.95, False, 3.60, False), (4.20, False, 3.60, False),
        (4.80, True, 4.32, False), (5.20, True, 4.68, False),
        (6.00, True, 5.40, False), (6.50, True, 5.85, False),
        (6.20, True, 5.85, False), (5.85, True, 5.85, True),
    ]
    for bid, active, threshold, should_exit in expected:
        decision = evaluate_trailing(position, bid)
        apply_decision(position, decision)
        assert decision.trailing_active is active
        assert decision.stop_threshold == pytest.approx(threshold)
        assert decision.should_exit is should_exit
    previous = position.stop_threshold
    decision = evaluate_trailing(position, 5.0)
    assert decision.stop_threshold == previous
    assert decision.should_exit

    fake_broker.fill_price = 5.84  # A trigger is not a guaranteed fill.
    engine = ExecutionEngine(fake_broker, FakeNotifier(), SessionLocal)
    order = engine.request_exit(position.id, decision.reason, quote(5.85), BotSettings())
    assert order.status == "filled"
    session.expire_all()
    closed = session.get(PositionRecord, position.id)
    trade = session.scalar(select(TradeRecord).where(TradeRecord.position_id == position.id))
    assert closed.status == "closed"
    assert trade.exit_price == 5.84
    assert trade.realized_pnl == pytest.approx(184.0)
    session.refresh(daily)
    assert daily.realized_pnl == pytest.approx(184.0)
    assert daily.completed_trades == 1


def test_initial_stop_before_trailing_activation(session, daily):
    position = make_position(session, daily)
    decision = evaluate_trailing(position, 3.60)
    assert decision.should_exit and decision.reason == "initial_stop"
    assert not decision.trailing_active


def test_gap_and_rapid_reversal_never_lowers_threshold(session, daily):
    position = make_position(session, daily)
    for bid in (4.9, 7.0, 6.9, 6.0):
        decision = evaluate_trailing(position, bid)
        apply_decision(position, decision)
        assert decision.stop_threshold >= position.stop_threshold - 1e-9
    assert position.stop_threshold == pytest.approx(6.30)
    assert decision.should_exit


def test_duplicate_exit_prevention(session, daily, fake_broker):
    position = make_position(session, daily)
    fake_broker.exit_status = "accepted"
    # accepted sells are not filled in the fake
    original_submit = fake_broker.submit_limit_order
    def accepted(*args, **kwargs):
        state = original_submit(*args, **kwargs)
        if args[2] == "sell":
            from dataclasses import replace
            state = replace(state, status="accepted", filled_qty=0, filled_avg_price=None)
            fake_broker.orders[-1] = state
            fake_broker.submitted[-1] = state
        return state
    fake_broker.submit_limit_order = accepted
    engine = ExecutionEngine(fake_broker, FakeNotifier(), SessionLocal)
    first = engine.request_exit(position.id, "trailing_stop", quote(5.85), BotSettings())
    second = engine.request_exit(position.id, "trailing_stop", quote(5.85), BotSettings())
    assert first.id == second.id
    assert len(fake_broker.submitted) == 1


def test_partial_exit_fill_keeps_remaining_position(session, daily, fake_broker):
    position = make_position(session, daily)
    position.qty = position.initial_qty = 2
    session.commit()
    fake_broker.exit_status = "accepted"
    engine = ExecutionEngine(fake_broker, FakeNotifier(), SessionLocal)
    # Submit accepted exit, then apply a one-contract partial fill.
    original = fake_broker.submit_limit_order
    def accepted(*args, **kwargs):
        from dataclasses import replace
        state = original(*args, **kwargs)
        state = replace(state, status="accepted", filled_qty=0, filled_avg_price=None)
        fake_broker.orders[-1] = state
        return state
    fake_broker.submit_limit_order = accepted
    order = engine.request_exit(position.id, "trailing_stop", quote(5.85), BotSettings())
    from dataclasses import replace
    partial = replace(fake_broker.orders[-1], status="partially_filled", filled_qty=1, filled_avg_price=5.85)
    with SessionLocal() as write:
        stored = write.get(OrderRecord, order.id)
        engine._apply_order_state(write, stored, partial)
        write.commit()
    session.expire_all()
    position = session.get(PositionRecord, position.id)
    assert position.qty == 1 and position.status == "closing"
    assert session.scalar(select(TradeRecord)) is None


def test_entry_fill_persists_score_and_incremental_partial_fill_price(session, daily, fake_broker):
    symbol = "SPY260120C00670000"
    order = OrderRecord(
        client_order_id="test-entry-score", mode="paper", symbol=symbol, side="buy",
        position_intent="buy_to_open", qty=3, status="accepted", signal_score=93.5,
    )
    session.add(order)
    session.commit()
    engine = ExecutionEngine(fake_broker, FakeNotifier(), SessionLocal)
    first = OrderState("broker-entry", order.client_order_id, symbol, "buy", 3, "partially_filled", 1, 4.0, 4.1, {})
    engine._apply_order_state(session, order, first)
    session.commit()
    second = OrderState("broker-entry", order.client_order_id, symbol, "buy", 3, "filled", 3, 5.0, 4.1, {})
    engine._apply_order_state(session, order, second)
    session.commit()
    position = session.get(PositionRecord, order.position_id)
    incremental = session.scalar(select(FillRecord).where(FillRecord.cumulative_qty == 3))
    assert position.score == pytest.approx(93.5)
    assert position.entry_premium == pytest.approx(5.0)
    assert incremental.price == pytest.approx(5.5)


def test_emergency_fill_is_fully_recorded_and_closes_position(session, daily, fake_broker):
    position = make_position(session, daily)
    fake_broker.fill_price = 5.5
    engine = ExecutionEngine(fake_broker, FakeNotifier(), SessionLocal)
    result = engine.emergency_liquidate()
    session.expire_all()
    closed = session.get(PositionRecord, position.id)
    order = session.get(OrderRecord, closed.exit_order_id)
    trade = session.scalar(select(TradeRecord).where(TradeRecord.position_id == position.id))
    fill = session.scalar(select(FillRecord).where(FillRecord.order_id == order.id))
    assert result["position_count"] == 1
    assert closed.status == "closed" and order.status == "filled"
    assert fill.qty == 1 and fill.price == pytest.approx(5.5)
    assert trade.realized_pnl == pytest.approx(150.0)
    assert trade.slippage is None


def test_terminal_unfilled_exit_releases_protection_for_retry(session, daily, fake_broker):
    position = make_position(session, daily)
    engine = ExecutionEngine(fake_broker, FakeNotifier(), SessionLocal)
    original = fake_broker.submit_limit_order

    def accepted(*args, **kwargs):
        state = original(*args, **kwargs)
        if args[2] == "sell":
            state = replace(state, status="accepted", filled_qty=0, filled_avg_price=None)
            fake_broker.orders[-1] = state
        return state

    fake_broker.submit_limit_order = accepted
    order = engine.request_exit(position.id, "trailing_stop", quote(5.85), BotSettings())
    expired = replace(fake_broker.orders[-1], status="expired")
    with SessionLocal() as write:
        stored = write.get(OrderRecord, order.id)
        engine._apply_order_state(write, stored, expired)
        write.commit()
    session.expire_all()
    released = session.get(PositionRecord, position.id)
    assert released.status == "open" and released.exit_order_id is None
    replacement = engine.request_exit(position.id, "trailing_stop", quote(5.85), BotSettings())
    assert replacement.id != order.id


def test_unknown_orders_age_out_only_after_reconciliation_grace(session, daily, fake_broker):
    position = make_position(session, daily)
    order = OrderRecord(
        client_order_id="unknown-exit", mode="paper", symbol=position.contract_symbol,
        side="sell", position_intent="sell_to_close", qty=1, status="unknown",
        position_id=position.id, updated_at=datetime.now(timezone.utc) - timedelta(minutes=3),
    )
    session.add(order)
    session.flush()
    position.exit_order_id = order.id
    position.status = "closing"
    session.commit()
    fake_broker.positions = [BrokerPosition(position.contract_symbol, 1, 4.0, 400, {})]
    engine = ExecutionEngine(fake_broker, FakeNotifier(), SessionLocal)
    result = engine.reconcile(BotSettings())
    session.expire_all()
    assert result["aged_out"] == ["unknown-exit"]
    assert session.get(OrderRecord, order.id).status == "error"
    assert session.get(PositionRecord, position.id).status == "open"


def test_unpriced_reconciliation_pauses_entries_without_fabricating_pnl(session, daily, fake_broker):
    position = make_position(session, daily)
    engine = ExecutionEngine(fake_broker, FakeNotifier(), SessionLocal)
    result = engine.reconcile(BotSettings())
    session.expire_all()
    closed = session.get(PositionRecord, position.id)
    session.refresh(daily)
    assert result["unexpected"] == [position.contract_symbol]
    assert closed.realized_pnl is None
    assert session.scalar(select(TradeRecord)) is None
    assert daily.completed_trades == 1 and daily.circuit_breaker


def test_emergency_attempts_all_positions_before_reporting_failure(session, daily, fake_broker):
    first = make_position(session, daily)
    second = PositionRecord(
        mode="paper", contract_symbol="QQQ260121P00500000", underlying="QQQ", option_type="put",
        strike=500, expiration=date(2026, 1, 21), qty=1, initial_qty=1, entry_premium=4.0,
        highest_bid=4.0, stop_threshold=3.6, strategy="trend_continuation",
        protection=protection_snapshot(BotSettings()),
    )
    session.add(second)
    session.commit()
    original = fake_broker.emergency_close

    def fail_first(symbol, qty=None):
        if symbol == first.contract_symbol:
            raise BrokerError("test rejection")
        return original(symbol, qty)

    fake_broker.emergency_close = fail_first
    engine = ExecutionEngine(fake_broker, FakeNotifier(), SessionLocal)
    with pytest.raises(BrokerError, match="all positions were attempted"):
        engine.emergency_liquidate()
    session.expire_all()
    assert session.get(PositionRecord, first.id).status == "open"
    assert session.get(PositionRecord, second.id).status == "closed"


def test_limit_rounding_respects_order_side():
    assert option_limit_price(5.851, "buy") == pytest.approx(5.86)
    assert option_limit_price(5.859, "sell") == pytest.approx(5.85)
