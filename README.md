# Apex Options Pilot

A full-stack, autonomous SPY/QQQ long-options trading application with a React operations terminal, a persistent FastAPI trading engine, official Alpaca SDK integration, deterministic signal scoring, fill-confirmed position management, and application-managed trailing stops.

**Default environment: Alpaca paper trading. Automated entries start paused. Live order submission requires separate server authorization and the protected live-activation workflow.**

> Short-dated options can lose their entire premium. A stop trigger is not a guaranteed fill. No score, backtest, ML output, or automation setting guarantees profitability.

## Implemented workflow

`Monitor SPY/QQQ → analyze closed candles and regime → select a real option contract → score and size → submit a limit entry → confirm fills → manage initial/trailing protection → sell to close → persist performance`

- SPY and QQQ only; long calls and long puts only.
- One open position and up to three completed trade cycles per day by default.
- 1–3 calendar-DTE contracts, with configurable absolute-delta, spread, liquidity, slippage, premium, and buying-power gates.
- Momentum breakout, trend continuation, and stronger-confirmation VWAP reversal strategies.
- Closed 1-, 5-, and 15-minute candles; VWAP, EMA 9/21/50, RSI, MACD, ATR, relative volume, opening range, support/resistance, momentum, and volatility measures.
- Explainable weighted score from 0–100. This is a deterministic rules score, **not a calibrated win probability**.
- Score allocation tiers: 80–89 → 5%, 90–94 → 10%, 95–100 → 15%, capped by whole-contract affordability and actual options buying power.
- Actual Alpaca account equity is authoritative. `INITIAL_PAPER_CAPITAL=10000` is only a nominal reference; the app never changes or fabricates account equity.
- Initial stop defaults to 10% below actual average entry premium. Trailing activates at +20% in a valid executable bid and follows 10% below the highest valid bid. The threshold never decreases.
- Optional fixed target is off by default. Active-position exit settings are snapshotted and do not change retroactively when dashboard settings are edited.
- Daily goal, daily/weekly loss breakers, consecutive-loss pause, entry cutoff, early-close-adjusted liquidation, restart recovery, and manual emergency liquidation.
- SQLite persistence for settings, signals, orders, incremental fills, positions, trailing state, account snapshots, events, daily/weekly limits, and replay results.
- Telegram and SMTP email alerts, controlled by the persisted notification toggle and server credentials.

## Architecture

- **Frontend:** React 19, TypeScript, Vite, Tailwind, Radix/shadcn components, Recharts, responsive SVG candles, PWA assets.
- **Backend:** FastAPI, SQLAlchemy 2, SQLite WAL, official `alpaca-py`, pandas/NumPy, scikit-learn.
- **Runtime:** an asyncio-supervised engine runs in the server process and uses a thread for blocking SDK calls. The browser is not required for monitoring or liquidation.
- **Single-instance lease:** `data/engine.lock` prevents two local workers from managing the same account. Run exactly one application replica per database/account.

## Quick start with Docker

Prerequisites: Docker with Compose, an Alpaca paper account, API keys, options approval, and an execution-quality options data entitlement.

```bash
cp .env.example .env
# Edit .env on the server. Never commit it or expose its values to frontend code.
docker compose up --build
```

Open `http://localhost:8000`. Compose binds the port to `127.0.0.1` by default and stores SQLite/engine state in the `apex-data` volume.

The API does not provide multi-user authentication. Keep it on a trusted local/private environment. Do not expose the operator controls directly to the public internet; use a private authenticated proxy if remote access is required.

## Local development in Visual Studio Code on macOS

These steps run the app directly on your Mac; Docker is not required. Open the `app` folder (the folder containing `package.json`) in Visual Studio Code, then use **Terminal → New Terminal** for the commands below.

### 1. Install prerequisites

- Visual Studio Code
- Node.js 22 or newer
- Python 3.12 (do not use the macOS system Python 3.9 for these pinned dependencies)

Check Node.js and install Python 3.12 with Homebrew if needed:

```bash
node --version
brew install python@3.12
python3.12 --version
```

### 2. Create and edit the server environment file

In the VS Code terminal, create `.env` only if it does not already exist:

```bash
test -f .env || cp .env.example .env
```

Open `.env` in VS Code and set the **paper** credentials on these exact assignment lines:

```dotenv
ALPACA_PAPER_API_KEY=your_actual_paper_key
ALPACA_PAPER_SECRET_KEY=your_actual_paper_secret
```

Do not put the values on separate lines or comment out the assignments with `#`. Edit `.env`, not `.env.example`, and never commit or share the real values. Save the file before starting or restarting the backend. Paper trading is the default; automated entries remain paused and live trading remains disabled until their separate activation workflows pass.

### 3. Install dependencies

