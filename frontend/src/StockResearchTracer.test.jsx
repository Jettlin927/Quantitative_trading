import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
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
  it('A→B→A 快速选择只复用同 generation 请求，ReadAdapter 忽略 abort 时旧响应不能覆盖', async () => {
    const oldBars = deferred()
    const oldDetail = deferred()
    let aBarCalls = 0
    let aDetailCalls = 0
    /** @type {AbortSignal[]} */
    const oldSignals = []
    const readAdapter = vi.fn(({ path, signal }) => {
      if (path === '/api/stocks/screen?limit=50&offset=0') return Promise.resolve(stockPage([
        { ts_code: '000001.SZ', symbol: '000001', name: '甲公司' },
        { ts_code: '000002.SZ', symbol: '000002', name: '乙公司' },
      ]))
      if (path === '/api/daily-bars?ts_code=000001.SZ') {
        aBarCalls += 1
        if (aBarCalls === 1) { oldSignals.push(signal); return oldBars.promise }
        return Promise.resolve([])
      }
      if (path === '/api/stocks/000001.SZ/detail') {
        aDetailCalls += 1
        if (aDetailCalls === 1) { oldSignals.push(signal); return oldDetail.promise }
        return Promise.resolve({ listing: { listStatus: '甲股重新选中状态' }, valuation_history: [], financial_history: [] })
      }
      if (path === '/api/daily-bars?ts_code=000002.SZ') return Promise.resolve([])
      if (path === '/api/stocks/000002.SZ/detail') return Promise.resolve({ listing: { listStatus: '乙股当前状态' }, valuation_history: [], financial_history: [] })
      if (path === '/api/us-experiment/instruments?current_only=true&limit=50&offset=0') return Promise.resolve(instrumentPage([]))
      return Promise.resolve(fallback(path))
    })

    const view = render(<App readAdapter={readAdapter} />)
    fireEvent.click(screen.getByRole('button', { name: /A 股数据/ }))
    await waitFor(() => expect(oldSignals).toHaveLength(2))

    fireEvent.click(screen.getByRole('button', { name: /000002.*乙公司/ }))
    expect(oldSignals.every((signal) => signal.aborted)).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: /000001.*甲公司/ }))
    expect(await screen.findByText('甲股重新选中状态')).toBeInTheDocument()
    expect(aBarCalls).toBe(2)
    expect(aDetailCalls).toBe(2)

    oldBars.resolve([{ trade_date: '2026-01-01', open: 1, high: 2, low: 1, close: 2, vol: 3, amount: 4 }])
    oldDetail.resolve({ listing: { listStatus: '甲股过期状态' }, valuation_history: [], financial_history: [] })
    await waitFor(() => expect(screen.queryByText('甲股过期状态')).not.toBeInTheDocument())
    expect(screen.getByText('甲股重新选中状态')).toBeInTheDocument()

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

  it('StrictMode setup→cleanup→setup 后仍会提交当前股票明细', async () => {
    const readAdapter = vi.fn(({ path }) => {
      if (path === '/api/stocks/screen?limit=50&offset=0') return Promise.resolve(stockPage([{ ts_code: '000001.SZ', symbol: '000001', name: '甲公司' }]))
      if (path === '/api/daily-bars?ts_code=000001.SZ') return Promise.resolve([])
      if (path === '/api/stocks/000001.SZ/detail') return Promise.resolve({ listing: { listStatus: 'StrictMode 当前状态' }, valuation_history: [], financial_history: [] })
      if (path === '/api/us-experiment/instruments?current_only=true&limit=50&offset=0') return Promise.resolve(instrumentPage([]))
      return Promise.resolve(fallback(path))
    })

    render(<StrictMode><App readAdapter={readAdapter} /></StrictMode>)
    fireEvent.click(screen.getByRole('button', { name: /A 股数据/ }))

    expect(await screen.findByText('StrictMode 当前状态')).toBeInTheDocument()
  })

  it('快速搜索会取消旧列表请求，adapter 忽略 abort 时不提交 stale，也不显示全局失败', async () => {
    const oldList = deferred()
    /** @type {AbortSignal[]} */
    const oldSignals = []
    const readAdapter = vi.fn(({ path, signal }) => {
      if (path === '/api/stocks/screen?limit=50&offset=0') { oldSignals.push(signal); return oldList.promise }
      if (path === '/api/stocks/screen?limit=50&offset=0&q=%E4%B9%99') return Promise.resolve(stockPage([{ ts_code: '000002.SZ', symbol: '000002', name: '乙公司' }]))
      if (path === '/api/daily-bars?ts_code=000002.SZ') return Promise.resolve([])
      if (path === '/api/stocks/000002.SZ/detail') return Promise.resolve({ listing: { listStatus: '乙股当前状态' }, valuation_history: [], financial_history: [] })
      if (path === '/api/us-experiment/instruments?current_only=true&limit=50&offset=0') return Promise.resolve(instrumentPage([]))
      return Promise.resolve(fallback(path))
    })

    render(<App readAdapter={readAdapter} />)
    fireEvent.click(screen.getByRole('button', { name: /A 股数据/ }))
    await waitFor(() => expect(oldSignals).toHaveLength(1))
    fireEvent.change(screen.getByPlaceholderText('代码 / 名称 / 拼音'), { target: { value: '乙' } })
    fireEvent.click(screen.getByRole('button', { name: '查询' }))
    expect(await screen.findByRole('button', { name: /000002.*乙公司/ })).toBeInTheDocument()
    expect(oldSignals[0].aborted).toBe(true)

    oldList.resolve(stockPage([{ ts_code: '000001.SZ', symbol: '000001', name: '甲公司' }]))
    await waitFor(() => expect(screen.getByRole('button', { name: /全局刷新/ })).not.toBeDisabled())
    expect(screen.queryByText(/部分只读数据读取失败/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /000001.*甲公司/ })).not.toBeInTheDocument()
  })

  it('当前列表真实失败仍显示局部错误和全局失败，不与 cancelled/stale/skipped 混淆', async () => {
    const readAdapter = vi.fn(({ path }) => {
      if (path === '/api/stocks/screen?limit=50&offset=0') return Promise.reject(new Error('股票目录不可用'))
      if (path === '/api/us-experiment/instruments?current_only=true&limit=50&offset=0') return Promise.resolve(instrumentPage([]))
      return Promise.resolve(fallback(path))
    })

    render(<App readAdapter={readAdapter} />)
    fireEvent.click(screen.getByRole('button', { name: /A 股数据/ }))

    expect(await screen.findByText('股票目录不可用')).toBeInTheDocument()
    expect(screen.getByText(/部分只读数据读取失败.*stocks: 股票目录不可用/)).toBeInTheDocument()
  })

  it('研究驾驶舱刷新不等待隐藏的 A 股明细请求', async () => {
    let barsCalls = 0
    let detailCalls = 0
    const readAdapter = vi.fn(({ path }) => {
      if (path.startsWith('/api/stocks/screen?')) {
        return Promise.resolve(stockPage([{ ts_code: '000001.SZ', symbol: '000001', name: '甲公司' }]))
      }
      if (path === '/api/daily-bars?ts_code=000001.SZ') {
        barsCalls += 1
        return Promise.resolve([])
      }
      if (path === '/api/stocks/000001.SZ/detail') {
        detailCalls += 1
        return Promise.resolve({ listing: { listStatus: '甲股当前状态' }, valuation_history: [], financial_history: [] })
      }
      if (path === '/api/us-experiment/instruments?current_only=true&limit=50&offset=0') return Promise.resolve(instrumentPage([]))
      return Promise.resolve(fallback(path))
    })

    render(<App readAdapter={readAdapter} />)
    await waitFor(() => expect(screen.getByRole('button', { name: /全局刷新/ })).not.toBeDisabled())
    expect(barsCalls).toBe(0)
    expect(detailCalls).toBe(0)

    fireEvent.click(screen.getByRole('button', { name: /全局刷新/ }))
    await waitFor(() => expect(screen.getByRole('button', { name: /全局刷新/ })).not.toBeDisabled())

    expect(barsCalls).toBe(0)
    expect(detailCalls).toBe(0)

    fireEvent.click(screen.getByRole('button', { name: /A 股数据/ }))
    await waitFor(() => expect(detailCalls).toBe(1))
    expect(barsCalls).toBe(1)
  })

  it('全局刷新更换选中项时复用同一明细请求，不因 effect 并发 abort 产生虚假失败', async () => {
    let listCalls = 0
    let nextDetailCalls = 0
    const nextDetail = deferred()
    const readAdapter = vi.fn(({ path }) => {
      if (path.startsWith('/api/stocks/screen?')) {
        listCalls += 1
        return Promise.resolve(stockPage([listCalls === 1
          ? { ts_code: '000001.SZ', symbol: '000001', name: '甲公司' }
          : { ts_code: '000002.SZ', symbol: '000002', name: '乙公司' }]))
      }
      if (path === '/api/daily-bars?ts_code=000001.SZ') return Promise.resolve([])
      if (path === '/api/stocks/000001.SZ/detail') return Promise.resolve({ listing: { listStatus: '甲股当前状态' }, valuation_history: [], financial_history: [] })
      if (path === '/api/daily-bars?ts_code=000002.SZ') return Promise.resolve([])
      if (path === '/api/stocks/000002.SZ/detail') {
        nextDetailCalls += 1
        return nextDetailCalls === 1
          ? nextDetail.promise
          : Promise.reject(new Error('不应出现的重复明细请求'))
      }
      if (path === '/api/us-experiment/instruments?current_only=true&limit=50&offset=0') return Promise.resolve(instrumentPage([]))
      return Promise.resolve(fallback(path))
    })

    render(<App readAdapter={readAdapter} />)
    fireEvent.click(screen.getByRole('button', { name: /A 股数据/ }))
    expect(await screen.findByText('甲股当前状态')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /全局刷新/ }))
    await waitFor(() => expect(nextDetailCalls).toBe(1))
    nextDetail.resolve({ listing: { listStatus: '乙股刷新状态' }, valuation_history: [], financial_history: [] })

    expect(await screen.findByText('乙股刷新状态')).toBeInTheDocument()
    expect(nextDetailCalls).toBe(1)
    expect(screen.queryByText('不应出现的重复明细请求')).not.toBeInTheDocument()
  })

  it('列表请求在 unmount 时 abort，一级导航与既有文案保持兼容', async () => {
    const pendingList = deferred()
    /** @type {AbortSignal[]} */
    const signals = []
    const readAdapter = vi.fn(({ path, signal }) => {
      if (path === '/api/stocks/screen?limit=50&offset=0') { signals.push(signal); return pendingList.promise }
      if (path === '/api/us-experiment/instruments?current_only=true&limit=50&offset=0') return Promise.resolve(instrumentPage([]))
      return Promise.resolve(fallback(path))
    })

    const view = render(<App readAdapter={readAdapter} />)
    expect(screen.getByRole('button', { name: /研究驾驶舱/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /A 股数据/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /美股数据/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /数据与系统/ })).toBeInTheDocument()
    await waitFor(() => expect(signals).toHaveLength(1))

    view.unmount()
    expect(signals[0].aborted).toBe(true)
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
