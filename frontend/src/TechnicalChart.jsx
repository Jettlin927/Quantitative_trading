import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  createChart,
} from 'lightweight-charts'

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
  return (
    <div className="chart-stack">
      <div className="chart-toolbar">
        <div className="chart-legend"><span><i className="ma10" />MA10</span><span><i className="ma20" />MA20</span><span><i className="volume" />成交量</span></div>
        <div className="chart-ranges" aria-label="行情显示区间">
          {CHART_RANGES.map((item) => (
            <button className={range === item.id ? 'active' : ''} key={item.id} onClick={() => setRange(item.id)}>{item.label}</button>
          ))}
        </div>
      </div>
      <div className="chart-pane price" ref={priceRef} />
      <div className="chart-pane volume" ref={volumeRef} />
    </div>
  )
}

function buildTechnicalSeries(bars) {
  const candles = bars
    .filter((bar) => [bar.open, bar.high, bar.low, bar.close].every((value) => Number.isFinite(Number(value))))
    .map((bar) => ({ time: bar.trade_date, open: Number(bar.open), high: Number(bar.high), low: Number(bar.low), close: Number(bar.close) }))
  const closes = candles.map((bar) => bar.close)
  return {
    candles,
    ma10: calcSma(candles, closes, 10),
    ma20: calcSma(candles, closes, 20),
    volume: bars.map((bar, index) => ({ time: bar.trade_date, value: Number(bar.vol || 0), color: candles[index]?.close >= candles[index]?.open ? '#d84b4b99' : '#07876199' })),
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
  const years = { '1y': 1, '3y': 3, '5y': 5 }[range]
  let from = Math.max(0, candles.length - 180)
  if (years) {
    const cutoff = new Date(`${candles[candles.length - 1].time}T00:00:00`)
    cutoff.setFullYear(cutoff.getFullYear() - years)
    const cutoffText = cutoff.toISOString().slice(0, 10)
    const firstVisible = candles.findIndex((bar) => bar.time >= cutoffText)
    from = firstVisible < 0 ? 0 : firstVisible
  }
  const visibleRange = { from: Math.max(-0.5, from - 0.5), to: candles.length - 0.5 }
  charts.forEach(({ chart }) => chart.timeScale().setVisibleLogicalRange(visibleRange))
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
