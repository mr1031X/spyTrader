# Allocation and delta diagnostic

## Hardcoded score-80 allocation gate

`backend/signal_engine.py:94-101` implements `allocation_for_score()` independently of `settings.min_signal_score`:

- Final option-aware score below 80: 0% allocation.
- 80 to below 90: configured minimum allocation.
- 90 to below 95: configured standard allocation, capped by the maximum.
- 95 or higher: configured maximum allocation.

`backend/risk_manager.py:74-86` converts that allocation and available buying power into a whole-contract quantity. `backend/trading_bot.py:538-546` rejects quantities below one as `INSUFFICIENT_BUYING_POWER`.

The configurable threshold controls candidate qualification, not this allocation gate. Lowering `min_signal_score` below 80 therefore cannot create a nonzero position by itself. Live trading applies the allocation tiers to the final score after option liquidity and reward/risk are added (`backend/trading_bot.py:497-540`), not to the normalized underlying-only technical score reported by this replay. No allocation or score code was changed.

## Delta and affordability comparison

The comparison used 200 previously saved Alpaca OPRA option snapshots from 2026-11-20 20:19:24 UTC, with the existing 1–3 calendar DTE, freshness, optional open-interest/volume, spread, and slippage rules preserved. Current read-only paper account equity and options buying power were both $9,994.87 when read at 2026-11-20 15:58:02 Central. Applying this current balance to the earlier snapshot gives budgets of approximately $499.74, $999.49, and $1,499.23 at 5%, 10%, and 15%.

Across SPY/QQQ calls and puts:

| Absolute delta | Filter-eligible before affordability | Affordable at 5% | Affordable at 10% | Affordable at 15% |
|---|---:|---:|---:|---:|
| 0.40–0.70 (current) | 62 | 0 | 28 | 56 |
| 0.25–0.60 | 70 | 12 | 56 | 70 |

The 0.25–0.60 band adds lower-delta choices but removes 0.60–0.70 choices; it is not a strict superset. For example, SPY call eligibility fell from 13 to 9, while the other three symbol/right groups increased. At 5%, the wider-lower band made 3 QQQ calls, 3 QQQ puts, and 6 SPY puts affordable, but no SPY calls. Lower premiums improve whole-contract affordability; the snapshot also shows generally higher theta relative to option midpoint and lower gamma in the lower-delta choices. This does not establish superior returns.

See `delta_allocation_comparison.csv` for every symbol/right/band/allocation row and `delta_allocation_analysis.json` for metrics and limitations. These are current eligibility observations, not historical November 19–20 contract availability, fills, or profits. No settings were saved and no orders were submitted.
