# SpyTrader signal-threshold and missed-opportunity diagnostic

## Conclusion

The two-candidate result is reproducible. The main constraint is **candidate discovery**, not the numeric score threshold: lowering the threshold from 80 to 60 does not change the two raw candidates. Mandatory setup geometry, anti-chasing extension limits, continuation triggers, and volume checks eliminate nearly all observations before scoring.

The current trend and volatility classifications are already independent; replacing them with the same model was a no-op. Duplicate suppression is not applied by underlying replay and cannot explain this replay's low count. Historical options eligibility, historical risk decisions, exits, and option profits were **not evaluated** because aligned historical option bid/ask data was unavailable.

No production code or settings were changed for this diagnostic. No paper or live orders were submitted. Existing credentials, settings, SQLite data, and trading history were not modified.

## Scope and method

- Reproduced the baseline with the current `Backtester().run_underlying()` implementation and cached Alpaca underlying bars.
- Raw cache: `/tmp/spytrader-underlying-2026-11-19_20.csv`, 6,939 rows (SPY 3,473; QQQ 3,466), including earlier warm-up sessions and data through 2026-11-20 18:47 UTC / 13:47 ET. November 20 is therefore a partial session.
- Evaluation: 685 replay scans per symbol, 1,370 total, and three strategies per scan = 4,110 strategy observations.
- The original replay contains 1,296 regular-session scans and 74 extended-hours iterations: 22 November 19 premarket, 44 November 20 premarket, and 8 November 19 after-hours. Extended-hours iterations reuse the latest completed regular-session candles. Live entry scans run only when the Alpaca clock is open (`backend/trading_bot.py:196`).
- The instrumented replay used only candles closed at or before each timestamp. Threshold experiments were applied after candidate generation, matching `backend/backtesting.py:231-264`.
- Reported technical scores are normalized to 0–100. They are not final option-aware execution scores.
- Controlled filter variants were implemented only in diagnostic scripts. Other sessions in the existing cache (November 10–18) were used to check generalization without new historical requests.

## A. Baseline results

| Date/time ET | Symbol | Strategy | Direction | Trend / volatility | Technical score | Score-80 result |
|---|---|---|---|---|---:|---|
| 2026-11-20 09:41 | QQQ | Trend continuation | Put | Strong bearish / normal | 74.69 | Rejected |
| 2026-11-20 10:17 | SPY | VWAP reversal | Put | Strong bearish / normal | 79.21 | Rejected |

- November 19: no candidates for either symbol.
- November 20: two bearish candidates; no bullish candidates.
- Both candidates passed the separate stronger-confirmation check.
- Neither met the production threshold of 80.

## B. Threshold sensitivity

`Candidates` below means raw baseline candidates meeting that score threshold. Raw candidate generation remains two at every threshold. `Score rejections` counts the other raw candidates below the threshold.

| Minimum score | Market scans | Strategy observations | Candidates | Bullish | Bearish | Breakout / continuation / VWAP | Score rejections | Pass stronger confirmation | Allocation gate |
|---:|---:|---:|---:|---:|---:|---|---:|---:|---|
| 90 | 1,370 | 4,110 | 0 | 0 | 0 | 0 / 0 / 0 | 2 | 0 | None |
| 85 | 1,370 | 4,110 | 0 | 0 | 0 | 0 / 0 / 0 | 2 | 0 | None |
| 80 | 1,370 | 4,110 | 0 | 0 | 0 | 0 / 0 / 0 | 2 | 0 | None |
| 78 | 1,370 | 4,110 | 1 | 0 | 1 | 0 / 0 / 1 | 1 | 1 | Zero below 80 |
| 75 | 1,370 | 4,110 | 1 | 0 | 1 | 0 / 0 / 1 | 1 | 1 | Zero below 80 |
| 70 | 1,370 | 4,110 | 2 | 0 | 2 | 0 / 1 / 1 | 0 | 2 | Zero below 80 |
| 65 | 1,370 | 4,110 | 2 | 0 | 2 | 0 / 1 / 1 | 0 | 2 | Zero below 80 |
| 60 | 1,370 | 4,110 | 2 | 0 | 2 | 0 / 1 / 1 | 0 | 2 | Zero below 80 |

Time of day among threshold-meeting candidates:

