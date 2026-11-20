import { useMemo, useState } from 'react'
import { Download, Search } from 'lucide-react'
import { usePolling } from '@/hooks/use-polling'
import { downloadFile, formatTime, money, percent, signedMoney, strategyName } from '@/lib/format'
import type { Trade } from '@/types'
import { EmptyState, PageHeader, Panel } from '@/components/common'

export default function History() {
  const { data: trades } = usePolling<Trade[]>('/trades', 8000)
  const [query, setQuery] = useState('')
  const [strategy, setStrategy] = useState('all')
  const filtered = useMemo(() => (trades ?? []).filter((trade) => {
    const text = `${trade.contract_symbol} ${trade.underlying} ${trade.exit_reason}`.toLowerCase()
    return text.includes(query.toLowerCase()) && (strategy === 'all' || trade.strategy === strategy)
  }), [trades, query, strategy])
  const exportCsv = () => {
    const columns = ['entry_time', 'exit_time', 'contract_symbol', 'strategy', 'qty', 'entry_price', 'exit_price', 'realized_pnl', 'return_percent', 'fees', 'exit_reason']
    const rows = filtered.map((trade) => columns.map((key) => JSON.stringify(trade[key as keyof Trade] ?? '')).join(','))
    downloadFile(`apex-trades-${new Date().toISOString().slice(0, 10)}.csv`, [columns.join(','), ...rows].join('\n'), 'text/csv')
  }
  return <div className="page-stack">
    <PageHeader title="Trade history" description="Actual realized outcomes, execution slippage, fees, and exit reasons for the selected broker environment." action={<button className="button button-secondary" disabled={!filtered.length} onClick={exportCsv}><Download size={14} />Export CSV</button>} />
    <Panel title="Completed trade cycles" subtitle={`${filtered.length} records`} action={<div className="flex flex-col gap-2 sm:flex-row"><label className="search-field"><Search size={13} /><input aria-label="Search trades" placeholder="Contract or exit" value={query} onChange={(event) => setQuery(event.target.value)} /></label><select className="form-select" aria-label="Filter strategy" value={strategy} onChange={(event) => setStrategy(event.target.value)}><option value="all">All strategies</option><option value="momentum_breakout">Momentum breakout</option><option value="trend_continuation">Trend continuation</option><option value="vwap_reversal">VWAP reversal</option></select></div>}>
      {filtered.length ? <div className="table-wrap"><table className="data-table"><thead><tr><th>Entry ET</th><th>Exit ET</th><th>Contract</th><th>Strategy</th><th>Qty</th><th>Entry</th><th>Exit</th><th className="text-right">Net P&L</th><th>Return</th><th>Exit reason</th></tr></thead><tbody>{filtered.map((trade) => <tr key={trade.id}><td>{formatTime(trade.entry_time, true)}</td><td>{formatTime(trade.exit_time, true)}</td><td><strong>{trade.underlying} {trade.option_type.toUpperCase()}</strong><span className="block text-[10px] text-muted-foreground">{trade.expiration}</span></td><td>{strategyName(trade.strategy)}</td><td>{trade.qty}</td><td>{money(trade.entry_price)}</td><td>{money(trade.exit_price)}</td><td className={`text-right font-medium ${trade.realized_pnl >= 0 ? 'text-positive' : 'text-negative'}`}>{signedMoney(trade.realized_pnl)}</td><td className={trade.return_percent >= 0 ? 'text-positive' : 'text-negative'}>{percent(trade.return_percent)}</td><td>{trade.exit_reason.replaceAll('_', ' ')}</td></tr>)}</tbody></table></div> : <EmptyState title={trades?.length ? 'No matching trades' : 'No completed trades'} description={trades?.length ? 'Change the search or strategy filter.' : 'Closed broker-confirmed trade cycles will be recorded here. No simulated results are mixed into this ledger.'} />}
    </Panel>
  </div>
}
