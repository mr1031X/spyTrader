"""Real-time market-data adapters, validation, and streaming quote cache."""
from __future__ import annotations

import asyncio
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx
import pandas as pd
from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.live import OptionDataStream, StockDataStream
from alpaca.data.requests import (
    OptionBarsRequest, OptionLatestQuoteRequest, OptionSnapshotRequest, StockBarsRequest, StockLatestQuoteRequest,
)
from alpaca.data.timeframe import TimeFrame

from .broker import OptionContract
from .config import BotSettings, Environment, get_env


class MarketDataError(RuntimeError):
    pass


class DataEntitlementError(MarketDataError):
    pass


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    timestamp: datetime
    provider: str
    bid_size: float | None = None
    ask_size: float | None = None

    def validation_error(self, now: datetime | None = None, max_age_seconds: float = 3, max_spread_percent: float = 8) -> str | None:
        now = now or datetime.now(timezone.utc)
        timestamp = self.timestamp if self.timestamp.tzinfo else self.timestamp.replace(tzinfo=timezone.utc)
        age = (now - timestamp).total_seconds()
        if age < -5:
            return "Quote timestamp is in the future"
        if age > max_age_seconds:
            return f"Quote is stale ({age:.1f}s old)"
        if self.bid <= 0 or self.ask <= 0:
            return "Bid and ask must be positive"
        if self.bid > self.ask:
            return "Crossed market"
        midpoint = (self.bid + self.ask) / 2
        if midpoint <= 0:
            return "Invalid midpoint"
        spread_percent = (self.ask - self.bid) / midpoint * 100
        if spread_percent > max_spread_percent:
            return f"Spread {spread_percent:.1f}% exceeds {max_spread_percent:.1f}%"
        return None

    @property
    def midpoint(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_percent(self) -> float:
        return (self.ask - self.bid) / self.midpoint * 100 if self.midpoint else float("inf")

    def to_dict(self, now: datetime | None = None) -> dict[str, Any]:
        timestamp = self.timestamp if self.timestamp.tzinfo else self.timestamp.replace(tzinfo=timezone.utc)
        age = ((now or datetime.now(timezone.utc)) - timestamp).total_seconds()
        return {**asdict(self), "timestamp": timestamp.isoformat(), "age_seconds": age,
                "spread_percent": self.spread_percent, "fresh": 0 <= age <= 3}


@dataclass(frozen=True)
class OptionSnapshot:
    contract: OptionContract
    quote: Quote
    delta: float | None
    gamma: float | None
    theta: float | None
    implied_volatility: float | None
    open_interest: int | None
    volume: int | None


class OptionsProvider(ABC):
    name: str

    @abstractmethod
    def option_snapshot(self, contract: OptionContract) -> OptionSnapshot:
        raise NotImplementedError

    def option_snapshots(self, contracts: list[OptionContract]) -> list[OptionSnapshot]:
        results = []
        for contract in contracts:
            try:
                results.append(self.option_snapshot(contract))
            except MarketDataError:
                continue
        return results


class AlpacaMarketData(OptionsProvider):
    name = "alpaca_opra"

    def __init__(self, mode: str = "paper", env: Environment | None = None):
        self.env = env or get_env()
        key, secret = self.env.credentials(mode)
        if not key or not secret:
            raise DataEntitlementError(f"Alpaca {mode} market-data credentials are not configured")
        if self.env.alpaca_options_feed != "opra":
            raise DataEntitlementError("Automated execution requires ALPACA_OPTIONS_FEED=opra; indicative data is blocked")
        self.key = key
        self.secret = secret
        self.stock = StockHistoricalDataClient(api_key=key, secret_key=secret)
        self.options = OptionHistoricalDataClient(api_key=key, secret_key=secret)
        self.stock_feed = DataFeed.IEX if self.env.alpaca_stock_feed == "iex" else DataFeed.SIP
        self.options_feed = OptionsFeed.OPRA

    def _call(self, operation: str, func, *args):
        try:
            return func(*args)
        except Exception as exc:
            raise MarketDataError(f"Alpaca {operation} failed: {exc}") from exc

    @staticmethod
    def _timestamp(value: Any) -> datetime:
        if not isinstance(value, datetime):
            raise MarketDataError("Quote timestamp is missing")
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    def stock_quote(self, symbol: str) -> Quote:
        request = StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=self.stock_feed)
        response = self._call("stock quote", self.stock.get_stock_latest_quote, request)
        item = response.get(symbol)
        if item is None:
            raise MarketDataError(f"No stock quote returned for {symbol}")
        return Quote(symbol, float(item.bid_price), float(item.ask_price), self._timestamp(item.timestamp),
                     self.name, float(item.bid_size or 0), float(item.ask_size or 0))

    def stock_bars(self, symbol: str, timeframe: TimeFrame = TimeFrame.Minute, lookback_minutes: int = 240) -> pd.DataFrame:
        end = datetime.now(timezone.utc)
        request = StockBarsRequest(
            symbol_or_symbols=symbol, timeframe=timeframe, start=end - timedelta(minutes=lookback_minutes),
            end=end, feed=self.stock_feed,
        )
        response = self._call("stock bars", self.stock.get_stock_bars, request)
        frame = response.df
        if frame.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "timestamp"])
        if isinstance(frame.index, pd.MultiIndex):
            try:
                frame = frame.xs(symbol, level="symbol")
            except (KeyError, ValueError):
                frame = frame.xs(symbol, level=0)
        frame = frame.reset_index().rename(columns={"index": "timestamp"})
        if "timestamp" not in frame:
            raise MarketDataError("Stock bars have no timestamps")
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        return frame.sort_values("timestamp").reset_index(drop=True)

    def _snapshots(self, symbols: list[str]) -> dict[str, Any]:
        request = OptionSnapshotRequest(symbol_or_symbols=symbols, feed=self.options_feed)
        return self._call("OPRA option snapshot", self.options.get_option_snapshot, request)

    def _daily_volumes(self, symbols: list[str]) -> dict[str, int]:
        """Fetch observed volume separately; current SDK snapshots omit daily bars."""
        if not symbols:
            return {}
        end = datetime.now(timezone.utc)
        request = OptionBarsRequest(
            symbol_or_symbols=symbols, timeframe=TimeFrame.Day,
            start=end - timedelta(days=10), end=end,
            limit=min(1000, max(100, len(symbols) * 10)),
        )
        response = self._call("OPRA option daily bars", self.options.get_option_bars, request)
        data = getattr(response, "data", response)
        volumes: dict[str, int] = {}
        for symbol in symbols:
            bars = data.get(symbol, []) if hasattr(data, "get") else []
            if not bars:
                continue
            latest = max(bars, key=lambda bar: bar.timestamp)
            if latest.volume is not None:
                volumes[symbol] = int(float(latest.volume))
        return volumes

    def _convert_snapshot(self, contract: OptionContract, item: Any,
                          volume: int | None = None) -> OptionSnapshot:
        latest = item.latest_quote
        if latest is None:
            raise MarketDataError(f"No OPRA quote returned for {contract.symbol}")
        quote = Quote(contract.symbol, float(latest.bid_price), float(latest.ask_price),
                      self._timestamp(latest.timestamp), self.name,
                      float(latest.bid_size or 0), float(latest.ask_size or 0))
        greeks = item.greeks
        daily = getattr(item, "daily_bar", None)  # Compatibility with older alpaca-py snapshots.
        if volume is None and daily is not None and daily.volume is not None:
            volume = int(float(daily.volume))
        open_interest = contract.open_interest
        if open_interest is None and getattr(item, "open_interest", None) is not None:
            open_interest = int(float(item.open_interest))
        return OptionSnapshot(
            contract=contract, quote=quote,
            delta=float(greeks.delta) if greeks and greeks.delta is not None else None,
            gamma=float(greeks.gamma) if greeks and greeks.gamma is not None else None,
            theta=float(greeks.theta) if greeks and greeks.theta is not None else None,
            implied_volatility=float(item.implied_volatility) if item.implied_volatility is not None else None,
            open_interest=open_interest,
            volume=volume,
        )

    def option_snapshot(self, contract: OptionContract) -> OptionSnapshot:
        snapshots = self._snapshots([contract.symbol])
        item = snapshots.get(contract.symbol)
        if item is None:
            raise MarketDataError(f"No OPRA snapshot returned for {contract.symbol}")
        daily = getattr(item, "daily_bar", None)
        volume = None
        if daily is None or daily.volume is None:
            volume = self._daily_volumes([contract.symbol]).get(contract.symbol)
        return self._convert_snapshot(contract, item, volume)

    def option_snapshots(self, contracts: list[OptionContract]) -> list[OptionSnapshot]:
        # Batch requests reduce chain-scanning latency while retaining real OPRA quotes.
        results: list[OptionSnapshot] = []
        for start in range(0, len(contracts), 50):
            batch = contracts[start:start + 50]
            raw = self._snapshots([contract.symbol for contract in batch])
            missing_volume = []
            for contract in batch:
                item = raw.get(contract.symbol)
                daily = getattr(item, "daily_bar", None) if item is not None else None
                if item is not None and item.latest_quote is not None and (daily is None or daily.volume is None):
                    missing_volume.append(contract.symbol)
            volumes = self._daily_volumes(missing_volume)
            for contract in batch:
                item = raw.get(contract.symbol)
                if item is None or item.latest_quote is None:
                    continue
                results.append(self._convert_snapshot(contract, item, volumes.get(contract.symbol)))
        return results


