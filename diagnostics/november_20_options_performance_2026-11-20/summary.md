# SpyTrader November 20, 2026 historical options performance analysis

## Bottom line

A credible options P&L reconstruction is **blocked by the absence of historical bid/ask quotes** in the currently configured providers. Alpaca returned historical option trades and one-minute OHLCV bars, but its historical quotes REST request returned **404 Not Found**. Those bars and trades were not substituted for executable asks and bids.

Consequently, gross/net P&L, stops, trailing exits, win rate, expectancy, and whether $100 gross was achievable are **not evaluable**, not zero. No theoretical option prices, current snapshots, or daily highs were substituted. No production code or settings were changed, and no orders were submitted.

The underlying-only analysis is complete and separately reported below.

## 1. Historical options data availability

A bounded probe requested SPY November 23 $711 put data for 10:20–10:25 ET on November 20. This contract was used only to test provider capability, not selected as a profitable trade.

| Data | Result | Evidence |
|---|---|---|
| Alpaca current OPRA snapshots | Available | The earlier diagnostic collected 200 real-time snapshots successfully |
| Alpaca historical one-minute option bars | Available | 6 rows with OHLC, volume, trade count, and VWAP |
| Alpaca historical option trades | Available | 20 returned rows with timestamp, price, size, exchange, and conditions |
| Alpaca historical option quotes | **Unavailable** | `GET /v1beta1/options/quotes` returned HTTP 404 `Not Found` |
| Tradier historical quotes | Not available in this workspace | No Tradier API credential is configured; the existing adapter is real-time quote-only |

Raw results are in `data_availability.json`, `probe_historical_bars.csv`, and `probe_historical_trades.csv`.

Alpaca's [historical options documentation](https://docs.alpaca.markets/us/docs/historical-option-data) distinguishes actual OPRA data from the free indicative derivative. Its [market-data plan documentation](https://docs.alpaca.markets/us/docs/about-market-data-api) lists Algo Trader Plus at **$99/month** with real-time OPRA and unrestricted historical access, but the current API and installed SDK expose historical bars/trades—not the historical NBBO quotes required here. Upgrading Alpaca alone is therefore not a verified solution to this specific backtest requirement.

### Why bars and trades cannot answer the execution question

- A trade price does not establish that SpyTrader could buy at the ask or sell at the bid.
- An OHLC bar does not provide the bid/ask spread or the chronological quote path within the minute.
- The 10% initial stop and 10% trailing stop require successive executable option bids.
- Selling at a bar high or daily high would overstate execution and introduce hindsight.

No stop, trailing, or P&L simulation was run from these unsupported inputs.

## 2. Complete November 20 underlying session

The cached session contains 390 regular-session one-minute bars for each symbol.

| Symbol | Open | Close | Open-to-close | Low | High | Range / open |
|---|---:|---:|---:|---:|---:|---:|
| SPY | 713.56 | 690.68 | **-3.206%** | 686.75 | 718.26 | 4.416% |
| QQQ | 638.48 | 609.75 | **-4.500%** | 606.28 | 643.21 | 5.784% |

Completing November 20 materially changed the earlier partial-session picture and exposed another substantial afternoon decline in both symbols.

### Objective movement detection

Detection used only trailing information: a 15-minute close change of at least `max(0.3%, 2 × ATR / price)` plus sustained five-minute direction. Same-direction detections separated by no more than ten minutes were grouped. The top three groups per symbol were retained. Group start/end and total move are retrospective measurements; the first-observable timestamp is not.

`Remaining` is the directional close-to-close movement still available after first detection, and `% left` divides it by the full measured move. All six detections occurred before the 15:00 entry cutoff.

| Symbol | Move window ET | First observable | Total move | Remaining after detection | % left | Bot trend | Baseline / A candidates |
|---|---|---:|---:|---:|---:|---|---:|
| QQQ | 10:16–11:06 | 10:31 | -1.757% | 0.922% bearish | 52.5% | Strong bearish | 0 / 0 |
| QQQ | 11:11–11:49 | 11:26 | -0.993% | 0.585% bearish | 58.9% | Strong bearish | 0 / 0 |
| QQQ | 13:55–14:36 | 14:10 | -1.380% | 0.857% bearish | 62.1% | Moderate bearish | 0 / 0 |
| SPY | 10:16–10:48 | 10:31 | -1.067% | 0.445% bearish | 41.7% | Strong bearish | 1 / 2 |
| SPY | 11:12–11:49 | 11:27 | -0.811% | 0.488% bearish | 60.1% | Strong bearish | 0 / 0 |
| SPY | 13:55–14:35 | 14:10 | -1.291% | 0.913% bearish | 70.7% | Moderate bearish | 0 / 0 |

Every move retained substantial movement when objectively recognizable. The lack of candidates in five groups is therefore a genuine discovery issue worth investigating. It does **not** establish that a put trade would have survived the option stop or made money.

### Technical rejection reasons at first detection

- **QQQ 10:31:** `BREAKOUT_NOT_CONFIRMED`, `OVEREXTENDED_PRICE`, `VWAP_MISALIGNMENT`.
- **QQQ 11:26:** those plus `INSUFFICIENT_VOLUME` and `TREND_STRUCTURE_MISSING`.
- **QQQ 14:10:** breakout, volume, extension, structure, and VWAP rejection.
- **SPY 10:31:** breakout, volume, extension, and VWAP rejection; candidates had already occurred at 10:17 and experimentally at 10:20.
- **SPY 11:27:** continuation trigger, volume, extension, structure, and VWAP rejection.
- **SPY 14:10:** breakout, volume, extension, structure, and VWAP rejection.

The afternoon opportunities are new evidence from the complete session: the strategy recognized moderate bearish trend but still found no valid entry-specific setup. Full data is in `underlying_move_opportunities.csv`.

