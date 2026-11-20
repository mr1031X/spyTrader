import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

os.environ["DATABASE_URL"] = "sqlite:////tmp/apex-pytest.db"
os.environ["ALPACA_PAPER_API_KEY"] = ""
os.environ["ALPACA_PAPER_SECRET_KEY"] = ""

import pytest

from backend.database import Base, DailyState, WeeklyState, engine, SessionLocal, init_db
from backend.broker import AccountState, OrderState


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    init_db()
    yield


@pytest.fixture
def session():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def daily(session):
    trading_day = datetime.now(ZoneInfo("America/New_York")).date()
    item = DailyState(day=trading_day, mode="paper", start_equity=10_000)
    weekly = WeeklyState(week_start=trading_day, mode="paper", start_equity=10_000)
    session.add_all([item, weekly])
    session.commit()
    return item


class FakeBroker:
    mode = "paper"

    def __init__(self):
        self.submitted = []
        self.canceled = []
        self.positions = []
        self.orders = []
        self.exit_status = "filled"
        self.fill_price = None

    def submit_limit_order(self, symbol, qty, side, limit_price, client_order_id, position_intent):
        status = "filled" if side == "sell" else "accepted"
        filled = qty if side == "sell" and self.exit_status == "filled" else 0
        state = OrderState(
            broker_order_id=f"broker-{len(self.submitted) + 1}", client_order_id=client_order_id,
            symbol=symbol, side=side, qty=qty, status=status, filled_qty=filled,
            filled_avg_price=(self.fill_price or limit_price) if filled else None,
            limit_price=limit_price, raw={},
        )
        self.submitted.append(state)
        self.orders.append(state)
        return state

    def get_order(self, broker_order_id):
        return next(order for order in self.orders if order.broker_order_id == broker_order_id)

    def find_by_client_id(self, client_order_id):
        return next((order for order in self.orders if order.client_order_id == client_order_id), None)

    def cancel_order(self, broker_order_id):
        self.canceled.append(broker_order_id)

    def list_orders(self, open_only=False):
        return self.orders

    def get_positions(self):
        return self.positions

    def emergency_close(self, symbol, qty=None):
        return self.submit_limit_order(symbol, qty or 1, "sell", 1.0, f"emergency-{symbol}", "sell_to_close")


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def send(self, subject, message, enabled=True):
        if enabled:
            self.messages.append((subject, message))


@pytest.fixture
def fake_broker():
    return FakeBroker()


@pytest.fixture
def account():
    return AccountState(10_000, 10_000, 10_000, 10_000, 2, False, False, {})
