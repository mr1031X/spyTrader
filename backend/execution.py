"""Idempotent order execution, confirmed fills, exits, and reconciliation."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .broker import (
    AlpacaBroker, BrokerError, BrokerPosition, OrderState, new_client_order_id, parse_occ_symbol,
)
from .config import BotSettings
from .database import (
    DailyState, EventLog, FillRecord, OrderRecord, PositionRecord, SessionLocal, TradeRecord,
    log_event, utcnow,
)
from .market_data import Quote
from .notifications import Notifier
from .option_selector import SelectedOption
from .risk_manager import PositionSize
from .signal_engine import ScoredSignal
from .trailing_stop import initial_stop, option_limit_price, protection_snapshot

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
ACTIVE_ORDER_STATES = {"new", "accepted", "pending_new", "partially_filled", "held", "submitting", "unknown", "pending_cancel"}


class ExecutionEngine:
    def __init__(self, broker: AlpacaBroker, notifier: Notifier | None = None, session_factory=SessionLocal):
        self.broker = broker
        self.notifier = notifier or Notifier()
        self.session_factory = session_factory

    def submit_entry(self, signal: ScoredSignal, selected: SelectedOption, size: PositionSize,
                     settings: BotSettings) -> OrderRecord:
        if size.quantity < 1:
            raise ValueError("Position size is below one whole contract")
        quote = selected.snapshot.quote
        error = quote.validation_error(max_age_seconds=settings.max_quote_age_seconds,
                                       max_spread_percent=settings.max_spread_percent)
        if error:
            raise ValueError(error)
        # Aggressive but bounded: three quarters through the spread, never above ask.
        limit = option_limit_price(min(quote.ask, quote.midpoint + (quote.ask - quote.bid) * 0.75))
        if (limit - quote.midpoint) / quote.midpoint * 100 > settings.max_slippage_percent:
            raise ValueError("Estimated entry slippage exceeds configured maximum")
        client_id = new_client_order_id(self.broker.mode, "entry")
        with self.session_factory() as session:
            duplicate = session.scalar(select(OrderRecord).where(OrderRecord.client_order_id == client_id))
            if duplicate:
                return duplicate
            order = OrderRecord(
                client_order_id=client_id, mode=self.broker.mode, symbol=selected.snapshot.contract.symbol,
                side="buy", position_intent="buy_to_open", qty=size.quantity, limit_price=limit,
                status="submitting", reason=signal.candidate.strategy, signal_score=signal.score,
            )
            session.add(order)
            session.commit()
            order_id = order.id
        try:
            broker_order = self.broker.submit_limit_order(
                selected.snapshot.contract.symbol, size.quantity, "buy", limit, client_id, "buy_to_open",
            )
        except Exception as exc:
            with self.session_factory() as session:
                order = session.get(OrderRecord, order_id)
                order.status = "unknown"  # Never blind-retry: reconciliation must find the client ID.
                order.reason = f"Submission outcome unknown: {exc}"
                session.commit()
                log_event(session, "critical", "order_unknown", str(exc), client_order_id=client_id)
            raise
        with self.session_factory() as session:
            order = session.get(OrderRecord, order_id)
            self._apply_order_state(session, order, broker_order)
            session.commit()
            log_event(session, "info", "entry_submitted", "Buy-to-open limit order submitted",
                      symbol=order.symbol, qty=order.qty, limit_price=limit)
            return order

    def sync_orders(self, settings: BotSettings | None = None) -> None:
        with self.session_factory() as session:
            orders = session.scalars(select(OrderRecord).where(
                OrderRecord.mode == self.broker.mode, OrderRecord.status.in_(ACTIVE_ORDER_STATES)
            )).all()
            identities = [(order.id, order.broker_order_id, order.client_order_id) for order in orders]
        for order_id, broker_id, client_id in identities:
            try:
                state = self.broker.get_order(broker_id) if broker_id else self.broker.find_by_client_id(client_id)
                if state is None:
                    continue
                with self.session_factory() as session:
                    order = session.get(OrderRecord, order_id)
                    if not order:
                        continue
                    self._apply_order_state(session, order, state)
                    session.commit()
                    created = order.created_at
                    intent = order.position_intent
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                timed_out = (
                    settings is not None and intent == "buy_to_open" and state.status in ACTIVE_ORDER_STATES
                    and utcnow() - created >= timedelta(seconds=settings.entry_order_timeout_seconds)
                )
                if timed_out and broker_id:
                    self.broker.cancel_order(broker_id)
                    confirmed = self.broker.get_order(broker_id)
                    with self.session_factory() as session:
                        order = session.get(OrderRecord, order_id)
                        self._apply_order_state(session, order, confirmed)
                        session.commit()
                        if confirmed.status in ACTIVE_ORDER_STATES:
                            log_event(session, "warning", "entry_cancel_pending",
                                      "Opening order timed out; cancellation is not yet confirmed",
                                      client_order_id=client_id)
                        else:
                            log_event(session, "info", "entry_timeout_canceled",
                                      "Stale opening order canceled; a new signal must pass revalidation",
                                      client_order_id=client_id)
            except BrokerError as exc:
                logger.error("Order sync failed for %s: %s", client_id, exc)

    def _apply_order_state(self, session, order: OrderRecord, state: OrderState) -> None:
        previous_filled = order.filled_qty
        previous_average = order.filled_avg_price
        order.broker_order_id = state.broker_order_id
        order.status = state.status
        order.filled_qty = state.filled_qty
        order.filled_avg_price = state.filled_avg_price
        order.raw = state.raw
        order.updated_at = utcnow()
        delta = max(0, state.filled_qty - previous_filled)
        if delta and state.filled_avg_price is not None:
            # Broker callbacks expose cumulative quantity and average price. Recover
            # this increment's average instead of mislabeling it with the cumulative average.
            fill_price = state.filled_avg_price
            if previous_filled and previous_average is not None:
                fill_price = (
                    state.filled_avg_price * state.filled_qty - previous_average * previous_filled
                ) / delta
            session.add(FillRecord(
                order_id=order.id, broker_order_id=state.broker_order_id, symbol=order.symbol,
                qty=delta, price=fill_price, cumulative_qty=state.filled_qty,
            ))
            session.flush()
            if order.position_intent == "buy_to_open":
                self._apply_entry_fill(session, order, state)
            elif order.position_intent == "sell_to_close":
                self._apply_exit_fill(session, order, state, previous_filled)
        if order.position_intent == "sell_to_close" and state.terminal and state.filled_qty < order.qty:
            self._release_incomplete_exit(session, order)

    def _release_incomplete_exit(self, session, order: OrderRecord) -> None:
        """Allow a fresh protective exit after rejection, expiry, or cancellation."""
        position = session.get(PositionRecord, order.position_id) if order.position_id else None
        if (position and position.qty > 0 and position.status != "closed"
                and position.exit_order_id == order.id):
            position.exit_order_id = None
            position.status = "open"
            position.updated_at = utcnow()

    def _apply_entry_fill(self, session, order: OrderRecord, state: OrderState) -> None:
        position = session.get(PositionRecord, order.position_id) if order.position_id else None
        underlying, kind, strike, expiration = parse_occ_symbol(order.symbol)
        if position is None:
            with self.session_factory() as config_session:
                from .database import get_settings
                settings = get_settings(config_session)
            position = PositionRecord(
                mode=self.broker.mode, contract_symbol=order.symbol, underlying=underlying,
                option_type=kind, strike=strike, expiration=expiration, qty=state.filled_qty,
                initial_qty=state.filled_qty, entry_premium=state.filled_avg_price,
                highest_bid=state.filled_avg_price,
                stop_threshold=initial_stop(state.filled_avg_price, settings.initial_stop_loss_percent),
                strategy=order.reason or "recovered", score=order.signal_score,
                status="open", protection=protection_snapshot(settings),
            )
            session.add(position)
            session.flush()
            order.position_id = position.id
            self.notifier.send("Apex: entry filled", f"{order.symbol} × {state.filled_qty} at ${state.filled_avg_price:.2f}",
                               enabled=settings.notifications_enabled)
        else:
            position.qty = state.filled_qty
            position.initial_qty = state.filled_qty
            position.entry_premium = state.filled_avg_price
            position.highest_bid = max(position.highest_bid, state.filled_avg_price)
            stop_percent = float((position.protection or {}).get("initial_stop_loss_percent", 10))
            position.stop_threshold = max(
                position.stop_threshold, initial_stop(state.filled_avg_price, stop_percent),
            )
        position.updated_at = utcnow()

    def _apply_exit_fill(self, session, order: OrderRecord, state: OrderState, previous_filled: int) -> None:
        position = session.get(PositionRecord, order.position_id) if order.position_id else None
        if not position:
            log_event(session, "critical", "orphan_exit_fill", "Exit fill has no local position",
                      broker_order_id=state.broker_order_id)
            return
        delta = state.filled_qty - previous_filled
        position.qty = max(0, position.qty - delta)
        position.updated_at = utcnow()
        if position.qty == 0 and state.terminal:
            self._close_position(session, position, order.reason or "exit")

    def _close_position(self, session, position: PositionRecord, reason: str) -> None:
        if position.status == "closed":
            return
        orders = session.scalars(select(OrderRecord).where(
            OrderRecord.position_id == position.id, OrderRecord.position_intent == "sell_to_close"
        )).all()
        proceeds = sum((order.filled_avg_price or 0) * order.filled_qty for order in orders)
        filled = sum(order.filled_qty for order in orders)
        if filled < position.initial_qty:
            logger.error("Position %s has incomplete exit fill accounting", position.id)
            return
        exit_price = proceeds / filled
        from .database import get_settings
        settings = get_settings(session)
        fees = settings.transaction_fee_per_contract * (position.initial_qty + filled)
        realized = (exit_price - position.entry_premium) * 100 * position.initial_qty - fees
        if reason in {"initial_stop", "trailing_stop"}:
            intended = position.stop_threshold
        elif reason == "fixed_profit_target":
            target_percent = float((position.protection or {}).get("fixed_profit_target_percent", 50))
            intended = position.entry_premium * (1 + target_percent / 100)
        else:
            intended = None
        position.status = "closed"
        position.qty = 0
        position.realized_pnl = realized
        position.fees = fees
        position.exit_reason = reason
        position.closed_at = utcnow()
        trade = TradeRecord(
            position_id=position.id, mode=position.mode, contract_symbol=position.contract_symbol,
            underlying=position.underlying, option_type=position.option_type, strike=position.strike,
            expiration=position.expiration, strategy=position.strategy, qty=position.initial_qty,
            entry_price=position.entry_premium, exit_price=exit_price, realized_pnl=realized,
            fees=fees, return_percent=(exit_price / position.entry_premium - 1) * 100,
            exit_reason=reason, entry_time=position.opened_at, exit_time=position.closed_at,
            intended_exit_price=intended,
            slippage=exit_price - intended if intended is not None else None,
        )
        session.add(trade)
        closed_at = position.closed_at
        if closed_at.tzinfo is None:
            closed_at = closed_at.replace(tzinfo=timezone.utc)
        day = closed_at.astimezone(ET).date()
        daily = session.scalar(select(DailyState).where(DailyState.day == day, DailyState.mode == position.mode))
        if daily:
            daily.completed_trades += 1
            daily.realized_pnl += realized
            daily.consecutive_losses = daily.consecutive_losses + 1 if realized < 0 else 0
            if settings.daily_profit_goal > 0 and daily.realized_pnl >= settings.daily_profit_goal:
                daily.goal_reached = True
        session.flush()
        self.notifier.send("Apex: position closed",
                           f"{position.contract_symbol}: ${realized:.2f} net P&L ({reason})",
                           enabled=settings.notifications_enabled)

    def request_exit(self, position_id: str, reason: str, quote: Quote, settings: BotSettings) -> OrderRecord | None:
        error = quote.validation_error(max_age_seconds=settings.max_quote_age_seconds,
                                       max_spread_percent=None)
        if error:
            raise ValueError(error)
        with self.session_factory() as session:
            position = session.get(PositionRecord, position_id)
            if not position or position.qty <= 0 or position.status == "closed":
                return None
            if position.exit_order_id:
                existing = session.get(OrderRecord, position.exit_order_id)
                if existing and existing.status in ACTIVE_ORDER_STATES:
                    return existing
            client_id = new_client_order_id(self.broker.mode, "exit")
            order = OrderRecord(
                client_order_id=client_id, mode=self.broker.mode, symbol=position.contract_symbol,
                side="sell", position_intent="sell_to_close", qty=position.qty,
                limit_price=option_limit_price(quote.bid, "sell"), status="submitting", reason=reason,
                position_id=position.id,
            )
            session.add(order)
            session.flush()
            position.exit_order_id = order.id
            position.status = "closing"
            session.commit()
            order_id = order.id
            symbol, qty, limit = position.contract_symbol, position.qty, order.limit_price
        try:
            state = self.broker.submit_limit_order(symbol, qty, "sell", limit, client_id, "sell_to_close")
        except Exception as exc:
            with self.session_factory() as session:
                order = session.get(OrderRecord, order_id)
                order.status = "unknown"
                session.commit()
                log_event(session, "critical", "exit_unknown", str(exc), position_id=position_id)
            raise
        with self.session_factory() as session:
            order = session.get(OrderRecord, order_id)
            self._apply_order_state(session, order, state)
            session.commit()
            return order

    def manage_exit(self, position_id: str, quote: Quote, settings: BotSettings) -> OrderRecord | None:
        error = quote.validation_error(max_age_seconds=settings.max_quote_age_seconds, max_spread_percent=None)
        if error:
            raise ValueError(error)
        with self.session_factory() as session:
            position = session.get(PositionRecord, position_id)
            order = session.get(OrderRecord, position.exit_order_id) if position and position.exit_order_id else None
            if not position or not order or order.status not in ACTIVE_ORDER_STATES:
                return order
            created = order.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if utcnow() - created < timedelta(seconds=settings.exit_order_reprice_seconds):
                return order
            broker_id = order.broker_order_id
        if not broker_id:
            return order
        state = self.broker.get_order(broker_id)
        with self.session_factory() as session:
            order = session.get(OrderRecord, order.id)
            self._apply_order_state(session, order, state)
            session.commit()
        if state.status in ACTIVE_ORDER_STATES:
            self.broker.cancel_order(broker_id)
            state = self.broker.get_order(broker_id)
            with self.session_factory() as session:
                order = session.get(OrderRecord, order.id)
                self._apply_order_state(session, order, state)
                session.commit()
            if state.status in ACTIVE_ORDER_STATES:
                logger.warning("Protective exit %s is awaiting confirmed cancellation", order.client_order_id)
                return order
        with self.session_factory() as session:
            position = session.get(PositionRecord, position_id)
            remaining = position.qty if position else 0
            reason = order.reason or "protective_exit"
        if remaining > 0:
            replacement = self.request_exit(position_id, reason, quote, settings)
            if replacement:
                with self.session_factory() as session:
                    log_event(session, "warning", "exit_repriced",
                              "Protective exit replaced after confirmed cancellation",
                              position_id=position_id, previous_broker_order_id=broker_id,
                              replacement_client_order_id=replacement.client_order_id)
            return replacement
        return order

    def cancel_opening_orders(self) -> None:
        with self.session_factory() as session:
            orders = session.scalars(select(OrderRecord).where(
                OrderRecord.mode == self.broker.mode, OrderRecord.position_intent == "buy_to_open",
                OrderRecord.status.in_(ACTIVE_ORDER_STATES), OrderRecord.broker_order_id.is_not(None),
            )).all()
            ids = [order.broker_order_id for order in orders]
        for broker_id in ids:
            try:
                self.broker.cancel_order(broker_id)
            except BrokerError as exc:
                logger.error("Could not cancel opening order %s: %s", broker_id, exc)
        self.sync_orders()

    def emergency_liquidate(self) -> dict[str, Any]:
        self.cancel_opening_orders()
        self.sync_orders()
        failures: list[str] = []
        blocked_symbols: set[str] = set()
        with self.session_factory() as session:
            positions = session.scalars(select(PositionRecord).where(
                PositionRecord.mode == self.broker.mode, PositionRecord.status.in_(["open", "closing"])
            )).all()
            exit_data = []
            for position in positions:
                if not position.exit_order_id:
                    continue
                exit_order = session.get(OrderRecord, position.exit_order_id)
                if exit_order and exit_order.status in ACTIVE_ORDER_STATES:
                    exit_data.append((exit_order.id, position.contract_symbol, exit_order.broker_order_id,
                                      exit_order.client_order_id))
        for order_id, symbol, broker_id, client_id in exit_data:
            try:
                if not broker_id:
                    state = self.broker.find_by_client_id(client_id)
                    if state:
                        broker_id = state.broker_order_id
                        with self.session_factory() as session:
                            order = session.get(OrderRecord, order_id)
                            self._apply_order_state(session, order, state)
                            session.commit()
                if not broker_id:
                    blocked_symbols.add(symbol)
                    failures.append(f"{symbol}: existing exit submission remains unresolved")
                    continue
                self.broker.cancel_order(broker_id)
                confirmed = self.broker.get_order(broker_id)
                with self.session_factory() as session:
                    order = session.get(OrderRecord, order_id)
                    self._apply_order_state(session, order, confirmed)
                    session.commit()
                if confirmed.status in ACTIVE_ORDER_STATES:
                    blocked_symbols.add(symbol)
                    failures.append(f"{symbol}: existing exit cancellation is not confirmed")
            except Exception as exc:
                blocked_symbols.add(symbol)
                failures.append(f"{symbol}: could not confirm existing exit cancellation: {exc}")
        self.sync_orders()
        with self.session_factory() as session:
            positions = session.scalars(select(PositionRecord).where(
                PositionRecord.mode == self.broker.mode, PositionRecord.status.in_(["open", "closing"])
            )).all()
            position_data = [(p.id, p.contract_symbol, p.qty) for p in positions]
        submitted = []
        for position_id, symbol, qty in position_data:
            if qty <= 0 or symbol in blocked_symbols:
                continue
            try:
                state = self.broker.emergency_close(symbol, qty)
            except Exception as exc:
                failures.append(f"{symbol}: {exc}")
                logger.error("Emergency close failed for %s: %s", symbol, exc)
                continue
            with self.session_factory() as session:
                position = session.get(PositionRecord, position_id)
                order = OrderRecord(
                    client_order_id=state.client_order_id, mode=self.broker.mode, symbol=symbol,
                    side="sell", position_intent="sell_to_close", qty=qty, status="submitting",
                    position_id=position_id, reason="emergency_liquidation",
                )
                session.add(order)
                session.flush()
                position.exit_order_id = order.id
                position.status = "closing"
                self._apply_order_state(session, order, state)
                session.commit()
            submitted.append(state.broker_order_id)
        if failures:
            raise BrokerError("Some emergency closes failed after all positions were attempted: " + "; ".join(failures))
        return {"submitted_orders": submitted, "position_count": len(submitted)}

    def reconcile(self, settings: BotSettings) -> dict[str, Any]:
        broker_positions = {p.symbol: p for p in self.broker.get_positions() if _is_allowed_option(p.symbol, settings.symbols)}
        broker_orders = self.broker.list_orders(False)
        by_client = {order.client_order_id: order for order in broker_orders}
        unexpected = []
        recovered = []
        aged_out = []
        now = utcnow()
        with self.session_factory() as session:
            local_orders = session.scalars(select(OrderRecord).where(OrderRecord.mode == self.broker.mode)).all()
            for order in local_orders:
                state = by_client.get(order.client_order_id)
                if state and (order.status in ACTIVE_ORDER_STATES or not order.broker_order_id):
                    self._apply_order_state(session, order, state)
                elif (order.status in {"unknown", "submitting"} and not order.broker_order_id):
                    updated = order.updated_at or order.created_at
                    if updated.tzinfo is None:
                        updated = updated.replace(tzinfo=timezone.utc)
                    if now - updated > timedelta(minutes=2):
                        # A crash before submission and an ambiguous timeout are not
                        # distinguishable locally. Age out only after repeated broker
                        # history checks find no matching client order ID.
                        order.status = "error"
                        order.reason = "No matching broker order after reconciliation timeout"
                        order.updated_at = now
                        if order.position_intent == "sell_to_close":
                            self._release_incomplete_exit(session, order)
                        aged_out.append(order.client_order_id)
            local_positions = session.scalars(select(PositionRecord).where(
                PositionRecord.mode == self.broker.mode, PositionRecord.status.in_(["open", "closing"])
            )).all()
            local_by_symbol = {p.contract_symbol: p for p in local_positions}
            for symbol, position in local_by_symbol.items():
                actual = broker_positions.get(symbol)
                if actual is None:
                    open_broker_order = any(o.symbol == symbol and o.status in ACTIVE_ORDER_STATES for o in broker_orders)
                    if not open_broker_order:
                        position.status = "closed"
                        position.qty = 0
                        position.closed_at = now
                        position.exit_reason = "broker_reconciliation_unpriced"
                        position.realized_pnl = None
                        unexpected.append(symbol)
                        daily = session.scalar(select(DailyState).where(
                            DailyState.day == now.astimezone(ET).date(), DailyState.mode == position.mode))
                        if daily:
                            daily.completed_trades += 1
                            # The accounting is incomplete, so new entries pause for
                            # operator review rather than assuming a fill or P&L.
                            daily.circuit_breaker = True
                elif actual.qty != position.qty:
                    # Broker exposure and average cost are authoritative for the
                    # remaining recovered position; prior fills remain in the ledger.
                    position.qty = actual.qty
                    position.initial_qty = actual.qty
                    position.entry_premium = actual.avg_entry_price
                    position.highest_bid = max(position.highest_bid, actual.avg_entry_price)
                    position.stop_threshold = max(
                        position.stop_threshold, initial_stop(actual.avg_entry_price, settings.initial_stop_loss_percent))
                    position.recovered = True
                    position.updated_at = now
                    unexpected.append(symbol)
                    daily = session.scalar(select(DailyState).where(
                        DailyState.day == now.astimezone(ET).date(), DailyState.mode == position.mode))
                    if daily:
                        daily.circuit_breaker = True
            for symbol, actual in broker_positions.items():
                if symbol in local_by_symbol:
                    continue
                underlying, kind, strike, expiration = parse_occ_symbol(symbol)
                position = PositionRecord(
                    mode=self.broker.mode, contract_symbol=symbol, underlying=underlying, option_type=kind,
                    strike=strike, expiration=expiration, qty=actual.qty, initial_qty=actual.qty,
                    entry_premium=actual.avg_entry_price, highest_bid=actual.avg_entry_price,
                    stop_threshold=initial_stop(actual.avg_entry_price, settings.initial_stop_loss_percent),
                    strategy="recovered_broker_position", protection=protection_snapshot(settings), recovered=True,
                )
                session.add(position)
                recovered.append(symbol)
            session.commit()
            if unexpected or recovered or aged_out:
                log_event(session, "warning", "reconciliation",
                          "Broker/local state reconciled; unpriced closures require operator review",
                          unexpected=unexpected, recovered=recovered, aged_out=aged_out)
        return {"unexpected": unexpected, "recovered": recovered, "aged_out": aged_out, "reconciled": True}


def _is_allowed_option(symbol: str, underlyings: list[str]) -> bool:
    try:
        underlying, _, _, _ = parse_occ_symbol(symbol)
        return underlying in underlyings
    except ValueError:
        return False