- At 78 or 75: one SPY VWAP-reversal put in 10:00–11:00 ET.
- At 70, 65, or 60: that SPY candidate plus one QQQ continuation put in 09:30–10:00 ET.

There is also a hidden execution gate independent of `min_signal_score`: `allocation_for_score()` returns zero below 80 (`backend/signal_engine.py:94-96`). Thus, lowering only the configured threshold to 70 or 78 would still produce zero allocation. It was not changed.

Options and risk columns are `NOT_EVALUATED`, not zero: there were no aligned historical option quotes or reconstructed historical account states.

## C. Filter and rejection analysis

Each strategy had 1,370 observations. Counts below include all regular and extended replay scans. `Not reached` means an earlier mandatory condition prevented evaluation. Conditions and rejection codes are nonexclusive, so counts must not be added to obtain a unique-scan total.

### Momentum breakout

| Condition | Evaluated | Passed | Failed | Not reached |
|---|---:|---:|---:|---:|
| Fresh opening-range cross | 1,370 | 11 | 1,359 | 0 |
| Relative volume >= 1.2 | 1,370 | 410 | 960 | 0 |
| EMA alignment | 11 | 4 | 7 | 1,359 |
| VWAP alignment | 11 | 11 | 0 | 1,359 |
| Not overextended (<= 2.5 ATR) | 11 | 3 | 8 | 1,359 |
| Candle confirmation | 11 | 7 | 4 | 1,359 |
| ATR expansion | 11 | 11 | 0 | 1,359 |

Only 0.8% of scans had a fresh opening-range cross. Of those 11, only three passed the extension check and none survived every condition. `BREAKOUT_NOT_CONFIRMED` occurred 1,363 times: 1,359 missing crosses plus four candle-confirmation failures.

### Trend continuation

| Condition | Evaluated | Passed | Failed | Not reached |
|---|---:|---:|---:|---:|
| EMA alignment | 1,370 | 1,275 | 95 | 0 |
| VWAP alignment | 1,275 | 1,092 | 183 | 95 |
| Directional structure | 1,275 | 701 | 574 | 95 |
| Relative volume >= 0.8 | 1,275 | 637 | 638 | 95 |
| Not overextended (<= 2.5 ATR) | 1,275 | 198 | 1,077 | 95 |
| Continuation trigger | 1,275 | 438 | 837 | 95 |
| Continuation location | 1,275 | 1,175 | 100 | 95 |
| Confirmed beyond opening range | 1,275 | 1,010 | 265 | 95 |
| Pullback / dynamic support | 1,275 | 743 | 532 | 95 |
| Failed retest | 1,275 | 6 | 1,269 | 95 |
| Consolidation continuation | 1,275 | 166 | 1,109 | 95 |
| Subsequent structure break | 1,275 | 296 | 979 | 95 |
| Renewed momentum | 1,275 | 299 | 976 | 95 |

EMA direction was usually present, so EMA alignment was not the principal bottleneck. The dominant restrictions were the anti-chasing extension limit, a renewed/consolidation/structure-break trigger, volume, and directional structure. Only one candidate survived all mandatory conditions.

### VWAP reversal

| Condition | Evaluated | Passed | Failed | Not reached |
|---|---:|---:|---:|---:|
| VWAP cross | 1,370 | 33 | 1,337 | 0 |
| Relative volume >= 1.3 | 33 | 3 | 30 | 1,337 |
| Momentum reversal | 33 | 9 | 24 | 1,337 |
| Failed breakout/retest proximity | 33 | 20 | 13 | 1,337 |
| Not overextended (<= 2.0 ATR) | 33 | 33 | 0 | 1,337 |

A VWAP cross was rare. Among 33 crosses, volume was the most restrictive subsequent requirement; only one candidate survived all conditions.

### Interpretation

- The score threshold rejected two already-discovered candidates; it did not suppress the other 4,108 strategy observations.
- The fresh-cross requirement strongly limits breakout frequency, but removing it did not add candidates on November 19–20. It did add activity on earlier sessions, so it is not a free improvement.
- The current continuation strategy already recognizes structure breaks, pullbacks, failed retests, renewed momentum, and consolidation. The low count comes from their mandatory conjunction with other rules, not absence of continuation logic.
- The current production regime model already separates trend from volatility. The independent-regime variant exactly matched baseline.
- Underlying replay does not apply the live same-candidate suppression cache. Live event keys include candle timestamps; this is distinct from move-level deduplication.

