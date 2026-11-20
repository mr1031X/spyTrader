"""Trading-session-aware historical bars with incremental per-symbol caching."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import pandas as pd
from alpaca.data.timeframe import TimeFrame

from .indicators import filter_session_candles


class HistoricalBarCache:
    def __init__(self, market_data: Any, calendar: Callable[[date, date], list[dict[str, Any]]],
                 min_sessions: int = 6, refresh_seconds: float = 45):
        self.market_data = market_data
        self.calendar = calendar
        self.min_sessions = min_sessions
        self.refresh_seconds = refresh_seconds
        self._bars: dict[str, pd.DataFrame] = {}
        self._coverage: dict[str, tuple[datetime, datetime]] = {}
        self._refreshed: dict[str, datetime] = {}

    def clear(self) -> None:
        self._bars.clear()
        self._coverage.clear()
        self._refreshed.clear()

    def get(self, symbol: str, now: datetime) -> pd.DataFrame:
        sessions = self.sessions(now)
        if not sessions:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "timestamp"])
        first_open = sessions[0][0]
        cached = self._bars.get(symbol)
        coverage = self._coverage.get(symbol)
        refreshed = self._refreshed.get(symbol)
        fresh = refreshed is not None and (now - refreshed).total_seconds() < self.refresh_seconds
        covers = coverage is not None and coverage[0] <= first_open
        if cached is not None and fresh and covers:
            return cached.copy()
        if cached is not None and covers and not cached.empty:
            start = max(first_open, cached["timestamp"].max().to_pydatetime() - timedelta(minutes=2))
        else:
            start = first_open
        recent = self.market_data.stock_bars(
            symbol, TimeFrame.Minute, start=start, end=now,
        )
        if cached is not None and covers and not cached.empty:
            combined = pd.concat([cached, recent], ignore_index=True)
        else:
            combined = recent.copy()
        if combined.empty:
            result = combined
        else:
            combined["timestamp"] = pd.to_datetime(combined["timestamp"], utc=True)
            combined = combined.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
            result = filter_session_candles(combined, sessions).reset_index(drop=True)
        self._bars[symbol] = result
        self._coverage[symbol] = (min(start, first_open), now)
        self._refreshed[symbol] = now
        return result.copy()

    def sessions(self, now: datetime) -> list[tuple[datetime, datetime]]:
        today = now.astimezone(timezone.utc).date()
        raw = self.calendar(today - timedelta(days=28), today)
        parsed = []
        for item in raw:
            try:
                opened = _datetime(item["open"], item.get("date"))
                closed = _datetime(item["close"], item.get("date"))
            except (KeyError, TypeError, ValueError):
                continue
            if opened <= now and opened < closed:
                parsed.append((opened, min(closed, now) if opened.date() == now.date() else closed))
        parsed.sort(key=lambda item: item[0])
        complete_or_current = [item for item in parsed if item[0] <= now]
        return complete_or_current[-self.min_sessions:]


def _datetime(value: Any, session_date: Any = None) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value)
        if session_date and len(text) == 5 and ":" in text:
            text = f"{session_date}T{text}:00"
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if result.tzinfo is None:
            from zoneinfo import ZoneInfo
            result = result.replace(tzinfo=ZoneInfo("America/New_York"))
    return result.astimezone(timezone.utc)
