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

afterEach(() => cleanup())

const trace = {
  synthetic: true,
  research_eligible: false,
  analysis_id: 'analysis-001',
  holding: { symbol: 'SYNTH-001', name: '合成边界测试标的', quantity: '12.5000', average_cost: '80.0000', currency: 'USD' },
  market: {
    source_health: 'unavailable',
    as_of: '2026-08-01T20:00:00Z',
    bars: [
      { date: '2026-07-30', open: '78.0000', high: '81.0000', low: '77.5000', close: '80.0000', volume: '1000' },
      { date: '2026-07-31', open: '80.0000', high: '82.5000', low: '79.5000', close: '82.0000', volume: '1200' },
    ],
  },
  rule_evaluations: [
    { rule_id: '1', label: '合成条件命中', result: 'hit', reason: '达到阈值' },
    { rule_id: '2', label: '合成条件未命中', result: 'not_hit', reason: '未达到阈值' },
    { rule_id: '3', label: '合成数据不足', result: 'insufficient_data', reason: '窗口不足' },
    { rule_id: '4', label: '合成计算失败', result: 'calculation_failed', reason: '分母为零' },
  ],
  analysis_preview: {
    status: 'ready', provider: 'synthetic-model', model: 'scripted-deny-v1',
    included_fields: ['user_symbol', 'user_question'],
    excluded_fields: [{ field: 'market_prices', reason_code: 'source_ai_context_denied' }],
    preview_sha256: 'a'.repeat(64), retention: '合成路径；不使用真实 provider，也不外发',
  },
  analysis_claim: { claim_id: 'claim-001', kind: 'inference', statement: '合成证据只支持条件性影响机制。', evidence_ids: ['evidence-001'] },
  issues: ['provider_unavailable'],
}

function noOpReadAdapter({ path }) {
  if (path.includes('screen')) return Promise.resolve({ items: [], total: 0, limit: 50, offset: 0 })
  if (path.includes('/instruments?')) return Promise.resolve({ items: [], total: 0, limit: 50, offset: 0 })
  if (path.includes('strategies') || path.includes('/indices') || path.includes('/funds') || path.includes('/industries')) return Promise.resolve([])
  return Promise.resolve({})
}

describe('个人美股 synthetic tracer', () => {
  it('标准 URL 七区路由以 /today 为根，A 股留在市场次级入口', () => {
    const personalClient = { openToday: vi.fn(() => Promise.resolve({ trace, record: null })) }
    render(<App readAdapter={noOpReadAdapter} personalClient={personalClient} />)

    for (const name of ['今日工作台', '我的持仓', '市场与标的', '规则与策略', '研究驾驶舱', '研究记录', '数据与系统']) {
      expect(screen.getByRole('button', { name: new RegExp(name) })).toBeInTheDocument()
    }
    expect(screen.getByRole('button', { name: /A 股数据/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /A 股数据/ }))
    expect(screen.getByRole('button', { name: /市场与标的/ })).toHaveAttribute('aria-current', 'page')
  })

  it('个人首页不加载其他工作区的全局数据', async () => {
    const readAdapter = vi.fn(noOpReadAdapter)
    const personalClient = { openToday: vi.fn(() => Promise.resolve({ trace, record: null })) }

    render(<App initialPath="/today" readAdapter={readAdapter} personalClient={personalClient} />)

    await waitFor(() => expect(readAdapter).toHaveBeenCalledWith({ path: '/api/health?include_counts=false' }))
    expect(readAdapter.mock.calls.map(([request]) => request.path)).toEqual([
      '/api/health?include_counts=false',
    ])
  })

  it('K 线先于证据预览，四态不用颜色区分，provider unavailable 不阻断显式保存', async () => {
    const personalClient = {
      openToday: vi.fn(() => Promise.resolve({ trace, record: null })),
      saveSyntheticRecord: vi.fn(() => Promise.resolve({ record_id: 'record-001', version: 1, status: 'saved', synthetic: true })),
    }
    const chartAdapter = { create: vi.fn(() => ({ setData: vi.fn(), setRange: vi.fn(), resize: vi.fn(), dispose: vi.fn() })) }
    render(<App initialPath="/today" readAdapter={noOpReadAdapter} personalClient={personalClient} chartAdapter={chartAdapter} />)

    expect(await screen.findByText('SYNTH-001')).toBeInTheDocument()
    expect(screen.getAllByText('Provider 不可用').length).toBeGreaterThan(0)
    expect(screen.getByText('推断 ◇')).toBeInTheDocument()
    expect(screen.getByText('合成证据只支持条件性影响机制。')).toBeInTheDocument()
    expect(screen.getByText('证据身份：evidence-001')).toBeInTheDocument()
    for (const label of ['命中 ◆', '未命中 ○', '数据不足 △', '计算失败 ×']) expect(screen.getByText(label)).toBeInTheDocument()
    const chart = screen.getByRole('img', { name: /价格图/ })
    const preview = screen.getByRole('heading', { name: 'AI 外发排除预览' })
    expect(chart.compareDocumentPosition(preview) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Provider 恢复后可追问' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: '确认并保存合成记录' }))
    await waitFor(() => expect(personalClient.saveSyntheticRecord).toHaveBeenCalledWith(expect.objectContaining({
      analysisId: 'analysis-001', previewSha256: 'a'.repeat(64),
    })))
    expect(await screen.findByText('合成记录 v1 已保存')).toBeInTheDocument()
  })
})
