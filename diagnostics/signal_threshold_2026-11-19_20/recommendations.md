# Recommended changes — diagnostic only

No production setting or code change is recommended for immediate deployment from this two-day, underlying-only sample. Rankings below describe the next validation steps, not changes already made.

| Rank | Recommendation | Expected benefit | Evidence confidence | Implementation complexity | Unintended-consequence risk |
|---:|---|---|---|---|---|
| 1 | Keep the production technical threshold at **80**. Do not treat threshold reduction as the fix for sparse discovery. | Avoids admitting weak setups without addressing the actual bottleneck. | **High**: raw candidates stay at two from thresholds 80 down to 60. | None | Low |
| 2 | As the next isolated experiment, test a **breakout-only** extension ceiling of **3.0 ATR**, leaving VWAP, EMA, volume, candle, ATR-expansion, and regime rules unchanged. Test continuation separately rather than applying a global increase. | Could recover the November 20 SPY bearish breakout at 10:20 ET (score 94.48) without also relaxing every strategy. | **Low–moderate**: the global 3.0 experiment found this candidate, but the breakout-only version was not separately measured. | Low | **Moderate**: the global 3.0 variant increased November 18 score-qualified candidates from 1 to 3; 3.5 increased them to 5. More candidates are not evidence of profitability. |
| 3 | Preserve the existing strategy-specific volume rules, mandatory VWAP/EMA alignment, candle confirmation, fresh-cross behavior, and current independent trend/volatility model until broader evidence supports a change. | Avoids added noise and duplicate setups. | **Moderate–high** within the cached sessions: EMA/fresh-cross/candle relaxation increased activity elsewhere without adding November 19–20 candidates; global volume 1.0/1.1 tightened continuation and removed its QQQ candidate. | None | Low |
| 4 | Keep the production absolute-delta range unchanged for now. If affordability is the research goal, evaluate **0.25–0.60** in a larger current-snapshot sample and later with aligned historical quotes. | More lower-premium contracts and potentially more whole-contract choices. | **Low–moderate** for current eligibility only; no historical or return evidence. | Low | **Moderate–high**: lower-delta samples had lower gamma and higher theta relative to premium; cheap does not mean better. |
| 5 | Before any production strategy change, replay a materially larger, disjoint set of trend and sideways sessions with aligned historical option bid/ask data, realistic fills, the existing 10% stop, +20% trailing activation, and 10% trailing distance. | Establishes whether added candidates have acceptable execution quality and risk-adjusted outcomes. | **High** as a validation requirement. | Moderate | Low |

## Smallest plausible candidate-generation adjustment

The narrowest hypothesis worth testing next is **3.0 ATR for momentum breakouts only**, not a global filter relaxation. It must be measured separately first. The already-completed global 3.0 ATR experiment increased November 19–20 raw candidates from 2 to 3 and score-80 candidates from 0 to 1, but also increased November 10–18 raw candidates from 20 to 28 and score-80 candidates from 4 to 10. The 3.5 ATR experiment increased those totals further, including sideways-session activity. This is a frequency/false-positive proxy, not proof of losses or profits.

## Production threshold

**Keep 80.** There is insufficient evidence to recommend another threshold. Lower thresholds do not create raw candidates, and `allocation_for_score()` returns zero below 80, so lowering only the configured minimum would not authorize an allocation.
