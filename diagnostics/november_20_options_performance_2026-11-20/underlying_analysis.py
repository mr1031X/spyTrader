from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from backend.database import SessionLocal, get_settings
from backend.indicators import filter_regular_session

OUT = Path(__file__).resolve().parent
ROOT = OUT.parent
DATA = ROOT / 'momentum_entry_experiment_2026-11-10_20' / 'underlying_2026-11-10_20.csv'
DIAGNOSTIC_MODULE = ROOT / 'signal_threshold_2026-11-19_20' / 'run_signal_diagnostics.py'
DAY = '2026-11-20'
ET = 'America/New_York'


def load_diagnostic_module():
    spec = importlib.util.spec_from_file_location('prior_signal_diagnostics', DIAGNOSTIC_MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidates(path):
    payload = json.loads(path.read_text())
    rows = []
    for item in payload['decisions']:
        timestamp = pd.Timestamp(item['timestamp'])
        if timestamp.tz_convert(ET).date().isoformat() != DAY:
            continue
        rows.append({**item, 'timestamp_utc': timestamp.isoformat()})
    return rows


def close_at_or_before(frame, timestamp):
    rows = frame[frame['timestamp'] < timestamp]
    return float(rows['close'].iloc[-1]) if len(rows) else np.nan


def directional_return(start, end, direction):
    value = end / start - 1
    return (-value if direction == 'put' else value) * 100


def main():
    data = pd.read_csv(DATA)
    data['timestamp'] = pd.to_datetime(data['timestamp'], utc=True)
    data = filter_regular_session(data)
    data['session'] = data['timestamp'].dt.tz_convert(ET).dt.date.astype(str)
    day_rows = data[data['session'] == DAY]
    assert len(day_rows) == 780
    with SessionLocal() as session:
        settings = get_settings(session)
    assert settings.trading_mode == 'paper'

    module = load_diagnostic_module()
    contexts = module.build_contexts(
        data, settings,
        timeline_start=datetime(2026, 11, 20, 14, 30, tzinfo=timezone.utc),
        timeline_end=datetime(2026, 11, 20, 21, 0, tzinfo=timezone.utc),
    )
    baseline = candidates(ROOT / 'momentum_entry_experiment_2026-11-10_20' / 'baseline_replay.json')
    experiment_a = candidates(ROOT / 'momentum_entry_experiment_2026-11-10_20' / 'experiment_a_breakout_3_atr_replay.json')
    moves = module.detect_moves(day_rows, contexts, baseline)

    move_rows = []
    for move in moves:
        symbol = move['underlying']
        frame = day_rows[day_rows['symbol'] == symbol].sort_values('timestamp')
        start = pd.Timestamp(move['window_start_et'])
        detection = pd.Timestamp(move['first_observable_et'])
        end = pd.Timestamp(move['last_confirmed_et'])
        direction = 'put' if move['direction'] == 'bearish' else 'call'
        start_price = close_at_or_before(frame, start)
        detection_price = close_at_or_before(frame, detection)
        end_price = close_at_or_before(frame, end)
        total_move = directional_return(start_price, end_price, direction)
        remaining_move = directional_return(detection_price, end_price, direction)
        a_during = [item for item in experiment_a if item['underlying'] == symbol and
                    start <= pd.Timestamp(item['timestamp']) <= end]
        row = {
            **move,
            'detection_reference_price': detection_price,
            'directional_move_after_detection_percent': remaining_move,
            'move_remaining_at_detection_percent_of_total': remaining_move / total_move * 100 if total_move else np.nan,
            'experiment_a_candidates_during_move': len(a_during),
            'experiment_a_candidate_scores': json.dumps([item['technical_score'] for item in a_during]),
            'entry_before_15_00_cutoff': detection < pd.Timestamp('2026-11-20T15:00:00-05:00'),
            'options_pnl_status': 'NOT_EVALUABLE_NO_HISTORICAL_BID_ASK_QUOTES',
        }
        for horizon in (5, 15, 30, 60):
            target = detection + pd.Timedelta(minutes=horizon)
            available = frame[frame['timestamp'] < target]
            if available.empty or available['timestamp'].iloc[-1] + pd.Timedelta(minutes=1) < target:
                row[f'directional_return_{horizon}m_percent'] = np.nan
            else:
                row[f'directional_return_{horizon}m_percent'] = directional_return(
                    detection_price, float(available['close'].iloc[-1]), direction,
                )
        move_rows.append(row)
    pd.DataFrame(move_rows).to_csv(OUT / 'underlying_move_opportunities.csv', index=False)

    candidate_rows = []
    candidate_outcomes = pd.read_csv(ROOT / 'momentum_entry_experiment_2026-11-10_20' / 'candidate_outcomes.csv')
    candidate_outcomes['timestamp'] = pd.to_datetime(candidate_outcomes['timestamp'], utc=True)
    candidate_outcomes = candidate_outcomes[candidate_outcomes['session'] == DAY]
    for item in baseline + [item for item in experiment_a if item not in baseline]:
        match = candidate_outcomes[
            (candidate_outcomes['underlying'] == item['underlying']) &
            (candidate_outcomes['strategy'] == item['strategy']) &
            (candidate_outcomes['timestamp'] == pd.Timestamp(item['timestamp_utc']))
        ].iloc[0]
        if item['technical_score'] < 80:
            rejection = 'SIGNAL_SCORE_TOO_LOW_AND_ZERO_ALLOCATION_BELOW_80'
        elif item['strategy'] == 'momentum_breakout':
            rejection = 'EXPERIMENT_DISABLED_BASELINE_2_5_ATR_EXTENSION_LIMIT'
        else:
            rejection = 'NONE_TECHNICAL'
        candidate_rows.append({
            'timestamp_utc': item['timestamp_utc'],
            'time_et': pd.Timestamp(item['timestamp']).tz_convert(ET).strftime('%H:%M'),
            'underlying': item['underlying'], 'strategy': item['strategy'],
            'direction': item['direction'], 'technical_score': item['technical_score'],
            'technical_rejection': rejection,
            'reference_price': match['reference_price'], 'extension_atr': match['extension_atr'],
            **{key: match[key] for key in (
                'directional_return_5m_pct', 'directional_return_15m_pct',
                'directional_return_30m_pct', 'directional_return_60m_pct', 'mfe_60m_pct', 'mae_60m_pct',
            )},
            'options_pnl_status': 'NOT_EVALUABLE_NO_HISTORICAL_BID_ASK_QUOTES',
        })
    pd.DataFrame(candidate_rows).to_csv(OUT / 'signal_opportunities.csv', index=False)

    sessions = []
    for symbol, frame in day_rows.groupby('symbol'):
        frame = frame.sort_values('timestamp')
        sessions.append({
            'underlying': symbol, 'regular_bars': len(frame),
            'open': float(frame.iloc[0]['open']), 'close': float(frame.iloc[-1]['close']),
            'open_to_close_percent': (float(frame.iloc[-1]['close']) / float(frame.iloc[0]['open']) - 1) * 100,
            'session_low': float(frame['low'].min()), 'session_high': float(frame['high'].max()),
            'range_percent_of_open': (float(frame['high'].max()) - float(frame['low'].min())) / float(frame.iloc[0]['open']) * 100,
        })
    pd.DataFrame(sessions).to_csv(OUT / 'november_20_underlying_summary.csv', index=False)
    result = {
        'session': DAY, 'underlying_bars': len(day_rows), 'bars_per_symbol': 390,
        'objective_move_method': 'Trailing 15-minute change >= max(0.3%, 2*ATR/price), sustained five-minute direction; grouped same-direction detections separated by <=10 minutes; top three moves per symbol.',
        'move_count': len(move_rows), 'signal_candidate_count': len(candidate_rows),
        'options_simulated_trades': 0,
        'options_simulation_status': 'NOT_EVALUABLE_NO_HISTORICAL_BID_ASK_QUOTES',
        'production_changes': 'NONE', 'orders_submitted': 0,
    }
    (OUT / 'underlying_analysis.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
