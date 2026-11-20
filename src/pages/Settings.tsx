import { useEffect, useState } from 'react'
import { AlertTriangle, Check, RefreshCw, Save, ShieldCheck, X } from 'lucide-react'
import { useDashboard } from '@/hooks/use-dashboard'
import { get, post } from '@/lib/api'
import type { Preflight, Settings, TradingMode } from '@/types'
import { Badge, DataLabel, PageHeader, Panel } from '@/components/common'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'

const sections = [
  ['general', 'Trading & allocation'], ['exits', 'Exit management'], ['risk', 'Risk limits'],
  ['signals', 'Signals & strategies'], ['data', 'Data & session'], ['account', 'Account & mode'],
] as const

type Section = typeof sections[number][0]

export default function SettingsPage() {
  const { status, saveSettings, busy, refresh } = useDashboard()
  const [draft, setDraft] = useState<Settings | null>(status?.settings ?? null)
  const [dirty, setDirty] = useState(false)
  const [section, setSection] = useState<Section>(() => window.location.hash === '#configuration' ? 'account' : 'general')
  const [liveOpen, setLiveOpen] = useState(false)
  if (!draft) return null
  const update = <K extends keyof Settings>(key: K, value: Settings[K]) => { setDraft((old) => old ? { ...old, [key]: value } : old); setDirty(true) }
  const updateWeight = (key: keyof Settings['score_weights'], value: number) => { setDraft((old) => old ? { ...old, score_weights: { ...old.score_weights, [key]: value } } : old); setDirty(true) }
  const save = async () => { if (await saveSettings(draft)) setDirty(false) }
  const weightsTotal = Object.values(draft.score_weights).reduce((sum, value) => sum + value, 0)

  return <div className="page-stack">
    <PageHeader title="Settings" description="Persistent execution policy. Changes apply to new entries; active-position protection remains snapshotted." action={<div className="flex gap-2"><button className="button button-secondary" disabled={!dirty} onClick={() => { setDraft(status?.settings ?? null); setDirty(false) }}>Discard</button><button className="button button-primary" disabled={!dirty || busy === 'settings' || Math.abs(weightsTotal - 100) > 0.001} onClick={() => void save()}><Save size={14} />{busy === 'settings' ? 'Saving…' : 'Save changes'}</button></div>} />
    {dirty && <div className="alert-warning"><AlertTriangle size={15} />Unsaved changes. Active-position stops will not be retroactively changed.</div>}
    <div className="settings-layout">
      <nav className="settings-nav" aria-label="Settings sections">{sections.map(([key, label]) => <button key={key} className={section === key ? 'active' : ''} onClick={() => setSection(key)}>{label}</button>)}</nav>
      <div className="min-w-0">
        {section === 'general' && <div className="space-y-5">
          <Panel title="Trading universe" subtitle="Version 1 is restricted to long SPY and QQQ options">
            <div className="form-grid"><div><DataLabel>Enabled underlyings</DataLabel><div className="mt-3 flex gap-2">{['SPY', 'QQQ'].map((symbol) => <label key={symbol} className={`choice-chip ${draft.symbols.includes(symbol) ? 'selected' : ''}`}><input type="checkbox" className="sr-only" checked={draft.symbols.includes(symbol)} onChange={(e) => update('symbols', e.target.checked ? [...draft.symbols, symbol] : draft.symbols.filter((s) => s !== symbol))} />{symbol}</label>)}</div></div><NumberField label="Maximum completed trades / day" value={draft.max_trades_per_day} min={1} max={20} onChange={(v) => update('max_trades_per_day', v)} /><NumberField label="Maximum open positions" value={1} min={1} max={1} disabled onChange={() => undefined} hint="Version 1 safety invariant" /></div>
          </Panel>
          <Panel title="Adaptive capital allocation" subtitle="Score 80–89: minimum · 90–94: standard · 95–100: maximum">
            <div className="form-grid form-grid-3"><NumberField label="Minimum %" value={draft.min_allocation_percent} min={0.1} max={100} step={0.5} onChange={(v) => update('min_allocation_percent', v)} /><NumberField label="Standard %" value={draft.standard_allocation_percent} min={0.1} max={100} step={0.5} onChange={(v) => update('standard_allocation_percent', v)} /><NumberField label="Maximum %" value={draft.max_allocation_percent} min={0.1} max={100} step={0.5} onChange={(v) => update('max_allocation_percent', v)} /></div>
            <p className="form-note">Whole contracts only. Budget is capped by allocation and actual options buying power. Full premium remains at risk.</p>
          </Panel>
          <Panel title="Daily profit goal" subtitle="A stop-new-entries condition, never a reason to force trades">
            <div className="form-grid"><NumberField label="Net realized goal ($)" value={draft.daily_profit_goal} min={0} step={10} onChange={(v) => update('daily_profit_goal', v)} /><Toggle label="Pause after daily goal" description="Continue managing existing exposure" checked={draft.pause_after_daily_goal} onChange={(v) => update('pause_after_daily_goal', v)} /></div>
          </Panel>
        </div>}

        {section === 'exits' && <div className="space-y-5">
          <Panel title="Initial protection" subtitle="Based on actual average option entry premium"><NumberField label="Initial stop loss %" value={draft.initial_stop_loss_percent} min={0.1} max={90} step={0.5} onChange={(v) => update('initial_stop_loss_percent', v)} hint="A 10% stop can be tight for short-dated options; paper-test alternatives." /></Panel>
          <Panel title="Dynamic trailing profits" subtitle="Peak valid executable bid; thresholds never decrease">
            <Toggle label="Use application-managed trailing stops" description="No fixed profit ceiling" checked={draft.trailing_stop_enabled} onChange={(v) => update('trailing_stop_enabled', v)} />
            <div className="form-grid mt-5"><NumberField label="Activation gain %" value={draft.trailing_activation_percent} min={0} max={1000} step={1} onChange={(v) => update('trailing_activation_percent', v)} /><NumberField label="Distance below peak %" value={draft.trailing_distance_percent} min={0.1} max={100} step={0.5} onChange={(v) => update('trailing_distance_percent', v)} hint="Paper-test 5–30% before changing policy." /></div>
          </Panel>
          <Panel title="Optional fixed target" subtitle="Disabled by default so profitable positions can run"><div className="form-grid"><Toggle label="Enable fixed profit target" description="Overrides the no-ceiling default" checked={draft.fixed_profit_target_enabled} onChange={(v) => update('fixed_profit_target_enabled', v)} /><NumberField label="Fixed target gain %" value={draft.fixed_profit_target_percent} min={0.1} max={1000} step={1} disabled={!draft.fixed_profit_target_enabled} onChange={(v) => update('fixed_profit_target_percent', v)} /></div></Panel>
        </div>}

        {section === 'risk' && <div className="space-y-5">
          <Panel title="Loss circuit breakers" subtitle="Cancel entries and request safe liquidation when breached"><div className="form-grid"><NumberField label="Daily loss limit %" value={draft.daily_loss_limit_percent} min={0.1} max={100} step={0.5} onChange={(v) => update('daily_loss_limit_percent', v)} hint="Measured from starting-day equity" /><NumberField label="Weekly loss limit %" value={draft.weekly_loss_limit_percent} min={0.1} max={100} step={0.5} onChange={(v) => update('weekly_loss_limit_percent', v)} hint="Measured from starting-week equity" /><NumberField label="Consecutive-loss pause" value={draft.max_consecutive_losses} min={1} max={20} onChange={(v) => update('max_consecutive_losses', v)} /></div></Panel>
          <Panel title="Exposure policy" subtitle="Emergency liquidation always overrides routine trade quotas"><Toggle label="Allow overnight positions" description="Off by default; enabling disables forced EOD liquidation" checked={draft.overnight_positions} onChange={(v) => update('overnight_positions', v)} /><Toggle label="Close positions at end of day" description="Adjusts automatically for early closes" checked={draft.close_positions_eod} onChange={(v) => update('close_positions_eod', v)} /></Panel>
          <Panel title="Known transaction costs" subtitle="Used in net realized P&L; regulatory and execution costs may vary"><NumberField label="Fee per contract per side ($)" value={draft.transaction_fee_per_contract} min={0} step={0.01} onChange={(v) => update('transaction_fee_per_contract', v)} /></Panel>
        </div>}

        {section === 'signals' && <div className="space-y-5">
          <Panel title="Strategy activation" subtitle="Only regimes compatible with each setup are eligible"><div className="grid gap-3 sm:grid-cols-3">{[['momentum_breakout', 'Momentum breakout'], ['trend_continuation', 'Trend continuation'], ['vwap_reversal', 'VWAP reversal']].map(([key, label]) => <Toggle key={key} label={label} description={key === 'vwap_reversal' ? 'Stronger countertrend confirmation' : 'Closed-candle confirmation'} checked={draft.enabled_strategies.includes(key)} onChange={(checked) => update('enabled_strategies', checked ? [...draft.enabled_strategies, key] : draft.enabled_strategies.filter((s) => s !== key))} />)}</div><div className="mt-5 max-w-xs"><NumberField label="Minimum qualifying score" value={draft.min_signal_score} min={0} max={100} step={1} onChange={(v) => update('min_signal_score', v)} /></div></Panel>
          <Panel title="Explainable factor weights" subtitle={`Total ${weightsTotal.toFixed(1)} / 100 · score is not a win probability`}><div className="form-grid form-grid-3">{([['momentum', 'Momentum'], ['trend', 'Trend confirmation'], ['volume', 'Volume'], ['regime', 'Market regime'], ['volatility', 'Volatility'], ['liquidity', 'Options liquidity'], ['reward_risk', 'Reward / risk']] as const).map(([key, label]) => <NumberField key={key} label={`${label} %`} value={draft.score_weights[key]} min={0} max={100} step={1} onChange={(v) => updateWeight(key, v)} />)}</div>{Math.abs(weightsTotal - 100) > 0.001 && <p className="mt-4 text-xs text-critical">Weights must total exactly 100 before saving.</p>}</Panel>
          <Panel title="Optional intelligence" subtitle="Neither module can bypass deterministic risk controls"><div className="form-grid"><Toggle label="Market-news regime filter" description="Requires a configured optional news provider" checked={draft.news_filter_enabled} onChange={(v) => update('news_filter_enabled', v)} /><Toggle label="ML signal filter" description="Fail-closed without an approved model; passing output must be ≥ 0.50" checked={draft.ml_filter_enabled} onChange={(v) => update('ml_filter_enabled', v)} /></div></Panel>
        </div>}

        {section === 'data' && <div className="space-y-5">
          <Panel title="Execution market data" subtitle="Delayed or indicative option quotes cannot enable entries"><div className="form-grid"><label className="form-label">Options provider<select className="form-select mt-1" value={draft.options_provider} onChange={(e) => update('options_provider', e.target.value as Settings['options_provider'])}><option value="alpaca">Alpaca OPRA</option><option value="tradier">Tradier production</option></select></label><NumberField label="Maximum quote age (seconds)" value={draft.max_quote_age_seconds} min={0.1} max={60} step={0.5} onChange={(v) => update('max_quote_age_seconds', v)} /><NumberField label="Maximum bid/ask spread %" value={draft.max_spread_percent} min={0.1} max={100} step={0.5} onChange={(v) => update('max_spread_percent', v)} /><NumberField label="Maximum estimated slippage %" value={draft.max_slippage_percent} min={0} max={100} step={0.1} onChange={(v) => update('max_slippage_percent', v)} /></div></Panel>
          <Panel title="Contract quality" subtitle="Absolute delta, actual liquidity, and whole-contract affordability"><div className="form-grid form-grid-3"><NumberField label="Minimum |delta|" value={draft.min_absolute_delta} min={0} max={1} step={0.05} onChange={(v) => update('min_absolute_delta', v)} /><NumberField label="Maximum |delta|" value={draft.max_absolute_delta} min={0} max={1} step={0.05} onChange={(v) => update('max_absolute_delta', v)} /><NumberField label="Opening range (minutes)" value={draft.opening_range_minutes} min={5} max={60} onChange={(v) => update('opening_range_minutes', v)} /><NumberField label="Minimum open interest" value={draft.min_open_interest} min={0} onChange={(v) => update('min_open_interest', v)} /><NumberField label="Minimum contract volume" value={draft.min_contract_volume} min={0} onChange={(v) => update('min_contract_volume', v)} /></div></Panel>
          <Panel title="Regular-session schedule" subtitle="24-hour Eastern time; early-close offsets are preserved"><div className="form-grid form-grid-3"><TimeField label="New entry cutoff" value={draft.new_entry_cutoff_et} onChange={(v) => update('new_entry_cutoff_et', v)} /><TimeField label="Liquidation start" value={draft.liquidation_start_et} onChange={(v) => update('liquidation_start_et', v)} /><TimeField label="Target flat" value={draft.target_flat_time_et} onChange={(v) => update('target_flat_time_et', v)} /></div></Panel>
          <Panel title="Optional operator alerts" subtitle="Delivery failures never interrupt risk management"><Toggle label="Enable Telegram / email alerts" description="Configure delivery credentials in .env" checked={draft.notifications_enabled} onChange={(v) => update('notifications_enabled', v)} /></Panel>
        </div>}

        {section === 'account' && <AccountSettings mode={status?.mode ?? 'paper'} draft={draft} onLive={() => setLiveOpen(true)} onPaper={async () => { await post('/mode/paper'); await refresh() }} />}
      </div>
    </div>
    <LiveDialog open={liveOpen} onOpenChange={setLiveOpen} settings={draft} />
  </div>
}

