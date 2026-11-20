import { useNavigate } from 'react-router'
import { ArrowRight, Check, CircleAlert, KeyRound, X } from 'lucide-react'
import { useDashboard } from '@/hooks/use-dashboard'
import { usePolling } from '@/hooks/use-polling'
import { formatTime, money, signedMoney, strategyName } from '@/lib/format'
import type { EventItem, MarketData, OpportunityDiagnostics, Status } from '@/types'
import { Badge, DataLabel, EmptyState, Metric, PageHeader, Panel, StatusDot } from '@/components/common'
import PositionPanel from '@/components/PositionPanel'

export default function Operations() {
  const { status } = useDashboard()
  const navigate = useNavigate()
  const { data: events } = usePolling<EventItem[]>('/events', 5000)
  const { data: diagnostics } = usePolling<OpportunityDiagnostics>('/diagnostics', 5000)
  const settings = status?.settings
  const account = status?.account
  const daily = status?.daily
  const pnlTone = (daily?.realized_pnl ?? 0) > 0 ? 'positive' : (daily?.realized_pnl ?? 0) < 0 ? 'negative' : 'default'
  const goalProgress = daily && settings && settings.daily_profit_goal > 0 ? Math.min(100, Math.max(0, daily.realized_pnl / settings.daily_profit_goal * 100)) : 0

  return (
    <div className="page-stack">
      <PageHeader title="Operations" description="Autonomous SPY & QQQ long-options execution with persistent, deterministic risk controls."
        action={<div className="text-right"><DataLabel>Last completed engine tick</DataLabel><p className="mt-1 text-xs">{status?.last_tick_at ? `${formatTime(status.last_tick_at)} ET` : 'Waiting'}</p></div>} />

      {status?.state === 'setup_required' && (
        <section className="setup-banner" id="setup">
          <div className="flex items-start gap-3"><KeyRound size={20} className="mt-0.5 shrink-0 text-warning" /><div><h2>Connect your Alpaca paper account</h2><p>The runtime is healthy, but broker authentication or execution-quality data still needs configuration. No account values or trading activity are shown until Alpaca reports them.</p></div></div>
          <button className="button button-warning shrink-0" onClick={() => navigate('/settings#configuration')}>View setup<ArrowRight size={14} /></button>
        </section>
      )}
      {status?.last_error && status.state !== 'setup_required' && <div className="alert-critical" role="alert"><CircleAlert size={17} /><span>{status.last_error}</span></div>}

      <div className="metrics-grid">
        <Metric label="Account equity" value={money(account?.equity)} detail="Alpaca reported" />
        <Metric label="Options buying power" value={money(account?.options_buying_power)} detail={`Options level ${account?.options_level ?? '—'}`} />
        <Metric label="Daily net realized P&L" value={signedMoney(daily?.realized_pnl)} tone={pnlTone} detail={daily?.goal_reached ? 'Goal achieved' : `Target ${money(settings?.daily_profit_goal, 0)}`} />
        <Metric label="Trade cycles remaining" value={daily && settings ? String(Math.max(0, settings.max_trades_per_day - daily.completed_trades)) : '—'} detail={`${daily?.completed_trades ?? 0} of ${settings?.max_trades_per_day ?? 3} completed`} />
      </div>

      <OpportunityPanel diagnostics={diagnostics} />

      <div className="operations-grid">
        <div className="min-w-0 space-y-5">
          <PositionPanel position={status?.active_position ?? null} settings={settings} />
          <Panel title="Underlying watch" subtitle="IEX prices when connected; consolidated volume is not implied" action={<button className="text-button" onClick={() => navigate('/markets')}>Open charts<ArrowRight size={13} /></button>}>
            <div className="grid gap-px overflow-hidden border border-border bg-border sm:grid-cols-2">
              <MarketMini symbol="SPY" enabled={Boolean(status?.credentials_configured)} />
              <MarketMini symbol="QQQ" enabled={Boolean(status?.credentials_configured)} />
            </div>
          </Panel>
          <Panel title="Signal ledger" subtitle="Deterministic scores are setup quality, not calibrated win probabilities">
            {status?.latest_signals.length ? (
              <div className="table-wrap"><table className="data-table"><thead><tr><th>Time ET</th><th>Setup</th><th>Direction</th><th>Regime</th><th className="text-right">Score</th><th>Decision</th></tr></thead><tbody>
                {status.latest_signals.map((signal) => <tr key={signal.id}><td>{formatTime(signal.created_at)}</td><td><strong>{signal.underlying}</strong><span className="block text-[10px] text-muted-foreground">{strategyName(signal.strategy)}</span></td><td className={signal.direction === 'call' ? 'text-positive' : 'text-negative'}>{signal.direction.toUpperCase()}</td><td>{signal.regime}</td><td className="text-right font-medium">{signal.score.toFixed(1)}</td><td><Badge tone={signal.qualified ? 'positive' : 'neutral'}>{signal.qualified ? 'Qualified' : 'Rejected'}</Badge></td></tr>)}
              </tbody></table></div>
            ) : <EmptyState title="No scored signals yet" description="Signals appear only after closed-candle setups and actual option liquidity have been evaluated." />}
          </Panel>
        </div>

        <aside className="min-w-0 space-y-5">
          {status && <StartupChecklist status={status} scanWarning={Boolean(diagnostics?.scan_warning)} />}
          <Panel title="Daily objective" subtitle={settings?.pause_after_daily_goal ? 'Pause new entries after the goal' : 'Goal tracking only'}>
            <div className="flex items-end justify-between"><strong className={`text-2xl font-medium ${pnlTone === 'negative' ? 'text-negative' : 'text-positive'}`}>{signedMoney(daily?.realized_pnl)}</strong><span className="text-xs text-muted-foreground">/ {money(settings?.daily_profit_goal, 0)}</span></div>
            <div className="progress-track mt-4"><span style={{ width: `${goalProgress}%` }} /></div>
            <p className="mt-3 text-xs leading-relaxed text-muted-foreground">A goal is never a reason to force a trade. Existing positions remain managed after it is reached.</p>
          </Panel>
          <Panel title="Safety envelope" subtitle="Current entry policy">
            <dl className="space-y-3 text-xs">
              <div><dt>Allocation by score</dt><dd>{settings?.min_allocation_percent}% / {settings?.standard_allocation_percent}% / {settings?.max_allocation_percent}%</dd></div>
              <div><dt>Initial premium stop</dt><dd>{settings?.initial_stop_loss_percent}%</dd></div>
              <div><dt>Trailing activation / distance</dt><dd>+{settings?.trailing_activation_percent}% / {settings?.trailing_distance_percent}%</dd></div>
              <div><dt>Daily / weekly loss breaker</dt><dd>{settings?.daily_loss_limit_percent}% / {settings?.weekly_loss_limit_percent}%</dd></div>
              <div><dt>Consecutive-loss pause</dt><dd>{settings?.max_consecutive_losses} losses</dd></div>
            </dl>
          </Panel>
          <Panel title="System activity" subtitle="Server-side events, newest first">
            {events?.length ? <div className="event-list">{events.slice(0, 6).map((event) => <div key={event.id} className="event-item"><span className={`event-level text-${event.level === 'critical' ? 'critical' : event.level === 'warning' ? 'warning' : 'muted-foreground'}`}>{event.level}</span><p>{event.message}</p><time>{formatTime(event.created_at)}</time></div>)}</div> : <p className="text-xs text-muted-foreground">No system events recorded.</p>}
          </Panel>
        </aside>
      </div>
    </div>
  )
}