From the `app` directory:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
npm ci
```

For Python language support in VS Code, run **Python: Select Interpreter** from the Command Palette and select `app/.venv/bin/python`. This editor setting is optional; the terminal commands above use the virtual environment directly.

### 4. Start the backend

In the first VS Code terminal:

```bash
source .venv/bin/activate
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Leave this terminal running. The backend health endpoint is `http://127.0.0.1:8000/api/health`.

### 5. Start the frontend

Open a second terminal with **Terminal → New Terminal** (or split the existing terminal). From the `app` directory:

```bash
npm run dev -- --host 127.0.0.1
```

Leave this terminal running too, then open **http://localhost:3000**. Vite proxies `/api` requests to the backend on port `8000`.

### Restart and stop

- After changing `.env` or backend code, select the backend terminal, press **Ctrl+C**, and rerun the `python -m uvicorn ...` command. A fresh backend process is required to reload credentials.
- If a new terminal does not have the virtual environment activated, run `source .venv/bin/activate` before starting the backend.
- The frontend normally reloads source changes automatically. To restart it, press **Ctrl+C** in its terminal and rerun the `npm run dev ...` command.
- To stop the application, press **Ctrl+C** in both terminals.

For a unified production-style process after installing dependencies:

```bash
npm run build
source .venv/bin/activate
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Then open `http://localhost:8000`.

## Alpaca and market-data setup

1. Create separate paper and live API credentials. Only paper keys are needed initially.
2. Enable options trading on the Alpaca account and verify its actual options approval level and options buying power through **Settings → Account & mode → Run checks**.
3. Keep `ALPACA_STOCK_FEED=iex` unless the account is entitled to SIP. **IEX is not consolidated market data.**
4. For Alpaca options execution data, use `ALPACA_OPTIONS_FEED=opra` with an entitled OPRA subscription. Indicative options data is deliberately rejected for execution.
5. Alternatively, select **Tradier production** in dashboard settings and configure `TRADIER_API_KEY` and `https://api.tradier.com/v1`. Tradier sandbox URLs are rejected for execution.
6. Quotes must be positive, non-crossed, within the configured spread limit, and no older than three seconds by default. Stale or delayed data blocks new entries. No delayed source is silently substituted.
7. Use Alpaca’s actual paper-account equity. If a nominal $10,000 starting balance is desired, adjust/reset it through Alpaca’s own paper-account controls.

### Start and operating controls

- **Start / Resume:** runs a real preflight and requires risk acknowledgment. It does not bypass credentials, permissions, reconciliation, market hours, or risk limits.
- **Pause:** stops new entries but continues managing existing positions.
- **Stop:** stops new entries and cancels working opening orders; it does not pretend to close existing exposure.
- **Emergency:** requires `LIQUIDATE NOW`, cancels conflicting orders, refreshes state, and requests broker liquidation. A submitted close order is not represented as a guaranteed fill.
- **Live activation:** requires separate live credentials, fresh real-time option data, reviewed settings, and exact `ENABLE LIVE TRADING` confirmation. The server-side `LIVE_TRADING_ENABLED` gate and persisted activation must both be valid. A restart with the server gate disabled revokes entry authorization until reactivation.
- `AUTO_TRADING_ENABLED=true` attempts the same gated Start workflow at server startup. It does not bypass any check. The default is `false`.

## Configuration persistence

`.env` stores credentials, provider selection, delivery settings, runtime settings, and initial strategy/risk defaults for a **new** database. After initialization, strategy and risk changes are saved through the dashboard in SQLite. To deliberately rebuild defaults, back up and remove the database volume while the application is stopped.

Paper/live order and position records are separated by mode. Secrets are never stored in SQLite or returned to the browser. Raw account payloads are stripped from browser status responses.

## Exit and accounting semantics

- Only valid real-time **bids** update the high-water mark; last trades do not.
- The initial stop remains active before trailing activation.
- The exit threshold is the greater of the existing threshold and the active trailing calculation, so it cannot ratchet downward.
- Stop triggers create limit sell-to-close orders. Actual realized P&L uses confirmed entry/exit quantities and prices, not the trigger price.
- Partial entries and exits update cumulative quantities. Incremental fill prices are recovered from the broker’s cumulative average-price reports.
- Terminal rejected/canceled/expired incomplete exits release the active-exit lock so protection can be requested again.
- Ambiguous submissions are marked `unknown`, reconciled by client order ID, and never blindly retried. Unmatched submissions age into an error state only after a reconciliation grace period.
- If the broker is flat but local accounting has no verified closing fill, the app records an unpriced reconciliation closure and pauses new entries for review. It does **not** invent an exit price, TradeRecord, or realized P&L.

## Optional news and ML modules

### News

The optional Benzinga integration classifies high-impact SPY/QQQ headlines and can mark the regime unstable/news-driven, blocking incompatible entries. With no provider configured, technical analysis remains available. `TRADING_ECONOMICS_API_KEY` is reserved configuration only; Trading Economics is not an active provider in this version.

