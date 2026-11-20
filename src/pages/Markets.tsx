import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router'
import { RefreshCw } from 'lucide-react'
import { useDashboard } from '@/hooks/use-dashboard'
import { usePolling } from '@/hooks/use-polling'
import { money, number } from '@/lib/format'
import type { Candle, MarketData } from '@/types'
import CandleChart from '@/components/CandleChart'
import { Badge, DataLabel, EmptyState, ErrorState, LoadingBlock, PageHeader, Panel, StatusDot } from '@/components/common'

export default function Markets() {
  const { status } = useDashboard()
  const [params, setParams] = useSearchParams()
  const symbol = params.get('symbol') === 'QQQ' ? 'QQQ' : 'SPY'
  const [timeframe, setTimeframe] = useState(1)
  const { data, error, loading, refresh } = usePolling<MarketData>(status?.credentials_configured ? `/markets/${symbol}` : null, 15000)
  const candles = useMemo(() => resample(data?.candles ?? [], timeframe), [data, timeframe])
  const midpoint = data ? (data.quote.bid + data.quote.ask) / 2 : null
  const indicators = data?.indicators
  return <div className="page-stack">
    <PageHeader title="Market monitor" description="Closed-candle SPY and QQQ context with live quote freshness and execution-data provenance." action={<div className="segmented"><button className={symbol === 'SPY' ? 'active' : ''} onClick={() => setParams({ symbol: 'SPY' })}>SPY</button><button className={symbol === 'QQQ' ? 'active' : ''} onClick={() => setParams({ symbol: 'QQQ' })}>QQQ</button></div>} />
    <Panel title={`${symbol} intraday`} subtitle={data ? `${data.quote.provider} · Quote received ${new Date(data.quote.timestamp).toLocaleTimeString('en-US', { timeZone: 'America/New_York' })} ET` : 'Awaiting entitled market data'} action={<div className="flex items-center gap-3"><StatusDot status={data?.quote.fresh ? 'live' : 'warning'} label={data?.quote.fresh ? 'FRESH' : 'NOT EXECUTION READY'} /><button className="icon-button" onClick={() => void refresh()} aria-label="Refresh market data"><RefreshCw size={14} /></button></div>}>
      {!status?.credentials_configured ? <EmptyState title="Market data is not configured" description="Add Alpaca credentials and an entitled OPRA options feed in .env. The dashboard never substitutes delayed or fabricated prices." /> : loading && !data ? <LoadingBlock /> : error && !data ? <ErrorState message={error} retry={() => void refresh()} /> : <>
        <div className="mb-5 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
          <div><DataLabel>Latest midpoint</DataLabel><div className="mt-1 flex items-baseline gap-3"><strong className="text-3xl font-medium">{money(midpoint)}</strong>{data && <span className="text-xs text-muted-foreground">{money(data.quote.bid)} bid × {money(data.quote.ask)} ask</span>}</div></div>
          <div className="flex items-center gap-3">{data && <Badge tone={data.quote.spread_percent < 0.1 ? 'positive' : 'warning'}>Spread {number(data.quote.spread_percent, 3)}%</Badge>}<div className="segmented">{[1, 5, 15].map((value) => <button key={value} className={timeframe === value ? 'active' : ''} onClick={() => setTimeframe(value)}>{value}m</button>)}</div></div>
        </div>
        {candles.length ? <CandleChart candles={candles} /> : <EmptyState title="No closed candles returned" description="The provider returned no bars for this session. The engine remains ineligible for entries." />}
        {error && <p className="mt-3 text-xs text-warning">Last refresh failed: {error}. Showing the last confirmed data.</p>}
      </>}
    </Panel>
    <div className="indicator-grid">
      <Indicator label="VWAP" value={money(indicators?.vwap)} />
      <Indicator label="EMA 9 / 21 / 50" value={indicators ? `${number(indicators.ema9)} / ${number(indicators.ema21)} / ${number(indicators.ema50)}` : '—'} />
      <Indicator label="RSI 14" value={number(indicators?.rsi, 1)} />
      <Indicator label="ATR 14" value={money(indicators?.atr)} />
      <Indicator label="Relative volume" value={indicators ? `${number(indicators.relative_volume)}×` : '—'} />
      <Indicator label="MACD histogram" value={number(indicators?.macd_histogram, 3)} />
    </div>
    <div className="data-note"><strong>IEX scope notice.</strong> Free IEX stock quotes and volume do not represent the consolidated market. Option entries and exits require fresh executable OPRA or production Tradier data; indicative option quotes are blocked.</div>
  </div>
}

function Indicator({ label, value }: { label: string; value: string }) {
  return <div className="indicator"><DataLabel>{label}</DataLabel><strong className="mt-2 block text-sm font-medium">{value}</strong></div>
}

function resample(candles: Candle[], minutes: number): Candle[] {
  if (minutes === 1) return candles
  const groups = new Map<number, Candle[]>()
  candles.forEach((candle) => {
    const date = new Date(candle.timestamp)
    const bucket = Math.floor(date.getTime() / (minutes * 60_000)) * minutes * 60_000
    groups.set(bucket, [...(groups.get(bucket) ?? []), candle])
  })
  return [...groups.entries()].map(([bucket, group]) => ({ timestamp: new Date(bucket).toISOString(), open: group[0].open, high: Math.max(...group.map((c) => c.high)), low: Math.min(...group.map((c) => c.low)), close: group[group.length - 1].close, volume: group.reduce((sum, c) => sum + c.volume, 0) }))
}