function OpportunityPanel({ diagnostics }: { diagnostics?: OpportunityDiagnostics | null }) {
  const execution = diagnostics?.execution
  return <Panel title="Opportunity Diagnostics" subtitle={diagnostics?.last_successful_scan ? `Last successful scan ${formatTime(diagnostics.last_successful_scan)} ET` : 'Waiting for a successful market scan'}
    action={<Badge tone={diagnostics?.scan_warning ? 'negative' : 'neutral'}>{diagnostics?.scan_warning ? 'Evaluation blocked' : 'Scanner status'}</Badge>}>
    {diagnostics?.scan_warning && <div className="alert-critical mb-4" role="alert"><CircleAlert size={17} /><span>The market is open, but no scan has completed recently. The bot is unable to evaluate opportunities.</span></div>}
    <div className="grid gap-3 md:grid-cols-2">
      {(['SPY', 'QQQ'] as const).map((symbol) => {
        const item = diagnostics?.symbols.find((entry) => entry.underlying === symbol)
        return <section className="border border-border bg-background/40 p-4" key={symbol}>
          <div className="flex items-center justify-between"><strong>{symbol}</strong><Badge tone={item?.qualified_signals ? 'positive' : 'neutral'}>{item?.last_scan?.final_decision?.replaceAll('_', ' ') || 'AWAITING SCAN'}</Badge></div>
          {item ? <>
            <div className="mt-4 grid grid-cols-3 gap-3 text-xs"><div><DataLabel>Scans</DataLabel><p className="mt-1 text-lg">{item.scans}</p></div><div><DataLabel>Bull / bear</DataLabel><p className="mt-1 text-lg">{item.bullish_considered} / {item.bearish_considered}</p></div><div><DataLabel>Qualified / rejected</DataLabel><p className="mt-1 text-lg">{item.qualified_signals} / {item.rejected_signals}</p></div></div>
            <div className="mt-3 flex justify-between gap-3 text-[11px] text-muted-foreground"><span>Last scan {formatTime(item.last_scan_at)} ET</span><span>Contracts {item.contracts_evaluated} evaluated / {item.contracts_rejected} rejected</span></div>
            <div className="mt-3 flex flex-wrap gap-1">{item.primary_rejections.length ? item.primary_rejections.slice(0, 4).map((reason) => <Badge key={reason.reason} tone="neutral">{reason.reason.replaceAll('_', ' ')} × {reason.count}</Badge>) : <span className="text-xs text-muted-foreground">No strategy or contract rejections recorded.</span>}</div>
            <div className="mt-3 grid grid-cols-2 gap-3 text-[10px] text-muted-foreground"><span>Last qualifying signal<br />{formatTime(item.last_qualifying_signal?.timestamp)} ET</span><span>Last order attempt<br />{formatTime(item.last_order_attempt?.timestamp)} ET</span></div>
          </> : <EmptyState title="No scan data" description="This symbol has not completed a scan in the current session." />}
        </section>
      })}
    </div>
    <div className="mt-4 grid grid-cols-3 gap-px border border-border bg-border text-center sm:grid-cols-6">
      <ExecutionMetric label="Submitted" value={execution?.orders_submitted} /><ExecutionMetric label="Acknowledged" value={execution?.orders_acknowledged} />
      <ExecutionMetric label="Filled" value={execution?.orders_filled} /><ExecutionMetric label="Canceled" value={execution?.orders_canceled} />
      <ExecutionMetric label="Rejected" value={execution?.orders_rejected} /><ExecutionMetric label="Avg fill latency" value={execution?.average_execution_latency_seconds == null ? undefined : `${execution.average_execution_latency_seconds.toFixed(1)}s`} />
    </div>
  </Panel>
}

