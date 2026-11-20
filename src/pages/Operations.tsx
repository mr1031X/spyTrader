import { useNavigate } from 'react-router'
import { ArrowRight, Check, CircleAlert, KeyRound, X } from 'lucide-react'
import { useDashboard } from '@/hooks/use-dashboard'
import { usePolling } from '@/hooks/use-polling'
import { formatTime, money, signedMoney, strategyName } from '@/lib/format'
import type { EventItem, MarketData } from '@/types'
import { Badge, DataLabel, EmptyState, Metric, PageHeader, Panel, StatusDot } from '@/components/common'
import PositionPanel from '@/components/PositionPanel'

export default function Operations() {
  const { status } = useDashboard()
  const navigate = useNavigate()
  const { data: events } = usePolling<EventItem[]>('/events', 5000)
  const settings = status?.settings
  const account = status?.account
  const daily = status?.daily
  const pnlTone = (daily?.realized_pnl ?? 0) > 0 ? 'positive' : (daily?.realized_pnl ?? 0) < 0 ? 'negative' : 'default'
  const goalProgress = daily && settings && settings.daily_profit_goal > 0 ? Math.min(100, Math.max(0, daily.realized_pnl / settings.daily_profit_goal * 100)) : 0

  return (
    <div className="page-stack">
      <PageHeader title="Operations" description="Autonomous SPY & QQQ long-options execution with persistent, deterministic risk controls."
        action={<div className="text-right"><DataLabel>Engine heartbeat</DataLabel><p className="mt-1 text-xs">{status?.updated_at ? `${formatTime(status.updated_at)} ET` : 'Waiting'}</p></div>} />

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
          <Panel title="Automation gate" subtitle="Every gate must pass before a new entry">
            <div className="space-y-1">
              <Gate label="Broker authenticated" passed={Boolean(status?.credentials_configured && account)} detail={account ? 'Alpaca account reported' : 'Credentials / connection'} />
              <Gate label="Options permission" passed={Boolean(account && account.options_level > 0 && !account.trade_blocked)} detail={account ? `Approval level ${account.options_level}` : 'Not verified'} />
              <Gate label="Real-time option data" passed={Boolean(status?.data_ready)} detail={status?.provider || 'OPRA or production Tradier'} />
              <Gate label="Broker state reconciled" passed={Boolean(status?.reconciled)} detail={status?.reconciled ? 'Orders and positions compared' : 'Reconciliation pending'} />
              <Gate label="Regular session" passed={Boolean(status?.clock?.is_open)} detail={status?.clock?.is_open ? 'Market open' : 'Entries wait for next session'} />
            </div>
          </Panel>
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

function Gate({ label, passed, detail }: { label: string; passed: boolean; detail: string }) {
  return <div className="flex items-center gap-3 border-b border-border py-3 last:border-0"><span className={`gate-icon ${passed ? 'gate-passed' : ''}`}>{passed ? <Check size={12} /> : <X size={12} />}</span><div className="min-w-0"><strong className="block text-xs font-medium">{label}</strong><span className="block truncate text-[10px] text-muted-foreground" title={detail}>{detail}</span></div></div>
}

function MarketMini({ symbol, enabled }: { symbol: 'SPY' | 'QQQ'; enabled: boolean }) {
  const { data, error } = usePolling<MarketData>(enabled ? `/markets/${symbol}` : null, 15000)
  const midpoint = data?.quote ? (data.quote.bid + data.quote.ask) / 2 : null
  return <button className="market-mini" onClick={() => window.location.assign(`/markets?symbol=${symbol}`)}><span className="flex items-center justify-between"><strong>{symbol}</strong><StatusDot status={data?.quote.fresh ? 'live' : 'neutral'} label={data ? (data.quote.fresh ? 'LIVE' : 'STALE') : 'NO FEED'} /></span><span className="mt-4 block text-xl font-medium">{midpoint ? money(midpoint) : '—'}</span><span className="mt-1 block truncate text-[10px] text-muted-foreground">{data ? `${money(data.quote.bid)} × ${money(data.quote.ask)}` : enabled ? error || 'Awaiting quote' : 'Connect market data'}</span></button>
}
