import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  createChart,
} from 'lightweight-charts'
import { formatDailyAmount, formatNumber } from './viewSupport.jsx'

const CHART_RANGES = [
  { id: 'recent', label: '近 180 日' },
  { id: '1y', label: '近 1 年' },
  { id: '3y', label: '近 3 年' },
  { id: '5y', label: '近 5 年' },
  { id: 'all', label: '全部历史' },
]

export function TechnicalChart({ bars }) {
  const priceRef = useRef(null)
  const volumeRef = useRef(null)
  const [range, setRange] = useState('recent')
  const series = useMemo(() => buildTechnicalSeries(bars), [bars])
  const rangeLabel = CHART_RANGES.find((item) => item.id === range)?.label || '当前区间'
  const visibleStart = chartRangeStartIndex(series.candles, range)
  const visibleCandles = series.candles.slice(visibleStart)
  const visibleBars = series.bars.slice(visibleStart)

  useEffect(() => {
    if (!series.candles.length) return undefined
    const charts = [renderPriceChart(priceRef.current, series), renderVolumeChart(volumeRef.current, series)].filter(Boolean)
    applyChartRange(charts, series.candles, range)
    synchronizeChartRanges(charts)
    const resize = () => charts.forEach(({ chart, element }) => chart.applyOptions({ width: element.clientWidth }))
    window.addEventListener('resize', resize)
    return () => {
      window.removeEventListener('resize', resize)
      charts.forEach(({ chart }) => chart.remove())
    }
  }, [range, series])

  if (!series.candles.length) return <div className="chart-empty">暂无可绘制的日线数据</div>
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
              {accessibleBars.map((bar) => <tr key={bar.trade_date}><td>{bar.trade_date}</td><td>{formatNumber(bar.open)}</td><td>{formatNumber(bar.high)}</td><td>{formatNumber(bar.low)}</td><td className="mono strong">{formatNumber(bar.close)}</td><td>{formatNumber(bar.vol)}</td><td>{formatDailyAmount(bar.amount)}</td></tr>)}
            </tbody>
          </table>
        </div>
      </details>
    </>
  )
}

function buildTechnicalSeries(bars) {
  const validBars = bars.filter((bar) => bar.trade_date && [bar.open, bar.high, bar.low, bar.close].every((value) => Number.isFinite(Number(value))))
  const candles = validBars
    .map((bar) => ({ time: bar.trade_date, open: Number(bar.open), high: Number(bar.high), low: Number(bar.low), close: Number(bar.close) }))
  const closes = candles.map((bar) => bar.close)
  return {
    bars: validBars,
    candles,
    ma10: calcSma(candles, closes, 10),
    ma20: calcSma(candles, closes, 20),
    volume: validBars.map((bar, index) => ({ time: bar.trade_date, value: Number(bar.vol || 0), color: candles[index].close >= candles[index].open ? '#d84b4b99' : '#07876199' })),
  }
}

function calcSma(candles, values, period) {
  return values.map((_, index) => {
    if (index + 1 < period) return null
    const slice = values.slice(index + 1 - period, index + 1)
    return { time: candles[index].time, value: slice.reduce((sum, value) => sum + value, 0) / period }
  }).filter(Boolean)
}

function renderPriceChart(element, series) {
  if (!element) return null
  element.replaceChildren()
  const chart = createBaseChart(element, 430)
  chart.addSeries(CandlestickSeries, {
    upColor: '#d84b4b', downColor: '#078761', borderUpColor: '#d84b4b', borderDownColor: '#078761', wickUpColor: '#d84b4b', wickDownColor: '#078761',
  }).setData(series.candles)
  chart.addSeries(LineSeries, { color: '#087ea4', lineWidth: 2, priceLineVisible: false }).setData(series.ma10)
  chart.addSeries(LineSeries, { color: '#d78a17', lineWidth: 2, priceLineVisible: false }).setData(series.ma20)
  return { chart, element }
}

function renderVolumeChart(element, series) {
  if (!element) return null
  element.replaceChildren()
  const chart = createBaseChart(element, 125)
  chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceLineVisible: false }).setData(series.volume)
  return { chart, element }
}

function applyChartRange(charts, candles, range) {
  if (!candles.length) return
  if (range === 'all') {
    charts.forEach(({ chart }) => chart.timeScale().fitContent())
    return
  }
  const from = chartRangeStartIndex(candles, range)
  const visibleRange = { from: Math.max(-0.5, from - 0.5), to: candles.length - 0.5 }
  charts.forEach(({ chart }) => chart.timeScale().setVisibleLogicalRange(visibleRange))
}

function chartRangeStartIndex(candles, range) {
  if (!candles.length || range === 'all') return 0
  const years = { '1y': 1, '3y': 3, '5y': 5 }[range]
  if (!years) return Math.max(0, candles.length - 180)
  const cutoff = new Date(`${candles[candles.length - 1].time}T00:00:00`)
  cutoff.setFullYear(cutoff.getFullYear() - years)
  const cutoffText = cutoff.toISOString().slice(0, 10)
  const firstVisible = candles.findIndex((bar) => bar.time >= cutoffText)
  return firstVisible < 0 ? 0 : firstVisible
}

function synchronizeChartRanges(charts) {
  if (charts.length < 2) return
  let syncing = false
  for (const source of charts) {
    source.chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range || syncing) return
      syncing = true
      for (const target of charts) {
        if (target !== source) target.chart.timeScale().setVisibleLogicalRange(range)
      }
      syncing = false
    })
  }
}

function createBaseChart(element, height) {
  return createChart(element, {
    width: element.clientWidth,
    height,
    layout: { background: { color: '#f8fafb' }, textColor: '#61707c', fontFamily: 'IBM Plex Mono, Consolas, monospace', fontSize: 11 },
    grid: { vertLines: { color: '#e9eef1' }, horzLines: { color: '#e3e9ed' } },
    rightPriceScale: { borderColor: '#ccd5db', scaleMargins: { top: 0.08, bottom: 0.08 } },
    timeScale: { borderColor: '#ccd5db', timeVisible: false },
    crosshair: { mode: 1 },
  })
}
