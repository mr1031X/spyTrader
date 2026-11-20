import { useState } from 'react'
import { Download, FileSpreadsheet, Play, RotateCcw } from 'lucide-react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '@/lib/api'
import { downloadFile, formatTime, money, number, percent, signedMoney } from '@/lib/format'
import { usePolling } from '@/hooks/use-polling'
import type { BacktestResult } from '@/types'
import { EmptyState, Metric, PageHeader, Panel } from '@/components/common'
import { toast } from 'sonner'

interface StoredBacktest { id: string; name: string; created_at: string; parameters: Record<string, unknown>; results: Omit<BacktestResult, 'id' | 'created_at'> }

export default function Backtests() {
  const [underlying, setUnderlying] = useState<File | null>(null)
  const [options, setOptions] = useState<File | null>(null)
  const [name, setName] = useState('Historical quote replay')
  const [equity, setEquity] = useState(10_000)
  const [slippage, setSlippage] = useState(0.5)
  const [fee, setFee] = useState(0)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<BacktestResult | null>(null)
  const { data: history, refresh } = usePolling<StoredBacktest[]>('/backtests', 0)

  const run = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!underlying || !options) return
    setBusy(true)
    try {
      const form = new FormData()
      form.append('underlying_file', underlying)
      form.append('options_file', options)
      form.append('parameters', JSON.stringify({ name, initial_equity: equity, slippage_percent: slippage, fee_per_contract: fee }))
      const response = await api<BacktestResult>('/backtests', { method: 'POST', body: form })
      setResult(response)
      toast.success('Quote replay completed with observed historical bids and asks')
      void refresh()
    } catch (reason) { toast.error(reason instanceof Error ? reason.message : 'Replay failed') } finally { setBusy(false) }
  }
  const reset = () => { setUnderlying(null); setOptions(null); setResult(null); setName('Historical quote replay'); setEquity(10_000); setSlippage(0.5); setFee(0) }

  return <div className="page-stack">
    <PageHeader title="Historical quote replay" description="Test strategies and trailing parameters against supplied historical bars and observed option quotes." action={<button className="button button-secondary" onClick={reset}><RotateCcw size={14} />Reset</button>} />
    <div className="backtest-grid">
      <Panel title="Replay inputs" subtitle="No theoretical option prices are accepted as observed fills">
        <form className="space-y-4" onSubmit={run}>
          <label className="form-label">Replay name<input className="form-input mt-1" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} required /></label>
          <FileField label="Underlying 1-minute bars" file={underlying} onChange={setUnderlying} />
          <FileField label="Historical option quotes" file={options} onChange={setOptions} />
          <div className="grid grid-cols-3 gap-3">
            <label className="form-label">Equity<input className="form-input mt-1" type="number" min="1" value={equity} onChange={(e) => setEquity(Number(e.target.value))} /></label>
            <label className="form-label">Slippage %<input className="form-input mt-1" type="number" min="0" max="20" step="0.1" value={slippage} onChange={(e) => setSlippage(Number(e.target.value))} /></label>
            <label className="form-label">Fee / side<input className="form-input mt-1" type="number" min="0" step="0.01" value={fee} onChange={(e) => setFee(Number(e.target.value))} /></label>
          </div>
          <button className="button button-primary w-full" type="submit" disabled={!underlying || !options || busy}><Play size={14} />{busy ? 'Replaying observed quotes…' : 'Run quote replay'}</button>
        </form>
      </Panel>
      <Panel title="CSV contract" subtitle="Timestamps must be ISO-8601 with a timezone">
        <div className="space-y-4 text-xs leading-relaxed">
          <div><strong className="text-foreground">Underlying file</strong><code className="code-block">timestamp,symbol,open,high,low,close,volume</code></div>
          <div><strong className="text-foreground">Options file</strong><code className="code-block">timestamp,symbol,underlying,option_type,strike,expiration,delta,bid,ask,open_interest,volume</code></div>
          <p className="text-muted-foreground">Option timestamps must align with underlying bar timestamps. Delta must be the historically observed/provider value. Quotes with missing, crossed, nonpositive, wide, illiquid, or out-of-range DTE values are excluded. Open positions are not force-filled at an invented terminal price.</p>
        </div>
      </Panel>
    </div>
    {result ? <ReplayResult result={result} /> : <Panel title="Replay output"><EmptyState title="No replay selected" description="Upload both CSV files to run a new replay, or select a saved replay below." /></Panel>}
    <Panel title="Saved replays" subtitle="Stored separately from real trading performance">
      {history?.length ? <div className="table-wrap"><table className="data-table"><thead><tr><th>Run ET</th><th>Name</th><th>Trades</th><th>Net P&L</th><th>Win rate</th><th /></tr></thead><tbody>{history.map((item) => <tr key={item.id}><td>{formatTime(item.created_at, true)}</td><td>{item.name}</td><td>{item.results.trade_count}</td><td className={item.results.net_realized_pnl >= 0 ? 'text-positive' : 'text-negative'}>{signedMoney(item.results.net_realized_pnl)}</td><td>{percent(item.results.win_rate)}</td><td className="text-right"><button className="text-button" onClick={() => setResult({ id: item.id, created_at: item.created_at, ...item.results })}>View</button></td></tr>)}</tbody></table></div> : <p className="text-xs text-muted-foreground">No saved historical replays.</p>}
    </Panel>
  </div>
}