function AccountSettings({ mode, draft, onLive, onPaper }: { mode: TradingMode; draft: Settings; onLive: () => void; onPaper: () => Promise<void> }) {
  const [preflight, setPreflight] = useState<Preflight | null>(null)
  const [checking, setChecking] = useState(false)
  const run = async () => { setChecking(true); try { setPreflight(await get<Preflight>(`/preflight?mode=${mode}&fresh=false`)) } finally { setChecking(false) } }
  return <div className="space-y-5">
    <Panel title="Broker environment" subtitle="Paper and live credentials, orders, and records are isolated" action={<Badge tone={mode === 'live' ? 'negative' : 'neutral'}>{mode.toUpperCase()}</Badge>}>
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center"><div><strong className="text-sm">{mode === 'paper' ? 'Alpaca paper trading' : 'Alpaca live trading'}</strong><p className="mt-1 text-xs text-muted-foreground">{mode === 'paper' ? 'Real Alpaca paper orders and simulated broker fills.' : 'Real funds. Automated entries still require a separate Start action.'}</p></div>{mode === 'paper' ? <button className="button button-danger" onClick={onLive}>Review live activation</button> : <div className="flex flex-wrap gap-2">{!draft.live_trading_enabled && <button className="button button-danger" onClick={onLive}>Reauthorize live</button>}<button className="button button-secondary" onClick={() => void onPaper()}>Return to paper</button></div>}</div>
    </Panel>
    <Panel title="Connection preflight" subtitle="Calls actual broker and provider endpoints" action={<button className="button button-secondary" disabled={checking} onClick={() => void run()}><RefreshCw size={14} className={checking ? 'animate-spin' : ''} />Run checks</button>}>
      {!preflight ? <p className="text-xs text-muted-foreground">Run preflight to verify credentials, options permissions, buying power, market data, and unresolved local state.</p> : <PreflightChecks preflight={preflight} />}
    </Panel>
    <Panel title="Server configuration" subtitle="Secrets never enter browser JavaScript or SQLite" id="configuration">
      <ol className="setup-steps"><li><span>1</span><div><strong>Copy the environment template</strong><code>cp .env.example .env</code></div></li><li><span>2</span><div><strong>Add paper API credentials</strong><code>ALPACA_PAPER_API_KEY=…</code><code>ALPACA_PAPER_SECRET_KEY=…</code></div></li><li><span>3</span><div><strong>Configure an entitled options feed</strong><code>ALPACA_OPTIONS_FEED=opra</code><p>Alpaca indicative data and Tradier sandbox data are intentionally blocked for execution.</p></div></li><li><span>4</span><div><strong>Restart and run preflight</strong><code>docker compose up --build</code><p>Use Alpaca's actual reported equity. If the paper account is not $10,000, adjust its balance/reset it through Alpaca's paper-account controls; the bot never overrides broker equity.</p></div></li></ol>
    </Panel>
  </div>
}

