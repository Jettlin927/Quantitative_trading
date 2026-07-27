import { useEffect, useMemo, useRef, useState } from 'react'
import { lightweightChartAdapter, chartRangeStartIndex } from './marketChartAdapter.js'
import { formatDailyAmount, formatNumber } from './viewSupport.jsx'

const CHART_RANGES = [
  { id: 'recent', label: '近 180 日' },
  { id: '1y', label: '近 1 年' },
  { id: '3y', label: '近 3 年' },
  { id: '5y', label: '近 5 年' },
  { id: 'all', label: '全部历史' },
]

/**
 * MarketChart accepts ascending MarketBar values. OHLC must be finite; volume
 * and amount may be null. Chart lifecycle is delegated to the injected adapter.
 */
export function MarketChart({ bars, chartAdapter = lightweightChartAdapter }) {
  const priceRef = useRef(null)
  const volumeRef = useRef(null)
  const chartRef = useRef(null)
  const [range, setRange] = useState('recent')
  const series = useMemo(() => buildMarketSeries(bars), [bars])
  const hasData = series.candles.length > 0

  useEffect(() => {
    if (!hasData) return undefined
    const chart = chartAdapter.create({ priceElement: priceRef.current, volumeElement: volumeRef.current })
    chartRef.current = chart
    const resize = () => chart.resize()
    window.addEventListener('resize', resize)
    return () => {
      window.removeEventListener('resize', resize)
      chart.dispose()
      if (chartRef.current === chart) chartRef.current = null
    }
  }, [chartAdapter, hasData])

  useEffect(() => {
    if (!chartRef.current || !hasData) return
    chartRef.current.setData(series)
    chartRef.current.setRange(series.candles, range)
  }, [hasData, range, series])

  if (!hasData) return <div className="chart-empty">暂无可绘制的日线数据</div>
  const rangeLabel = CHART_RANGES.find((item) => item.id === range)?.label || '当前区间'
  const visibleStart = chartRangeStartIndex(series.candles, range)
  const visibleCandles = series.candles.slice(visibleStart)
  const visibleBars = series.bars.slice(visibleStart)
  const first = visibleCandles[0]
  const latest = visibleCandles[visibleCandles.length - 1]
  const accessibleBars = [...visibleBars].reverse()
  const summary = `${rangeLabel}：日 K 线共 ${visibleCandles.length} 个交易日，范围 ${first.time} 至 ${latest.time}，最新收盘 ${formatNumber(latest.close)}`

  return (
    <>
      <div className="chart-stack">
        <div className="chart-toolbar">
          <div className="chart-legend"><span><i className="ma10" />MA10</span><span><i className="ma20" />MA20</span><span><i className="volume" />成交量</span></div>
          <div className="chart-ranges" aria-label="行情显示区间">
            {CHART_RANGES.map((item) => (
              <button aria-pressed={range === item.id} className={range === item.id ? 'active' : ''} key={item.id} onClick={() => setRange(item.id)}>{item.label}</button>
            ))}
          </div>
        </div>
        <div className="chart-pane price" ref={priceRef} role="img" aria-label={`价格图。${summary}`} />
        <div className="chart-pane volume" ref={volumeRef} role="img" aria-label={`成交量图。${summary}`} />
      </div>
      <details className="chart-accessible-table">
        <summary>查看{rangeLabel}行情数据表（{accessibleBars.length} 条）</summary>
        <div className="table-scroll compact-history">
          <table className="data-table">
            <caption className="sr-only">{summary}</caption>
            <thead><tr><th>交易日</th><th>开盘</th><th>最高</th><th>最低</th><th>收盘</th><th>成交量</th><th>成交额</th></tr></thead>
            <tbody>
              {accessibleBars.map((bar) => <tr key={bar.time}><td>{bar.time}</td><td>{formatNumber(bar.open)}</td><td>{formatNumber(bar.high)}</td><td>{formatNumber(bar.low)}</td><td className="mono strong">{formatNumber(bar.close)}</td><td>{formatNumber(bar.volume)}</td><td>{formatDailyAmount(bar.amount)}</td></tr>)}
            </tbody>
          </table>
        </div>
      </details>
    </>
  )
}

function buildMarketSeries(bars) {
  const validBars = bars.filter((bar) => bar.time && [bar.open, bar.high, bar.low, bar.close].every((value) => Number.isFinite(Number(value))))
  const candles = validBars.map((bar) => ({ time: bar.time, open: Number(bar.open), high: Number(bar.high), low: Number(bar.low), close: Number(bar.close) }))
  const closes = candles.map((bar) => bar.close)
  return {
    bars: validBars,
    candles,
    ma10: calcSma(candles, closes, 10),
    ma20: calcSma(candles, closes, 20),
    volume: validBars.map((bar, index) => ({ time: bar.time, value: Number(bar.volume || 0), color: candles[index].close >= candles[index].open ? '#d84b4b99' : '#07876199' })),
  }
}

function calcSma(candles, values, period) {
  return values.map((_, index) => {
    if (index + 1 < period) return null
    const slice = values.slice(index + 1 - period, index + 1)
    return { time: candles[index].time, value: slice.reduce((sum, value) => sum + value, 0) / period }
  }).filter(Boolean)
}