class TradierOptionsProvider(OptionsProvider):
    """Production Tradier quotes only; delayed sandbox data is never accepted."""
    name = "tradier_production"

    def __init__(self, env: Environment | None = None):
        self.env = env or get_env()
        if not self.env.tradier_api_key:
            raise DataEntitlementError("TRADIER_API_KEY is not configured")
        if "sandbox" in self.env.tradier_base_url.lower():
            raise DataEntitlementError("Tradier sandbox quotes are delayed and cannot drive execution")
        self.base_url = self.env.tradier_base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {self.env.tradier_api_key}", "Accept": "application/json"}

    def option_snapshot(self, contract: OptionContract) -> OptionSnapshot:
        try:
            response = httpx.get(f"{self.base_url}/markets/quotes", params={"symbols": contract.symbol, "greeks": "true"},
                                 headers=self.headers, timeout=8)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise MarketDataError(f"Tradier option quote failed: {exc}") from exc
        item = (payload.get("quotes") or {}).get("quote")
        if isinstance(item, list):
            item = item[0] if item else None
        if not item:
            raise MarketDataError(f"No Tradier quote returned for {contract.symbol}")
        timestamp = datetime.now(timezone.utc)  # Receipt time; provider quote timestamps vary by entitlement.
        quote = Quote(contract.symbol, float(item.get("bid") or 0), float(item.get("ask") or 0), timestamp,
                      self.name, item.get("bidsize"), item.get("asksize"))
        greeks = item.get("greeks") or {}
        return OptionSnapshot(contract, quote, greeks.get("delta"), greeks.get("gamma"), greeks.get("theta"),
                              item.get("implied_volatility"), item.get("open_interest"), item.get("volume"))


