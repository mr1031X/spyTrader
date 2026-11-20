from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd
from alpaca.data.requests import OptionBarsRequest, OptionTradesRequest
from alpaca.data.timeframe import TimeFrame

from backend.market_data import AlpacaMarketData

OUT = Path(__file__).resolve().parent
CONTRACT = 'SPY261123P00711000'
START = datetime(2026, 11, 20, 15, 20, tzinfo=timezone.utc)
END = datetime(2026, 11, 20, 15, 25, tzinfo=timezone.utc)


def main():
    market = AlpacaMarketData('paper')
    key, secret = market.env.credentials('paper')
    result = {
        'contract': CONTRACT, 'start': START.isoformat(), 'end': END.isoformat(),
        'provider': 'Alpaca', 'feed_requested': 'opra', 'orders_submitted': 0,
        'requests': {},
    }
    url = 'https://data.alpaca.markets/v1beta1/options/quotes'
    try:
        response = httpx.get(
            url,
            headers={'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': secret},
            params={
                'symbols': CONTRACT, 'start': START.isoformat(), 'end': END.isoformat(),
                'limit': 20, 'feed': 'opra', 'sort': 'asc',
            },
            timeout=15,
        )
        result['requests']['historical_quotes_rest'] = {
            'url': url, 'status_code': response.status_code,
            'response_excerpt': response.text[:1000],
        }
        if response.is_success:
            payload = response.json()
            quotes = payload.get('quotes', {})
            rows = quotes.get(CONTRACT, []) if isinstance(quotes, dict) else quotes
            pd.DataFrame(rows).to_csv(OUT / 'probe_quotes.csv', index=False)
            result['requests']['historical_quotes_rest']['rows'] = len(rows)
            result['requests']['historical_quotes_rest']['next_page_token'] = payload.get('next_page_token')
    except Exception as exc:
        result['requests']['historical_quotes_rest'] = {'url': url, 'error': f'{type(exc).__name__}: {exc}'}

    for name, request, method in (
        ('historical_bars', OptionBarsRequest(
            symbol_or_symbols=CONTRACT, timeframe=TimeFrame.Minute, start=START, end=END, limit=20,
        ), market.options.get_option_bars),
        ('historical_trades', OptionTradesRequest(
            symbol_or_symbols=CONTRACT, start=START, end=END, limit=20,
        ), market.options.get_option_trades),
    ):
        try:
            response = method(request)
            frame = response.df.reset_index()
            frame.to_csv(OUT / f'probe_{name}.csv', index=False)
            result['requests'][name] = {'rows': len(frame), 'columns': list(frame.columns)}
        except Exception as exc:
            result['requests'][name] = {'error': f'{type(exc).__name__}: {exc}'}

    (OUT / 'data_availability.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
