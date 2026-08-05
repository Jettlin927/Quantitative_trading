import { AreaSeries, LineSeries, createChart } from 'lightweight-charts'

/**
 * 权益日线适配器：单一折线 + 面积填充。points 为升序
 * [{ time: 'yyyy-mm-dd', value: number, after_close: boolean }]。
 * 图表生命周期委托给注入的 adapter，便于测试替换。
 */
export const lightweightEquityChartAdapter = {
  create({ element }) {
    element.replaceChildren()
    const chart = createChart(element, {
      width: element.clientWidth,
      height: 300,
      layout: { background: { color: '#f8fafb' }, textColor: '#61707c', fontFamily: 'IBM Plex Mono, Consolas, monospace', fontSize: 11 },
      grid: { vertLines: { color: '#e9eef1' }, horzLines: { color: '#e3e9ed' } },
      rightPriceScale: { borderColor: '#ccd5db', scaleMargins: { top: 0.12, bottom: 0.12 } },
      timeScale: { borderColor: '#ccd5db', timeVisible: false },
      crosshair: { mode: 1 },
    })
    const line = chart.addSeries(LineSeries, { color: '#0e7490', lineWidth: 2, priceLineVisible: false })
    const area = chart.addSeries(AreaSeries, {
      lineColor: '#0e7490', topColor: 'rgba(14, 116, 144, 0.18)', bottomColor: 'rgba(14, 116, 144, 0.01)',
      lineWidth: 2, priceLineVisible: false,
    })
    return {
      setData(points) {
        area.setData(points.map(({ time, value }) => ({ time, value })))
        line.setData(points.map(({ time, value }) => ({ time, value })))
        chart.timeScale().fitContent()
      },
      setRange() {},
      resize() {
        chart.applyOptions({ width: element.clientWidth })
      },
      dispose() {
        chart.remove()
      },
    }
  },
}