## 3. Previously identified signals and underlying outcomes

Returns below are in the option direction; positive put returns mean SPY/QQQ declined. They are underlying returns, not option returns.

| Entry ET | Setup | Score | Technical decision | 5m | 15m | 30m | 60m | 60m underlying MFE / MAE |
|---|---|---:|---|---:|---:|---:|---:|---:|
| QQQ 09:41 put continuation | Baseline candidate | 74.69 | Below 80; allocation would also be zero below 80 | -0.330% | -0.994% | -0.665% | +0.491% | +0.755% / -1.449% |
| SPY 10:17 put VWAP reversal | Baseline candidate | 79.21 | Below 80; allocation would also be zero below 80 | +0.283% | +0.474% | +0.876% | +0.854% | +1.181% / -0.030% |
| SPY 10:20 put breakout | Experiment A only | 94.48 | Experiment disabled; baseline 2.5 ATR ceiling rejected it | +0.195% | +0.262% | +0.228% | +0.667% | +0.939% / -0.195% |

The QQQ path was initially adverse and illustrates why lowering the threshold cannot be justified from eventual session direction alone. Whether its 10% option stop triggered is unknown without sequential option bids.

The SPY paths were favorable, but they cannot be converted into premium returns or dollar profits from underlying bars. See `signal_opportunities.csv`.

## 4. Contract selection and delta bands

The requested 0.40–0.70, 0.30–0.45, and 0.25–0.60 historical comparisons were **not performed**. Current delta snapshots cannot establish delta at 09:41, 10:17, 10:20, or the later objective detection times. Historical quotes alone also do not provide historical Greeks.

A valid comparison needs, at each hypothetical entry:

- The contracts actually listed and tradable at that time, with expiration, strike, right, multiplier, and 1–3 calendar DTE status.
- Timestamp-aligned NBBO bid/ask and quote size.
- Point-in-time delta from a historical-Greeks provider, or a clearly disclosed model-based delta using only then-available underlying price, option quote/IV, time to expiration, rates, and dividend assumptions. Model-derived delta must not be presented as an exchange observation.
- Then-available volume/open interest and a disclosed historical account-equity assumption for whole-contract sizing.

No contract was selected using future price movement.

## 5. Options performance metrics

| Requested result | Status |
|---|---|
| Quote-supported simulated trades | **0** |
| Profitable trades after costs | Not evaluable |
| Stopped-out trades | Not evaluable |
| Average gain / loss | Not evaluable |
| Maximum adverse option-price excursion | Not evaluable |
| Win rate / expectancy | Not evaluable |
| Gross / net simulated P&L | Not evaluable |
| Trades achieving $100 gross under actual exits | Not evaluable |

Zero supported trades means no simulation had the required data. It does not mean zero opportunities or zero profitable trades. The configured transaction fee is $0 per contract, but realistic slippage still cannot be measured without quotes. Machine-readable status is in `performance_status.csv`.

## 6. Data sources that can support a credible rerun

Verifiable current offerings include:

- **[ThetaData Options Standard](https://www.thetadata.net/pricing): $80/month**, advertising tick-level data, ten years of history, and every OPRA NBBO quote. Options Pro is $160/month and also includes every NBBO quote. Standard appears to meet the core quote requirement; API field coverage and historical Greeks should be confirmed before purchase.
- **[Massive Options Advanced](https://massive.com/options): $199/month**, advertising nanosecond quote events and at least five years of history. The $79 Developer plan lists trades but not quotes and is therefore insufficient for this reconstruction.
- **[Databento OPRA.PILLAR](https://databento.com/docs/datasets/opra-pillar):** historical consolidated NBBO through `CMBP-1`, with event and receive timestamps. Databento recommends batch download because of data volume. A verifiable fixed price was not published on the retrieved dataset page, so cost requires a usage/licensing quote.

For the smallest useful dataset, request only the evaluated SPY/QQQ contracts from each entry through 15:45 ET, but include enough near-the-money strikes at each entry to perform selection without hindsight. Tick NBBO is preferred; one-second consolidated BBO may be acceptable only after a sensitivity review. One-minute OHLC or trades alone remain insufficient.

## 7. Next strategy experiment

Do not change production parameters yet. Once quote data is available:

1. Reconstruct these three named candidates and all six objective detection points using chronological quotes and actual whole-contract affordability.
2. Test the existing 10% stop, +20% trailing activation, 10% trailing distance, 15:00 cutoff, and end-of-day liquidation exactly as implemented.
3. Compare the three delta bands using point-in-time delta and ask-based entry affordability; do not reuse current Greeks.
4. Measure whether the QQQ 09:41 candidate would have been stopped before its later favorable underlying move, and whether either SPY candidate could achieve $100 gross under actual exits.
5. Investigate the repeated afternoon combination of volume, extension, structure, and continuation-trigger rejection before proposing another entry rule. Experiment A recovered only the first SPY move and is not a general solution.
6. Add disjoint bullish, bearish, sideways, high-volatility, and low-volatility sessions before calculating win rate or expectancy. November 20 alone cannot support a profitability conclusion.

## Output files

- `data_availability.json`, `probe_historical_bars.csv`, `probe_historical_trades.csv`: provider evidence.
- `november_20_underlying_summary.csv`: complete-session movement.
- `underlying_move_opportunities.csv`: objective detection, remaining movement, signals, and rejection reasons.
- `signal_opportunities.csv`: the three named candidates and underlying outcomes.
- `performance_status.csv`: explicit unsupported-performance status.
- `probe_historical_options.py`, `underlying_analysis.py`: reproducible diagnostic scripts.

Production settings, credentials, SQLite trading history, and strategy behavior were not modified. No paper or live orders were placed.
