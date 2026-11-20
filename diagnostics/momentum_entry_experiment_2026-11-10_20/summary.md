# SpyTrader targeted momentum-entry experiment

## Decision

**Neither experiment is validated for unattended deployment.** Experiment A is the only configuration worth considering for a closely monitored Alpaca Paper trial; Experiment B should remain disabled because it found no qualifying setups in 7,020 scans. Experiment A did **not** recover any November 19 opportunity, so the original missed-move problem remains unresolved.

If a monitored paper trial is run next week, test A alone, with its 3.0 ATR ceiling restricted to momentum breakouts. Keep B off. The evidence is only two added A observations, one with weak follow-through, so this is a data-collection trial—not evidence of profitability or a recommendation for live trading.

Both feature flags remain disabled by default. Production score thresholds, allocations, delta filters, stops, trailing settings, and stored settings were not changed. No paper or live orders were submitted.

## Data and method

- Replayed November 10, 11, 12, 13, 16, 17, 18, 19, and 20, 2026: 7,020 SPY/QQQ scans per variant.
- November 20 is complete with 390 regular-session bars per symbol through 15:59 ET. Two bounded requests added 132 bars per symbol; request metadata is in `data_refresh.json`.
- Earlier cached sessions retain their known gaps. They were not hidden or backfilled; exact counts are in each replay JSON.
- The replay passes only visible candles through `closed_candles()` and completed 5m/15m resampling (`backend/backtesting.py:219-230`). B uses four contiguous, completed, same-session 5m consolidation candles and a fresh closed-1m trigger. Outcome calculations are ex-post measurements only.
- Scores below are normalized underlying technical scores. They are not final option-aware execution scores, and replay did not simulate historical option eligibility, account risk, order execution, or exits.
- A distinct event groups the same variant, symbol, session, and direction, merging strategies; a gap over 15 minutes starts a new event. This avoids treating repeated scans as independent opportunities.

## Before-and-after comparison

| Variant | Raw candidates | Candidates >=80 | Distinct events | Events >=80 | New events vs baseline | Nov 19 moves recovered | Nov 18 raw / >=80 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline | 22 | 4 | 20 | 4 | 0 | 0 of 6 | 5 / 1 |
| A: breakout-only 3.0 ATR | 24 | 6 | 21 | 6 | 1 | 0 of 6 | 6 / 2 |
| B: confirmed continuation | 22 | 4 | 20 | 4 | 0 | 0 of 6 | 5 / 1 |

A added two SPY put breakouts. One was a new November 18 event; the other joined an existing November 20 bearish event containing the baseline 10:17 VWAP-reversal observation. B's added strategy was evaluated 7,020 times, confirmed by 14,040 strategy evaluations per symbol versus 10,530 at baseline, and generated zero candidates.

### Candidates by symbol and strategy

| Variant | Symbol | Strategy | Raw | >=80 |
|---|---|---|---:|---:|
| Baseline | QQQ | Momentum breakout | 1 | 0 |
| Baseline | QQQ | Trend continuation | 5 | 3 |
| Baseline | QQQ | VWAP reversal | 5 | 0 |
| Baseline | SPY | Momentum breakout | 1 | 0 |
| Baseline | SPY | Trend continuation | 1 | 1 |
| Baseline | SPY | VWAP reversal | 9 | 0 |
| A | SPY | Momentum breakout | 3 | 2 |
| A | All other groups | Unchanged | — | — |
| B | All groups, including confirmed continuation | No new candidates | 0 added | 0 added |

Full group and per-session counts, including zero-candidate sessions, are in `candidates_by_symbol_strategy.csv` and `session_comparison.csv`.

## Subsequent underlying movement

Returns are in the candidate's direction: a positive put return means the underlying declined. MFE/MAE use underlying highs/lows over up to 60 minutes. Late-session windows can be incomplete. These are underlying observations, not option returns.

| Variant | Median 5m | Median 15m | Median 30m | Median 60m | Median 60m MFE | Median 60m MAE |
|---|---:|---:|---:|---:|---:|---:|
| Baseline | -0.0496% | +0.0658% | +0.0541% | -0.0003% | +0.2713% | -0.2161% |
| A | -0.0383% | +0.0047% | +0.0411% | -0.0003% | +0.2371% | -0.2115% |
| B | -0.0496% | +0.0658% | +0.0541% | -0.0003% | +0.2713% | -0.2161% |

Available 5/15/30/60-minute event outcomes: baseline 20/18/19/18; A 21/19/20/18; B same as baseline. Positive-outcome percentages and per-event observations are in `comparison.csv` and `event_outcomes.csv`.

### The two candidates added by A

