import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { usePolling } from '@/hooks/use-polling'
import { money, number, percent, signedMoney, strategyName } from '@/lib/format'
import type { Performance as PerformanceData } from '@/types'
import { EmptyState, LoadingBlock, Metric, PageHeader, Panel } from '@/components/common'

const tooltipStyle = { background: '#0c1220', border: '1px solid #1a2540', borderRadius: 3, fontSize: 11, color: '#f0f4f8' }

export default function Performance() {
  const { data, loading } = usePolling<PerformanceData>('/performance', 10000)
  if (loading && !data) return <LoadingBlock label="Calculating realized performance" />
  const value = data!
  const pnlTone = value.net_realized_pnl > 0 ? 'positive' : value.net_realized_pnl < 0 ? 'negative' : 'default'
  return <div className="page-stack">
    <PageHeader title="Performance" description="Forward paper-trading analytics from completed broker-confirmed trades and account snapshots." />
    <div className="metrics-grid performance-metrics">
      <Metric label="Net realized P&L" value={signedMoney(value.net_realized_pnl)} tone={pnlTone} detail="After configured known fees" />
      <Metric label="Win rate" value={percent(value.win_rate)} detail={`${value.trade_count} completed trades`} />
      <Metric label="Profit factor" value={value.profit_factor === null ? '—' : number(value.profit_factor)} detail="Gross gains / gross losses" />
      <Metric label="Max realized drawdown" value={money(value.maximum_drawdown)} detail="Cumulative closed-trade P&L" />
    </div>
    <div className="chart-grid">
      <Panel title="Cumulative net P&L" subtitle="Realized performance; open exposure is excluded">
        {value.equity_curve.length ? <div className="h-72"><ResponsiveContainer width="100%" height="100%"><AreaChart data={value.equity_curve} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}><defs><linearGradient id="pnlFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#22d3ee" stopOpacity={0.28} /><stop offset="1" stopColor="#22d3ee" stopOpacity={0} /></linearGradient></defs><CartesianGrid stroke="#1a2540" strokeDasharray="2 4" vertical={false} /><XAxis dataKey="time" tickFormatter={(time) => new Date(time).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} tick={{ fill: '#95959f', fontSize: 10 }} axisLine={false} tickLine={false} /><YAxis tickFormatter={(v) => money(v, 0)} tick={{ fill: '#95959f', fontSize: 10 }} axisLine={false} tickLine={false} width={65} /><Tooltip contentStyle={tooltipStyle} labelFormatter={(time) => new Date(time).toLocaleString()} formatter={(v) => [money(Number(v)), 'Net P&L']} /><Area type="monotone" dataKey="value" stroke="#22d3ee" fill="url(#pnlFill)" strokeWidth={1.5} /></AreaChart></ResponsiveContainer></div> : <EmptyState title="No equity history yet" description="A curve is drawn only after real completed trades; no assumed starting return is plotted." />}
      </Panel>
      <Panel title="Daily net profit" subtitle="Realized P&L by Eastern trading day">
        {value.daily_profit.length ? <div className="h-72"><ResponsiveContainer width="100%" height="100%"><BarChart data={value.daily_profit} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}><CartesianGrid stroke="#1a2540" strokeDasharray="2 4" vertical={false} /><XAxis dataKey="day" tick={{ fill: '#95959f', fontSize: 10 }} axisLine={false} tickLine={false} /><YAxis tickFormatter={(v) => money(v, 0)} tick={{ fill: '#95959f', fontSize: 10 }} axisLine={false} tickLine={false} width={65} /><Tooltip contentStyle={tooltipStyle} formatter={(v) => [money(Number(v)), 'Daily P&L']} /><Bar dataKey="pnl" radius={[2, 2, 0, 0]}>{value.daily_profit.map((item) => <Cell key={item.day} fill={item.pnl >= 0 ? '#34d399' : '#f87171'} />)}</Bar></BarChart></ResponsiveContainer></div> : <EmptyState title="No daily profit data" description="Daily bars appear after completed trade cycles." />}
      </Panel>
    </div>
    <div className="chart-grid">
      <Panel title="Execution quality" subtitle="Actual versus intended exits">
        <dl className="space-y-4 text-xs"><div><dt>Average trade return</dt><dd>{percent(value.average_trade_return)}</dd></div><div><dt>Average gain / loss</dt><dd>{money(value.average_gain)} / {money(value.average_loss)}</dd></div><div><dt>Average holding time</dt><dd>{number(value.average_holding_minutes, 1)} minutes</dd></div><div><dt>Average exit slippage</dt><dd className={value.average_slippage < 0 ? 'text-negative' : ''}>{money(value.average_slippage, 3)} / option</dd></div></dl>
      </Panel>
      <Panel title="Strategy attribution" subtitle="No short-term result establishes a durable edge">
        {value.strategy_performance.length ? <div className="table-wrap"><table className="data-table"><thead><tr><th>Strategy</th><th>Trades</th><th>Win rate</th><th className="text-right">Net P&L</th></tr></thead><tbody>{value.strategy_performance.map((item) => <tr key={item.name}><td>{strategyName(item.name)}</td><td>{item.trades}</td><td>{percent(item.win_rate)}</td><td className={`text-right ${item.pnl >= 0 ? 'text-positive' : 'text-negative'}`}>{signedMoney(item.pnl)}</td></tr>)}</tbody></table></div> : <EmptyState title="No attribution available" description="Strategy performance is calculated from real closed trades." />}
      </Panel>
    </div>
  </div>
}
