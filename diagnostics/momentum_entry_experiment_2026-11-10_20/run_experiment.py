from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from backend.backtesting import Backtester
from backend.database import SessionLocal, get_settings
from backend.indicators import filter_regular_session

OUT = Path(__file__).resolve().parent
DATA = OUT / 'underlying_2026-11-10_20.csv'
VARIANTS = {
    'baseline': {},
    'experiment_a_breakout_3_atr': {'experiment_breakout_3_atr_enabled': True},
    'experiment_b_confirmed_continuation': {'experiment_confirmed_continuation_enabled': True},
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('variant', choices=VARIANTS)
    args = parser.parse_args()
    data = pd.read_csv(DATA)
    data['timestamp'] = pd.to_datetime(data['timestamp'], utc=True)
    data = filter_regular_session(data)
    sessions = data['timestamp'].dt.tz_convert('America/New_York').dt.date.astype(str)
    expected = {f'2026-11-{day:02d}' for day in (10, 11, 12, 13, 16, 17, 18, 19, 20)}
    assert set(sessions) == expected
    counts = data.assign(session=sessions).groupby(['session', 'symbol']).size()
    complete_november_20 = counts.loc['2026-11-20']
    assert (complete_november_20 == 390).all(), complete_november_20
    with SessionLocal() as session:
        stored = get_settings(session)
    assert stored.trading_mode == 'paper'
    settings = stored.model_copy(update=VARIANTS[args.variant])
    assert settings.min_signal_score == stored.min_signal_score
    assert settings.min_absolute_delta == stored.min_absolute_delta
    assert settings.max_absolute_delta == stored.max_absolute_delta
    result = Backtester().run_underlying(
        data, settings,
        evaluation_start=datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc),
        evaluation_end=datetime(2026, 11, 20, 21, 0, tzinfo=timezone.utc),
    )
    result.update({
        'variant': args.variant,
        'feature_flags': VARIANTS[args.variant],
        'regular_sessions': sorted(expected),
        'november_20_regular_bars_per_symbol': 390,
        'bars_per_symbol_session': {f'{session}_{symbol}': int(value) for (session, symbol), value in counts.items()},
        'production_settings_saved': False,
        'orders_submitted': 0,
    })
    (OUT / f'{args.variant}_replay.json').write_text(json.dumps(result, indent=2))
    pd.DataFrame(result['decisions']).to_csv(OUT / f'{args.variant}_candidates.csv', index=False)
    print(json.dumps({
        'variant': args.variant,
        'scans': result['scans_completed'],
        'raw_candidates': result['candidate_count'],
        'score_80': sum(item['technical_score'] >= 80 for item in result['decisions']),
        'output': str(OUT / f'{args.variant}_replay.json'),
    }, indent=2, default=str), flush=True)


if __name__ == '__main__':
    main()
