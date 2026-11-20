"""Environment and persisted strategy configuration.

Secrets are read exclusively from the server environment. Strategy and risk values
are persisted in SQLite and validated before they reach the trading engine.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


class ScoreWeights(BaseModel):
    momentum: float = Field(20, ge=0, le=100)
    trend: float = Field(20, ge=0, le=100)
    volume: float = Field(15, ge=0, le=100)
    regime: float = Field(15, ge=0, le=100)
    volatility: float = Field(10, ge=0, le=100)
    liquidity: float = Field(10, ge=0, le=100)
    reward_risk: float = Field(10, ge=0, le=100)

    @model_validator(mode="after")
    def total_is_one_hundred(self):
        if abs(sum(self.model_dump().values()) - 100) > 0.001:
            raise ValueError("Signal factor weights must total 100")
        return self


class BotSettings(BaseModel):
    trading_mode: Literal["paper", "live"] = "paper"
    live_trading_enabled: bool = False
    symbols: list[str] = Field(default_factory=lambda: ["SPY", "QQQ"])
    max_trades_per_day: int = Field(3, ge=1, le=20)
    max_open_positions: int = Field(1, ge=1, le=1)
    standard_allocation_percent: float = Field(10, gt=0, le=100)
    min_allocation_percent: float = Field(5, gt=0, le=100)
    max_allocation_percent: float = Field(15, gt=0, le=100)
    initial_stop_loss_percent: float = Field(10, gt=0, le=90)
    trailing_stop_enabled: bool = True
    trailing_activation_percent: float = Field(20, ge=0, le=1000)
    trailing_distance_percent: float = Field(10, gt=0, le=100)
    fixed_profit_target_enabled: bool = False
    fixed_profit_target_percent: float = Field(50, gt=0, le=1000)
    daily_profit_goal: float = Field(100, ge=0)
    pause_after_daily_goal: bool = True
    daily_loss_limit_percent: float = Field(6, gt=0, le=100)
    weekly_loss_limit_percent: float = Field(12, gt=0, le=100)
    max_consecutive_losses: int = Field(2, ge=1, le=20)
    overnight_positions: bool = False
    min_signal_score: float = Field(80, ge=0, le=100)
    enabled_strategies: list[str] = Field(
        default_factory=lambda: ["momentum_breakout", "trend_continuation", "vwap_reversal"]
    )
    score_weights: ScoreWeights = Field(default_factory=ScoreWeights)
    options_provider: Literal["alpaca", "tradier"] = "alpaca"
    max_quote_age_seconds: float = Field(3, gt=0, le=60)
    max_spread_percent: float = Field(8, gt=0, le=100)
    max_slippage_percent: float = Field(2, ge=0, le=100)
    entry_order_timeout_seconds: int = Field(45, ge=5, le=600)
    exit_order_reprice_seconds: int = Field(15, ge=5, le=300)
    min_absolute_delta: float = Field(0.40, ge=0, le=1)
    max_absolute_delta: float = Field(0.70, ge=0, le=1)
    min_open_interest: int = Field(100, ge=0)
    min_contract_volume: int = Field(10, ge=0)
    opening_range_minutes: int = Field(15, ge=5, le=60)
    new_entry_cutoff_et: str = "15:00"
    liquidation_start_et: str = "15:40"
    target_flat_time_et: str = "15:45"
    close_positions_eod: bool = True
    transaction_fee_per_contract: float = Field(0, ge=0)
    notifications_enabled: bool = False
    news_filter_enabled: bool = False
    ml_filter_enabled: bool = False

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: list[str]) -> list[str]:
        result = list(dict.fromkeys(s.strip().upper() for s in value if s.strip()))
        if not result:
            raise ValueError("At least one underlying is required")
        return result

    @field_validator("enabled_strategies")
    @classmethod
    def valid_strategies(cls, value: list[str]) -> list[str]:
        allowed = {"momentum_breakout", "trend_continuation", "vwap_reversal"}
        if not set(value).issubset(allowed):
            raise ValueError("Unknown strategy")
        return value

    @field_validator("new_entry_cutoff_et", "liquidation_start_et", "target_flat_time_et")
    @classmethod
    def valid_time(cls, value: str) -> str:
        try:
            hour, minute = map(int, value.split(":"))
            assert 0 <= hour <= 23 and 0 <= minute <= 59
        except (ValueError, AssertionError) as exc:
            raise ValueError("Time must use 24-hour HH:MM format") from exc
        return value

    @model_validator(mode="after")
    def allocation_and_delta_ranges(self):
        if not self.min_allocation_percent <= self.standard_allocation_percent <= self.max_allocation_percent:
            raise ValueError("Allocation tiers must satisfy minimum ≤ standard ≤ maximum")
        if self.min_absolute_delta > self.max_absolute_delta:
            raise ValueError("Minimum delta cannot exceed maximum delta")
        return self


class Environment(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    trading_mode: Literal["paper", "live"] = "paper"
    live_trading_enabled: bool = False
    auto_trading_enabled: bool = False
    market_data_provider: Literal["alpaca", "tradier"] = "alpaca"
    trading_symbols: str = "SPY,QQQ"
    initial_paper_capital: float = 10_000  # Informational; actual broker equity is authoritative.
    max_trades_per_day: int = 3
    min_signal_score: float = 80
    standard_allocation_percent: float = 10
    min_allocation_percent: float = 5
    max_allocation_percent: float = 15
    initial_stop_loss_percent: float = 10
    trailing_stop_enabled: bool = True
    trailing_activation_percent: float = 20
    trailing_distance_percent: float = 10
    fixed_profit_target_enabled: bool = False
    fixed_profit_target_percent: float = 50
    daily_profit_goal: float = 100
    pause_after_daily_goal: bool = True
    daily_loss_limit_percent: float = 6
    weekly_loss_limit_percent: float = 12
    max_consecutive_losses: int = 2
    new_entry_cutoff_et: str = "15:00"
    liquidation_start_et: str = "15:40"
    target_flat_time_et: str = "15:45"
    close_positions_eod: bool = True
    notifications_enabled: bool = False
    alpaca_paper_api_key: str = ""
    alpaca_paper_secret_key: str = ""
    alpaca_live_api_key: str = ""
    alpaca_live_secret_key: str = ""
    alpaca_stock_feed: Literal["iex", "sip"] = "iex"
    alpaca_options_feed: Literal["opra", "indicative"] = "opra"
    tradier_api_key: str = ""
    tradier_base_url: str = "https://api.tradier.com/v1"
    benzinga_api_key: str = ""
    trading_economics_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    alert_email: str = ""
    database_url: str = f"sqlite:///{DATA_DIR / 'apex.db'}"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    def initial_settings(self) -> BotSettings:
        """Environment values seed a new database; the dashboard owns later changes."""
        return BotSettings(
            trading_mode=self.trading_mode,
            live_trading_enabled=self.live_trading_enabled,
            symbols=[item.strip() for item in self.trading_symbols.split(",")],
            max_trades_per_day=self.max_trades_per_day,
            min_signal_score=self.min_signal_score,
            standard_allocation_percent=self.standard_allocation_percent,
            min_allocation_percent=self.min_allocation_percent,
            max_allocation_percent=self.max_allocation_percent,
            initial_stop_loss_percent=self.initial_stop_loss_percent,
            trailing_stop_enabled=self.trailing_stop_enabled,
            trailing_activation_percent=self.trailing_activation_percent,
            trailing_distance_percent=self.trailing_distance_percent,
            fixed_profit_target_enabled=self.fixed_profit_target_enabled,
            fixed_profit_target_percent=self.fixed_profit_target_percent,
            daily_profit_goal=self.daily_profit_goal,
            pause_after_daily_goal=self.pause_after_daily_goal,
            daily_loss_limit_percent=self.daily_loss_limit_percent,
            weekly_loss_limit_percent=self.weekly_loss_limit_percent,
            max_consecutive_losses=self.max_consecutive_losses,
            options_provider=self.market_data_provider,
            new_entry_cutoff_et=self.new_entry_cutoff_et,
            liquidation_start_et=self.liquidation_start_et,
            target_flat_time_et=self.target_flat_time_et,
            close_positions_eod=self.close_positions_eod,
            notifications_enabled=self.notifications_enabled,
        )

    def credentials(self, mode: str) -> tuple[str, str]:
        if mode == "paper":
            return self.alpaca_paper_api_key, self.alpaca_paper_secret_key
        return self.alpaca_live_api_key, self.alpaca_live_secret_key

    def credentials_configured(self, mode: str) -> bool:
        key, secret = self.credentials(mode)
        return bool(key.strip() and secret.strip())


@lru_cache
def get_env() -> Environment:
    return Environment()