## D. Controlled filter sensitivity

`Other sessions` means November 10–18. `Q80` means technical score >= 80 and stronger confirmation passed. November 18 is the sideways/low-directional-efficiency check. Additional candidates are frequency/false-positive proxies, not proven wins or losses.

| Variant | Nov 19–20 raw | Nov 19–20 Q80 | Other sessions raw | Other sessions Q80 | Nov 18 raw / Q80 |
|---|---:|---:|---:|---:|---:|
| Baseline | 2 | 0 | 20 | 4 | 5 / 1 |
| Global relative volume 1.0 | 1 | 0 | 29 | 3 | 11 / 1 |
| Global relative volume 1.1 | 1 | 0 | 26 | 3 | 11 / 1 |
| VWAP weighted, not mandatory | 3 | 0 | 21 | 4 | 6 / 1 |
| EMA weighted, not mandatory | 2 | 0 | 40 | 5 | 18 / 2 |
| Confirmed beyond opening; no fresh cross | 2 | 0 | 30 | 4 | 5 / 1 |
| Candle weighted, not mandatory | 2 | 0 | 24 | 6 | 8 / 3 |
| Extension 3.0 ATR, all strategies | 3 | 1 | 28 | 10 | 8 / 3 |
| Extension 3.5 ATR, all strategies | 4 | 2 | 36 | 16 | 10 / 5 |
| Independent trend/volatility (current no-op) | 2 | 0 | 20 | 4 | 5 / 1 |
| Volume 1.0 + VWAP weighted | 1 | 0 | 29 | 3 | 11 / 1 |
| Volume 1.0 + extension 3.0 | 2 | 1 | 35 | 9 | 13 / 3 |
| No fresh cross + volume 1.0 | 1 | 0 | 43 | 3 | 11 / 1 |

Important details:

- Production volume minima differ by strategy: breakout 1.2, continuation 0.8, VWAP reversal 1.3 (`backend/strategies.py:123`, `:210`, `:271`). A global 1.0 or 1.1 therefore **tightens continuation**, explaining loss of the QQQ candidate. These were not relaxations for every strategy.
- Making VWAP non-mandatory added one November 19 SPY bullish continuation at 09:37 ET, score 61.12. It did not add a score-80 candidate.
- EMA, fresh-cross, and candle relaxations added no November 19–20 candidates. EMA relaxation substantially increased activity on November 18.
- Extension 3.0 added a November 20 SPY bearish opening-range breakout at 10:20 ET, score 94.48. Extension 3.5 additionally added SPY bearish continuation at 10:29 ET, score 89.45.
- The global extension changes also increased activity and Q80 counts in other and sideways sessions. A narrower breakout-only experiment is preferable to deploying the global change.

Generalization sessions included stronger bullish movement on November 12, strong bearish trends on November 16, higher volatility on November 17, sideways trade on November 18, and lower volatility on November 11. Baseline generated no candidates on November 10–11; all tested variants also generated none there.

## E. Significant moves and missed opportunities

Moves were detected chronologically with a trailing 15-minute change of at least `max(0.3%, 2 × ATR / price)` plus sustained five-minute direction. Nearby detections were grouped and the top three per symbol/day retained. The first-observable time uses only then-available bars. The eventual move percentage and last-confirmed time describe the group retrospectively; they were not exposed to the strategy at entry.

