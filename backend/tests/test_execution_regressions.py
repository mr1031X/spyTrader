from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.broker import BrokerError, OrderState
from backend.config import BotSettings
from backend.database import OrderRecord, PositionRecord, SessionLocal
from backend.execution import ExecutionEngine
from backend.market_data import Quote

from .conftest import FakeNotifier
from .test_trailing_and_execution import make_position


def test_protective_exit_allows_wide_spread(session, daily, fake_broker):
    position = make_position(session, daily)
    quote = Quote(position.contract_symbol, 3.5, 5.0, datetime.now(timezone.utc), "test")
    assert quote.spread_percent > BotSettings().max_spread_percent
    engine = ExecutionEngine(fake_broker, FakeNotifier(), SessionLocal)
    order = engine.request_exit(position.id, "initial_stop", quote, BotSettings())
    assert order.status == "filled"
    session.expire_all()
    assert session.get(PositionRecord, position.id).status == "closed"


def accepted_exit(engine, broker, position):
    broker.exit_status = "accepted"
    original = broker.submit_limit_order

    def accepted(*args, **kwargs):
        state = original(*args, **kwargs)
        if args[2] == "sell":
            state = replace(state, status="accepted", filled_qty=0, filled_avg_price=None)
            broker.orders[-1] = state
            broker.submitted[-1] = state
        return state

    broker.submit_limit_order = accepted
    quote = Quote(position.contract_symbol, 3.5, 3.6, datetime.now(timezone.utc), "test")
    order = engine.request_exit(position.id, "initial_stop", quote, BotSettings())
    with SessionLocal() as session:
        stored = session.get(OrderRecord, order.id)
        stored.created_at = datetime.now(timezone.utc) - timedelta(seconds=30)
        session.commit()
    return order


def test_entry_order_timeout_cancels_only_after_broker_confirmation(session, daily, fake_broker):
    state = OrderState("broker-entry", "client-entry", "SPY260120C00670000", "buy", 1,
                       "accepted", 0, None, 4.0, {})
    fake_broker.orders = [state]
    order = OrderRecord(
        client_order_id="client-entry", broker_order_id="broker-entry", mode="paper",
        symbol=state.symbol, side="buy", position_intent="buy_to_open", qty=1,
        status="accepted", created_at=datetime.now(timezone.utc) - timedelta(minutes=2),
    )
    session.add(order)
    session.commit()

    def cancel(broker_id):
        canceled = replace(fake_broker.orders[0], status="canceled")
        fake_broker.orders[0] = canceled
    fake_broker.cancel_order = cancel
    engine = ExecutionEngine(fake_broker, FakeNotifier(), SessionLocal)
    engine.sync_orders(BotSettings())
    session.expire_all()
    assert session.get(OrderRecord, order.id).status == "canceled"


def test_exit_reprice_waits_for_confirmed_cancellation(session, daily, fake_broker):
    position = make_position(session, daily)
    engine = ExecutionEngine(fake_broker, FakeNotifier(), SessionLocal)
    first = accepted_exit(engine, fake_broker, position)
    quote = Quote(position.contract_symbol, 3.4, 3.7, datetime.now(timezone.utc), "test")

    engine.manage_exit(position.id, quote, BotSettings())
    assert len(fake_broker.submitted) == 1
    assert fake_broker.canceled == [first.broker_order_id]

    def confirm_cancel(broker_id):
        fake_broker.canceled.append(broker_id)
        fake_broker.orders = [replace(item, status="canceled") if item.broker_order_id == broker_id else item
                              for item in fake_broker.orders]
    fake_broker.cancel_order = confirm_cancel
    replacement = engine.manage_exit(position.id, quote, BotSettings())
    assert replacement.id != first.id
    assert len(fake_broker.submitted) == 2
    session.expire_all()
    assert session.get(PositionRecord, position.id).qty == 1


def test_emergency_does_not_overclose_while_exit_cancellation_pending(session, daily, fake_broker):
    position = make_position(session, daily)
    engine = ExecutionEngine(fake_broker, FakeNotifier(), SessionLocal)
    accepted_exit(engine, fake_broker, position)
    with pytest.raises(BrokerError, match="cancellation is not confirmed"):
        engine.emergency_liquidate()
    assert len(fake_broker.submitted) == 1


def test_partial_entry_average_updates_stop_basis(session, daily, fake_broker):
    position = make_position(session, daily)
    session.delete(position)
    session.commit()
    order = OrderRecord(
        client_order_id="partial-entry-stop", mode="paper", symbol="SPY260120C00670000",
        side="buy", position_intent="buy_to_open", qty=2, status="accepted",
    )
    session.add(order)
    session.commit()
    engine = ExecutionEngine(fake_broker, FakeNotifier(), SessionLocal)
    first = OrderState("broker-partial", order.client_order_id, order.symbol, "buy", 2,
                       "partially_filled", 1, 4.0, 4.0, {})
    second = replace(first, status="filled", filled_qty=2, filled_avg_price=5.0)
    engine._apply_order_state(session, order, first)
    engine._apply_order_state(session, order, second)
    session.commit()
    position = session.get(PositionRecord, order.position_id)
    assert position.entry_premium == 5.0
    assert position.stop_threshold == pytest.approx(4.5)