function ExecutionMetric({ label, value }: { label: string; value?: number | string }) {
  return <div className="bg-card p-3"><DataLabel>{label}</DataLabel><p className="mt-1 text-sm font-medium">{value ?? '—'}</p></div>
}

function StartupChecklist({ status, scanWarning }: { status: Status; scanWarning: boolean }) {
  const readiness = status.readiness
  const settings = status.settings
  const account = status.account
  const responseTime = Date.parse(status.updated_at)
  const ageSeconds = (value: string | null) => value ? (responseTime - Date.parse(value)) / 1000 : Number.POSITIVE_INFINITY
  const heartbeatReady = status.runtime_active && ageSeconds(status.last_tick_at) <= 15
  const brokerReady = Boolean(status.credentials_configured && readiness?.broker_connected && readiness.order_execution
    && account && !account.blocked && !account.trade_blocked && account.options_level >= 1)
  const evaluationReady = Boolean(heartbeatReady && brokerReady && status.reconciled
    && readiness?.underlying_data_ready && readiness.historical_data_ready && !scanWarning)
  const optionsConfigured = settings.options_provider === 'tradier' || status.options_feed === 'opra'
  const optionsReady = Boolean(optionsConfigured && readiness?.options_entitlement && readiness.option_quotes)
  const experimentsOff = !settings.experiment_breakout_3_atr_enabled && !settings.experiment_confirmed_continuation_enabled
  const executionReady = evaluationReady && optionsReady && experimentsOff
  const scanRequired = status.entries_enabled && Boolean(status.clock?.is_open)
  const scanHealthy = !scanRequired || (!scanWarning && ageSeconds(status.last_scan_at) <= 120)

  return <Panel title="Startup & premarket checklist" subtitle="Evaluation and order execution have independent data gates"
    action={<div className="flex flex-wrap justify-end gap-1"><Badge tone={evaluationReady ? 'positive' : 'neutral'}>{evaluationReady ? 'Evaluation ready' : 'Evaluation waiting'}</Badge><Badge tone={executionReady ? 'positive' : 'neutral'}>{executionReady ? 'Execution ready' : 'Execution waiting'}</Badge></div>}>
    <div className="space-y-1">
      <Gate label="Runtime heartbeat" passed={heartbeatReady} detail={status.last_tick_at ? `Last completed tick ${formatTime(status.last_tick_at)} ET` : 'No completed engine tick'} />
      <Gate label="Persistent paper environment" passed={status.mode === 'paper' && settings.trading_mode === 'paper' && !settings.live_trading_enabled} detail={`${settings.trading_mode.toUpperCase()} · live authorization off`} />
      <Gate label="Paper credentials configured" passed={status.credentials_configured} detail="Secrets remain server-side" />
      <Gate label="Broker and options permission" passed={brokerReady} detail={account ? `Connected · options level ${account.options_level}` : 'Broker account not reported'} />
      <Gate label="Broker state reconciled" passed={status.reconciled} detail={status.reconciled ? 'Orders and positions compared' : 'Reconciliation pending'} />
      <Gate label="SPY and QQQ underlying bars" passed={Boolean(readiness?.underlying_data_ready)} detail="Fresh, completed regular-session candles" />
      <Gate label="Historical indicators" passed={Boolean(readiness?.historical_data_ready)} detail="Calendar-aware multi-session warm-up" />
      <Gate label="Execution options feed" passed={optionsReady} detail={`${status.provider || settings.options_provider} · ${status.options_feed.toUpperCase()} · ${readiness?.last_option_quote ? `last provider quote ${formatTime(readiness.last_option_quote)} ET` : 'awaiting fresh quote'}`} />
      <Gate label="Experimental strategies disabled" passed={experimentsOff} detail={experimentsOff ? 'Production strategies only' : 'An experimental entry setup is enabled'} />
      <Gate label="Automated entries armed" passed={status.entries_enabled} detail={status.entries_enabled ? 'Operator risk acknowledgment completed' : 'Paused until Start / Resume'} />
      <Gate label="Regular market session" passed={Boolean(status.clock?.is_open)} detail={status.clock?.is_open ? 'Market open' : 'Premarket or closed; execution waits'} />
      <Gate label="Recent scan loop" passed={scanHealthy} detail={scanRequired ? `Last completed scan ${formatTime(status.last_scan_at)} ET` : 'Not required while paused or closed'} />
    </div>
  </Panel>
}

