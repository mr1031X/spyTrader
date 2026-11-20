"""Validated API payloads shared by the dashboard and trading runtime."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field

from .config import BotSettings


class ControlRequest(BaseModel):
    acknowledge_risk: bool = False


class LiveActivationRequest(BaseModel):
    confirmation: str
    risk_reviewed: bool
    settings: BotSettings


class EmergencyRequest(BaseModel):
    confirmation: str = Field(min_length=1)


class ModeRequest(BaseModel):
    mode: Literal["paper", "live"]


class BacktestParameters(BaseModel):
    name: str = Field(default="Historical quote replay", max_length=120)
    initial_equity: float = Field(default=10_000, gt=0)
    slippage_percent: float = Field(default=0.5, ge=0, le=20)
    fee_per_contract: float = Field(default=0, ge=0)