| Symbol / day | Direction | Window start ET | First observable ET | Move to last confirmation | Bot trend at detection | Baseline candidate during move | Primary rejection codes at detection |
|---|---|---:|---:|---:|---|---|---|
| QQQ Nov 19 | Bearish | 11:37 | 11:52 | -2.238% | Sideways | None | BREAKOUT_NOT_CONFIRMED, EMA_MISALIGNMENT, REGIME_MISMATCH, VWAP_MISALIGNMENT |
| QQQ Nov 19 | Bullish | 12:06 | 12:21 | +1.605% | Sideways | None | BREAKOUT_NOT_CONFIRMED, CONTINUATION_NOT_CONFIRMED, INSUFFICIENT_VOLUME, OVEREXTENDED_PRICE, VWAP_MISALIGNMENT |
| QQQ Nov 19 | Bearish | 12:26 | 12:41 | -1.266% | Strong bearish | None | BREAKOUT_NOT_CONFIRMED, INSUFFICIENT_VOLUME, OVEREXTENDED_PRICE, TREND_STRUCTURE_MISSING, VWAP_MISALIGNMENT |
| SPY Nov 19 | Bearish | 10:16 | 10:31 | -0.771% | Moderate bearish | None | BREAKOUT_NOT_CONFIRMED, CONTINUATION_NOT_CONFIRMED, INSUFFICIENT_VOLUME, NO_PULLBACK_OR_FAILED_RETEST, OVEREXTENDED_PRICE, TREND_STRUCTURE_MISSING, VWAP_MISALIGNMENT |
| SPY Nov 19 | Bearish | 11:37 | 11:52 | -1.628% | Strong bearish | None | BREAKOUT_NOT_CONFIRMED, OVEREXTENDED_PRICE, TREND_STRUCTURE_MISSING, VWAP_MISALIGNMENT |
| SPY Nov 19 | Bullish | 12:06 | 12:21 | +1.155% | Strong bearish | None | BREAKOUT_NOT_CONFIRMED, CONTINUATION_NOT_CONFIRMED, OVEREXTENDED_PRICE, VWAP_MISALIGNMENT |
| QQQ Nov 20 | Bearish | 10:16 | 10:31 | -1.757% | Strong bearish | None | BREAKOUT_NOT_CONFIRMED, OVEREXTENDED_PRICE, VWAP_MISALIGNMENT |
| QQQ Nov 20 | Bullish | 10:58 | 11:13 | +0.470% | Strong bearish | None | BREAKOUT_NOT_CONFIRMED, CONTINUATION_NOT_CONFIRMED, OVEREXTENDED_PRICE, VWAP_MISALIGNMENT |
| QQQ Nov 20 | Bearish | 11:11 | 11:26 | -0.993% | Strong bearish | None | BREAKOUT_NOT_CONFIRMED, INSUFFICIENT_VOLUME, OVEREXTENDED_PRICE, TREND_STRUCTURE_MISSING, VWAP_MISALIGNMENT |
| SPY Nov 20 | Bearish | 10:16 | 10:31 | -1.067% | Strong bearish | One put at 10:17, score 79.21 | BREAKOUT_NOT_CONFIRMED, INSUFFICIENT_VOLUME, OVEREXTENDED_PRICE, VWAP_MISALIGNMENT |
| SPY Nov 20 | Bearish | 11:12 | 11:27 | -0.811% | Strong bearish | None | BREAKOUT_NOT_CONFIRMED, CONTINUATION_NOT_CONFIRMED, INSUFFICIENT_VOLUME, OVEREXTENDED_PRICE, TREND_STRUCTURE_MISSING, VWAP_MISALIGNMENT |
| SPY Nov 20 | Bearish | 12:00 | 12:15 | -0.525% | Strong bearish | None | BREAKOUT_NOT_CONFIRMED, CONTINUATION_NOT_CONFIRMED, INSUFFICIENT_VOLUME, OVEREXTENDED_PRICE, TREND_STRUCTURE_MISSING, VWAP_MISALIGNMENT |

For every row, historical options selection and historical risk rules are `NOT_EVALUATED`; duplicate suppression is `NOT_APPLIED_BY_UNDERLYING_REPLAY`. Exact window end times and machine-readable reasons are in `missed_opportunities.csv`.

The bot often recognized a bearish trend without generating a candidate. Trend recognition alone is therefore not the blocker; the missing entry-specific setup, anti-chasing, volume, or trigger conditions were decisive. Some bullish reversals occurred while the higher-timeframe trend remained bearish, so the lack of calls was not automatically erroneous.

## F. Slightly OTM option eligibility — current snapshot only

A read-only Alpaca OPRA snapshot was collected at 2026-11-20 20:19:24 UTC. For each symbol/right, 1–3 DTE contracts were discovered, filtered to strikes within 12% of spot, and the nearest 50 were snapshotted: 200 snapshots total, no provider errors. Counts below apply to that sample. `Eligible` includes fresh valid quotes, current liquidity/spread/slippage gates, and affordability within maximum allocation. `Min-affordable` counts contracts for which at least one whole contract fits the 5% minimum allocation.

