# SpyTrader Monday paper-readiness report

Checked November 20, 2026 for the intended November 23 Alpaca Paper session. No paper or live orders were submitted. The production SQLite database, credentials, trading history, strategy selections, and risk settings were not changed.

## Component status

| Component | Status | Evidence / limitation |
|---|---|---|
| Persistent configuration | PASS | SQLite settings are authoritative: paper mode, SPY/QQQ, three production strategies, score 80, experiments disabled. |
| Broker authentication and permission | PASS | Read-only Alpaca Paper check: options level 3, unblocked, $9,994.87 equity and options buying power. |
| Underlying data and historical warm-up | PASS | Existing calendar-aware cache, session/candle tests, and fresh-start SPY/QQQ scan tests pass. |
| Candidate generation | PASS | Fresh-start scanning and qualifying bullish/bearish pipeline fixtures reach option evaluation. |
| Score and allocation gates | PASS | Production threshold remains 80. Allocation now follows the configured qualifying threshold instead of a separate hardcoded 80 gate. |
| Options selection logic | PASS | Real selector used in mocked end-to-end tests; liquidity, delta, spread, slippage, and DTE checks remain intact. |
| Current options execution freshness | CONDITIONAL | OPRA structure and entitlement passed, but after-hours quotes were stale. Fresh quotes must pass on Monday; no fallback was used. |
| Whole-contract sizing | PASS WITH CONSTRAINTS | At current closed-market prices, 5% ($499.74) affords zero structurally eligible SPY contracts; 10% and 15% afford some SPY contracts. QQQ had zero after-hours candidates passing spread/slippage. |
| Risk approval | PASS | Existing position, pending-order, trade-count, loss, cutoff, and readiness gates remain active. |
| Paper order construction | PASS (MOCKED) | Exact Alpaca `LimitOrderRequest`: buy-to-open, whole quantity, DAY limit, unique client ID, paper client. |
| Acknowledgement and fills | PASS (MOCKED) | Broker/client IDs persist; mocked acknowledgement and fill create a position using actual fill premium. |
| Reconciliation and duplicate prevention | PASS | Existing restart, ambiguous submission, timeout, partial-fill, capacity, and cancellation tests pass. |
| Protective exits | PASS (MOCKED) | Initial-stop sell-to-close succeeds with a wide spread; quantity reaches zero without overclosing. |
| Dashboard checklist | PASS | New startup/premarket panel distinguishes evaluation and execution readiness and uses real tick/scan timestamps. |
| Frontend/backend integration | PASS | Isolated production-server smoke test loaded health, status, diagnostics, settings, SPA, and built JavaScript. |
| Continuous market-hours operation | PASS (AUTOMATED), LIVE SESSION UNVERIFIED | Tick/scan scheduling and stall detection are tested. No full real-market continuous session has been observed. |

## Fixes completed

1. A transient option-data or pre-submission failure no longer suppresses the same setup for six hours. Existing risk capacity still prevents retries while an ambiguous or active order exists.
2. `allocation_for_score` now uses `settings.min_signal_score`; production remains 80, so current production allocation behavior is unchanged.
3. Added real `last_tick_at` and `last_scan_at` timestamps. The dashboard no longer labels API snapshot generation as the engine heartbeat.
4. Preflight selects a tradable near-the-money 1–3 DTE SPY contract instead of an arbitrary first chain entry.
5. Added the Operations startup/premarket checklist, including paper isolation, credentials, broker/options permission, reconciliation, bars, history, OPRA freshness, entries, session, scan heartbeat, and disabled experiments.

## Current production settings

- Mode: Alpaca Paper; live authorization false.
- Symbols: SPY and QQQ.
- Strategies: momentum breakout, trend continuation, VWAP reversal.
- Experimental breakout 3 ATR: disabled.
- Experimental confirmed continuation: disabled.
- Minimum score: 80.
- Allocation tiers: 5% / 10% / **20% persisted maximum** (the 15% default was not imposed over the existing setting).
- Delta: absolute 0.40–0.70; DTE 1–3 calendar days.
- Initial stop: 10%; trailing activation +20%; trailing distance 10%; fixed target disabled.
- Maximum completed trades: 3; maximum open positions: 1.
- Daily/weekly loss limits: 6% / 12%; consecutive-loss pause: 2.
- Entry cutoff: 15:00 ET; EOD liquidation enabled; overnight positions disabled.

## Verification

- Backend: **84 passed**; one unrelated Starlette deprecation warning.
- Frontend: ESLint and production TypeScript/Vite build passed; 25 PWA precache entries.
- HTTP integration: passed with temporary SQLite, disabled credentials, and paused entries.
- Actual Alpaca interaction was read-only. Real order submission and live-market fill quality remain intentionally unverified.

## Monday procedure

1. From `app`, run `npm run build`.
2. Start `PYTHONPATH=. .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000` and open `http://127.0.0.1:8000`.
3. Before arming entries, open Settings → Account & mode → Run checks. Confirm paper mode and every required check. After-hours option freshness warnings are expected; during entry hours, stale OPRA data must block orders.
4. On Operations, verify the runtime heartbeat advances, paper environment is locked, reconciliation passes, SPY/QQQ bars and history become ready, and experiments remain disabled.
5. After the regular session opens, arm entries through Start / Resume with risk acknowledgment. Do not treat “no valid setup” as an error.
6. Monitor Opportunity Diagnostics, the signal ledger, order acknowledgements/fills, and active-position protection. A 5% score-tier allocation may produce `INSUFFICIENT_BUYING_POWER` even with a valid signal; this is a whole-contract affordability decision, not a reason to bypass limits.

## Remaining limitations

- Monday readiness cannot be final until current OPRA quotes pass the three-second freshness gate during the session.
- The heartbeat detects an overdue tick but is not a nonblocking watchdog around third-party SDK calls.
- No actual Alpaca Paper order was submitted in this validation, per instruction.
- Closed-market QQQ spread results do not predict Monday spreads; rerun preflight with current data before entries.
