"""FastAPI application, dashboard API, and production frontend hosting."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .backtesting import BacktestError, Backtester, parse_csv
from .config import ROOT, BotSettings, get_env
from .database import (
    AccountSnapshot, BacktestRecord, DailyState, EventLog, FillRecord, OpportunityDiagnosticRecord, OrderRecord,
    PositionRecord, SessionLocal, SettingsRecord, SignalRecord, TradeRecord, get_settings, init_db, log_event, save_settings,
)
from .schemas import BacktestParameters, ControlRequest, EmergencyRequest, LiveActivationRequest, ModeRequest
from .trading_bot import ET, TradingBot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)
bot = TradingBot()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    env = get_env()
    with SessionLocal() as session:
        settings = get_settings(session)
        if settings.trading_mode == "live" and settings.live_trading_enabled and not env.live_trading_enabled:
            save_settings(session, settings.model_copy(update={"live_trading_enabled": False}))
            log_event(session, "critical", "live_authorization_reset",
                      "Server live gate is disabled after restart; repeat live activation before entries")
    if env.auto_trading_enabled:
        try:
            await asyncio.to_thread(bot.start_entries)
        except Exception as exc:
            logger.warning("Automatic entry startup did not pass its gates: %s", exc)
            with SessionLocal() as session:
                log_event(session, "warning", "auto_start_blocked", str(exc))
    await bot.start_runtime()
    yield
    await bot.stop_runtime()


def create_app() -> FastAPI:
    app = FastAPI(title="Apex Options Pilot", version="1.0.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://localhost:5173"],
                       allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

    def db():
        with SessionLocal() as session:
            yield session

    def current_settings(session: Session) -> BotSettings:
        return get_settings(session)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "apex-options-pilot", "time": datetime.now(timezone.utc).isoformat()}

    @app.get("/api/status")
    def status(session: Session = Depends(db)):
        settings = current_settings(session)
        runtime = bot.snapshot()
        now = datetime.now(timezone.utc)
        daily = session.scalar(select(DailyState).where(DailyState.day == now.astimezone(ET).date(),
                                                        DailyState.mode == settings.trading_mode))
        position = session.scalar(select(PositionRecord).where(
            PositionRecord.mode == settings.trading_mode, PositionRecord.status.in_(["open", "closing"])
        ).order_by(PositionRecord.opened_at.desc()))
        latest_signals = session.scalars(select(SignalRecord).where(SignalRecord.mode == settings.trading_mode)
                                         .order_by(SignalRecord.created_at.desc()).limit(5)).all()
        return {
            **runtime, "mode": settings.trading_mode,
            "credentials_configured": get_env().credentials_configured(settings.trading_mode),
            "options_feed": get_env().alpaca_options_feed,
            "daily": _daily_dict(daily), "active_position": _position_dict(position),
            "latest_signals": [_signal_dict(signal) for signal in latest_signals],
            "settings": settings.model_dump(),
        }

    @app.get("/api/diagnostics")
    def diagnostics(session: Session = Depends(db)):
        settings = current_settings(session)
        now = datetime.now(timezone.utc)
        items = session.scalars(select(OpportunityDiagnosticRecord).where(
            OpportunityDiagnosticRecord.day == now.astimezone(ET).date(),
            OpportunityDiagnosticRecord.mode == settings.trading_mode,
        ).order_by(OpportunityDiagnosticRecord.underlying)).all()
        runtime = bot.snapshot()
        successful_times = [item.last_scan_at for item in items
                            if (item.last_scan or {}).get("scan_status") == "SCANNED" and item.last_scan_at]
        last_success = max(successful_times) if successful_times else None
        if last_success and last_success.tzinfo is None:
            last_success = last_success.replace(tzinfo=timezone.utc)
        active_positions = session.scalar(select(func.count(PositionRecord.id)).where(
            PositionRecord.mode == settings.trading_mode, PositionRecord.status.in_(["open", "closing"]))) or 0
        scan_warning = bool(
            runtime.get("entries_enabled") and (runtime.get("clock") or {}).get("is_open")
            and not active_positions and (last_success is None or (now - last_success).total_seconds() > 120)
        )
        return {
            "symbols": [_diagnostic_dict(item) for item in items],
            "readiness": runtime.get("readiness"),
            "last_successful_scan": _iso(last_success), "scan_warning": scan_warning,
            "evaluation_status": "UNABLE_TO_EVALUATE" if scan_warning else "EVALUATING",
            "execution": _execution_metrics(session, settings.trading_mode, now),
            "updated_at": now.isoformat(),
        }

    @app.get("/api/settings")
    def settings_get(session: Session = Depends(db)):
        return current_settings(session).model_dump()

    @app.put("/api/settings")
    async def settings_put(payload: dict[str, Any], session: Session = Depends(db)):
        try:
            current = current_settings(session)
            proposed = BotSettings.model_validate(payload)
            # Environment transitions and live authorization have a separate protected workflow.
            proposed = proposed.model_copy(update={
                "trading_mode": current.trading_mode,
                "live_trading_enabled": current.live_trading_enabled,
            })
            save_settings(session, proposed)
            log_event(session, "info", "settings_updated", "Trading settings saved; active-position protection unchanged")
            return proposed.model_dump()
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_context=False)) from exc

    @app.get("/api/preflight")
    async def preflight(mode: str = "paper", fresh: bool = False):
        if mode not in {"paper", "live"}:
            raise HTTPException(400, "Invalid mode")
        return await asyncio.to_thread(bot.preflight, mode, fresh)

    @app.post("/api/control/{action}")
    async def control(action: str, request: ControlRequest, session: Session = Depends(db)):
        if action not in {"start", "pause", "resume", "stop"}:
            raise HTTPException(404, "Unknown control action")
        if action in {"start", "resume"} and not request.acknowledge_risk:
            raise HTTPException(400, "Risk acknowledgment is required to enable automated entries")
        try:
            if action in {"start", "resume"}:
                await asyncio.to_thread(bot.start_entries)
            elif action == "pause":
                bot.pause_entries()
            else:
                await asyncio.to_thread(bot.stop_entries)
            log_event(session, "info", f"control_{action}", f"Operator selected {action}")
            return {"ok": True, "state": bot.snapshot()["state"]}
        except Exception as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/emergency")
    async def emergency(request: EmergencyRequest, session: Session = Depends(db)):
        if request.confirmation != "LIQUIDATE NOW":
            raise HTTPException(400, "Type LIQUIDATE NOW to confirm")
        try:
            return await asyncio.to_thread(bot.emergency_liquidate)
        except Exception as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/mode/paper")
    def mode_paper(session: Session = Depends(db)):
        try:
            bot.switch_to_paper()
            return {"ok": True, "mode": "paper"}
        except Exception as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/mode/live")
    async def mode_live(request: LiveActivationRequest, session: Session = Depends(db)):
        if request.confirmation != "ENABLE LIVE TRADING" or not request.risk_reviewed:
            raise HTTPException(400, "Live risk review and exact confirmation are required")
        if request.settings.trading_mode != "live":
            raise HTTPException(400, "Invalid live activation payload")
        try:
            result = await asyncio.to_thread(bot.activate_live, request.settings)
            return {"ok": True, "mode": "live", "preflight": result}
        except Exception as exc:
            raise HTTPException(409, str(exc))

    @app.get("/api/positions")
    def positions(session: Session = Depends(db)):
        settings = current_settings(session)
        items = session.scalars(select(PositionRecord).where(PositionRecord.mode == settings.trading_mode)
                                .order_by(PositionRecord.opened_at.desc()).limit(100)).all()
        return [_position_dict(item) for item in items]

    @app.get("/api/orders")
    def orders(session: Session = Depends(db)):
        settings = current_settings(session)
        items = session.scalars(select(OrderRecord).where(OrderRecord.mode == settings.trading_mode)
                                .order_by(OrderRecord.created_at.desc()).limit(150)).all()
        return [_order_dict(item) for item in items]

    @app.get("/api/trades")
    def trades(session: Session = Depends(db)):
        settings = current_settings(session)
        items = session.scalars(select(TradeRecord).where(TradeRecord.mode == settings.trading_mode)
                                .order_by(TradeRecord.exit_time.desc()).limit(250)).all()
        return [_trade_dict(item) for item in items]

    @app.get("/api/signals")
    def signals(session: Session = Depends(db)):
        settings = current_settings(session)
        items = session.scalars(select(SignalRecord).where(SignalRecord.mode == settings.trading_mode)
                                .order_by(SignalRecord.created_at.desc()).limit(100)).all()
        return [_signal_dict(item) for item in items]

    @app.get("/api/events")
    def events(session: Session = Depends(db)):
        items = session.scalars(select(EventLog).order_by(EventLog.created_at.desc()).limit(100)).all()
        return [_event_dict(item) for item in items]

    @app.get("/api/performance")
    def performance(session: Session = Depends(db)):
        settings = current_settings(session)
        items = session.scalars(select(TradeRecord).where(TradeRecord.mode == settings.trading_mode)
                                .order_by(TradeRecord.exit_time.asc())).all()
        snapshots = session.scalars(select(AccountSnapshot).where(AccountSnapshot.mode == settings.trading_mode)
                                    .order_by(AccountSnapshot.created_at.asc()).limit(2000)).all()
        pnls = [item.realized_pnl for item in items]
        gains = [value for value in pnls if value > 0]
        losses = [value for value in pnls if value < 0]
        curve = []
        running = 0.0
        for item in items:
            running += item.realized_pnl
            curve.append({"time": item.exit_time.isoformat(), "value": running})
        peaks = []
        maximum = float("-inf")
        for point in curve:
            maximum = max(maximum, point["value"])
            peaks.append(maximum - point["value"])
        by_strategy: dict[str, list[float]] = {}
        by_hour: dict[str, list[float]] = {}
        for item in items:
            by_strategy.setdefault(item.strategy, []).append(item.realized_pnl)
            hour = item.entry_time.astimezone(ET).strftime("%H:00")
            by_hour.setdefault(hour, []).append(item.realized_pnl)
        return {
            "net_realized_pnl": sum(pnls), "trade_count": len(items),
            "win_rate": len(gains) / len(items) * 100 if items else 0,
            "average_gain": sum(gains) / len(gains) if gains else 0,
            "average_loss": sum(losses) / len(losses) if losses else 0,
            "profit_factor": sum(gains) / abs(sum(losses)) if losses and sum(losses) else None,
            "maximum_drawdown": max(peaks) if peaks else 0,
            "average_trade_return": sum(item.return_percent for item in items) / len(items) if items else 0,
            "average_holding_minutes": sum((item.exit_time - item.entry_time).total_seconds() / 60 for item in items) / len(items) if items else 0,
            "average_slippage": sum(item.slippage or 0 for item in items) / len(items) if items else 0,
            "equity_curve": curve,
            "account_curve": [{"time": s.created_at.isoformat(), "equity": s.equity, "daily_pnl": s.daily_pnl} for s in snapshots],
            "daily_profit": _daily_profit(items),
            "strategy_performance": [{"name": k, "pnl": sum(v), "trades": len(v),
                                      "win_rate": len([p for p in v if p > 0]) / len(v) * 100} for k, v in by_strategy.items()],
            "time_of_day": [{"hour": k, "pnl": sum(v), "trades": len(v)} for k, v in sorted(by_hour.items())],
        }

    @app.get("/api/markets/{symbol}")
    async def market(symbol: str):
        symbol = symbol.upper()
        if symbol not in {"SPY", "QQQ"}:
            raise HTTPException(404, "Version 1 monitors SPY and QQQ")
        if not bot._market:
            raise HTTPException(503, "Market data is not configured")
        try:
            quote = await asyncio.to_thread(bot._market.stock_quote, symbol)
            now = datetime.now(timezone.utc)
            if bot._history:
                bars = await asyncio.to_thread(bot._history.get, symbol, now)
            else:
                bars = await asyncio.to_thread(bot._market.stock_bars, symbol, lookback_minutes=10080)
            from .indicators import closed_candles, compute_indicators
            closed = closed_candles(bars, now=now)
            indicators = compute_indicators(closed).to_dict() if len(closed) >= 55 else None
            return {"symbol": symbol, "quote": quote.to_dict(), "indicators": indicators,
                    "candles": closed.tail(180).to_dict("records")}
        except Exception as exc:
            raise HTTPException(503, str(exc))

    @app.post("/api/backtests")
    async def run_backtest(
        underlying_file: UploadFile = File(...), options_file: UploadFile = File(...),
        parameters: str = Form(default="{}"), session: Session = Depends(db),
    ):
        try:
            params = BacktestParameters.model_validate_json(parameters)
            underlying = parse_csv(await underlying_file.read(), "underlying")
            options = parse_csv(await options_file.read(), "options")
            settings = current_settings(session)
            result = await asyncio.to_thread(Backtester().run, underlying, options, settings,
                                             params.initial_equity, params.slippage_percent, params.fee_per_contract)
            record = BacktestRecord(name=params.name, parameters=params.model_dump(), results=result)
            session.add(record)
            session.commit()
            return {"id": record.id, "created_at": record.created_at.isoformat(), **result}
        except (BacktestError, ValidationError, ValueError) as exc:
            raise HTTPException(422, str(exc))

    @app.post("/api/backtests/underlying")
    async def run_underlying_backtest(
        underlying_file: UploadFile = File(...), parameters: str = Form(default="{}"),
        session: Session = Depends(db),
    ):
        try:
            params = BacktestParameters.model_validate_json(parameters)
            underlying = parse_csv(await underlying_file.read(), "underlying")
            result = await asyncio.to_thread(Backtester().run_underlying, underlying, current_settings(session))
            record = BacktestRecord(name=params.name, parameters=params.model_dump(), results=result)
            session.add(record)
            session.commit()
            return {"id": record.id, "created_at": record.created_at.isoformat(), **result}
        except (BacktestError, ValidationError, ValueError) as exc:
            raise HTTPException(422, str(exc))

    @app.get("/api/backtests")
    def backtests(session: Session = Depends(db)):
        items = session.scalars(select(BacktestRecord).order_by(BacktestRecord.created_at.desc()).limit(30)).all()
        return [{"id": item.id, "name": item.name, "created_at": item.created_at.isoformat(),
                 "parameters": item.parameters, "results": item.results} for item in items]

    dist = ROOT / "dist"
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    if (dist / "icons").exists():
        app.mount("/icons", StaticFiles(directory=dist / "icons"), name="icons")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404, "API route not found")
        index = dist / "index.html"
        if not index.exists():
            raise HTTPException(503, "Frontend has not been built; run npm run build")
        requested = (dist / full_path).resolve()
        dist_root = dist.resolve()
        if full_path and requested.is_file() and requested.is_relative_to(dist_root):
            return FileResponse(requested)
        return FileResponse(index)

    return app


def _iso(value):
    return value.isoformat() if value else None


def _daily_dict(item: DailyState | None):
    if not item:
        return None
    return {"day": item.day.isoformat(), "start_equity": item.start_equity, "realized_pnl": item.realized_pnl,
            "completed_trades": item.completed_trades, "consecutive_losses": item.consecutive_losses,
            "goal_reached": item.goal_reached, "circuit_breaker": item.circuit_breaker,
            "final_equity": item.final_equity}


def _position_dict(item: PositionRecord | None):
    if not item:
        return None
    unrealized = ((item.current_bid - item.entry_premium) * 100 * item.qty
                  if item.current_bid is not None and item.status != "closed" else None)
    protected = (item.stop_threshold - item.entry_premium) * 100 * item.qty
    return {"id": item.id, "mode": item.mode, "contract_symbol": item.contract_symbol,
            "underlying": item.underlying, "option_type": item.option_type, "strike": item.strike,
            "expiration": item.expiration.isoformat(), "qty": item.qty, "initial_qty": item.initial_qty,
            "entry_premium": item.entry_premium, "current_bid": item.current_bid, "current_ask": item.current_ask,
            "highest_bid": item.highest_bid, "stop_threshold": item.stop_threshold,
            "trailing_active": item.trailing_active, "exit_order_id": item.exit_order_id,
            "status": item.status, "strategy": item.strategy, "score": item.score,
            "realized_pnl": item.realized_pnl, "fees": item.fees, "exit_reason": item.exit_reason,
            "opened_at": _iso(item.opened_at), "closed_at": _iso(item.closed_at), "recovered": item.recovered,
            "unrealized_pnl": unrealized, "protected_pnl_before_costs_if_filled": protected,
            "protection": item.protection}


def _signal_dict(item: SignalRecord):
    return {"id": item.id, "underlying": item.underlying, "strategy": item.strategy, "direction": item.direction,
            "score": item.score, "qualified": item.qualified, "regime": item.regime, "factors": item.factors,
            "explanation": item.explanation, "rejection_reason": item.rejection_reason,
            "created_at": _iso(item.created_at)}


def _order_dict(item: OrderRecord):
    return {"id": item.id, "client_order_id": item.client_order_id, "broker_order_id": item.broker_order_id,
            "symbol": item.symbol, "side": item.side, "position_intent": item.position_intent, "qty": item.qty,
            "limit_price": item.limit_price, "status": item.status, "filled_qty": item.filled_qty,
            "filled_avg_price": item.filled_avg_price, "position_id": item.position_id, "reason": item.reason,
            "created_at": _iso(item.created_at), "updated_at": _iso(item.updated_at)}


def _trade_dict(item: TradeRecord):
    return {"id": item.id, "position_id": item.position_id, "contract_symbol": item.contract_symbol,
            "underlying": item.underlying, "option_type": item.option_type, "strike": item.strike,
            "expiration": item.expiration.isoformat(), "strategy": item.strategy, "qty": item.qty,
            "entry_price": item.entry_price, "exit_price": item.exit_price, "realized_pnl": item.realized_pnl,
            "fees": item.fees, "return_percent": item.return_percent, "exit_reason": item.exit_reason,
            "entry_time": _iso(item.entry_time), "exit_time": _iso(item.exit_time),
            "intended_exit_price": item.intended_exit_price, "slippage": item.slippage}


def _diagnostic_dict(item: OpportunityDiagnosticRecord):
    reasons = sorted((item.rejection_counts or {}).items(), key=lambda pair: pair[1], reverse=True)
    return {
        "underlying": item.underlying, "day": item.day.isoformat(), "scans": item.scans,
        "bullish_considered": item.bullish_considered, "bearish_considered": item.bearish_considered,
        "rejected_signals": item.rejected_signals, "qualified_signals": item.qualified_signals,
        "contracts_evaluated": item.contracts_evaluated, "contracts_rejected": item.contracts_rejected,
        "primary_rejections": [{"reason": reason, "count": count} for reason, count in reasons[:8]],
        "last_scan": item.last_scan, "recent_scans": item.recent_scans,
        "last_qualifying_signal": item.last_qualifying_signal,
        "last_order_attempt": item.last_order_attempt,
        "last_scan_at": _iso(item.last_scan_at),
    }


def _execution_metrics(session: Session, mode: str, now: datetime):
    day_start = datetime.combine(now.astimezone(ET).date(), datetime.min.time(), tzinfo=ET).astimezone(timezone.utc)
    orders = session.scalars(select(OrderRecord).where(
        OrderRecord.mode == mode, OrderRecord.created_at >= day_start,
    )).all()
    by_id = {order.id: order for order in orders}
    fills = session.scalars(select(FillRecord).join(OrderRecord, FillRecord.order_id == OrderRecord.id).where(
        OrderRecord.mode == mode, FillRecord.created_at >= day_start,
    )).all()
    latencies = []
    for fill in fills:
        order = by_id.get(fill.order_id)
        if order:
            created = order.created_at if order.created_at.tzinfo else order.created_at.replace(tzinfo=timezone.utc)
            filled = fill.created_at if fill.created_at.tzinfo else fill.created_at.replace(tzinfo=timezone.utc)
            latencies.append((filled - created).total_seconds())
    signals = session.scalars(select(SignalRecord).where(
        SignalRecord.mode == mode, SignalRecord.created_at >= day_start,
    )).all()
    estimated = [
        float(signal.explanation["option"]["estimated_slippage_percent"])
        for signal in signals
        if isinstance(signal.explanation, dict)
        and isinstance(signal.explanation.get("option"), dict)
        and signal.explanation["option"].get("estimated_slippage_percent") is not None
    ]
    trades = session.scalars(select(TradeRecord).where(
        TradeRecord.mode == mode, TradeRecord.exit_time >= day_start, TradeRecord.slippage.is_not(None),
    )).all()
    actual = [trade.slippage for trade in trades if trade.slippage is not None]
    return {
        "orders_submitted": len(orders),
        "orders_acknowledged": sum(order.broker_order_id is not None for order in orders),
        "orders_filled": sum(order.status == "filled" for order in orders),
        "orders_canceled": sum(order.status in {"canceled", "expired", "done_for_day"} for order in orders),
        "orders_rejected": sum(order.status in {"rejected", "error"} for order in orders),
        "average_execution_latency_seconds": sum(latencies) / len(latencies) if latencies else None,
        "average_estimated_slippage_percent": sum(estimated) / len(estimated) if estimated else None,
        "average_realized_slippage": sum(actual) / len(actual) if actual else None,
    }


def _event_dict(item: EventLog):
    return {"id": item.id, "level": item.level, "event_type": item.event_type, "message": item.message,
            "context": item.context, "created_at": _iso(item.created_at)}


def _daily_profit(items: list[TradeRecord]):
    totals: dict[str, float] = {}
    for item in items:
        day = item.exit_time.astimezone(ET).date().isoformat()
        totals[day] = totals.get(day, 0) + item.realized_pnl
    return [{"day": key, "pnl": value} for key, value in sorted(totals.items())]


app = create_app()
