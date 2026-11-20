from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from backend.indicators import closed_candles, compute_indicators, filter_regular_session

OUT = Path(__file__).resolve().parent
DATA = OUT / 'underlying_2026-11-10_20.csv'
VARIANTS = ['baseline', 'experiment_a_breakout_3_atr', 'experiment_b_confirmed_continuation']
ET = 'America/New_York'


def add_event_ids(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        frame['event_id'] = []
        return frame
    frame = frame.sort_values(['underlying', 'timestamp', 'strategy']).copy()
    parts = []
    for (symbol, session, direction), group in frame.groupby(['underlying', 'session', 'direction'], sort=False):
        group = group.sort_values('timestamp').copy()
        new_event = group['timestamp'].diff().gt(pd.Timedelta(minutes=15)).fillna(True)
        numbers = new_event.cumsum().astype(int)
        group['event_id'] = [f'{symbol}_{session}_{direction}_{number}' for number in numbers]
        parts.append(group)
    return pd.concat(parts).sort_values(['timestamp', 'underlying', 'strategy'])


def load_candidates():
    frames = []
    for variant in VARIANTS:
        replay = json.loads((OUT / f'{variant}_replay.json').read_text())
        frame = pd.DataFrame(replay['decisions'])
        if frame.empty:
            frame = pd.DataFrame(columns=['timestamp', 'underlying', 'strategy', 'direction', 'technical_score'])
        frame['timestamp'] = pd.to_datetime(frame['timestamp'], utc=True)
        local = frame['timestamp'].dt.tz_convert(ET)
        frame['session'] = local.dt.date.astype(str)
        frame['time_et'] = local.dt.strftime('%H:%M')
        frame['variant'] = variant
        frame['score_80'] = frame['technical_score'] >= 80
        frames.append(add_event_ids(frame))
    return pd.concat(frames, ignore_index=True)


def outcome(row, data):
    symbol = row['underlying']
    timestamp = row['timestamp']
    rows = data[data['symbol'] == symbol].sort_values('timestamp')
    visible = rows[rows['timestamp'] < timestamp].tail(1600)
    one = filter_regular_session(closed_candles(visible, now=timestamp.to_pydatetime()))
    reference = float(one['close'].iloc[-1])
    indicators = compute_indicators(one)
    extension = abs(indicators.price - indicators.vwap) / indicators.atr
    result = {
        'reference_price': reference,
        'extension_atr': extension,
        'beyond_baseline_2_5_atr': extension > 2.5,
        'beyond_experiment_3_0_atr': extension > 3.0,
    }
    for horizon in (5, 15, 30, 60):
        target = timestamp + pd.Timedelta(minutes=horizon)
        available = rows[rows['timestamp'] < target]
        if available.empty or available['timestamp'].iloc[-1] + pd.Timedelta(minutes=1) < target:
            result[f'directional_return_{horizon}m_pct'] = np.nan
            result[f'close_{horizon}m_available'] = False
            continue
        future_close = float(available['close'].iloc[-1])
        signed = future_close / reference - 1
        if row['direction'] == 'put':
            signed = -signed
        result[f'directional_return_{horizon}m_pct'] = signed * 100
        result[f'close_{horizon}m_available'] = True
    window = rows[(rows['timestamp'] >= timestamp) & (rows['timestamp'] < timestamp + pd.Timedelta(minutes=60))]
    if window.empty:
        result.update({'mfe_60m_pct': np.nan, 'mae_60m_pct': np.nan, 'excursion_bars': 0})
    else:
        if row['direction'] == 'call':
            favorable = float(window['high'].max()) / reference - 1
            adverse = float(window['low'].min()) / reference - 1
        else:
            favorable = 1 - float(window['low'].min()) / reference
            adverse = 1 - float(window['high'].max()) / reference
        result.update({'mfe_60m_pct': favorable * 100, 'mae_60m_pct': adverse * 100,
                       'excursion_bars': len(window)})
    return result


def main():
    data = pd.read_csv(DATA)
    data['timestamp'] = pd.to_datetime(data['timestamp'], utc=True)
    data = filter_regular_session(data)
    candidates = load_candidates()
    measurements = [outcome(row, data) for _, row in candidates.iterrows()]
    candidates = pd.concat([candidates.reset_index(drop=True), pd.DataFrame(measurements)], axis=1)
    candidates.to_csv(OUT / 'candidate_outcomes.csv', index=False)

    baseline = candidates[candidates['variant'] == 'baseline']
    moves = pd.read_csv(OUT.parent / 'signal_threshold_2026-11-19_20' / 'missed_opportunities.csv')
    moves['move_id'] = [f'move_{index}' for index in moves.index]
    for column in ('window_start_et', 'first_observable_et', 'last_confirmed_et'):
        moves[column] = pd.to_datetime(moves[column], utc=True)

    event_rows = []
    for (variant, event_id), group in candidates.groupby(['variant', 'event_id']):
        group = group.sort_values('timestamp')
        first = group.iloc[0]
        matching_moves = moves[
            (moves['underlying'] == first['underlying']) &
            (moves['session'] == first['session']) &
            (moves['direction'] == ('bullish' if first['direction'] == 'call' else 'bearish')) &
            (moves['window_start_et'] <= first['timestamp']) &
            (moves['last_confirmed_et'] >= first['timestamp'])
        ]
        baseline_overlap = baseline[
            (baseline['underlying'] == first['underlying']) &
            (baseline['direction'] == first['direction']) &
            (baseline['timestamp'].sub(first['timestamp']).abs() <= pd.Timedelta(minutes=15))
        ]
        event_rows.append({
            'variant': variant, 'event_id': event_id, 'session': first['session'],
            'underlying': first['underlying'], 'direction': first['direction'],
            'first_timestamp': first['timestamp'].isoformat(), 'first_time_et': first['time_et'],
            'raw_candidates': len(group), 'strategies': ','.join(sorted(group['strategy'].unique())),
            'strategy_count': group['strategy'].nunique(), 'first_score': first['technical_score'],
            'maximum_score': group['technical_score'].max(), 'score_80': group['score_80'].any(),
            'new_versus_baseline': variant != 'baseline' and baseline_overlap.empty,
            'november_19_move_overlap': len(matching_moves) > 0,
            'november_19_move_ids': ','.join(matching_moves['move_id']),
            'november_19_recovered_move_ids': ','.join(matching_moves.loc[
                matching_moves['first_observable_et'] <= first['timestamp'], 'move_id'
            ]),
            'november_19_recovered_after_observable': bool((matching_moves['first_observable_et'] <= first['timestamp']).any()),
            'reference_price': first['reference_price'], 'extension_atr': first['extension_atr'],
            'beyond_baseline_2_5_atr': first['beyond_baseline_2_5_atr'],
            'beyond_experiment_3_0_atr': first['beyond_experiment_3_0_atr'],
            **{key: first[key] for key in (
                'directional_return_5m_pct', 'directional_return_15m_pct',
                'directional_return_30m_pct', 'directional_return_60m_pct',
                'close_5m_available', 'close_15m_available', 'close_30m_available', 'close_60m_available',
                'mfe_60m_pct', 'mae_60m_pct', 'excursion_bars',
            )},
        })
    events = pd.DataFrame(event_rows).sort_values(['variant', 'first_timestamp', 'underlying'])
    events['overlaps_prior_same_direction_event_within_60m'] = False
    for (variant, symbol, session, direction), group in events.groupby(['variant', 'underlying', 'session', 'direction']):
        indices = group.index
        timestamps = pd.to_datetime(group['first_timestamp'])
        for position in range(1, len(group)):
            events.loc[indices[position], 'overlaps_prior_same_direction_event_within_60m'] = (
                timestamps.iloc[position] - timestamps.iloc[position - 1] <= pd.Timedelta(minutes=60)
            )
    events.to_csv(OUT / 'event_outcomes.csv', index=False)

    comparison = []
    for variant in VARIANTS:
        group = candidates[candidates['variant'] == variant]
        event_group = events[events['variant'] == variant]
        sideways = group[group['session'] == '2026-11-18']
        sideways_events = event_group[event_group['session'] == '2026-11-18']
        november_19 = event_group[event_group['session'] == '2026-11-19']
        recovered = set(','.join(november_19['november_19_move_ids']).split(',')) - {''}
        recovered_after = set(','.join(november_19['november_19_recovered_move_ids']).split(',')) - {''}
        row = {
            'variant': variant, 'raw_candidates': len(group), 'candidates_score_80': int(group['score_80'].sum()),
            'distinct_events': len(event_group), 'distinct_events_score_80': int(event_group['score_80'].sum()),
            'new_events_versus_baseline': int(event_group['new_versus_baseline'].sum()),
            'november_19_events': len(november_19),
            'november_19_events_overlapping_objective_moves': int(november_19['november_19_move_overlap'].sum()),
            'november_19_moves_recovered': len(recovered),
            'november_19_moves_recovered_after_observable': len(recovered_after),
            'sideways_november_18_raw': len(sideways), 'sideways_november_18_events': len(sideways_events),
            'sideways_november_18_score_80': int(sideways['score_80'].sum()),
            'events_beyond_baseline_extension': int(event_group['beyond_baseline_2_5_atr'].sum()),
            'repeated_scans_within_events': len(group) - len(event_group),
            'events_with_multiple_strategies': int((event_group['strategy_count'] > 1).sum()),
            'events_overlapping_prior_within_60m': int(event_group['overlaps_prior_same_direction_event_within_60m'].sum()),
        }
        for horizon in (5, 15, 30, 60):
            values = event_group[f'directional_return_{horizon}m_pct'].dropna()
            row[f'median_directional_return_{horizon}m_pct'] = values.median()
            row[f'positive_directional_return_{horizon}m_pct'] = (values > 0).mean() * 100 if len(values) else np.nan
            row[f'outcomes_{horizon}m_available'] = len(values)
        row['median_mfe_60m_pct'] = event_group['mfe_60m_pct'].median()
        row['median_mae_60m_pct'] = event_group['mae_60m_pct'].median()
        comparison.append(row)
    comparison_frame = pd.DataFrame(comparison)
    comparison_frame.to_csv(OUT / 'comparison.csv', index=False)

    grouped = []
    for (variant, symbol, strategy), group in candidates.groupby(['variant', 'underlying', 'strategy']):
        event_group = add_event_ids(group.drop(columns=['event_id']))
        grouped.append({
            'variant': variant, 'underlying': symbol, 'strategy': strategy, 'raw_candidates': len(group),
            'candidates_score_80': int(group['score_80'].sum()), 'distinct_events': event_group['event_id'].nunique(),
        })
    pd.DataFrame(grouped).to_csv(OUT / 'candidates_by_symbol_strategy.csv', index=False)

    session_rows = []
    sessions = sorted(data['timestamp'].dt.tz_convert(ET).dt.date.astype(str).unique())
    for variant in VARIANTS:
        for session in sessions:
            group = candidates[(candidates['variant'] == variant) & (candidates['session'] == session)]
            event_group = events[(events['variant'] == variant) & (events['session'] == session)]
            session_rows.append({'variant': variant, 'session': session, 'raw_candidates': len(group),
                                 'candidates_score_80': int(group['score_80'].sum()), 'distinct_events': len(event_group),
                                 'distinct_events_score_80': int(event_group['score_80'].sum())})
    pd.DataFrame(session_rows).to_csv(OUT / 'session_comparison.csv', index=False)
    events[(events['session'] == '2026-11-19') & events['november_19_move_overlap']].to_csv(
        OUT / 'november_19_recoveries.csv', index=False)
    analysis = {
        'event_definition': 'Same variant, symbol, session, and direction; a gap over 15 minutes starts a new event. Strategies are merged within an event.',
        'outcome_method': 'Directional underlying close returns at 5/15/30/60 minutes and high/low MFE/MAE over up to 60 minutes. Outcomes are ex-post evaluation only.',
        'extension_method': 'Absolute close-to-session-VWAP distance divided by one-minute ATR at the candidate timestamp.',
        'comparison': comparison,
        'production_changes_to_parameters': 'NONE',
        'orders_submitted': 0,
        'historical_options_results': 'NOT_EVALUATED_NO_ALIGNED_HISTORICAL_BID_ASK_DATA',
    }
    (OUT / 'analysis.json').write_text(json.dumps(analysis, indent=2, default=str))
    print(comparison_frame.to_string(index=False))


if __name__ == '__main__':
    main()
