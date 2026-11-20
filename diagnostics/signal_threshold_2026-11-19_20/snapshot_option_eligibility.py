from __future__ import annotations

import json
from datetime import datetime, timezone
from math import floor
from pathlib import Path
from statistics import median

import pandas as pd

from backend.broker import AlpacaBroker
from backend.database import SessionLocal, get_settings
from backend.market_data import MarketDataService
from backend.trailing_stop import option_limit_price

OUT = Path(__file__).resolve().parent
BANDS = {'delta_0_30_0_45': (0.30, 0.45), 'delta_0_40_0_70': (0.40, 0.70), 'delta_0_25_0_60': (0.25, 0.60)}


def stats(values):
    values = [float(value) for value in values if value is not None]
    return {'min': min(values), 'median': median(values), 'max': max(values)} if values else {'min': None, 'median': None, 'max': None}


def main():
    with SessionLocal() as session:
        settings = get_settings(session)
    if settings.trading_mode != 'paper':
        raise RuntimeError('Diagnostic snapshot is restricted to paper configuration')
    broker = AlpacaBroker('paper')
    market = MarketDataService(settings)
    account = broker.get_account()
    rows = []
    snapshots_out = []
    errors = []
    now = datetime.now(timezone.utc)
    for symbol in ('SPY', 'QQQ'):
        try:
            underlying = market.stock_quote(symbol).midpoint
        except Exception as exc:
            errors.append({'symbol': symbol, 'option_type': '', 'error': f'Underlying quote: {exc}'})
            continue
        for option_type in ('call', 'put'):
            try:
                contracts = broker.get_contracts(symbol, option_type, 1, 3)
                nearby = [contract for contract in contracts if contract.tradable and underlying * 0.88 <= contract.strike <= underlying * 1.12]
                nearby.sort(key=lambda contract: (abs(contract.strike - underlying), contract.expiration))
                considered = nearby[:50]
                snapshots = market.option_snapshots(considered)
            except Exception as exc:
                errors.append({'symbol': symbol, 'option_type': option_type, 'error': str(exc)})
                continue
            for snapshot in snapshots:
                quote = snapshot.quote
                limit = option_limit_price(min(quote.ask, quote.midpoint + (quote.ask - quote.bid) * 0.75)) if quote.bid > 0 and quote.ask >= quote.bid else None
                slippage = (limit - quote.midpoint) / quote.midpoint * 100 if limit is not None and quote.midpoint > 0 else None
                snapshots_out.append({
                    'snapshot_at': now.isoformat(), 'underlying': symbol, 'option_type': option_type,
                    'contract': snapshot.contract.symbol, 'strike': snapshot.contract.strike,
                    'expiration': snapshot.contract.expiration.isoformat(), 'underlying_midpoint': underlying,
                    'strike_distance_percent': (snapshot.contract.strike / underlying - 1) * 100,
                    'delta': snapshot.delta, 'gamma': snapshot.gamma, 'theta': snapshot.theta,
                    'implied_volatility': snapshot.implied_volatility, 'open_interest': snapshot.open_interest,
                    'volume': snapshot.volume, 'bid': quote.bid, 'ask': quote.ask,
                    'provider_timestamp': quote.timestamp.isoformat(), 'received_at': quote.received_at.isoformat() if quote.received_at else None,
                    'quote_age_seconds': (now - quote.timestamp).total_seconds(), 'spread_percent': quote.spread_percent,
                    'premium_per_contract': quote.ask * snapshot.contract.multiplier,
                    'estimated_slippage_percent': slippage,
                })
            for band, (minimum, maximum) in BANDS.items():
                in_band = [snapshot for snapshot in snapshots if snapshot.delta is not None and minimum <= abs(snapshot.delta) <= maximum]
                structurally_valid = []
                fresh = []
                delta_unavailable = sum(snapshot.delta is None for snapshot in snapshots)
                for snapshot in in_band:
                    quote = snapshot.quote
                    if quote.bid > 0 and quote.ask > 0 and quote.bid <= quote.ask:
                        structurally_valid.append(snapshot)
                    if quote.validation_error(now=now, max_age_seconds=settings.max_quote_age_seconds, max_spread_percent=None) is None:
                        fresh.append(snapshot)
                liquid = [snapshot for snapshot in fresh if
                          (snapshot.open_interest is None or snapshot.open_interest >= settings.min_open_interest)
                          and (snapshot.volume is None or snapshot.volume >= settings.min_contract_volume)]
                spread_ok = [snapshot for snapshot in liquid if snapshot.quote.spread_percent <= settings.max_spread_percent]
                slippage_ok = []
                for snapshot in spread_ok:
                    quote = snapshot.quote
                    limit = option_limit_price(min(quote.ask, quote.midpoint + (quote.ask - quote.bid) * 0.75))
                    estimated = (limit - quote.midpoint) / quote.midpoint * 100
                    if estimated <= settings.max_slippage_percent:
                        slippage_ok.append(snapshot)
                max_budget = min(account.equity * settings.max_allocation_percent / 100, account.options_buying_power)
                min_budget = min(account.equity * settings.min_allocation_percent / 100, account.options_buying_power)
                affordable = [snapshot for snapshot in slippage_ok if 0 < snapshot.quote.ask * snapshot.contract.multiplier <= max_budget]
                quantities = [floor(min_budget / (snapshot.quote.ask * snapshot.contract.multiplier)) for snapshot in affordable]
                rows.append({
                    'snapshot_at': now.isoformat(), 'underlying': symbol, 'option_type': option_type,
                    'band': band, 'contracts_discovered': len(contracts), 'contracts_nearby': len(nearby),
                    'snapshots_received': len(snapshots), 'delta_unavailable': delta_unavailable,
                    'within_delta': len(in_band), 'structurally_valid_quotes': len(structurally_valid),
                    'fresh_quotes': len(fresh), 'liquidity_passed': len(liquid), 'spread_passed': len(spread_ok),
                    'slippage_passed': len(slippage_ok), 'execution_eligible_now': len(affordable),
                    'affordable_min_allocation_quantity_total': sum(quantities),
                    'contracts_with_at_least_one_at_min_allocation': sum(quantity >= 1 for quantity in quantities),
                    **{f'premium_{key}': value for key, value in stats([s.quote.ask * s.contract.multiplier for s in affordable]).items()},
                    **{f'abs_strike_distance_{key}': value for key, value in stats([abs(s.contract.strike / underlying - 1) * 100 for s in affordable]).items()},
                    **{f'spread_{key}': value for key, value in stats([s.quote.spread_percent for s in affordable]).items()},
                    **{f'gamma_{key}': value for key, value in stats([s.gamma for s in affordable]).items()},
                    **{f'theta_{key}': value for key, value in stats([s.theta for s in affordable]).items()},
                    **{f'theta_to_midpoint_{key}': value for key, value in stats([abs(s.theta) / s.quote.midpoint * 100 for s in affordable if s.theta is not None and s.quote.midpoint]).items()},
                    'note': 'Current read-only eligibility snapshot, not historical contract availability or returns',
                })
    pd.DataFrame(rows).to_csv(OUT / 'otm_contract_eligibility.csv', index=False)
    pd.DataFrame(snapshots_out).to_csv(OUT / 'otm_option_snapshots.csv', index=False)
    result = {
        'snapshot_at': now.isoformat(), 'provider': market.options_provider.name,
        'paper_account_only': True, 'orders_submitted': 0,
        'delta_bands': BANDS, 'results': rows, 'errors': errors,
        'limitations': [
            'Current snapshots do not establish November 19-20 historical option availability or delta.',
            'No historical option returns or dollar profits are inferred.',
            'Freshness and execution eligibility apply only at the snapshot time.',
        ],
    }
    (OUT / 'otm_analysis.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({'rows': len(rows), 'snapshots': len(snapshots_out), 'errors': errors,
                      'puts': [row for row in rows if row['option_type'] == 'put']}, indent=2))


if __name__ == '__main__':
    main()
