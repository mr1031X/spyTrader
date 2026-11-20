"""Bounded, per-session opportunity and execution decision diagnostics."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .database import OpportunityDiagnosticRecord, json_safe

ET = ZoneInfo("America/New_York")
RECENT_SCAN_LIMIT = 25


def record_scan(session, mode: str, symbol: str, now: datetime, payload: dict[str, Any]) -> None:
    day = now.astimezone(ET).date()
    item = session.scalar(select(OpportunityDiagnosticRecord).where(
        OpportunityDiagnosticRecord.day == day,
        OpportunityDiagnosticRecord.mode == mode,
        OpportunityDiagnosticRecord.underlying == symbol,
    ))
    if item is None:
        item = OpportunityDiagnosticRecord(
            day=day, mode=mode, underlying=symbol, scans=0, bullish_considered=0,
            bearish_considered=0, rejected_signals=0, qualified_signals=0,
            contracts_evaluated=0, contracts_rejected=0, rejection_counts={}, recent_scans=[],
        )
        session.add(item)
    safe = json_safe(payload)
    item.scans += 1
    item.last_scan_at = now
    item.last_scan = safe
    recent = list(item.recent_scans or [])
    recent.append(safe)
    item.recent_scans = recent[-RECENT_SCAN_LIMIT:]
    directions = [
        strategy.get("direction") for strategy in safe.get("strategies", [])
        if strategy.get("direction") in {"call", "put"}
    ]
    if not directions:
        directions = [candidate.get("direction") for candidate in safe.get("candidates", [])]
    for direction in directions:
        if direction == "call":
            item.bullish_considered += 1
        elif direction == "put":
            item.bearish_considered += 1
    item.rejected_signals += int(safe.get("signals_rejected", 0))
    item.qualified_signals += int(safe.get("qualified_signals", 0))
    selections = safe.get("contract_selections") or [safe.get("contract_selection") or {}]
    for stats in selections:
        item.contracts_evaluated += int(stats.get("discovered", 0))
        item.contracts_rejected += int(stats.get("rejected", 0))
    reasons = Counter(item.rejection_counts or {})
    reasons.update(code for code in safe.get("rejection_reasons", []) if code)
    item.rejection_counts = dict(reasons.most_common(30))
    decision = safe.get("final_decision")
    if decision in {"QUALIFIED", "ORDER_SUBMITTED"}:
        item.last_qualifying_signal = safe
    if decision in {"ORDER_SUBMITTED", "ORDER_REJECTED"}:
        item.last_order_attempt = safe
    item.updated_at = now
    session.commit()