class QuoteCache:
    def __init__(self):
        self._quotes: dict[str, Quote] = {}
        self._lock = threading.Lock()
        self.connected = False
        self.last_error: str | None = None

    async def update(self, data: Any) -> None:
        try:
            symbol = data.symbol
            quote = Quote(symbol, float(data.bid_price), float(data.ask_price),
                          data.timestamp if data.timestamp.tzinfo else data.timestamp.replace(tzinfo=timezone.utc),
                          "alpaca_stream", float(data.bid_size or 0), float(data.ask_size or 0))
            with self._lock:
                self._quotes[symbol] = quote
                self.connected = True
                self.last_error = None
        except Exception as exc:
            self.last_error = str(exc)

    def get(self, symbol: str) -> Quote | None:
        with self._lock:
            return self._quotes.get(symbol)


class AlpacaStreamManager:
    """Supervised WebSocket streams; the SDK reconnect loop is wrapped for failures."""
    def __init__(self, mode: str, env: Environment | None = None):
        self.env = env or get_env()
        self.mode = mode
        self.cache = QuoteCache()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._stock_stream = None
        self._option_stream = None

    def start(self, stock_symbols: list[str], option_symbols: list[str] | None = None) -> None:
        if self._threads:
            return
        key, secret = self.env.credentials(self.mode)
        if not key or not secret or self.env.alpaca_options_feed != "opra":
            return
        feed = DataFeed.IEX if self.env.alpaca_stock_feed == "iex" else DataFeed.SIP
        self._stock_stream = StockDataStream(key, secret, feed=feed)
        self._stock_stream.subscribe_quotes(self.cache.update, *stock_symbols)
        self._option_stream = OptionDataStream(key, secret, feed=OptionsFeed.OPRA)
        if option_symbols:
            self._option_stream.subscribe_quotes(self.cache.update, *option_symbols)
        for name, stream in (("stock", self._stock_stream), ("option", self._option_stream)):
            thread = threading.Thread(target=self._supervise, args=(name, stream), daemon=True, name=f"apex-{name}-stream")
            thread.start()
            self._threads.append(thread)

    def _supervise(self, name: str, stream: Any) -> None:
        while not self._stop.is_set():
            try:
                stream.run()
            except Exception as exc:
                self.cache.last_error = f"{name} stream: {exc}"
                self.cache.connected = False
                if self._stop.wait(3):
                    break

    def subscribe_options(self, symbols: list[str]) -> None:
        if self._option_stream and symbols:
            self._option_stream.subscribe_quotes(self.cache.update, *symbols)

    def stop(self) -> None:
        self._stop.set()
        for stream in (self._stock_stream, self._option_stream):
            if stream:
                try:
                    asyncio.run(stream.stop_ws())
                except Exception:
                    pass


class MarketDataService:
    def __init__(self, settings: BotSettings, env: Environment | None = None):
        self.settings = settings
        self.env = env or get_env()
        self.alpaca = AlpacaMarketData(settings.trading_mode, self.env)
        self.options_provider: OptionsProvider = (
            self.alpaca if settings.options_provider == "alpaca" else TradierOptionsProvider(self.env)
        )

    def stock_quote(self, symbol: str) -> Quote:
        return self.alpaca.stock_quote(symbol)

    def stock_bars(self, symbol: str, timeframe: TimeFrame = TimeFrame.Minute, lookback_minutes: int = 240) -> pd.DataFrame:
        return self.alpaca.stock_bars(symbol, timeframe, lookback_minutes)

    def option_snapshot(self, contract: OptionContract) -> OptionSnapshot:
        return self.options_provider.option_snapshot(contract)

    def option_snapshots(self, contracts: list[OptionContract]) -> list[OptionSnapshot]:
        return self.options_provider.option_snapshots(contracts)

    def validate_quote(self, quote: Quote) -> None:
        error = quote.validation_error(max_age_seconds=self.settings.max_quote_age_seconds,
                                       max_spread_percent=self.settings.max_spread_percent)
        if error:
            raise MarketDataError(error)