### Puts

| Symbol | Absolute delta band | Eligible | Min-affordable | Median premium | Median absolute strike distance | Median spread | Median gamma | Median abs(theta)/midpoint |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| SPY | 0.30–0.45 | 9 | 3 | $544.00 | 0.904% | 0.696% | 0.0181 | 23.716% |
| SPY | 0.40–0.70 (current) | 15 | 0 | $907.00 | 0.539% | 0.659% | 0.0200 | 13.719% |
| SPY | 0.25–0.60 | 19 | 6 | $612.00 | 0.683% | 0.696% | 0.0189 | 21.275% |
| QQQ | 0.30–0.45 | 10 | 0 | $693.50 | 1.275% | 1.007% | 0.01435 | 23.698% |
| QQQ | 0.40–0.70 (current) | 19 | 0 | $1,170.00 | 0.767% | 0.938% | 0.0158 | 13.634% |
| QQQ | 0.25–0.60 | 25 | 3 | $781.00 | 1.030% | 0.939% | 0.0149 | 21.273% |

The broader 0.25–0.60 band increased current eligible and minimum-allocation-affordable choices. Lower-delta bands were cheaper but had lower median gamma and higher theta relative to midpoint. They are not inherently superior. Calls are included in `otm_contract_eligibility.csv`; their current results do not change this conclusion.

This snapshot cannot establish November 19–20 historical availability, delta, fills, slippage, or returns. Historical options rejection counts are therefore `NOT_EVALUATED`.

## G. Exit and risk analysis

The existing initial 10% stop, +20% trailing activation, 10% trailing distance, and disabled fixed target were not changed. Without aligned historical option bid/ask data, no exit simulation or option P&L was produced. Underlying-only replay cannot determine whether a trade would have made $100.

Risk implications supported by the diagnostics:

- Lowering the score threshold alone does not create candidates and would still encounter zero allocation below 80.
- Wider extension limits may admit entries farther from VWAP, increasing chase/reversion risk. Increased November 18 activity is a warning to test sideways false positives, not evidence that those candidates would lose.
- Removing EMA/VWAP/candle requirements or fresh-cross behavior increases activity on other sessions without improving the diagnostic days' qualified count.
- Wider delta eligibility can improve whole-contract affordability but may increase theta burden and reduce directional sensitivity. No return advantage has been demonstrated.

## H. Recommendations

See `recommendations.md` for the ranked list. In brief:

1. Keep the production threshold at **80**; evidence is insufficient to select a lower one.
2. Next, run a separate **breakout-only 3.0 ATR** diagnostic, with all other rules unchanged. Do not deploy the already-tested global increase based on this sample.
3. Do not globally relax volume, VWAP, EMA, candle, or fresh-cross rules based on these results.
4. Keep production delta selection unchanged pending broader evidence.
5. Validate on more disjoint sessions with aligned historical options quotes and realistic exits before any production strategy change.

## Output files

- `baseline.json`: reproduced production underlying replay.
- `candidate_observations.csv`: candidate observations for every variant and session.
- `threshold_comparison.csv`: thresholds 60–90, strategy/direction/time counts, and execution limitations.
- `filter_condition_counts.csv`: pass/fail/not-reached counts for every measured condition.
- `filter_rejection_counts.csv`: nonexclusive baseline rejection-code counts.
- `missed_opportunities.csv`: objective movement windows and decision evidence.
- `session_metrics.csv`, `generalization.csv`: other-session regime and variant comparisons.
- `otm_analysis.json`, `otm_contract_eligibility.csv`, `otm_option_snapshots.csv`: current option eligibility evidence.
- `analysis.json`: combined machine-readable analysis.
- `recommendations.md`: ranked recommendations and production-threshold decision.
- `run_signal_diagnostics.py`, `complete_replay_scope.py`, `snapshot_option_eligibility.py`: temporary reproducible diagnostic scripts.

Validation completed: baseline identities/scores matched exactly; supplemental scan count was 74; final condition coverage was 4,110 observations; OTM collection returned 200 snapshots with no errors; orders submitted was zero. The production test suite was not rerun because this diagnostic made no production-code changes.
