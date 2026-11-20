"""Autonomous trading runtime, independent of browser and HTTP request lifecycles."""
from __future__ import annotations

import asyncio
import fcntl
import logging
import threading
import time
from dataclasses import asdict
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any

from alpaca.data.timeframe import TimeFrame
from sqlalchemy import func, select
from zoneinfo import ZoneInfo

from .broker import AlpacaBroker, BrokerConfigurationError, BrokerError
from .config import BotSettings, DATA_DIR, Environment, get_env
from .database import (
    AccountSnapshot, DailyState, EventLog, OrderRecord, PositionRecord, SessionLocal, SettingsRecord,
    TradeRecord, WeeklyState, get_settings, log_event, save_settings, utcnow,
)
from .execution import ACTIVE_ORDER_STATES, ExecutionEngine
from .indicators import closed_candles, resample_candles
from .market_data import AlpacaStreamManager, DataEntitlementError, MarketDataError, MarketDataService
from .ml_filter import MLSignalFilter, feature_vector
from .news import NewsService
from .notifications import Notifier
from .option_selector import OptionSelector
from .risk_manager import RiskManager, week_start
from .signal_engine import SignalEngine
from .strategies import StrategyEngine
from .trailing_stop import apply_decision, evaluate_trailing

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


class TradingBot:
    def __init__(self, env: Environment | None = None, session_factory=SessionLocal):
        self.env = env or get_env()
        self.session_factory = session_factory
        self.notifier = Notifier(self.env)
        self.strategy = StrategyEngine()
        self.scorer = SignalEngine()
        self.risk = RiskManager()
        self.news = NewsService(self.env)
        self.ml_filter = MLSignalFilter()
        self._lock = threading.RLock()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._lock_file = None
        self._broker: AlpacaBroker | None = None
        self._market: MarketDataService | None = None
        self._execution: ExecutionEngine | None = None
        self._streams: AlpacaStreamManager | None = None
        self._mode = "paper"
        self._entries_enabled = False
        self._runtime_active = False
        self._state = "setup_required"
        self._last_error: str | None = None
        self._last_account: dict[str, Any] | None = None
        self._clock: dict[str, Any] | None = None
        self._last_scan = 0.0
        self._last_reconcile = 0.0
        self._reconciled = False
        self._data_ready = False
        self._last_snapshot = 0.0
        self._last_event_at: dict[str, float] = {}
        self._eod_liquidation_date: date | None = None
        self._last_market_cache: dict[str, Any] = {}

    async def start_runtime(self) -> None:
        if self._task:
            return
        # Lease prevents two web workers from managing the same account.
        self._lock_file = (DATA_DIR / "engine.lock").open("w")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_file.write(str(threading.get_ident()))
            self._lock_file.flush()
        except BlockingIOError as exc:
            self._lock_file.close()
            self._lock_file = None
            raise RuntimeError("Another trading-engine instance holds the account lease") from exc
        self._runtime_active = True
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="apex-trading-engine")

    async def stop_runtime(self) -> None:
        self._runtime_active = False
        self._entries_enabled = False
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._streams:
            self._streams.stop()
        if self._lock_file:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self._tick)
                self._last_error = None
            except (BrokerConfigurationError, DataEntitlementError) as exc:
                self._set_state("setup_required", str(exc))
                self._data_ready = False
            except Exception as exc:
                logger.exception("Trading engine tick failed")
                self._set_state("error", str(exc))
                self._data_ready = False
                self._throttled_event("runtime_error", "critical", "runtime_error", str(exc), interval=30)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    def _set_state(self, state: str, error: str | None = None) -> None:
        with self._lock:
            self._state = state
            self._last_error = error

    def _ensure_clients(self, settings: BotSettings) -> None:
        provider_key = (settings.trading_mode, settings.options_provider)
        current_key = (self._mode, self._market.settings.options_provider if self._market else None)
        if self._broker is not None and provider_key == current_key:
            return
        if self._streams:
            self._streams.stop()
            self._streams = None
        self._mode = settings.trading_mode
        self._broker = AlpacaBroker(self._mode, self.env)
        self._market = MarketDataService(settings, self.env)
        self._execution = ExecutionEngine(self._broker, self.notifier, self.session_factory)
        self._reconciled = False
        self._last_reconcile = 0
        self._entries_enabled = False if settings.trading_mode == "live" and not settings.live_trading_enabled else self._entries_enabled
        self._streams = AlpacaStreamManager(self._mode, self.env)
        self._streams.start(settings.symbols)

    def _tick(self) -> None:
        with self.session_factory() as session:
            settings = get_settings(session)
        if not self.env.credentials_configured(settings.trading_mode):
            raise BrokerConfigurationError(f"Add Alpaca {settings.trading_mode} API credentials to .env")
        self._ensure_clients(settings)
        assert self._broker and self._market and self._execution
        account = self._broker.get_account()
        clock = self._broker.get_clock()
        now = datetime.now(timezone.utc)
        self._last_account = asdict(account)
        self._clock = clock
        self._ensure_periods(settings, account.equity, now)
        if not self._reconciled or time.monotonic() - self._last_reconcile > 60:
            self._execution.reconcile(settings)
            self._reconciled = True
            self._last_reconcile = time.monotonic()
        self._execution.sync_orders()
        self._manage_positions(settings)
        self._check_circuits(settings, account.equity, now)
        self._handle_eod(settings, clock, now)
        if time.monotonic() - self._last_snapshot >= 30:
            with self.session_factory() as session:
                daily = self._daily(session, settings.trading_mode, now)
                session.add(AccountSnapshot(mode=settings.trading_mode, equity=account.equity, cash=account.cash,
                                            buying_power=account.buying_power,
                                            options_buying_power=account.options_buying_power,
                                            daily_pnl=account.equity - daily.start_equity))
                session.commit()
            self._last_snapshot = time.monotonic()
        if self._state not in {"error", "circuit_breaker"}:
            self._set_state("running" if self._entries_enabled else "paused")
        if self._entries_enabled and clock.get("is_open") and time.monotonic() - self._last_scan >= 20:
            self._scan_entries(settings, account, now)
            self._last_scan = time.monotonic()

    def _ensure_periods(self, settings: BotSettings, equity: float, now: datetime) -> None:
        day = now.astimezone(ET).date()
        start_week = week_start(day)
        with self.session_factory() as session:
            daily = session.scalar(select(DailyState).where(DailyState.day == day, DailyState.mode == settings.trading_mode))
            if not daily:
                session.add(DailyState(day=day, mode=settings.trading_mode, start_equity=equity))
            weekly = session.scalar(select(WeeklyState).where(WeeklyState.week_start == start_week,
                                                              WeeklyState.mode == settings.trading_mode))
            if not weekly:
                session.add(WeeklyState(week_start=start_week, mode=settings.trading_mode, start_equity=equity))
            session.commit()

    def _daily(self, session, mode: str, now: datetime) -> DailyState:
        day = now.astimezone(ET).date()
        return session.scalar(select(DailyState).where(DailyState.day == day, DailyState.mode == mode))

    def _manage_positions(self, settings: BotSettings) -> None:
        assert self._market and self._execution
        with self.session_factory() as session:
            positions = session.scalars(select(PositionRecord).where(
                PositionRecord.mode == settings.trading_mode, PositionRecord.status.in_(["open", "closing"])
            )).all()
            ids = [p.id for p in positions]
        for position_id in ids:
            with self.session_factory() as session:
                position = session.get(PositionRecord, position_id)
                if not position:
                    continue
                from .broker import OptionContract
                contract = OptionContract(position.contract_symbol, position.underlying, position.option_type,
                                          position.strike, position.expiration)
                exit_pending = False
                if position.exit_order_id:
                    exit_order = session.get(OrderRecord, position.exit_order_id)
                    exit_pending = bool(exit_order and exit_order.status in ACTIVE_ORDER_STATES)
            try:
                snapshot = self._market.option_snapshot(contract)
                self._market.validate_quote(snapshot.quote)
                self._data_ready = True
                with self.session_factory() as session:
                    position = session.get(PositionRecord, position_id)
                    position.current_bid = snapshot.quote.bid
                    position.current_ask = snapshot.quote.ask
                    decision = evaluate_trailing(position, snapshot.quote.bid)
                    activated = decision.trailing_active and not position.trailing_active
                    apply_decision(position, decision)
                    position.updated_at = utcnow()
                    session.commit()
                    reason = decision.reason
                if activated:
                    self.notifier.send("Apex: trailing activated", f"{contract.symbol}; stop ${decision.stop_threshold:.2f}",
                                       enabled=settings.notifications_enabled)
                if decision.should_exit and not exit_pending:
                    self._execution.request_exit(position_id, reason, snapshot.quote, settings)
            except (MarketDataError, BrokerError, ValueError) as exc:
                self._data_ready = False
                self._throttled_event(f"quote_{position_id}", "warning", "position_data_blocked",
                                      f"Position protection is waiting for valid real-time data: {exc}", interval=15,
                                      position_id=position_id)

    def _check_circuits(self, settings: BotSettings, equity: float, now: datetime) -> None:
        day = now.astimezone(ET).date()
        start_week = week_start(day)
        with self.session_factory() as session:
            daily = self._daily(session, settings.trading_mode, now)
            weekly = session.scalar(select(WeeklyState).where(WeeklyState.week_start == start_week,
                                                              WeeklyState.mode == settings.trading_mode))
            if not daily or not weekly:
                return
            prior_realized = session.scalar(select(func.coalesce(func.sum(TradeRecord.realized_pnl), 0)).where(
                TradeRecord.mode == settings.trading_mode,
                TradeRecord.exit_time >= datetime.combine(start_week, dt_time.min, tzinfo=ET).astimezone(timezone.utc),
                TradeRecord.exit_time < datetime.combine(day, dt_time.min, tzinfo=ET).astimezone(timezone.utc),
            ))
            daily_trigger, weekly_trigger = self.risk.circuit_breakers(settings, daily, weekly, equity, float(prior_realized))
            if not daily_trigger and not weekly_trigger:
                return
            new_daily_trigger = daily_trigger and not daily.circuit_breaker
            new_weekly_trigger = weekly_trigger and not weekly.circuit_breaker
            daily.circuit_breaker = daily_trigger
            weekly.circuit_breaker = weekly_trigger
            session.commit()
        self._entries_enabled = False
        message = "Daily loss limit breached" if daily_trigger else "Weekly loss limit breached"
        self._set_state("circuit_breaker", message)
        if not new_daily_trigger and not new_weekly_trigger:
            return
        try:
            self._execution.cancel_opening_orders()
            self._execution.emergency_liquidate()
        except Exception:
            # Do not mark a first-time breaker as fully handled when liquidation
            # failed; the next engine tick must retry the safety workflow.
            with self.session_factory() as session:
                daily = self._daily(session, settings.trading_mode, now)
                weekly = session.scalar(select(WeeklyState).where(WeeklyState.week_start == start_week,
                                                                  WeeklyState.mode == settings.trading_mode))
                if new_daily_trigger and daily:
                    daily.circuit_breaker = False
                if new_weekly_trigger and weekly:
                    weekly.circuit_breaker = False
                session.commit()
            raise
        self.notifier.send("Apex: loss circuit breaker", message, enabled=settings.notifications_enabled)
        with self.session_factory() as session:
            log_event(session, "critical", "loss_circuit_breaker", message, equity=equity)

    def _handle_eod(self, settings: BotSettings, clock: dict[str, Any], now: datetime) -> None:
        if not settings.close_positions_eod or settings.overnight_positions or not clock.get("is_open"):
            return
        next_close_raw = clock.get("next_close")
        if not next_close_raw:
            return
        close = datetime.fromisoformat(str(next_close_raw).replace("Z", "+00:00"))
        deadlines = self.risk.session_deadlines(settings, close)
        now_et = now.astimezone(ET)
        if now_et >= deadlines["liquidation_start"] and self._eod_liquidation_date != now_et.date():
            self._entries_enabled = False
            self._execution.cancel_opening_orders()
            self._execution.emergency_liquidate()
            self._eod_liquidation_date = now_et.date()
            with self.session_factory() as session:
                log_event(session, "warning", "eod_liquidation", "End-of-day liquidation initiated")
        if now_et >= deadlines["target_flat"]:
            with self.session_factory() as session:
                remaining = session.scalars(select(PositionRecord).where(
                    PositionRecord.mode == settings.trading_mode, PositionRecord.status.in_(["open", "closing"])
                )).all()
                symbols = [p.contract_symbol for p in remaining]
            if symbols:
                self._throttled_event("eod_exposure", "critical", "eod_exposure",
                                      f"Exposure remains after target flat time: {', '.join(symbols)}", interval=30)

    def _scan_entries(self, settings: BotSettings, account, now: datetime) -> None:
        assert self._broker and self._market and self._execution
        close_raw = (self._clock or {}).get("next_close")
        session_close = datetime.fromisoformat(str(close_raw).replace("Z", "+00:00")) if close_raw else None
        if not self.risk.entry_time_allowed(settings, now, session_close):
            return
        with self.session_factory() as session:
            daily = self._daily(session, settings.trading_mode, now)
            weekly = session.scalar(select(WeeklyState).where(WeeklyState.week_start == week_start(now.astimezone(ET).date()),
                                                              WeeklyState.mode == settings.trading_mode))
            open_positions = session.scalar(select(func.count(PositionRecord.id)).where(
                PositionRecord.mode == settings.trading_mode, PositionRecord.status.in_(["open", "closing"]))) or 0
            pending_entries = session.scalar(select(func.count(OrderRecord.id)).where(
                OrderRecord.mode == settings.trading_mode, OrderRecord.position_intent == "buy_to_open",
                OrderRecord.status.in_(ACTIVE_ORDER_STATES))) or 0
            decision = self.risk.check_entry(settings, account, daily, weekly, open_positions, pending_entries,
                                             self._entries_enabled, self._reconciled, True, self._data_ready,
                                             now)
        if not decision.allowed:
            return
        news = self.news.assess(settings.news_filter_enabled)
        for symbol in settings.symbols:
            try:
                bars = self._market.stock_bars(symbol, TimeFrame.Minute, 4320)
                one = closed_candles(bars, 1, now)
                five = resample_candles(one, 5)
                fifteen = resample_candles(one, 15)
                if len(five) < 55 or len(fifteen) < 55:
                    continue
                candidates = self.strategy.evaluate(symbol, one, five, fifteen, settings.enabled_strategies,
                                                    settings.opening_range_minutes, news.unstable)
                self._last_market_cache[symbol] = {"bars": one.tail(180).to_dict("records"), "last_updated": now.isoformat()}
                self._data_ready = True
            except Exception as exc:
                self._data_ready = False
                self._throttled_event(f"bars_{symbol}", "warning", "market_data_error", str(exc), interval=30)
                continue
            selector = OptionSelector(self._broker, self._market)
            for candidate in candidates:
                try:
                    selected = selector.select(candidate, settings, account.equity)
                except (MarketDataError, BrokerError, ValueError) as exc:
                    self._data_ready = False
                    self._throttled_event(f"select_{symbol}", "warning", "contract_selection_blocked", str(exc), interval=30)
                    continue
                if not selected:
                    continue
                scored = self.scorer.score(candidate, settings, selected.liquidity_score, selected.reward_risk_score)
                ml_output = None
                ml_rejection = None
                if scored.qualified and settings.ml_filter_enabled:
                    ml_output = self.ml_filter.score(feature_vector(scored.factor_scores))
                    if ml_output is None and self.ml_filter.load():
                        ml_output = self.ml_filter.score(feature_vector(scored.factor_scores))
                    if ml_output is None:
                        ml_rejection = "ML filter is enabled but no out-of-sample-approved model is available"
                    elif ml_output < 0.50:
                        ml_rejection = f"Approved ML filter output {ml_output:.3f} is below 0.500"
                explanation = {"reasons": scored.explanation, "contributions": scored.contributions,
                               "option": selected.ranking_explanation}
                if settings.ml_filter_enabled:
                    explanation["ml_filter"] = {"enabled": True, "approved": self.ml_filter.approved,
                                                "output": ml_output, "threshold": 0.50}
                with self.session_factory() as session:
                    from .database import SignalRecord
                    record = SignalRecord(mode=settings.trading_mode, underlying=symbol, strategy=candidate.strategy,
                                          direction=candidate.direction, score=scored.score,
                                          qualified=scored.qualified and ml_rejection is None,
                                          regime=candidate.regime, factors=scored.factor_scores,
                                          explanation=explanation,
                                          rejection_reason=scored.rejection_reason or ml_rejection)
                    session.add(record)
                    session.commit()
                if not scored.qualified or ml_rejection:
                    continue
                size = self.risk.position_size(scored.score, selected.snapshot.quote.ask,
                                               selected.snapshot.contract.multiplier, account.equity,
                                               account.options_buying_power, settings)
                if size.quantity < 1:
                    continue
                try:
                    self._execution.submit_entry(scored, selected, size, settings)
                    return
                except (MarketDataError, BrokerError, ValueError) as exc:
                    self._throttled_event(f"entry_{symbol}", "warning", "entry_submission_blocked", str(exc), interval=30)
                    continue

    def preflight(self, mode: str, strict_fresh: bool = False) -> dict[str, Any]:
        checks = []

        def check(name: str, passed: bool, detail: str):
            checks.append({"name": name, "passed": bool(passed), "detail": detail})

        if not self.env.credentials_configured(mode):
            check("credentials", False, f"Alpaca {mode} credentials are missing")
            return {"ready": False, "checks": checks}
        check("credentials", True, f"Separate Alpaca {mode} credentials are configured")
        try:
            broker = AlpacaBroker(mode, self.env)
            account = broker.get_account()
            broker.verify_options_permission()
            check("broker", True, f"Connected; options level {account.options_level}")
            check("buying_power", account.options_buying_power > 0,
                  f"Options buying power ${account.options_buying_power:,.2f}")
            clock = broker.get_clock()
        except Exception as exc:
            check("broker", False, str(exc))
            return {"ready": False, "checks": checks}
        try:
            with self.session_factory() as session:
                settings = get_settings(session)
            settings = settings.model_copy(update={"trading_mode": mode})
            market = MarketDataService(settings, self.env)
            stock_quote = market.stock_quote("SPY")
            structural = stock_quote.bid > 0 and stock_quote.ask >= stock_quote.bid
            check("stock_data", structural, f"SPY {self.env.alpaca_stock_feed.upper()} quote received")
            contracts = broker.get_contracts("SPY", "call", 1, 3)
            snapshot = market.option_snapshot(contracts[0]) if contracts else None
            if snapshot is None:
                check("options_data", False, "No 1–3 DTE SPY option snapshot available")
            else:
                error = snapshot.quote.validation_error(max_age_seconds=settings.max_quote_age_seconds,
                                                        max_spread_percent=settings.max_spread_percent)
                structural = snapshot.quote.bid > 0 and snapshot.quote.ask >= snapshot.quote.bid
                fresh = error is None
                passed = fresh if strict_fresh else structural
                check("options_data", passed,
                      "Real-time OPRA quote validated" if fresh else
                      ("Quote received; freshness enforced at entry while market is closed" if structural and not strict_fresh else str(error)))
        except Exception as exc:
            check("options_data", False, str(exc))
        try:
            with self.session_factory() as session:
                pending = session.scalar(select(func.count(OrderRecord.id)).where(
                    OrderRecord.mode == mode, OrderRecord.status.in_(ACTIVE_ORDER_STATES))) or 0
            check("state", pending == 0, f"{pending} unresolved local orders" if pending else "No unresolved local orders")
        except Exception as exc:
            check("state", False, str(exc))
        return {"ready": all(item["passed"] for item in checks), "checks": checks}

    def start_entries(self) -> None:
        with self.session_factory() as session:
            settings = get_settings(session)
        if settings.trading_mode == "live" and not (
            settings.live_trading_enabled and self.env.live_trading_enabled
        ):
            raise RuntimeError("Live authorization is not active; repeat the protected live-activation workflow")
        result = self.preflight(settings.trading_mode, strict_fresh=False)
        if not result["ready"]:
            raise RuntimeError("Preflight failed: " + "; ".join(c["detail"] for c in result["checks"] if not c["passed"]))
        self._data_ready = True
        self._entries_enabled = True
        self._set_state("running")

    def pause_entries(self) -> None:
        self._entries_enabled = False
        self._set_state("paused")

    def stop_entries(self) -> None:
        self._entries_enabled = False
        if self._execution:
            self._execution.cancel_opening_orders()
        self._set_state("paused")

    def emergency_liquidate(self) -> dict[str, Any]:
        with self.session_factory() as session:
            settings = get_settings(session)
        self._ensure_clients(settings)
        assert self._execution
        self._entries_enabled = False
        result = self._execution.emergency_liquidate()
        with self.session_factory() as session:
            log_event(session, "critical", "manual_emergency", "Operator initiated emergency liquidation", **result)
        return result

    def activate_live(self, reviewed_settings: BotSettings) -> dict[str, Any]:
        with self.session_factory() as session:
            current = get_settings(session)
            open_positions = session.scalar(select(func.count(PositionRecord.id)).where(
                PositionRecord.status.in_(["open", "closing"]))) or 0
        if open_positions:
            raise RuntimeError("Close all positions before changing broker environments")
        live = reviewed_settings.model_copy(update={"trading_mode": "live", "live_trading_enabled": False})
        result = self.preflight("live", strict_fresh=True)
        if not result["ready"]:
            raise RuntimeError("Live preflight failed: " + "; ".join(c["detail"] for c in result["checks"] if not c["passed"]))
        # Runtime authorization is deliberate and revocable; restart requires fresh confirmation unless env enables it.
        self.env.live_trading_enabled = True
        live = live.model_copy(update={"live_trading_enabled": True})
        with self.session_factory() as session:
            save_settings(session, live)
            log_event(session, "critical", "live_activated", "Live trading activated after operator confirmation")
        self._broker = None
        self._market = None
        self._entries_enabled = False
        return result

    def switch_to_paper(self) -> None:
        with self.session_factory() as session:
            settings = get_settings(session)
            open_positions = session.scalar(select(func.count(PositionRecord.id)).where(
                PositionRecord.status.in_(["open", "closing"]))) or 0
            if open_positions:
                raise RuntimeError("Close all positions before changing broker environments")
            save_settings(session, settings.model_copy(update={"trading_mode": "paper", "live_trading_enabled": False}))
        self._entries_enabled = False
        self._broker = None
        self._market = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state, "entries_enabled": self._entries_enabled,
                "runtime_active": self._runtime_active, "mode": self._mode,
                "last_error": self._last_error,
                "account": ({k: v for k, v in self._last_account.items() if k != "raw"} if self._last_account else None),
                "clock": self._clock,
                "data_ready": self._data_ready, "reconciled": self._reconciled,
                "provider": self._market.options_provider.name if self._market else None,
                "stream_connected": self._streams.cache.connected if self._streams else False,
                "stream_error": self._streams.cache.last_error if self._streams else None,
                "updated_at": utcnow().isoformat(),
            }

    def _throttled_event(self, key: str, level: str, event_type: str, message: str,
                         interval: float = 30, **context) -> None:
        if time.monotonic() - self._last_event_at.get(key, 0) < interval:
            return
        self._last_event_at[key] = time.monotonic()
        try:
            with self.session_factory() as session:
                log_event(session, level, event_type, message, **context)
        except Exception:
            logger.exception("Could not persist event")
