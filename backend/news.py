"""Optional, fail-open-for-analysis / fail-closed-for-news-classification news module."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import Environment, get_env

HIGH_IMPACT_TERMS = (
    "federal reserve", "fomc", "powell", "cpi", "inflation", "nonfarm", "jobs report",
    "employment report", "gdp", "rate decision", "apple earnings", "microsoft earnings",
    "nvidia earnings", "amazon earnings", "meta earnings", "alphabet earnings",
)


@dataclass(frozen=True)
class NewsAssessment:
    unstable: bool
    headlines: list[dict[str, Any]]
    provider: str | None
    error: str | None = None


class NewsService:
    def __init__(self, env: Environment | None = None):
        self.env = env or get_env()
        self._cache: NewsAssessment | None = None
        self._cache_time = 0.0

    def assess(self, enabled: bool) -> NewsAssessment:
        if not enabled:
            return NewsAssessment(False, [], None)
        if self._cache and time.monotonic() - self._cache_time < 300:
            return self._cache
        headlines = []
        error = None
        provider = None
        if self.env.benzinga_api_key:
            provider = "benzinga"
            try:
                response = httpx.get(
                    "https://api.benzinga.com/api/v2/news",
                    params={"token": self.env.benzinga_api_key, "symbols": "SPY,QQQ", "pageSize": 20},
                    headers={"Accept": "application/json"}, timeout=8,
                )
                response.raise_for_status()
                for item in response.json() if isinstance(response.json(), list) else []:
                    headlines.append({"title": item.get("title"), "created": item.get("created"),
                                      "url": item.get("url")})
            except Exception as exc:
                error = str(exc)
        else:
            error = "No optional news provider configured; technical analysis remains available"
        unstable = any(any(term in (item.get("title") or "").lower() for term in HIGH_IMPACT_TERMS)
                       for item in headlines)
        self._cache = NewsAssessment(unstable, headlines, provider, error)
        self._cache_time = time.monotonic()
        return self._cache
