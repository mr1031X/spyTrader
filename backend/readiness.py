"""Independent broker and market-data readiness for scanning and execution."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ReadinessState:
    broker_connected: bool = False
    underlying: dict[str, bool] = field(default_factory=dict)
    historical: dict[str, bool] = field(default_factory=dict)
    options_entitlement: bool = False
    option_quotes: bool = False
    order_execution: bool = False
    last_underlying_bar: dict[str, datetime] = field(default_factory=dict)
    last_option_quote: datetime | None = None
    last_completed_candle: dict[str, datetime] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def reset_market(self) -> None:
        self.underlying.clear()
        self.historical.clear()
        self.options_entitlement = False
        self.option_quotes = False
        self.last_underlying_bar.clear()
        self.last_option_quote = None
        self.last_completed_candle.clear()

    def execution_ready(self, symbol: str, reconciled: bool) -> bool:
        return bool(
            self.broker_connected and self.order_execution and reconciled
            and self.underlying.get(symbol, False) and self.historical.get(symbol, False)
            and self.options_entitlement and self.option_quotes
        )

    def to_dict(self, symbols: list[str], reconciled: bool) -> dict[str, Any]:
        per_symbol = {
            symbol: self.execution_ready(symbol, reconciled) for symbol in symbols
        }
        return {
            **asdict(self),
            "underlying_data_ready": all(self.underlying.get(symbol, False) for symbol in symbols),
            "historical_data_ready": all(self.historical.get(symbol, False) for symbol in symbols),
            "execution_ready": any(per_symbol.values()),
            "execution_ready_by_symbol": per_symbol,
            "data_ready": any(per_symbol.values()),
            "last_option_quote": self.last_option_quote.isoformat() if self.last_option_quote else None,
            "last_underlying_bar": {key: value.isoformat() for key, value in self.last_underlying_bar.items()},
            "last_completed_candle": {key: value.isoformat() for key, value in self.last_completed_candle.items()},
        }
