"""Closed, regular-session candle technical indicators with no forward-looking calculations."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class IndicatorSnapshot:
    price: float
    vwap: float
    ema9: float
    ema21: float
    ema50: float
    rsi: float
    macd: float
    macd_signal: float
    macd_histogram: float
    atr: float
    relative_volume: float
    opening_range_high: float
    opening_range_low: float
    support: float
    resistance: float
    momentum: float
    volatility_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def closed_candles(frame: pd.DataFrame, minutes: int = 1, now: datetime | None = None) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    data = frame.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    now = now or datetime.now(timezone.utc)
    closes = data["timestamp"] + pd.to_timedelta(minutes, unit="m")
    return data.loc[closes <= pd.Timestamp(now)].sort_values("timestamp").reset_index(drop=True)


def filter_regular_session(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    data = frame.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    local = data["timestamp"].dt.tz_convert(ET)
    times = local.dt.time
    return data.loc[(times >= dt_time(9, 30)) & (times < dt_time(16, 0))].reset_index(drop=True)


def filter_session_candles(frame: pd.DataFrame,
                           sessions: Iterable[tuple[datetime, datetime]] | None = None) -> pd.DataFrame:
    if sessions is None:
        return filter_regular_session(frame)
    data = frame.copy()
    if data.empty:
        return data
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    keep = pd.Series(False, index=data.index)
    for opened, closed in sessions:
        keep |= (data["timestamp"] >= pd.Timestamp(opened)) & (data["timestamp"] < pd.Timestamp(closed))
    return data.loc[keep].reset_index(drop=True)


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    avg_loss = losses.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - 100 / (1 + rs)
    return result.fillna(50).where(avg_loss != 0, 100)


def atr(frame: pd.DataFrame, length: int = 14) -> pd.Series:
    previous = frame["close"].shift(1)
    true_range = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous).abs(),
        (frame["low"] - previous).abs(),
    ], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def session_vwap(frame: pd.DataFrame) -> pd.Series:
    data = filter_regular_session(frame)
    typical = (data["high"] + data["low"] + data["close"]) / 3
    day = data["timestamp"].dt.tz_convert(ET).dt.date
    cumulative_volume = data["volume"].groupby(day).cumsum().replace(0, np.nan)
    return (typical * data["volume"]).groupby(day).cumsum() / cumulative_volume


def compute_indicators(frame: pd.DataFrame, opening_range_minutes: int = 15) -> IndicatorSnapshot:
    data = filter_regular_session(frame)
    if len(data) < 55:
        raise ValueError("At least 55 closed regular-session candles are required")
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    close = data["close"].astype(float)
    data["ema9"] = ema(close, 9)
    data["ema21"] = ema(close, 21)
    data["ema50"] = ema(close, 50)
    data["rsi"] = rsi(close)
    macd_line = ema(close, 12) - ema(close, 26)
    signal_line = macd_line.ewm(span=9, adjust=False, min_periods=9).mean()
    data["atr"] = atr(data)
    data["vwap"] = session_vwap(data)
    local = data["timestamp"].dt.tz_convert(ET)
    latest_day = local.iloc[-1].date()
    session_mask = local.dt.date == latest_day
    session = data.loc[session_mask]
    session_open = pd.Timestamp(datetime.combine(latest_day, dt_time(9, 30), tzinfo=ET)).tz_convert(timezone.utc)
    opening = session.loc[
        (session["timestamp"] >= session_open)
        & (session["timestamp"] < session_open + pd.Timedelta(minutes=opening_range_minutes))
    ]
    if opening.empty:
        opening = session.iloc[:1]
    lookback = data.iloc[-20:]
    average_volume = data["volume"].iloc[-21:-1].mean()
    current_volume = float(data["volume"].iloc[-1])
    relative_volume = current_volume / average_volume if average_volume and average_volume > 0 else 0.0
    short_atr = data["atr"].iloc[-5:].mean()
    long_atr = data["atr"].iloc[-30:-5].mean()
    volatility_ratio = float(short_atr / long_atr) if long_atr and long_atr > 0 else 1.0
    price = float(close.iloc[-1])
    previous_close = float(close.iloc[-6])
    last = data.iloc[-1]
    return IndicatorSnapshot(
        price=price,
        vwap=_finite(last["vwap"], price),
        ema9=_finite(last["ema9"], price),
        ema21=_finite(last["ema21"], price),
        ema50=_finite(last["ema50"], price),
        rsi=_finite(last["rsi"], 50),
        macd=_finite(macd_line.iloc[-1]),
        macd_signal=_finite(signal_line.iloc[-1]),
        macd_histogram=_finite(macd_line.iloc[-1] - signal_line.iloc[-1]),
        atr=max(_finite(last["atr"], price * 0.001), 0.0001),
        relative_volume=max(relative_volume, 0),
        opening_range_high=float(opening["high"].max()),
        opening_range_low=float(opening["low"].min()),
        support=float(lookback["low"].min()),
        resistance=float(lookback["high"].max()),
        momentum=(price / previous_close - 1) if previous_close else 0,
        volatility_ratio=volatility_ratio,
    )


def resample_candles(frame: pd.DataFrame, minutes: int, now: datetime | None = None) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    data = filter_regular_session(frame)
    local_dates = data["timestamp"].dt.tz_convert(ET).dt.date
    pieces = []
    for _, session in data.groupby(local_dates):
        grouped = session.set_index("timestamp").resample(
            f"{minutes}min", label="left", closed="left",
        ).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
        pieces.append(grouped)
    if not pieces:
        return frame.iloc[:0].copy()
    result = pd.concat(pieces, ignore_index=True).sort_values("timestamp").reset_index(drop=True)
    return closed_candles(result, minutes, now) if now is not None else result


def _finite(value: Any, default: float = 0.0) -> float:
    number = float(value) if value is not None else default
    return number if np.isfinite(number) else default
