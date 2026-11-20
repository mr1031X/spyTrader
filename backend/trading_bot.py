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
    AccountSnapshot, DailyState, EventLog, OpportunityDiagnosticRecord, OrderRecord, PositionRecord, SessionLocal,
    SettingsRecord, SignalRecord, TradeRecord, WeeklyState, get_settings, log_event, save_settings, utcnow,
)
from .diagnostics import record_scan
from .execution import ACTIVE_ORDER_STATES, ExecutionEngine
from .history import HistoricalBarCache
from .indicators import closed_candles, compute_indicators, resample_candles
from .market_data import AlpacaStreamManager, DataEntitlementError, MarketDataError, MarketDataService
from .ml_filter import MLSignalFilter, feature_vector
from .news import NewsService
from .notifications import Notifier
from .option_selector import OptionSelector
from .risk_manager import RiskManager, week_start
from .readiness import ReadinessState
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
        self._readiness = ReadinessState()
        self._history: HistoricalBarCache | None = None
        self._candidate_events: dict[tuple[str, str, str, str], float] = {}
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
                self._readiness.broker_connected = False
                self._readiness.order_execution = False
            except Exception as exc:
                logger.exception("Trading engine tick failed")
                self._set_state("error", str(exc))
                self._readiness.order_execution = False
                self._readiness.errors["runtime"] = str(exc)
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
        self._history = HistoricalBarCache(self._market, self._broker.get_market_calendar)
        self._readiness = ReadinessState()
        self._reconciled = False
        self._last_reconcile = 0
        self._entries_enabled = False if settings.trading_mode == "live" and not settings.live_trading_enabled else self._entries_enabled
        self._streams = AlpacaStreamManager(self._mode, self.env)
        self._streams.start(settings.symbols, options_enabled=settings.options_provider == "alpaca")

    def _tick(self) -> None:
        with self.session_factory() as session:
            settings = get_settings(session)
        if not self.env.credentials_configured(settings.trading_mode):
            raise BrokerConfigurationError(f"Add Alpaca {settings.trading_mode} API credentials to .env")
        self._ensure_clients(settings)
        assert self._broker and self._market and self._execution
        account = self._broker.get_account()
        clock = self._broker.get_clock()
        self._readiness.broker_connected = True
        self._readiness.errors.pop("runtime", None)
        now = datetime.now(timezone.utc)
        self._last_account = asdict(account)
        self._clock = clock
        self._ensure_periods(settings, account.equity, now)
        if not self._reconciled or time.monotonic() - self._last_reconcile > 60:
            self._execution.reconcile(settings)
            self._reconciled = True
            self._last_reconcile = time.monotonic()
        self._readiness.order_execution = bool(
            self._reconciled and not account.blocked and not account.trade_blocked and account.options_level >= 1
        )
        self._execution.sync_orders(settings)
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
                self._market.validate_quote(snapshot.quote, protective=True)
                self._readiness.options_entitlement = True
                self._readiness.option_quotes = True
                self._readiness.last_option_quote = snapshot.quote.timestamp
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
                if exit_pending:
                    self._execution.manage_exit(position_id, snapshot.quote, settings)
                elif decision.should_exit:
                    self._execution.request_exit(position_id, reason, snapshot.quote, settings)
            except (MarketDataError, BrokerError, ValueError) as exc:
                self._readiness.option_quotes = False
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
            for symbol in settings.symbols:
                self._record_opportunity(settings, symbol, now, {
                    "scan_status": "RISK_BLOCKED", "final_decision": "ENTRY_CUTOFF",
                    "rejection_reasons": ["ENTRY_CUTOFF"],
                })
            return
        capacity = self._entry_risk(settings, account, now, execution=False)
        if not capacity.allowed:
            codes = self._risk_rejection_codes(capacity.reasons)
            for symbol in settings.symbols:
                self._record_opportunity(settings, symbol, now, {
                    "scan_status": "RISK_BLOCKED", "final_decision": "RISK_REJECTED",
                    "risk": {"eligible": False, "reasons": capacity.reasons},
                    "rejection_reasons": codes,
                })
            return
        news = self.news.assess(settings.news_filter_enabled)
        for symbol in settings.symbols:
            payload: dict[str, Any] = {
                "timestamp": now.isoformat(), "symbol": symbol, "scan_status": "EVALUATING",
                "candidates": [], "rejection_reasons": [], "threshold_comparison": {},
                "potential_threshold_comparison": {}, "contract_selections": [],
                "qualified_signals": 0, "signals_rejected": 0,
            }
            try:
                if self._history:
                    bars = self._history.get(symbol, now)
                else:
                    bars = self._market.stock_bars(symbol, TimeFrame.Minute, start=now - timedelta(days=14), end=now)
                one = closed_candles(bars, 1, now)
                five = resample_candles(one, 5, now)
                fifteen = resample_candles(one, 15, now)
                last_close = None
                age_seconds = None
                if not one.empty:
                    last_close = one["timestamp"].iloc[-1].to_pydatetime() + timedelta(minutes=1)
                    age_seconds = (now - last_close).total_seconds()
                underlying_ready = bool(not one.empty and age_seconds is not None and -5 <= age_seconds <= 180)
                historical_ready = len(one) >= 60 and len(five) >= 55 and len(fifteen) >= 55
                self._readiness.underlying[symbol] = underlying_ready
                self._readiness.historical[symbol] = historical_ready
                if last_close:
                    self._readiness.last_underlying_bar[symbol] = last_close
                    self._readiness.last_completed_candle[symbol] = last_close
                payload.update({
                    "price": float(one["close"].iloc[-1]) if not one.empty else None,
                    "market_data_fresh": underlying_ready, "market_data_age_seconds": age_seconds,
                    "historical_bars": {"one_minute": len(one), "five_minute": len(five), "fifteen_minute": len(fifteen)},
                    "indicator_warmup": historical_ready, "last_completed_candle": last_close,
                })
                if len(one) >= 60:
                    indicators = compute_indicators(one, settings.opening_range_minutes)
                    payload["indicators"] = indicators.to_dict()
                if not underlying_ready:
                    payload["rejection_reasons"].append("STALE_UNDERLYING_QUOTE")
                if not historical_ready:
                    payload["rejection_reasons"].append("INSUFFICIENT_HISTORICAL_DATA")
                evaluations = self.strategy.evaluate_detailed(
                    symbol, one, five, fifteen, settings.enabled_strategies,
                    settings.opening_range_minutes, news.unstable,
                    experiment_breakout_3_atr=settings.experiment_breakout_3_atr_enabled,
                    experiment_confirmed_continuation=settings.experiment_confirmed_continuation_enabled,
                ) if underlying_ready else []
                payload["strategies"] = [item.to_dict() for item in evaluations]
                payload["trend"] = evaluations[0].trend if evaluations else "unknown"
                payload["volatility_regime"] = evaluations[0].volatility if evaluations else "unknown"
                for item in evaluations:
                    if item.candidate is None:
                        payload["rejection_reasons"].extend(item.rejected)
                self._last_market_cache[symbol] = {
                    "bars": one.tail(180).to_dict("records"), "last_updated": now.isoformat(),
                }
            except Exception as exc:
                self._readiness.underlying[symbol] = False
                self._readiness.historical[symbol] = False
                self._readiness.errors[f"bars_{symbol}"] = str(exc)
                payload.update({"scan_status": "DATA_ERROR", "final_decision": "DATA_NOT_READY"})
                payload["rejection_reasons"].append("DATA_NOT_READY")
                self._record_opportunity(settings, symbol, now, payload)
                self._throttled_event(f"bars_{symbol}", "warning", "market_data_error", str(exc), interval=30)
                continue

            candidates = [item.candidate for item in evaluations if item.candidate is not None]
            payload["signals_rejected"] = len(evaluations) - len(candidates)
            if not candidates:
                payload["scan_status"] = "SCANNED"
                payload["final_decision"] = "DATA_NOT_READY" if not underlying_ready else (
                    "WARMING_UP" if not historical_ready else "NO_VALID_SETUP"
                )
                self._record_opportunity(settings, symbol, now, payload)
                continue

            selector = OptionSelector(self._broker, self._market)
            for candidate in candidates:
                event_key = candidate.event_key or f"{candidate.strategy}:{candidate.reference_price}"
                identity = (symbol, candidate.strategy, candidate.direction, event_key)
                summary = {"direction": candidate.direction, "strategy": candidate.strategy,
                           "trend": candidate.trend, "volatility": candidate.volatility}
                payload["candidates"].append(summary)
                if identity in self._candidate_events:
                    payload["rejection_reasons"].append("DUPLICATE_SETUP")
                    summary["rejection"] = "DUPLICATE_SETUP"
                    continue
                self._candidate_events[identity] = now.timestamp()
                preliminary = self.scorer.score_underlying(candidate, settings)
                summary["underlying_score"] = preliminary.score
                explanation: dict[str, Any] = {
                    "reasons": candidate.explanation, "contributions": preliminary.contributions,
                    "technical_score": preliminary.technical_score, "event_key": event_key,
                }
                with self.session_factory() as session:
                    record = SignalRecord(
                        mode=settings.trading_mode, underlying=symbol, strategy=candidate.strategy,
                        direction=candidate.direction, score=preliminary.score, qualified=False,
                        regime=candidate.regime, factors=preliminary.factor_scores,
                        explanation=explanation, rejection_reason="OPTION_EVALUATION_PENDING",
                    )
                    session.add(record)
                    session.commit()
                    signal_id = record.id
                maximum_score = preliminary.score + settings.score_weights.liquidity + settings.score_weights.reward_risk
                for threshold in (70, 80, 90):
                    key = str(threshold)
                    comparison = payload["potential_threshold_comparison"]
                    comparison[key] = comparison.get(key, 0) + int(maximum_score >= threshold)
                if maximum_score < settings.min_signal_score:
                    summary["rejection"] = "SIGNAL_SCORE_TOO_LOW"
                    payload["rejection_reasons"].append("SIGNAL_SCORE_TOO_LOW")
                    self._reject_signal(signal_id, "SIGNAL_SCORE_TOO_LOW", explanation)
                    continue
                try:
                    selection = selector.select_detailed(candidate, settings, account.equity)
                    contract_details = selection.to_dict()
                    selection_payload = {**selection.stats, "details": contract_details}
                    payload["contract_selection"] = selection_payload
                    payload["contract_selections"].append(selection_payload)
                    payload["rejection_reasons"].extend(selection.rejection_codes)
                    summary["contract_selection"] = contract_details
                except (MarketDataError, BrokerError, ValueError) as exc:
                    self._readiness.option_quotes = False
                    summary["rejection"] = "OPTIONS_DATA_NOT_READY"
                    payload["rejection_reasons"].append("OPTIONS_DATA_NOT_READY")
                    self._reject_signal(signal_id, "OPTIONS_DATA_NOT_READY", explanation, str(exc))
                    self._throttled_event(f"select_{symbol}", "warning", "contract_selection_blocked", str(exc), interval=30)
                    continue
                selected = selection.selected
                if selected is None:
                    summary["rejection"] = "NO_ELIGIBLE_OPTION"
                    payload["rejection_reasons"].append("NO_ELIGIBLE_OPTION")
                    self._reject_signal(signal_id, "NO_ELIGIBLE_OPTION", explanation)
                    continue
                self._readiness.options_entitlement = True
                self._readiness.option_quotes = True
                self._readiness.last_option_quote = selected.snapshot.quote.timestamp
                scored = self.scorer.score(candidate, settings, selected.liquidity_score, selected.reward_risk_score)
                summary["score"] = scored.score
                summary["technical_score"] = scored.technical_score
                summary["option_quality_score"] = scored.option_quality_score
                for threshold in (70, 80, 90):
                    key = str(threshold)
                    comparison = payload["threshold_comparison"]
                    comparison[key] = comparison.get(key, 0) + int(scored.score >= threshold)
                explanation.update({
                    "reasons": scored.explanation, "contributions": scored.contributions,
                    "technical_score": scored.technical_score, "option_quality_score": scored.option_quality_score,
                    "option": selected.ranking_explanation,
                })
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
                if settings.ml_filter_enabled:
                    explanation["ml_filter"] = {"enabled": True, "approved": self.ml_filter.approved,
                                                "output": ml_output, "threshold": 0.50}
                if not scored.qualified or ml_rejection:
                    code = "ML_FILTER_REJECTED" if ml_rejection else "SIGNAL_SCORE_TOO_LOW"
                    summary["rejection"] = code
                    payload["rejection_reasons"].append(code)
                    self._reject_signal(signal_id, code, explanation, ml_rejection or scored.rejection_reason, scored)
                    continue
                final_risk = self._entry_risk(settings, account, now, execution=True, symbol=symbol)
                explanation["risk"] = {"eligible": final_risk.allowed, "reasons": final_risk.reasons}
                payload["risk"] = explanation["risk"]
                if not final_risk.allowed:
                    codes = self._risk_rejection_codes(final_risk.reasons)
                    summary["rejection"] = codes[0] if codes else "RISK_REJECTED"
                    payload["rejection_reasons"].extend(codes)
                    self._reject_signal(signal_id, summary["rejection"], explanation, "; ".join(final_risk.reasons), scored)
                    continue
                size = self.risk.position_size(
                    scored.score, selected.snapshot.quote.ask, selected.snapshot.contract.multiplier,
                    account.equity, account.options_buying_power, settings,
                )
                explanation["position_size"] = size.__dict__
                if size.quantity < 1:
                    summary["rejection"] = "INSUFFICIENT_BUYING_POWER"
                    payload["rejection_reasons"].append("INSUFFICIENT_BUYING_POWER")
                    self._reject_signal(signal_id, "INSUFFICIENT_BUYING_POWER", explanation, scored=scored)
                    continue
                with self.session_factory() as session:
                    record = session.get(SignalRecord, signal_id)
                    record.score = scored.score
                    record.qualified = True
                    record.factors = scored.factor_scores
                    record.explanation = explanation
                    record.rejection_reason = None
                    session.commit()
                payload["qualified_signals"] += 1
                payload["scan_status"] = "SCANNED"
                payload["final_decision"] = "QUALIFIED"
                try:
                    order = self._execution.submit_entry(scored, selected, size, settings)
                    summary["order_id"] = order.id
                    summary["client_order_id"] = order.client_order_id
                    payload["final_decision"] = "ORDER_SUBMITTED"
                    payload["order"] = {"id": order.id, "client_order_id": order.client_order_id,
                                        "symbol": order.symbol, "qty": order.qty}
                except (MarketDataError, BrokerError, ValueError) as exc:
                    payload["final_decision"] = "ORDER_REJECTED"
                    payload["rejection_reasons"].append("ORDER_REJECTED")
                    summary["rejection"] = "ORDER_REJECTED"
                    self._reject_signal(signal_id, "ORDER_REJECTED", explanation, str(exc), scored)
                    self._throttled_event(f"entry_{symbol}", "warning", "entry_submission_blocked", str(exc), interval=30)
                if payload["final_decision"] == "ORDER_SUBMITTED":
                    self._record_opportunity(settings, symbol, now, payload)
                    return
            payload["scan_status"] = "SCANNED"
            if "final_decision" not in payload:
                payload["final_decision"] = "SIGNAL_REJECTED"
            self._record_opportunity(settings, symbol, now, payload)
        cutoff = now.timestamp() - 6 * 3600
        self._candidate_events = {key: value for key, value in self._candidate_events.items() if value >= cutoff}

    def _entry_risk(self, settings: BotSettings, account, now: datetime, execution: bool,
                    symbol: str | None = None):
        with self.session_factory() as session:
            daily = self._daily(session, settings.trading_mode, now)
            weekly = session.scalar(select(WeeklyState).where(
                WeeklyState.week_start == week_start(now.astimezone(ET).date()),
                WeeklyState.mode == settings.trading_mode,
            ))
            open_positions = session.scalar(select(func.count(PositionRecord.id)).where(
                PositionRecord.mode == settings.trading_mode, PositionRecord.status.in_(["open", "closing"]))) or 0
            pending_entries = session.scalar(select(func.count(OrderRecord.id)).where(
                OrderRecord.mode == settings.trading_mode, OrderRecord.position_intent == "buy_to_open",
                OrderRecord.status.in_(ACTIVE_ORDER_STATES))) or 0
        if execution:
            ready = bool(symbol and self._readiness.execution_ready(symbol, self._reconciled))
            return self.risk.check_entry(
                settings, account, daily, weekly, open_positions, pending_entries,
                self._entries_enabled, self._reconciled, True, ready, now,
            )
        return self.risk.check_entry_capacity(
            settings, account, daily, weekly, open_positions, pending_entries,
            self._entries_enabled, self._reconciled, True, now,
        )

    @staticmethod
    def _risk_rejection_codes(reasons: list[str]) -> list[str]:
        mapping = {
            "Daily completed-trade limit reached": "DAILY_TRADE_LIMIT",
            "Maximum open position or entry capacity reached": "EXISTING_POSITION",
            "A loss circuit breaker is active": "DAILY_LOSS_LIMIT",
            "Maximum consecutive losses reached": "CONSECUTIVE_LOSS_LIMIT",
            "Real-time execution-quality data is unavailable": "DATA_NOT_READY",
            "Broker state is not reconciled": "BROKER_NOT_RECONCILED",
            "Account or options permission check failed": "BROKER_NOT_READY",
            "Past the new-entry cutoff": "ENTRY_CUTOFF",
            "Automated entries are paused": "ENTRIES_PAUSED",
        }
        return [mapping.get(reason, "RISK_REJECTED") for reason in reasons]

    def _reject_signal(self, signal_id: str, code: str, explanation: dict[str, Any],
                       detail: str | None = None, scored=None) -> None:
        with self.session_factory() as session:
            record = session.get(SignalRecord, signal_id)
            if record is None:
                return
            if scored is not None:
                record.score = scored.score
                record.factors = scored.factor_scores
            record.qualified = False
            record.explanation = explanation
            record.rejection_reason = f"{code}: {detail}" if detail else code
            session.commit()

    def _record_opportunity(self, settings: BotSettings, symbol: str, now: datetime,
                            payload: dict[str, Any]) -> None:
        payload["signals_rejected"] = (
            sum(not item.get("candidate", False) for item in payload.get("strategies", []))
            + sum(bool(item.get("rejection")) for item in payload.get("candidates", []))
        )
        try:
            with self.session_factory() as session:
                record_scan(session, settings.trading_mode, symbol, now, payload)
        except Exception:
            logger.exception("Could not persist opportunity diagnostics for %s", symbol)

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
        blocking = [check for check in result["checks"] if not check["passed"] and check["name"] != "options_data"]
        if blocking:
            raise RuntimeError("Preflight failed: " + "; ".join(check["detail"] for check in blocking))
        option_warnings = [check["detail"] for check in result["checks"]
                           if not check["passed"] and check["name"] == "options_data"]
        if option_warnings:
            with self.session_factory() as session:
                log_event(session, "warning", "options_data_waiting",
                          "Underlying scanning enabled; order execution remains blocked until options data passes",
                          details=option_warnings)
        self._readiness.option_quotes = False
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
            market_settings = getattr(self._market, "settings", None)
            symbols = market_settings.symbols if market_settings else ["SPY", "QQQ"]
            readiness = self._readiness.to_dict(symbols, self._reconciled)
            return {
                "state": self._state, "entries_enabled": self._entries_enabled,
                "runtime_active": self._runtime_active, "mode": self._mode,
                "last_error": self._last_error,
                "account": ({k: v for k, v in self._last_account.items() if k != "raw"} if self._last_account else None),
                "clock": self._clock,
                "data_ready": readiness["data_ready"], "readiness": readiness, "reconciled": self._reconciled,
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