| Session / time ET | Setup | Score | Extension | 5m | 15m | 30m | 60m | MFE / MAE | Interpretation |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| Nov 18 15:21 SPY put | New distinct sideways-session event | 89.22 | 2.530 ATR | +0.018% | -0.031% | +0.028% | N/A | +0.094% / -0.188% | Weak follow-through; after the production 15:00 entry cutoff, so live risk rules would block it |
| Nov 20 10:20 SPY put | Same bearish event as 10:17 baseline candidate | 94.48 | 2.876 ATR | +0.195% | +0.262% | +0.228% | +0.667% | +0.939% / -0.195% | Favorable underlying follow-through, but close to the new 3.0 ATR ceiling and not an independent event |

Both added candidates exceeded the old 2.5 ATR ceiling, as expected, but neither exceeded 3.0. The November 20 observation is encouraging; the November 18 observation is a false-positive/chasing warning, not proof of a losing options trade.

## November 19 and sideways behavior

Neither experiment generated any November 19 candidate or recovered any of the six previously defined objective moves after they became observable. A breakout-only change therefore does not address the dominant November 19 continuation discovery failure.

On the November 18 sideways check, A increased raw candidates from 5 to 6, distinct events from 4 to 5, and score-80 observations from 1 to 2. This is not a numerical flood, but the added candidate had weak follow-through. B exactly matched baseline.

## Experiment B rejection evidence

B required established matching 5m/15m trend, EMA/VWAP alignment, a compact four-candle consolidation, relative volume >=0.8, extension <=2.5 ATR, a fresh directional consolidation break with MACD/candle confirmation, and a trigger no more than 0.5 ATR beyond the boundary.

Nonexclusive B-only rejection counts were:

| Reason | QQQ | SPY |
|---|---:|---:|
| Continuation trigger not confirmed | 1,792 | 2,033 |
| Entry location too extended | 1,615 | 1,787 |
| Price overextended | 1,450 | 1,729 |
| Consolidation not confirmed | 1,018 | 1,103 |
| Insufficient volume | 1,008 | 988 |
| Insufficient historical data | 825 | 825 |
| Regime mismatch | 825 | 560 |
| EMA misalignment | 490 | 330 |
| VWAP misalignment | 91 | 74 |

There is no evidence that this implementation improves discovery. Do not loosen all of its conditions merely to force candidates. See `experiment_b_rejection_counts.csv`.

## Duplicates, overlap, and look-ahead concerns

- Baseline had 22 raw observations but 20 distinct events; A had 24 but 21. A had three repeated scans within events and two multi-strategy events.
- A's November 20 breakout occurred three minutes after the baseline SPY put candidate and is not an independent opportunity. Underlying replay does not apply live duplicate suppression, historical options selection, or historical account risk.
- Events overlapping a prior same-direction event within 60 minutes remained three in all variants. A did not add a 60-minute event overlap under the chosen grouping.
- Production already limits open positions to one and applies entry cutoffs and daily limits; those outcomes were not simulated here.
- No look-ahead was used for candidate generation. Future bars appear only in the clearly separated outcome measurements.

## Allocation gate and delta comparison

`backend/signal_engine.py:94-101` hardcodes zero allocation below a final option-aware score of 80 regardless of `min_signal_score`; 80–<90 uses minimum allocation, 90–<95 standard, and >=95 maximum. `backend/risk_manager.py:74-86` then enforces whole-contract sizing and buying power. Lowering the configurable qualification threshold alone cannot produce an allocation below 80. This was documented, not changed.

Using the 200 saved current option snapshots and read-only paper equity/options buying power of $9,994.87:

| Absolute delta | Filter-eligible before affordability | Affordable at 5% | Affordable at 10% | Affordable at 15% |
|---|---:|---:|---:|---:|
| 0.40–0.70 (current) | 62 | 0 | 28 | 56 |
| 0.25–0.60 | 70 | 12 | 56 | 70 |

The lower/wider band improves affordability but is not a strict superset and has less favorable gamma/theta tradeoffs in this snapshot. One current snapshot is insufficient to recommend changing production delta selection. Details and limitations are in `allocation_audit.md`, `delta_allocation_comparison.csv`, and `delta_allocation_analysis.json`.

## Validation

- Full backend suite: **77 passed**, one unrelated Starlette deprecation warning.
- Focused experiment regressions: 6 tests included in that total, covering disabled defaults, A's breakout-only ceiling, bullish/bearish B triggers, completed-consolidation isolation, and rejection of already-triggered B entries.
- `git diff --check`: passed.
- No aligned historical option bid/ask data was available. No option fill, dollar-profit, exit-performance, or profitability claim is made.

## Recommendation

1. **A only, at most a monitored paper trial:** low implementation complexity and one promising November 20 observation, but low statistical confidence and no November 19 recovery. Do not enable for unattended operation.
2. **Keep B disabled:** zero added candidates across all sessions provides no deployment evidence.
3. **Keep score 80, delta 0.40–0.70, allocations, and exit parameters unchanged** pending broader evidence.
4. **Next evidence required:** more disjoint trend and sideways sessions, followed by aligned historical option quotes or monitored paper outcomes with realistic spreads, stops, trailing behavior, and execution. The smallest promising production candidate remains A's breakout-only adjustment, not a global anti-extension relaxation.