### Machine learning

The scikit-learn module is optional and separately gated. Offline training uses seven ordered normalized factors:

`momentum, trend, volume, regime, volatility, liquidity, reward_risk`

Use `MLSignalFilter.train(features, outcomes)` with chronological samples. It requires at least 200 aligned rows, both outcome classes in train and holdout partitions, a chronological 75/25 split, and holdout AUC ≥ 0.56. Approved artifacts are saved as `data/signal_model.joblib` plus a validation sidecar. The runtime loads only artifacts with that approval metadata.

When the dashboard ML filter is enabled, a qualified deterministic signal also requires an approved model output ≥ 0.50. Missing/unapproved models fail closed and block the entry. AUC and model output do not establish a durable edge or guarantee future results.

## Historical quote replay

Open **Quote replay** and upload two CSV files. Replay uses supplied observed option bids/asks, stated slippage, fees, actual DTE/delta/liquidity fields, and deterministic strategy rules. It does not manufacture a terminal fill when no valid quote exists; any remaining exposure is reported as an open position. Results are simulated historical execution, not Alpaca paper or live results.

Underlying CSV:

```text
timestamp,symbol,open,high,low,close,volume
```

Options CSV:

```text
timestamp,symbol,underlying,option_type,strike,expiration,delta,bid,ask,open_interest,volume
```

Timestamps are parsed as UTC (use ISO 8601 offsets for unambiguous alignment). Option `symbol` is the OCC contract symbol, `option_type` is `call` or `put`, and `expiration` is a date. Underlying and option observations must share timestamps for entry/exit evaluation.

## Tests and verification

```bash
python3 -m pytest -q
npm run lint
npm run build
```

The test suite uses isolated fake broker responses **only in tests**. It covers quote validation, closed-candle handling, scoring and allocation, position sizing, loss limits, early closes, paper/live isolation, initial and trailing stops, high-water persistence, separate trigger/fill pricing, partial fills, duplicate exits, expired-exit retry, ambiguous-order reconciliation, emergency accounting, restart recovery, notification gating/failure, API validation, and ML approval persistence. Replay coverage includes calendar-DTE alignment, exact per-side fee accounting, trailing-stop exits, CSV upload validation, separation from real-trading performance, and refusal to invent a terminal fill for an open position. Deterministic replay fixtures are synthetic test data, not market observations or broker results.

### Verification status for this delivery

- Backend suite: **47 passed**, including current Alpaca stream-handler, broker-clock publication, option affordability, and option-snapshot compatibility, contract open interest, and observed daily-bar volume coverage.
- Frontend ESLint, TypeScript compilation, and the Vite production build passed. The final PWA build generated 25 static-asset precache entries; HTML and API responses are not precached.
- All seven routes were checked in desktop production mode. All 28 route/viewport combinations showed no horizontal page overflow and exactly one visible start action.
- Production service-worker activation and controlled reload passed. A clean browser session loaded the application assets and APIs successfully with no console errors or warnings.
- `npm audit --omit=dev` reported **zero production dependency findings**. The full development audit still reported **26 findings (7 moderate, 19 high)** in build-tool dependency chains, primarily glob/minimatch/picomatch. Remediation suggested by npm requires breaking toolchain downgrades; no forced downgrade was applied.
- The Dockerfile and Compose configuration passed static review only. Docker was unavailable in the verification environment, so no image build or Compose runtime test was performed.

A subsequent real Alpaca paper preflight verified authentication, options permission, actual buying power, SPY IEX data, a fresh real-time OPRA option quote, and clean local order state. No paper order was submitted during that check. Automated market-hours execution, order fills, and live-market behavior remain unvalidated; they require an appropriate market session and deliberate operator action. Profitability has not been validated or guaranteed.

## Main API routes

- `GET /api/health`, `/api/status`, `/api/settings`, `/api/preflight`
- `PUT /api/settings`
- `POST /api/control/{start|pause|resume|stop}`, `/api/emergency`
- `POST /api/mode/paper`, `/api/mode/live`
- `GET /api/positions`, `/api/orders`, `/api/trades`, `/api/signals`, `/api/events`, `/api/performance`
- `GET /api/markets/{SPY|QQQ}`
- `POST /api/backtests`, `GET /api/backtests`

## Operational limitations

- Run one engine replica per account/database. SQLite and the lease are designed for this single-process deployment.
- There is no runtime mock broker and no fabricated disconnected-state account data.
- Unexpected external/manual broker activity can make complete P&L attribution impossible; unpriced recovery is explicitly flagged and paused for review.
- Optional ML training is an offline module workflow, not a dashboard training wizard.
- News delivery and operator alerts are best-effort and never take precedence over broker/risk procedures.
- Paper trading validates integration and workflows, not live fill quality or profitability.
