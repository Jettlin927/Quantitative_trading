import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from './main.jsx'

vi.mock('lightweight-charts', () => ({
  CandlestickSeries: 'candlestick',
  HistogramSeries: 'histogram',
  LineSeries: 'line',
  createChart: vi.fn(() => {
    const timeScale = { fitContent: vi.fn(), setVisibleLogicalRange: vi.fn(), subscribeVisibleLogicalRangeChange: vi.fn() }
    return { addSeries: vi.fn(() => ({ setData: vi.fn() })), applyOptions: vi.fn(), remove: vi.fn(), timeScale: () => timeScale }
  }),
}))

function deferred() {
  /** @type {(value: any) => void} */
  let resolve = () => {}
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

function stockPage(items) {
  return { items, total: items.length, limit: 50, offset: 0 }
}

function instrumentPage(items) {
  return { isExperimental: true, researchEligible: false, items, total: items.length, limit: 50, offset: 0 }
}

function fallback(path) {
  if (path === '/api/health?include_counts=false') return { status: 'ok', database: 'ok' }
  if (path === '/api/tushare/sync-progress?include_coverage=false') return { runs: [] }
  if (path.startsWith('/api/research/readiness')) return { level: 'inventory', status: 'inventory_available', blockers: [] }
  if (path === '/api/db/overview') return { aShare: {} }
  if (path.startsWith('/api/indices') || path.startsWith('/api/funds') || path.startsWith('/api/industries') || path === '/api/research/strategies') return []
  if (path === '/api/us-research/db-overview') return { counts: {}, assets: [] }
  if (path === '/api/us-experiment/overview') return { universe: {}, coverage: {}, validation: {}, schedule: {} }
  return {}
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('股票研究 tracer', () => {
  it('A 股新选择会取消旧请求，且 ReadAdapter 忽略 abort 时旧响应仍不能覆盖当前选择', async () => {
    const oldBars = deferred()
    const oldDetail = deferred()
    /** @type {AbortSignal[]} */
    const oldSignals = []
    const readAdapter = vi.fn(({ path, signal }) => {
      if (path === '/api/stocks/screen?limit=50&offset=0') return Promise.resolve(stockPage([
        { ts_code: '000001.SZ', symbol: '000001', name: '甲公司' },
        { ts_code: '000002.SZ', symbol: '000002', name: '乙公司' },
      ]))
      if (path === '/api/daily-bars?ts_code=000001.SZ') { oldSignals.push(signal); return oldBars.promise }
      if (path === '/api/stocks/000001.SZ/detail') { oldSignals.push(signal); return oldDetail.promise }
      if (path === '/api/daily-bars?ts_code=000002.SZ') return Promise.resolve([])
      if (path === '/api/stocks/000002.SZ/detail') return Promise.resolve({ listing: { listStatus: '乙股当前状态' }, valuation_history: [], financial_history: [] })
      if (path === '/api/us-experiment/instruments?current_only=true&limit=50&offset=0') return Promise.resolve(instrumentPage([]))
      return Promise.resolve(fallback(path))
    })

    const view = render(<App readAdapter={readAdapter} />)
    fireEvent.click(screen.getByRole('button', { name: /A 股数据/ }))
    await waitFor(() => expect(oldSignals).toHaveLength(2))

    fireEvent.click(screen.getByRole('button', { name: /000002.*乙公司/ }))
    expect(await screen.findByText('乙股当前状态')).toBeInTheDocument()
    expect(oldSignals.every((signal) => signal.aborted)).toBe(true)

    oldBars.resolve([{ trade_date: '2026-01-01', open: 1, high: 2, low: 1, close: 2, vol: 3, amount: 4 }])
    oldDetail.resolve({ listing: { listStatus: '甲股过期状态' }, valuation_history: [], financial_history: [] })
    await waitFor(() => expect(screen.queryByText('甲股过期状态')).not.toBeInTheDocument())

    view.unmount()
  })

  it('A 股与美股分别转换为带来源边界的 MarketBar，不把实验数据或 sample 混入实际市场数据', async () => {
    const setData = vi.fn()
    const chartAdapter = {
      create: vi.fn(() => ({ setData, setRange: vi.fn(), resize: vi.fn(), dispose: vi.fn() })),
    }
    const readAdapter = vi.fn(({ path }) => {
      if (path === '/api/stocks/screen?limit=50&offset=0') return Promise.resolve(stockPage([{ ts_code: '000001.SZ', symbol: '000001', name: '甲公司' }]))
      if (path === '/api/daily-bars?ts_code=000001.SZ') return Promise.resolve([{ trade_date: '2026-01-02', open: 1, high: 2, low: 1, close: 2, vol: 3, amount: 4 }])
      if (path === '/api/stocks/000001.SZ/detail') return Promise.resolve({ listing: {}, valuation_history: [], financial_history: [] })
      if (path === '/api/us-experiment/instruments?current_only=true&limit=50&offset=0') return Promise.resolve(instrumentPage([{ sourceCode: 'TGT.AAPL', symbol: 'AAPL', name: 'Apple' }]))
      if (path === '/api/us-experiment/instruments/TGT.AAPL/daily-bars') return Promise.resolve({ bars: [{ tradeDate: '2026-01-02', open: 10, high: 12, low: 9, close: 11, volume: 30, source: 'yfinance' }] })
      return Promise.resolve(fallback(path))
    })

    render(<App readAdapter={readAdapter} chartAdapter={chartAdapter} />)
    fireEvent.click(screen.getByRole('button', { name: /A 股数据/ }))
    await waitFor(() => expect(setData).toHaveBeenCalledWith(expect.objectContaining({
      bars: [expect.objectContaining({ provenance: { kind: 'actual_market', label: '实际市场数据', source: 'PostgreSQL', sample: false } })],
    })))

    fireEvent.click(screen.getByRole('button', { name: /美股数据/ }))
    await waitFor(() => expect(setData).toHaveBeenCalledWith(expect.objectContaining({
      bars: [expect.objectContaining({ provenance: { kind: 'experimental', label: '实验数据', source: 'yfinance', researchEligible: false, sample: false } })],
    })))
  })

  it('美股新选择与 unmount 会 abort，generation 和 selection 阻止过期实验行情提交', async () => {
    const oldBars = deferred()
    const currentBars = deferred()
    /** @type {AbortSignal[]} */
    const oldSignals = []
    /** @type {AbortSignal[]} */
    const currentSignals = []
    const readAdapter = vi.fn(({ path, signal }) => {
      if (path === '/api/stocks/screen?limit=50&offset=0') return Promise.resolve(stockPage([]))
      if (path === '/api/us-experiment/instruments?current_only=true&limit=50&offset=0') return Promise.resolve(instrumentPage([
        { sourceCode: 'TGT.AAPL', symbol: 'AAPL', name: 'Apple' },
        { sourceCode: 'TGT.NVDA', symbol: 'NVDA', name: 'NVIDIA' },
      ]))
      if (path === '/api/us-experiment/instruments/TGT.AAPL/daily-bars') { oldSignals.push(signal); return oldBars.promise }
      if (path === '/api/us-experiment/instruments/TGT.NVDA/daily-bars') { currentSignals.push(signal); return currentBars.promise }
      return Promise.resolve(fallback(path))
    })

    const view = render(<App readAdapter={readAdapter} />)
    fireEvent.click(screen.getByRole('button', { name: /美股数据/ }))
    await waitFor(() => expect(oldSignals).toHaveLength(1))
    fireEvent.click(screen.getByRole('button', { name: /NVDA.*NVIDIA/ }))
    expect(oldSignals[0].aborted).toBe(true)

    oldBars.resolve({ bars: [{ tradeDate: '2026-01-01', open: 1, high: 2, low: 1, close: 2, volume: 3, source: '过期源' }] })
    currentBars.resolve({ bars: [{ tradeDate: '2026-01-02', open: 10, high: 12, low: 9, close: 11, volume: 30, source: 'yfinance' }] })
    expect(await screen.findByRole('heading', { name: 'NVIDIA' })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('2026-01-01')).not.toBeInTheDocument())

    view.unmount()
    expect(currentSignals[0].aborted).toBe(true)
  })
})
