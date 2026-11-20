import { useMemo, useState } from 'react'
import type { Candle } from '@/types'
import { money, number } from '@/lib/format'

interface RichCandle extends Candle { vwap: number; ema9: number; ema21: number }

export default function CandleChart({ candles }: { candles: Candle[] }) {
  const [hovered, setHovered] = useState<number | null>(null)
  const data = useMemo(() => enrich(candles), [candles])
  if (!data.length) return null
  const width = 900, height = 360, left = 12, right = 64, top = 20, priceBottom = 258, volumeTop = 284, volumeBottom = 326
  const plotWidth = width - left - right
  const min = Math.min(...data.map((d) => d.low))
  const max = Math.max(...data.map((d) => d.high))
  const padding = Math.max((max - min) * 0.08, 0.01)
  const low = min - padding, high = max + padding
  const maxVolume = Math.max(...data.map((d) => d.volume), 1)
  const x = (index: number) => left + (index + 0.5) * plotWidth / data.length
  const y = (price: number) => top + (high - price) / (high - low) * (priceBottom - top)
  const candleWidth = Math.max(1.5, Math.min(9, plotWidth / data.length * 0.62))
  const active = hovered === null ? null : data[hovered]
  const linePath = (key: 'vwap' | 'ema9' | 'ema21') => data.map((d, index) => `${index ? 'L' : 'M'}${x(index).toFixed(2)},${y(d[key]).toFixed(2)}`).join(' ')
  const ticks = Array.from({ length: 5 }, (_, index) => low + (high - low) * index / 4).reverse()

  const onPointer = (event: React.PointerEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    const localX = (event.clientX - rect.left) / rect.width * width
    const index = Math.max(0, Math.min(data.length - 1, Math.floor((localX - left) / plotWidth * data.length)))
    setHovered(index)
  }

  return <div className="chart-container">
    <div className="chart-legend"><span className="text-positive">●</span> Candles <span className="text-[#22d3ee]">— VWAP</span><span className="text-[#fbbf24]">— EMA 9</span><span className="text-[#95959f]">— EMA 21</span></div>
    <svg viewBox={`0 0 ${width} ${height}`} className="block h-auto w-full select-none" role="img" aria-label="Intraday candlestick chart with VWAP and EMA indicators" onPointerMove={onPointer} onPointerDown={onPointer} onPointerLeave={() => setHovered(null)}>
      <defs><linearGradient id="chartFade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#22d3ee" stopOpacity=".06" /><stop offset="1" stopColor="#22d3ee" stopOpacity="0" /></linearGradient></defs>
      <rect x={left} y={top} width={plotWidth} height={priceBottom - top} fill="url(#chartFade)" />
      {ticks.map((tick) => <g key={tick}><line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} stroke="#1a2540" strokeDasharray="2 4" /><text x={width - right + 8} y={y(tick) + 3} fill="#95959f" fontSize="10">{number(tick, 2)}</text></g>)}
      <line x1={left} x2={width - right} y1={volumeTop - 12} y2={volumeTop - 12} stroke="#1a2540" />
      {data.map((candle, index) => {
        const positive = candle.close >= candle.open
        const color = positive ? '#34d399' : '#f87171'
        const bodyTop = y(Math.max(candle.open, candle.close))
        const bodyHeight = Math.max(1, Math.abs(y(candle.open) - y(candle.close)))
        return <g key={`${candle.timestamp}-${index}`} opacity={hovered === null || hovered === index ? 1 : 0.72}>
          <line x1={x(index)} x2={x(index)} y1={y(candle.high)} y2={y(candle.low)} stroke={color} strokeWidth="1" />
          <rect x={x(index) - candleWidth / 2} y={bodyTop} width={candleWidth} height={bodyHeight} fill={color} rx=".5" />
          <rect x={x(index) - candleWidth / 2} y={volumeBottom - candle.volume / maxVolume * (volumeBottom - volumeTop)} width={candleWidth} height={candle.volume / maxVolume * (volumeBottom - volumeTop)} fill={color} opacity=".3" />
        </g>
      })}
      <path d={linePath('vwap')} fill="none" stroke="#22d3ee" strokeWidth="1.25" />
      <path d={linePath('ema9')} fill="none" stroke="#fbbf24" strokeWidth="1" />
      <path d={linePath('ema21')} fill="none" stroke="#95959f" strokeWidth="1" />
      {active && <g pointerEvents="none"><line x1={x(hovered!)} x2={x(hovered!)} y1={top} y2={volumeBottom} stroke="#22d3ee" strokeDasharray="3 3" opacity=".6" /><rect x={Math.min(width - 235, Math.max(8, x(hovered!) + 10))} y="26" width="218" height="78" rx="3" fill="#0c1220" stroke="#1a2540" /><text x={Math.min(width - 222, Math.max(21, x(hovered!) + 23))} y="45" fill="#95959f" fontSize="10">{new Date(active.timestamp).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit' })} ET</text><text x={Math.min(width - 222, Math.max(21, x(hovered!) + 23))} y="64" fill="#f0f4f8" fontSize="11">O {money(active.open)}   H {money(active.high)}</text><text x={Math.min(width - 222, Math.max(21, x(hovered!) + 23))} y="81" fill="#f0f4f8" fontSize="11">L {money(active.low)}   C {money(active.close)}</text><text x={Math.min(width - 222, Math.max(21, x(hovered!) + 23))} y="97" fill="#95959f" fontSize="10">VOL {number(active.volume, 0)}</text></g>}
      <text x={left} y={350} fill="#95959f" fontSize="9">{new Date(data[0].timestamp).toLocaleString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</text>
      <text x={width - right} y={350} textAnchor="end" fill="#95959f" fontSize="9">{new Date(data[data.length - 1].timestamp).toLocaleString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })} ET</text>
    </svg>
  </div>
}

function enrich(candles: Candle[]): RichCandle[] {
  let cumulativeVolume = 0, cumulativeValue = 0, currentDay = ''
  let ema9: number | null = null, ema21: number | null = null
  return candles.map((candle) => {
    const day = new Date(candle.timestamp).toLocaleDateString('en-US', { timeZone: 'America/New_York' })
    if (day !== currentDay) { currentDay = day; cumulativeVolume = 0; cumulativeValue = 0 }
    const typical = (candle.high + candle.low + candle.close) / 3
    cumulativeVolume += candle.volume
    cumulativeValue += typical * candle.volume
    ema9 = ema9 === null ? candle.close : candle.close * (2 / 10) + ema9 * (8 / 10)
    ema21 = ema21 === null ? candle.close : candle.close * (2 / 22) + ema21 * (20 / 22)
    return { ...candle, vwap: cumulativeVolume ? cumulativeValue / cumulativeVolume : candle.close, ema9, ema21 }
  })
}