function Gate({ label, passed, detail }: { label: string; passed: boolean; detail: string }) {
  return <div className="flex items-center gap-3 border-b border-border py-3 last:border-0"><span className={`gate-icon ${passed ? 'gate-passed' : ''}`}>{passed ? <Check size={12} /> : <X size={12} />}</span><div className="min-w-0"><strong className="block text-xs font-medium">{label}</strong><span className="block truncate text-[10px] text-muted-foreground" title={detail}>{detail}</span></div></div>
}

function MarketMini({ symbol, enabled }: { symbol: 'SPY' | 'QQQ'; enabled: boolean }) {
  const { data, error } = usePolling<MarketData>(enabled ? `/markets/${symbol}` : null, 15000)
  const midpoint = data?.quote ? (data.quote.bid + data.quote.ask) / 2 : null
  return <button className="market-mini" onClick={() => window.location.assign(`/markets?symbol=${symbol}`)}><span className="flex items-center justify-between"><strong>{symbol}</strong><StatusDot status={data?.quote.fresh ? 'live' : 'neutral'} label={data ? (data.quote.fresh ? 'LIVE' : 'STALE') : 'NO FEED'} /></span><span className="mt-4 block text-xl font-medium">{midpoint ? money(midpoint) : '—'}</span><span className="mt-1 block truncate text-[10px] text-muted-foreground">{data ? `${money(data.quote.bid)} × ${money(data.quote.ask)}` : enabled ? error || 'Awaiting quote' : 'Connect market data'}</span></button>
}
