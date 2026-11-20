import { ShieldCheck, TrendingUp } from 'lucide-react'
import type { Position, Settings } from '@/types'
import { formatTime, money, number, signedMoney, strategyName } from '@/lib/format'
import { Badge, DataLabel, EmptyState, Panel } from './common'

export default function PositionPanel({ position, settings, compact = false }: { position: Position | null; settings?: Settings; compact?: boolean }) {
  if (!position) {
    return (
      <Panel title="Active position" subtitle="Fill-confirmed exposure and application-managed protection">
        <EmptyState
          title="No open position"
          description={`The engine will not force a trade. New entries require a score of at least ${settings?.min_signal_score ?? 80}/100 and valid real-time option quotes.`}
        />
      </Panel>
    )
  }
  const activation = position.entry_premium * (1 + Number(position.protection.trailing_activation_percent ?? 20) / 100)
  const progress = Math.max(0, Math.min(100, ((position.current_bid ?? position.entry_premium) / Math.max(activation * 1.25, 0.01)) * 100))
  const stopProgress = Math.max(0, Math.min(100, (position.stop_threshold / Math.max(activation * 1.25, 0.01)) * 100))
  return (
    <Panel
      title="Active position"
      subtitle={`${strategyName(position.strategy)} · Opened ${formatTime(position.opened_at)} ET`}
      action={<Badge tone={position.status === 'closing' ? 'warning' : position.trailing_active ? 'positive' : 'active'}>{position.status === 'closing' ? 'Exit working' : position.trailing_active ? 'Trailing active' : 'Initial stop'}</Badge>}
    >
      <div className="mb-5 flex flex-col justify-between gap-3 border-b border-border pb-5 sm:flex-row sm:items-start">
        <div>
          <div className="flex flex-wrap items-baseline gap-2">
            <strong className="text-xl font-medium tracking-tight">{position.underlying}</strong>
            <span className={`text-sm uppercase ${position.option_type === 'call' ? 'text-positive' : 'text-negative'}`}>{position.option_type}</span>
            <span className="text-sm text-muted-foreground">${number(position.strike, 0)} · {position.expiration}</span>
          </div>
          <span className="mt-1 block break-all font-mono text-[11px] text-muted-foreground">{position.contract_symbol}</span>
        </div>
        <div className="text-left sm:text-right">
          <DataLabel>Unrealized P&amp;L</DataLabel>
          <strong className={`block text-2xl font-medium ${(position.unrealized_pnl ?? 0) >= 0 ? 'text-positive' : 'text-negative'}`}>{signedMoney(position.unrealized_pnl)}</strong>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-x-5 gap-y-4 sm:grid-cols-4">
        <Field label="Contracts" value={String(position.qty)} />
        <Field label="Entry premium" value={money(position.entry_premium)} />
        <Field label="Current bid / ask" value={position.current_bid === null ? '—' : `${money(position.current_bid)} / ${money(position.current_ask)}`} />
        <Field label="Highest valid bid" value={money(position.highest_bid)} />
        <Field label="Active stop" value={money(position.stop_threshold)} tone="warning" />
        <Field label="Trailing activation" value={money(activation)} />
        <Field label="Protected P&L*" value={signedMoney(position.protected_pnl_before_costs_if_filled)} tone={position.protected_pnl_before_costs_if_filled >= 0 ? 'positive' : 'negative'} />
        <Field label="Time held" value={<HeldSince openedAt={position.opened_at} />} />
      </div>
      <div className="mt-6">
        <div className="mb-2 flex items-center justify-between text-[11px] text-muted-foreground"><span>STOP {money(position.stop_threshold)}</span><span>ACTIVATION {money(activation)}</span></div>
        <div className="trailing-track" aria-label={`Current bid progress ${Math.round(progress)} percent; stop at ${Math.round(stopProgress)} percent`}>
          <span className="trailing-stop-marker" style={{ left: `${stopProgress}%` }} />
          <span className="trailing-progress" style={{ width: `${progress}%` }} />
          <span className="trailing-current-marker" style={{ left: `${progress}%` }} />
        </div>
        <p className="mt-3 text-xs leading-relaxed text-muted-foreground">* Before costs and assuming a fill at the stop threshold. Stops trigger orders; they do not guarantee execution prices.</p>
      </div>
      {!compact && <div className="mt-4 flex items-center gap-2 border-t border-border pt-4 text-xs text-muted-foreground"><ShieldCheck size={14} className="text-positive" />Protection settings are snapshotted at entry and restored after restart.<TrendingUp size={14} className="ml-auto text-muted-foreground" /></div>}
    </Panel>
  )
}

function Field({ label, value, tone = '' }: { label: string; value: React.ReactNode; tone?: string }) {
  return <div><DataLabel>{label}</DataLabel><strong className={`mt-1 block text-sm font-medium ${tone ? `text-${tone}` : ''}`}>{value}</strong></div>
}

function HeldSince({ openedAt }: { openedAt: string }) {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(openedAt).getTime()) / 60000))
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}
