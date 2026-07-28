import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  createChart,
} from 'lightweight-charts'

/** Production chart adapter for the MarketChart seam. */
export const lightweightChartAdapter = {
  create({ priceElement, volumeElement }) {
    priceElement.replaceChildren()
    volumeElement.replaceChildren()
    const price = createBaseChart(priceElement, 430)
    const volume = createBaseChart(volumeElement, 125)
    const candles = price.addSeries(CandlestickSeries, {
      upColor: '#d84b4b', downColor: '#078761', borderUpColor: '#d84b4b', borderDownColor: '#078761', wickUpColor: '#d84b4b', wickDownColor: '#078761',
    })
    const ma10 = price.addSeries(LineSeries, { color: '#087ea4', lineWidth: 2, priceLineVisible: false })
    const ma20 = price.addSeries(LineSeries, { color: '#d78a17', lineWidth: 2, priceLineVisible: false })
    const volumes = volume.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceLineVisible: false })
    const charts = [price, volume]
    synchronizeRanges(charts)

    return {
      setData(series) {
        candles.setData(series.candles)
        ma10.setData(series.ma10)
        ma20.setData(series.ma20)
        volumes.setData(series.volume)
      },
      setRange(seriesCandles, range) {
        applyRange(charts, seriesCandles, range)
      },
      resize() {
        price.applyOptions({ width: priceElement.clientWidth })
        volume.applyOptions({ width: volumeElement.clientWidth })
      },
      dispose() {
        price.remove()
        volume.remove()
      },
    }
  },
}

function applyRange(charts, candles, range) {
  if (!candles.length) return
  if (range === 'all') {
    charts.forEach((chart) => chart.timeScale().fitContent())
    return
  }
  const from = chartRangeStartIndex(candles, range)
  const visibleRange = { from: Math.max(-0.5, from - 0.5), to: candles.length - 0.5 }
  charts.forEach((chart) => chart.timeScale().setVisibleLogicalRange(visibleRange))
}

function synchronizeRanges(charts) {
  let syncing = false
  for (const source of charts) {
    source.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range || syncing) return
      syncing = true
      charts.forEach((target) => {
        if (target !== source) target.timeScale().setVisibleLogicalRange(range)
      })
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

export function chartRangeStartIndex(candles, range) {
  if (!candles.length || range === 'all') return 0
  const years = { '1y': 1, '3y': 3, '5y': 5 }[range]
  if (!years) return Math.max(0, candles.length - 180)
  const cutoff = new Date(`${candles[candles.length - 1].time}T00:00:00Z`)
  cutoff.setUTCFullYear(cutoff.getUTCFullYear() - years)
  const cutoffText = cutoff.toISOString().slice(0, 10)
  const firstVisible = candles.findIndex((bar) => bar.time >= cutoffText)
  return firstVisible < 0 ? 0 : firstVisible
}
