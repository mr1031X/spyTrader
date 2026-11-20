from __future__ import annotations

import json
from datetime import datetime
from math import floor
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

import pandas as pd

from backend.broker import AlpacaBroker
from backend.database import SessionLocal, get_settings
from backend.trailing_stop import option_limit_price

OUT = Path(__file__).resolve().parent
SNAPSHOTS = OUT.parent / 'signal_threshold_2026-11-19_20' / 'otm_option_snapshots.csv'
BANDS = {'delta_0_40_0_70': (0.40, 0.70), 'delta_0_25_0_60': (0.25, 0.60)}
ALLOCATIONS = (5, 10, 15)


def stats(values):
    values = [float(value) for value in values if pd.notna(value)]
    return {'min': min(values), 'median': median(values), 'max': max(values)} if values else {
        'min': None, 'median': None, 'max': None,
    }


def main():
    with SessionLocal() as session:
        settings = get_settings(session)
    assert settings.trading_mode == 'paper'
    assert (settings.min_absolute_delta, settings.max_absolute_delta) == (0.40, 0.70)
    account = AlpacaBroker('paper').get_account()
    account_at = datetime.now().astimezone()
    data = pd.read_csv(SNAPSHOTS)
    assert len(data) == 200
    assert data['snapshot_at'].nunique() == 1
    snapshot_at = datetime.fromisoformat(data['snapshot_at'].iloc[0])
    rows = []
    for (symbol, right), group in data.groupby(['underlying', 'option_type']):
        expiration = pd.to_datetime(group['expiration'])
        snapshot_date = pd.Timestamp(snapshot_at.astimezone(ZoneInfo('America/New_York')).date())
        group = group.assign(dte=(expiration - snapshot_date).dt.days)
        for band, (minimum, maximum) in BANDS.items():
            in_band = group[group['delta'].abs().between(minimum, maximum) & group['dte'].between(1, 3)]
            structurally_valid = in_band[(in_band['bid'] > 0) & (in_band['ask'] >= in_band['bid'])]
            fresh = structurally_valid[
                (snapshot_at - pd.to_datetime(structurally_valid['provider_timestamp'])).dt.total_seconds().abs()
                <= settings.max_quote_age_seconds
            ]
            liquid = fresh[
                (fresh['open_interest'].isna() | (fresh['open_interest'] >= settings.min_open_interest)) &
                (fresh['volume'].isna() | (fresh['volume'] >= settings.min_contract_volume))
            ]
            spread_ok = liquid[liquid['spread_percent'] <= settings.max_spread_percent]
            eligible = spread_ok[spread_ok['estimated_slippage_percent'] <= settings.max_slippage_percent]
            for allocation in ALLOCATIONS:
                budget = min(account.equity * allocation / 100, account.options_buying_power)
                quantities = eligible['premium_per_contract'].map(
                    lambda premium: floor(budget / premium) if premium > 0 else 0
                )
                affordable = eligible[quantities >= 1]
                row = {
                    'snapshot_at': snapshot_at.isoformat(),
                    'account_read_at': account_at.isoformat(),
                    'underlying': symbol,
                    'option_type': right,
                    'delta_band': band,
                    'allocation_percent': allocation,
                    'allocation_budget': budget,
                    'snapshots_received': len(group),
                    'within_delta_and_dte': len(in_band),
                    'structurally_valid_quotes': len(structurally_valid),
                    'fresh_quotes_at_snapshot': len(fresh),
                    'liquidity_passed': len(liquid),
                    'spread_passed': len(spread_ok),
                    'slippage_passed': len(eligible),
                    'filter_eligible_before_affordability': len(eligible),
                    'contracts_with_at_least_one_affordable': len(affordable),
                    'whole_contract_quantity_total_if_considered_individually': int(quantities.sum()),
                    'execution_eligible_at_allocation': len(affordable),
                }
                for name, values in {
                    'premium': affordable['premium_per_contract'],
                    'absolute_strike_distance_percent': affordable['strike_distance_percent'].abs(),
                    'spread_percent': affordable['spread_percent'],
                    'estimated_slippage_percent': affordable['estimated_slippage_percent'],
                    'absolute_delta': affordable['delta'].abs(),
                    'gamma': affordable['gamma'],
                    'theta': affordable['theta'],
                    'theta_to_midpoint_percent': (
                        affordable['theta'].abs() / ((affordable['bid'] + affordable['ask']) / 2) * 100
                    ),
                    'volume': affordable['volume'],
                    'open_interest': affordable['open_interest'],
                }.items():
                    row.update({f'{name}_{key}': value for key, value in stats(values).items()})
                rows.append(row)
    pd.DataFrame(rows).to_csv(OUT / 'delta_allocation_comparison.csv', index=False)
    result = {
        'snapshot_source': str(SNAPSHOTS),
        'snapshot_at': snapshot_at.isoformat(),
        'snapshot_count': len(data),
        'paper_account': {
            'read_at': account_at.isoformat(), 'equity': account.equity,
            'options_buying_power': account.options_buying_power,
        },
        'delta_bands': BANDS,
        'allocation_percents': ALLOCATIONS,
        'filters_preserved_at_snapshot_time': {
            'max_quote_age_seconds': settings.max_quote_age_seconds,
            'max_spread_percent': settings.max_spread_percent,
            'max_slippage_percent': settings.max_slippage_percent,
            'min_open_interest': settings.min_open_interest,
            'min_contract_volume': settings.min_contract_volume,
            'dte_calendar_days': [1, 3],
        },
        'results': rows,
        'limitations': [
            'Current paper account equity and options buying power are applied to the earlier 2026-11-20T20:19:24Z option snapshot.',
            'This is a current contract-eligibility diagnostic, not historical option availability or a return simulation.',
            'Whole-contract quantity totals treat each contract independently; they are not a proposed multi-contract order.',
            'Open interest and volume remain optional exactly as in production selection.',
            'No aligned historical option bid/ask data is available, so no historical option profit is inferred.',
        ],
        'production_settings_saved': False,
        'orders_submitted': 0,
    }
    (OUT / 'delta_allocation_analysis.json').write_text(json.dumps(result, indent=2, default=str))
    print(pd.DataFrame(rows)[[
        'underlying', 'option_type', 'delta_band', 'allocation_percent',
        'filter_eligible_before_affordability', 'contracts_with_at_least_one_affordable',
        'whole_contract_quantity_total_if_considered_individually', 'premium_median',
    ]].to_string(index=False))


if __name__ == '__main__':
    main()