function FileField({ label, file, onChange }: { label: string; file: File | null; onChange: (file: File | null) => void }) {
  return <label className="file-field"><FileSpreadsheet size={18} /><span><strong>{file?.name || label}</strong><small>{file ? `${number(file.size / 1024, 1)} KB` : 'Select CSV file'}</small></span><input type="file" accept=".csv,text/csv" onChange={(e) => onChange(e.target.files?.[0] ?? null)} /></label>
}

function ReplayResult({ result }: { result: BacktestResult }) {
  const curve = result.equity_curve.map((value, index) => ({ index, value }))
  return <div className="space-y-5">
    <div className="flex items-center justify-between"><div><h2 className="panel-title">Replay result</h2><p className="panel-subtitle">Simulated historical execution—not broker fills or live performance</p></div><button className="button button-secondary" onClick={() => downloadFile(`apex-replay-${result.id}.json`, JSON.stringify(result, null, 2), 'application/json')}><Download size={14} />Download JSON</button></div>
    <div className="metrics-grid"><Metric label="Net realized P&L" value={signedMoney(result.net_realized_pnl)} tone={result.net_realized_pnl >= 0 ? 'positive' : 'negative'} /><Metric label="Trades / win rate" value={`${result.trade_count} / ${percent(result.win_rate)}`} /><Metric label="Profit factor" value={result.profit_factor === null ? '—' : number(result.profit_factor)} /><Metric label="Max drawdown" value={percent(result.maximum_drawdown_percent)} /></div>
    <Panel title="Replay equity curve" subtitle="Closed-trade equity; any open position is reported separately">{curve.length > 1 ? <div className="h-64"><ResponsiveContainer width="100%" height="100%"><AreaChart data={curve}><CartesianGrid stroke="#1a2540" vertical={false} strokeDasharray="2 4" /><XAxis dataKey="index" tick={{ fill: '#95959f', fontSize: 10 }} /><YAxis tick={{ fill: '#95959f', fontSize: 10 }} tickFormatter={(v) => money(v, 0)} width={70} /><Tooltip contentStyle={{ background: '#0c1220', border: '1px solid #1a2540', fontSize: 11 }} formatter={(v) => money(Number(v))} /><Area type="monotone" dataKey="value" stroke="#22d3ee" fill="#22d3ee20" /></AreaChart></ResponsiveContainer></div> : <EmptyState title="No qualifying historical trades" description="Try a longer aligned dataset; the engine did not lower its qualification gates." />}</Panel>
    {result.open_position && <div className="alert-warning">A position remains open because no valid observed terminal quote was available. It was not assigned a fabricated closing fill.</div>}
  </div>
}
