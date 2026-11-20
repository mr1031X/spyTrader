from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from backend.database import SessionLocal, get_settings

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT))
from run_signal_diagnostics import MAIN_DAYS, build_contexts, candidate_counts, condition_rows, filter_regular_session, run_variant
VARIANTS = {
    'baseline': {},
    'relative_volume_1_0': {'volume': 1.0},
    'relative_volume_1_1': {'volume': 1.1},
    'vwap_weighted': {'vwap_mandatory': False},
    'ema_weighted': {'ema_mandatory': False},
    'confirmed_beyond_opening_no_fresh_cross': {'fresh_cross': False},
    'candle_weighted': {'candle_mandatory': False},
    'extension_3_0_atr': {'extension': 3.0},
    'extension_3_5_atr': {'extension': 3.5},
    'independent_trend_volatility_current_noop': {'regime': 'independent'},
    'volume_1_0_plus_vwap_weighted': {'volume': 1.0, 'vwap_mandatory': False},
    'volume_1_0_plus_extension_3_0': {'volume': 1.0, 'extension': 3.0},
    'confirmed_beyond_opening_plus_volume_1_0': {'fresh_cross': False, 'volume': 1.0},
}


def main():
    analysis_path = OUT / 'analysis.json'
    analysis = json.loads(analysis_path.read_text())
    candidates_path = OUT / 'candidate_observations.csv'
    existing = pd.read_csv(candidates_path)
    data = pd.read_csv('/tmp/spytrader-underlying-2026-11-19_20.csv')
    with SessionLocal() as session:
        settings = get_settings(session)
    contexts = []
    for day in (19, 20):
        contexts.extend(build_contexts(
            data, settings,
            timeline_start=datetime(2026, 11, day, tzinfo=timezone.utc),
            timeline_end=datetime(2026, 11, day, 14, 29, 59, tzinfo=timezone.utc),
        ))
    contexts.extend(build_contexts(
        data, settings,
        timeline_start=datetime(2026, 11, 19, 21, 0, tzinfo=timezone.utc),
        timeline_end=datetime(2026, 11, 19, 23, 59, 59, tzinfo=timezone.utc),
    ))
    if len(contexts) != 74:
        raise RuntimeError(f'Expected 74 unsaved extended-hours observations, got {len(contexts)}')
    supplemental = []
    supplemental_evaluations = []
    for name, config in VARIANTS.items():
        observations, evaluations = run_variant(contexts, name, config, settings)
        supplemental.extend(observations)
        supplemental_evaluations.extend(evaluations)
    supplemental_frame = pd.DataFrame(supplemental)
    supplemental_frame.to_csv(OUT / 'premarket_candidate_observations.csv', index=False)
    combined = pd.concat([existing, supplemental_frame], ignore_index=True).drop_duplicates(
        subset=['variant', 'timestamp_utc', 'underlying', 'strategy'], keep='last')
    combined.to_csv(candidates_path, index=False)

    baseline = combined[(combined['variant'] == 'baseline') & (combined['session'].isin(MAIN_DAYS))].to_dict('records')
    if len(baseline) != 2:
        raise RuntimeError(f'Premarket supplement changed the reproduced baseline: {len(baseline)} candidates')
    variant_results = {}
    for name in VARIANTS:
        items = combined[(combined['variant'] == name) & (combined['session'].isin(MAIN_DAYS))].to_dict('records')
        variant_results[name] = candidate_counts(items)

    threshold_rows = []
    for threshold in (90, 85, 80, 78, 75, 70, 65, 60):
        meeting = [item for item in baseline if item['technical_score'] >= threshold]
        counts = candidate_counts(meeting)
        threshold_rows.append({
            'threshold': threshold, 'market_scans': 1370, 'technical_observations': 4110,
            **counts,
            'candidates_rejected_by_scoring': len(baseline) - len(meeting),
            'meeting_after_stronger_confirmation': sum(item['stronger_confirmation_passed'] for item in meeting),
            'time_buckets': json.dumps(dict(Counter(item['time_bucket'] for item in meeting)), sort_keys=True),
            'options_rejected': '', 'risk_rejected': '',
            'options_status': 'NOT_EVALUATED_NO_ALIGNED_HISTORICAL_QUOTES',
            'risk_status': 'NOT_EVALUATED_NO_HISTORICAL_ACCOUNT_STATE',
            'allocation_gate': 'ZERO_BELOW_80' if threshold < 80 else 'NONE',
        })
    pd.DataFrame(threshold_rows).to_csv(OUT / 'threshold_comparison.csv', index=False)

    regular_contexts = [
        ctx for ctx in build_contexts(filter_regular_session(data), settings)
        if ctx['ts'].tz_convert('America/New_York').date().isoformat() in MAIN_DAYS
    ]
    _, regular_evaluations = run_variant(regular_contexts, 'baseline', {}, settings)
    _, extended_evaluations = run_variant(contexts, 'baseline', {}, settings)
    condition_data, rejection_rows = condition_rows(regular_evaluations + extended_evaluations)
    conditions = pd.DataFrame(condition_data)
    if len(regular_evaluations + extended_evaluations) != 4110:
        raise RuntimeError('Baseline condition pass did not cover all 4,110 strategy observations')
    conditions.to_csv(OUT / 'filter_condition_counts.csv', index=False)
    pd.DataFrame(rejection_rows).to_csv(OUT / 'filter_rejection_counts.csv', index=False)

    analysis['variants'] = variant_results
    analysis['thresholds'] = threshold_rows
    analysis['conditions'] = condition_data
    analysis['rejections'] = rejection_rows
    analysis['replay_scope'] = {
        'original_replay_scans': 1370,
        'regular_session_scans': 1296,
        'november_19_premarket_replay_scans': 22,
        'november_20_premarket_replay_scans': 44,
        'november_19_after_hours_replay_scans': 8,
        'note': 'The original replay included 74 extended-hours iterations per symbol pair using prior-session RTH candles. Live entries scan only while the Alpaca market clock is open.',
    }
    analysis['extended_hours_supplement_completed'] = True
    analysis_path.write_text(json.dumps(analysis, indent=2, default=str))
    print(json.dumps({'supplemental_scans': len(contexts), 'baseline_candidates': len(baseline),
                      'variants': variant_results}, indent=2, default=str))


if __name__ == '__main__':
    main()