function LiveDialog({ open, onOpenChange, settings }: { open: boolean; onOpenChange: (open: boolean) => void; settings: Settings }) {
  const [preflight, setPreflight] = useState<Preflight | null>(null)
  const [confirmation, setConfirmation] = useState('')
  const [reviewed, setReviewed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!open) { setPreflight(null); setConfirmation(''); setReviewed(false); setError(null); return }
    setBusy(true)
    get<Preflight>('/preflight?mode=live&fresh=true').then(setPreflight).catch((e) => setError(e.message)).finally(() => setBusy(false))
  }, [open])
  const activate = async () => {
    setBusy(true); setError(null)
    try { await post('/mode/live', { confirmation, risk_reviewed: reviewed, settings: { ...settings, trading_mode: 'live' } }); onOpenChange(false); window.location.reload() } catch (e) { setError(e instanceof Error ? e.message : 'Activation failed') } finally { setBusy(false) }
  }
  return <AlertDialog open={open} onOpenChange={onOpenChange}><AlertDialogContent className="terminal-dialog max-h-[90dvh] overflow-y-auto border-critical/50"><AlertDialogHeader><ShieldCheck className="text-critical" size={24} /><AlertDialogTitle>Live-trading activation</AlertDialogTitle><AlertDialogDescription>This is a real-funds workflow. Live mode requires separate credentials, options approval, buying power, fresh executable option data, no unresolved state, reviewed risk settings, and deliberate confirmation.</AlertDialogDescription></AlertDialogHeader>{preflight ? <PreflightChecks preflight={preflight} /> : <p className="text-xs text-muted-foreground">Running live preflight…</p>}{error && <p className="text-xs text-critical">{error}</p>}<label className="flex min-h-11 cursor-pointer items-start gap-3 text-xs"><input type="checkbox" className="mt-0.5 h-4 w-4" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} /><span>I reviewed all live risk, allocation, stop, loss-limit, and session settings.</span></label><label className="form-label">Type <strong className="text-critical">ENABLE LIVE TRADING</strong><input className="form-input mt-1" value={confirmation} onChange={(e) => setConfirmation(e.target.value)} autoComplete="off" /></label><AlertDialogFooter><AlertDialogCancel className="button button-secondary">Cancel</AlertDialogCancel><AlertDialogAction className="button button-danger" disabled={!preflight?.ready || !reviewed || confirmation !== 'ENABLE LIVE TRADING' || busy} onClick={(e) => { e.preventDefault(); void activate() }}>{busy ? 'Verifying…' : 'Activate live mode'}</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
}

