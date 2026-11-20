from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from backend.database import SessionLocal, get_settings
from backend.market_data import AlpacaMarketData

OUT = Path(__file__).resolve().parent
SOURCE = Path('/tmp/spytrader-underlying-2026-11-19_20.csv')
DESTINATION = OUT / 'underlying_2026-11-10_20.csv'


def main():
    with SessionLocal() as session:
        settings = get_settings(session)
    if settings.trading_mode != 'paper':
        raise RuntimeError('Diagnostic data refresh is restricted to paper configuration')
    existing = pd.read_csv(SOURCE)
    existing['timestamp'] = pd.to_datetime(existing['timestamp'], utc=True)
    market = AlpacaMarketData('paper', options_enabled=False)
    pieces = [existing]
    requests = []
    for symbol in ('SPY', 'QQQ'):
        start = datetime(2026, 11, 20, 18, 48, tzinfo=timezone.utc)
        end = datetime(2026, 11, 20, 21, 0, tzinfo=timezone.utc)
        frame = market.stock_bars(symbol, start=start, end=end)
        frame['symbol'] = symbol
        pieces.append(frame)
        requests.append({'symbol': symbol, 'start': start.isoformat(), 'end': end.isoformat(), 'rows': len(frame)})
    combined = pd.concat(pieces, ignore_index=True)
    combined['timestamp'] = pd.to_datetime(combined['timestamp'], utc=True)
    combined = combined.drop_duplicates(['symbol', 'timestamp'], keep='last').sort_values(['timestamp', 'symbol'])
    combined.to_csv(DESTINATION, index=False)
    counts = {}
    for symbol in ('SPY', 'QQQ'):
        rows = combined[(combined['symbol'] == symbol) & (combined['timestamp'].dt.date == datetime(2026, 11, 20).date())]
        local_time = rows['timestamp'].dt.tz_convert('America/New_York').dt.time
        regular = rows[(local_time >= datetime.strptime('09:30', '%H:%M').time()) &
                       (local_time < datetime.strptime('16:00', '%H:%M').time())]
        counts[symbol] = {'november_20_regular_bars': len(regular),
                          'last_timestamp': rows['timestamp'].max().isoformat()}
    result = {'requests': requests, 'rows': len(combined), 'counts': counts, 'orders_submitted': 0}
    (OUT / 'data_refresh.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
