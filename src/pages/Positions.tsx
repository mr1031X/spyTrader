import { useState } from 'react'
import { useDashboard } from '@/hooks/use-dashboard'
import { usePolling } from '@/hooks/use-polling'
import { formatTime, money, number, strategyName } from '@/lib/format'
import type { Order, Position } from '@/types'
import { Badge, EmptyState, PageHeader, Panel } from '@/components/common'
import PositionPanel from '@/components/PositionPanel'

export default function Positions() {
  const { status } = useDashboard()
  const { data: positions } = usePolling<Position[]>('/positions', 4000)
  const { data: orders } = usePolling<Order[]>('/orders', 4000)
  const [tab, setTab] = useState<'orders' | 'positions'>('orders')
  return <div className="page-stack">
    <PageHeader title="Positions & orders" description="Broker-confirmed fills, working orders, restored protection, and complete position records." />
    <PositionPanel position={status?.active_position ?? null} settings={status?.settings} />
    <Panel title="Execution ledger" subtitle="Submissions are not treated as fills" action={<div className="segmented"><button className={tab === 'orders' ? 'active' : ''} onClick={() => setTab('orders')}>Orders</button><button className={tab === 'positions' ? 'active' : ''} onClick={() => setTab('positions')}>Position records</button></div>}>
      {tab === 'orders' ? <Orders orders={orders ?? []} /> : <PositionRecords positions={positions ?? []} />}
    </Panel>
  </div>
}

function Orders({ orders }: { orders: Order[] }) {
  if (!orders.length) return <EmptyState title="No orders in this environment" description="Paper and live order ledgers are isolated. Real Alpaca submissions will appear here." />
  return <div className="table-wrap"><table className="data-table"><thead><tr><th>Submitted ET</th><th>Contract</th><th>Intent</th><th>Qty</th><th>Limit</th><th>Filled</th><th>Avg. fill</th><th>Status</th></tr></thead><tbody>{orders.map((order) => <tr key={order.id}><td>{formatTime(order.created_at, true)}</td><td className="max-w-44 truncate" title={order.symbol}>{order.symbol}</td><td>{order.position_intent.replaceAll('_', ' ')}</td><td>{order.qty}</td><td>{money(order.limit_price)}</td><td>{order.filled_qty} / {order.qty}</td><td>{money(order.filled_avg_price)}</td><td><Badge tone={order.status === 'filled' ? 'positive' : ['rejected', 'unknown'].includes(order.status) ? 'negative' : 'active'}>{order.status.replaceAll('_', ' ')}</Badge></td></tr>)}</tbody></table></div>
}

function PositionRecords({ positions }: { positions: Position[] }) {
  if (!positions.length) return <EmptyState title="No position records" description="Only confirmed broker fills create a position record." />
  return <div className="table-wrap"><table className="data-table"><thead><tr><th>Opened ET</th><th>Contract</th><th>Strategy</th><th>Qty</th><th>Entry</th><th>Highest bid</th><th>Stop</th><th>Status</th></tr></thead><tbody>{positions.map((position) => <tr key={position.id}><td>{formatTime(position.opened_at, true)}</td><td><strong>{position.underlying} {position.option_type.toUpperCase()} ${number(position.strike, 0)}</strong><span className="block text-[10px] text-muted-foreground">{position.expiration}</span></td><td>{strategyName(position.strategy)}</td><td>{position.qty} / {position.initial_qty}</td><td>{money(position.entry_premium)}</td><td>{money(position.highest_bid)}</td><td>{money(position.stop_threshold)}</td><td><Badge tone={position.status === 'closed' ? 'neutral' : 'active'}>{position.status}</Badge></td></tr>)}</tbody></table></div>
}