function PreflightChecks({ preflight }: { preflight: Preflight }) {
  return <div className="space-y-2">{preflight.checks.map((check) => <div key={check.name} className="flex items-start gap-3 border-b border-border py-2 last:border-0"><span className={check.passed ? 'text-positive' : 'text-critical'}>{check.passed ? <Check size={14} /> : <X size={14} />}</span><div><strong className="block text-xs capitalize">{check.name.replaceAll('_', ' ')}</strong><p className="mt-0.5 text-[11px] text-muted-foreground">{check.detail}</p></div></div>)}</div>
}

function NumberField({ label, value, onChange, hint, ...props }: { label: string; value: number; onChange: (value: number) => void; hint?: string; min?: number; max?: number; step?: number; disabled?: boolean }) {
  return <label className="form-label">{label}<input className="form-input mt-1" type="number" value={value} onChange={(e) => onChange(Number(e.target.value))} {...props} />{hint && <small>{hint}</small>}</label>
}
function TimeField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <label className="form-label">{label}<input className="form-input mt-1" type="time" value={value} onChange={(e) => onChange(e.target.value)} /></label>
}
function Toggle({ label, description, checked, onChange }: { label: string; description: string; checked: boolean; onChange: (value: boolean) => void }) {
  return <label className="toggle-row"><span><strong>{label}</strong><small>{description}</small></span><input type="checkbox" role="switch" className="switch-input" checked={checked} onChange={(e) => onChange(e.target.checked)} /></label>
}
