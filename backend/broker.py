"""Official Alpaca SDK integration with strict paper/live environment isolation."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    AssetStatus, ContractType, OrderSide, PositionIntent, QueryOrderStatus, TimeInForce,
)
from alpaca.trading.requests import (
    ClosePositionRequest, GetCalendarRequest, GetOptionContractsRequest, GetOrdersRequest,
    LimitOrderRequest,
)

from .config import Environment, get_env
from .database import json_safe


class BrokerError(RuntimeError):
    pass


class BrokerConfigurationError(BrokerError):
    pass


class BrokerPermissionError(BrokerError):
    pass


@dataclass(frozen=True)
class AccountState:
    equity: float
    cash: float
    buying_power: float
    options_buying_power: float
    options_level: int
    blocked: bool
    trade_blocked: bool
    raw: dict[str, Any]


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    underlying: str
    option_type: Literal["call", "put"]
    strike: float
    expiration: date
    multiplier: int = 100
    tradable: bool = True
    open_interest: int | None = None


@dataclass(frozen=True)
class OrderState:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: str
    qty: int
    status: str
    filled_qty: int
    filled_avg_price: float | None
    limit_price: float | None
    raw: dict[str, Any]

    @property
    def terminal(self) -> bool:
        return self.status in {"filled", "canceled", "expired", "rejected", "done_for_day"}


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    qty: int
    avg_entry_price: float
    market_value: float | None
    raw: dict[str, Any]


OCC_PATTERN = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def parse_occ_symbol(symbol: str) -> tuple[str, str, float, date]:
    compact = symbol.replace(" ", "").upper()
    match = OCC_PATTERN.match(compact)
    if not match:
        raise ValueError(f"Unsupported option symbol format: {symbol}")
    root, expiry, kind, strike = match.groups()
    expiration = datetime.strptime(expiry, "%y%m%d").date()
    return root, "call" if kind == "C" else "put", int(strike) / 1000, expiration


def new_client_order_id(mode: str, purpose: str) -> str:
    # Alpaca allows up to 48 characters. The persisted ID makes retries idempotent.
    return f"apex-{mode[:1]}-{purpose[:3]}-{uuid.uuid4().hex}"[:48]


class AlpacaBroker:
    def __init__(self, mode: Literal["paper", "live"] = "paper", env: Environment | None = None):
        if mode not in {"paper", "live"}:
            raise ValueError("Invalid broker environment")
        self.mode = mode
        self.env = env or get_env()
        key, secret = self.env.credentials(mode)
        if not key or not secret:
            raise BrokerConfigurationError(f"Alpaca {mode} API credentials are not configured")
        if mode == "live" and not self.env.live_trading_enabled:
            # Credential verification for preflight is allowed; order submission remains gated.
            pass
        self.client = TradingClient(api_key=key, secret_key=secret, paper=(mode == "paper"))

    def _call(self, operation: str, func, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            raise BrokerError(f"Alpaca {operation} failed: {exc}") from exc

    def get_account(self) -> AccountState:
        account = self._call("account lookup", self.client.get_account)
        level = int(getattr(account, "options_approved_level", 0) or 0)
        equity = float(account.equity)
        buying_power = float(account.buying_power)
        options_bp = float(getattr(account, "options_buying_power", buying_power) or buying_power)
        return AccountState(
            equity=equity,
            cash=float(account.cash),
            buying_power=buying_power,
            options_buying_power=options_bp,
            options_level=level,
            blocked=bool(getattr(account, "account_blocked", False)),
            trade_blocked=bool(getattr(account, "trade_blocked", False)),
            raw=json_safe(account.model_dump()),
        )

    def verify_options_permission(self) -> None:
        account = self.get_account()
        if account.blocked or account.trade_blocked:
            raise BrokerPermissionError("Alpaca account is blocked or restricted from trading")
        if account.options_level < 1:
            raise BrokerPermissionError("Alpaca account does not have options trading approval")
        if account.options_buying_power <= 0:
            raise BrokerPermissionError("Account has no options buying power")

    def get_contracts(
        self, underlying: str, option_type: Literal["call", "put"], min_dte: int = 1, max_dte: int = 3,
        today: date | None = None,
    ) -> list[OptionContract]:
        today = today or datetime.now(timezone.utc).date()
        request = GetOptionContractsRequest(
            underlying_symbols=[underlying],
            status=AssetStatus.ACTIVE,
            expiration_date_gte=today + timedelta(days=min_dte),
            expiration_date_lte=today + timedelta(days=max_dte),
            type=ContractType.CALL if option_type == "call" else ContractType.PUT,
            limit=1000,
        )
        response = self._call("contract discovery", self.client.get_option_contracts, request)
        contracts = getattr(response, "option_contracts", response if isinstance(response, list) else [])
        result = []
        for item in contracts:
            result.append(OptionContract(
                symbol=item.symbol,
                underlying=underlying,
                option_type=option_type,
                strike=float(item.strike_price),
                expiration=item.expiration_date,
                multiplier=int(getattr(item, "multiplier", 100) or 100),
                tradable=bool(getattr(item, "tradable", True)),
                open_interest=(int(float(item.open_interest))
                               if getattr(item, "open_interest", None) is not None else None),
            ))
        return result

    def submit_limit_order(
        self, symbol: str, qty: int, side: Literal["buy", "sell"], limit_price: float,
        client_order_id: str, position_intent: Literal["buy_to_open", "sell_to_close"],
    ) -> OrderState:
        if self.mode == "live" and not self.env.live_trading_enabled:
            raise BrokerPermissionError("Live order submission is disabled by server configuration")
        if qty <= 0 or limit_price <= 0:
            raise ValueError("Order quantity and limit price must be positive")
        request = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            limit_price=round(limit_price, 2),
            client_order_id=client_order_id,
            position_intent=(
                PositionIntent.BUY_TO_OPEN if position_intent == "buy_to_open" else PositionIntent.SELL_TO_CLOSE
            ),
        )
        order = self._call("order submission", self.client.submit_order, request)
        return self.normalize_order(order)

    def normalize_order(self, order: Any) -> OrderState:
        return OrderState(
            broker_order_id=str(order.id),
            client_order_id=str(order.client_order_id),
            symbol=order.symbol,
            side=order.side.value if hasattr(order.side, "value") else str(order.side),
            qty=int(float(order.qty or 0)),
            status=order.status.value if hasattr(order.status, "value") else str(order.status),
            filled_qty=int(float(order.filled_qty or 0)),
            filled_avg_price=float(order.filled_avg_price) if order.filled_avg_price is not None else None,
            limit_price=float(order.limit_price) if order.limit_price is not None else None,
            raw=json_safe(order.model_dump()),
        )

    def get_order(self, broker_order_id: str) -> OrderState:
        order = self._call("order lookup", self.client.get_order_by_id, broker_order_id)
        return self.normalize_order(order)

    def list_orders(self, open_only: bool = False) -> list[OrderState]:
        request = GetOrdersRequest(status=QueryOrderStatus.OPEN if open_only else QueryOrderStatus.ALL, limit=500)
        orders = self._call("order history", self.client.get_orders, request)
        return [self.normalize_order(order) for order in orders]

    def find_by_client_id(self, client_order_id: str) -> OrderState | None:
        return next((order for order in self.list_orders(False) if order.client_order_id == client_order_id), None)

    def cancel_order(self, broker_order_id: str) -> None:
        self._call("order cancellation", self.client.cancel_order_by_id, broker_order_id)

    def get_positions(self) -> list[BrokerPosition]:
        positions = self._call("position lookup", self.client.get_all_positions)
        result = []
        for item in positions:
            qty = int(float(item.qty))
            if qty == 0:
                continue
            result.append(BrokerPosition(
                symbol=item.symbol,
                qty=qty,
                avg_entry_price=float(item.avg_entry_price),
                market_value=float(item.market_value) if item.market_value is not None else None,
                raw=json_safe(item.model_dump()),
            ))
        return result

    def emergency_close(self, symbol: str, qty: int | None = None) -> OrderState:
        if self.mode == "live" and not self.env.live_trading_enabled:
            raise BrokerPermissionError("Live order submission is disabled by server configuration")
        options = ClosePositionRequest(qty=str(qty)) if qty is not None else None
        order = self._call("emergency position close", self.client.close_position, symbol, options)
        return self.normalize_order(order)

    def get_clock(self) -> dict[str, Any]:
        clock = self._call("market clock", self.client.get_clock)
        return json_safe(clock.model_dump())

    def get_market_calendar(self, start: date, end: date) -> list[dict[str, Any]]:
        calendar = self._call("market calendar", self.client.get_calendar, GetCalendarRequest(start=start, end=end))
        return [json_safe(item.model_dump()) for item in calendar]
