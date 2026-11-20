"""SQLite persistence for all state that affects order and risk decisions."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, create_engine, event, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session, sessionmaker

from .config import BotSettings, get_env


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SettingsRecord(Base):
    __tablename__ = "settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    payload: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UniverseRecord(Base):
    __tablename__ = "trading_universe"
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    exchange: Mapped[str] = mapped_column(String(16), default="US")


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    equity: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    buying_power: Mapped[float] = mapped_column(Float)
    options_buying_power: Mapped[float | None] = mapped_column(Float, nullable=True)
    daily_pnl: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class SignalRecord(Base):
    __tablename__ = "signals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    mode: Mapped[str] = mapped_column(String(8), index=True)
    underlying: Mapped[str] = mapped_column(String(16), index=True)
    strategy: Mapped[str] = mapped_column(String(40))
    direction: Mapped[str] = mapped_column(String(8))
    score: Mapped[float] = mapped_column(Float)
    qualified: Mapped[bool] = mapped_column(Boolean, default=False)
    regime: Mapped[str] = mapped_column(String(32))
    factors: Mapped[dict] = mapped_column(JSON, default=dict)
    explanation: Mapped[dict] = mapped_column(JSON, default=dict)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class OrderRecord(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    client_order_id: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    side: Mapped[str] = mapped_column(String(8))
    position_intent: Mapped[str] = mapped_column(String(20))
    qty: Mapped[int] = mapped_column(Integer)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    filled_avg_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("positions.id"), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    signal_score: Mapped[float] = mapped_column(Float, default=0)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class FillRecord(Base):
    __tablename__ = "fills"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), index=True)
    broker_order_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(40))
    qty: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    cumulative_qty: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("broker_order_id", "cumulative_qty", name="uq_broker_fill_cumulative"),)


class PositionRecord(Base):
    __tablename__ = "positions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    mode: Mapped[str] = mapped_column(String(8), index=True)
    contract_symbol: Mapped[str] = mapped_column(String(40), index=True)
    underlying: Mapped[str] = mapped_column(String(16), index=True)
    option_type: Mapped[str] = mapped_column(String(8))
    strike: Mapped[float] = mapped_column(Float)
    expiration: Mapped[date] = mapped_column(Date)
    qty: Mapped[int] = mapped_column(Integer)
    initial_qty: Mapped[int] = mapped_column(Integer)
    entry_premium: Mapped[float] = mapped_column(Float)
    current_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    highest_bid: Mapped[float] = mapped_column(Float)
    stop_threshold: Mapped[float] = mapped_column(Float)
    trailing_active: Mapped[bool] = mapped_column(Boolean, default=False)
    exit_order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(24), index=True, default="open")
    strategy: Mapped[str] = mapped_column(String(40))
    score: Mapped[float] = mapped_column(Float, default=0)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees: Mapped[float] = mapped_column(Float, default=0)
    exit_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    protection: Mapped[dict] = mapped_column(JSON, default=dict)
    recovered: Mapped[bool] = mapped_column(Boolean, default=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TradeRecord(Base):
    __tablename__ = "trades"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    position_id: Mapped[str] = mapped_column(String(36), ForeignKey("positions.id"), unique=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    contract_symbol: Mapped[str] = mapped_column(String(40))
    underlying: Mapped[str] = mapped_column(String(16))
    option_type: Mapped[str] = mapped_column(String(8))
    strike: Mapped[float] = mapped_column(Float)
    expiration: Mapped[date] = mapped_column(Date)
    strategy: Mapped[str] = mapped_column(String(40))
    qty: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float, default=0)
    return_percent: Mapped[float] = mapped_column(Float)
    exit_reason: Mapped[str] = mapped_column(String(64))
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exit_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    intended_exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    slippage: Mapped[float | None] = mapped_column(Float, nullable=True)


class DailyState(Base):
    __tablename__ = "daily_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date)
    mode: Mapped[str] = mapped_column(String(8))
    start_equity: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0)
    completed_trades: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_losses: Mapped[int] = mapped_column(Integer, default=0)
    goal_reached: Mapped[bool] = mapped_column(Boolean, default=False)
    circuit_breaker: Mapped[bool] = mapped_column(Boolean, default=False)
    final_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    __table_args__ = (UniqueConstraint("day", "mode", name="uq_daily_mode"),)


class WeeklyState(Base):
    __tablename__ = "weekly_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    week_start: Mapped[date] = mapped_column(Date)
    mode: Mapped[str] = mapped_column(String(8))
    start_equity: Mapped[float] = mapped_column(Float)
    circuit_breaker: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("week_start", "mode", name="uq_weekly_mode"),)


class EventLog(Base):
    __tablename__ = "event_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(12), index=True)
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    message: Mapped[str] = mapped_column(Text)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class BacktestRecord(Base):
    __tablename__ = "backtests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(120))
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


engine = create_engine(get_env().database_url, connect_args={"check_same_thread": False}, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def init_db() -> None:
    Base.metadata.create_all(engine)
    # Small additive migration for databases created by early v1 builds.
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(orders)")}
            if "signal_score" not in columns:
                connection.exec_driver_sql("ALTER TABLE orders ADD COLUMN signal_score FLOAT NOT NULL DEFAULT 0")
    with SessionLocal() as session:
        if not session.get(SettingsRecord, 1):
            env = get_env()
            settings = env.initial_settings()
            session.add(SettingsRecord(id=1, payload=settings.model_dump()))
        for symbol in ("SPY", "QQQ"):
            if not session.get(UniverseRecord, symbol):
                session.add(UniverseRecord(symbol=symbol))
        session.commit()


def get_settings(session: Session) -> BotSettings:
    record = session.get(SettingsRecord, 1)
    if record is None:
        raise RuntimeError("Database is not initialized")
    return BotSettings.model_validate(record.payload)


def save_settings(session: Session, settings: BotSettings) -> None:
    record = session.get(SettingsRecord, 1)
    if record is None:
        record = SettingsRecord(id=1)
        session.add(record)
    record.payload = settings.model_dump()
    record.updated_at = utcnow()
    session.commit()


def log_event(session: Session, level: str, event_type: str, message: str, **context: Any) -> EventLog:
    item = EventLog(level=level, event_type=event_type, message=message, context=context)
    session.add(item)
    session.commit()
    return item


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)
