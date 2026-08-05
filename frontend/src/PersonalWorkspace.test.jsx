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
  it('没有合成 trace 时仍以真实组合和注意事项组成今日入口', async () => {
    const available = {
      availability: 'available', value: '241.0000', reason_code: null, source_health: 'fresh',
      as_of: '2026-08-03T20:00:00Z', source_ids: ['alpaca-acme'], feed: 'delayed_sip', delay_seconds: 900,
    }
    const unavailable = {
      availability: 'not_available', value: null, reason_code: 'provider_unavailable', source_health: 'unavailable',
      as_of: null, source_ids: [], feed: null, delay_seconds: null,
    }
    const portfolio = {
      portfolio_revision: 7, usd_cash: '100.0000', total_equity: unavailable,
      total_market_value: unavailable, issues: ['provider_unavailable'],
      holdings: [
        { holding_id: 'holding-acme', symbol: 'ACME', name: 'Acme', state: 'active', market_value: available },
        { holding_id: 'holding-beta', symbol: 'BETA', name: 'Beta', state: 'active', market_value: unavailable },
      ],
    }
    const personalClient = {
      openToday: vi.fn(() => Promise.resolve({
        trace: null,
        portfolio,
        attention_items: [{ attention_id: 'attention-1', symbol: 'BETA', label: '行情待恢复', result: 'insufficient_data', reason_code: 'provider_unavailable' }],
      })),
      openPortfolio: vi.fn(() => Promise.resolve(portfolio)),
    }

    render(<App initialPath="/today" readAdapter={noOpReadAdapter} personalClient={personalClient} />)

    expect(await screen.findByRole('heading', { name: '今日工作台' })).toBeInTheDocument()
    expect(screen.getByText('今天先看组合、数据缺口与待验证事项')).toBeInTheDocument()
    expect(screen.getByText('2 个活跃持仓')).toBeInTheDocument()
    expect(screen.getByText('行情覆盖 1/2')).toBeInTheDocument()
    expect(screen.getByText('BETA · 行情待恢复')).toBeInTheDocument()
    expect(screen.queryByText('合成信任纵切尚未创建')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '查看全部持仓' }))
    expect(await screen.findByRole('heading', { name: '手工美股持仓' })).toBeInTheDocument()
  })

  it('标准 URL 六区路由以 /today 为根，只保留美股市场入口', () => {
    const personalClient = { openToday: vi.fn(() => Promise.resolve({ trace })) }
    render(<App readAdapter={noOpReadAdapter} personalClient={personalClient} />)

    for (const name of ['今日工作台', '我的持仓', '市场与标的', '规则与策略', '研究驾驶舱', '数据与系统']) {
      expect(screen.getByRole('button', { name: new RegExp(name) })).toBeInTheDocument()
    }
    expect(screen.queryByRole('button', { name: /研究记录|A 股数据|美股数据/ })).not.toBeInTheDocument()
  })

  it('个人首页不加载其他工作区的全局数据', async () => {
    const readAdapter = vi.fn(noOpReadAdapter)
    const personalClient = { openToday: vi.fn(() => Promise.resolve({ trace })) }

    render(<App initialPath="/today" readAdapter={readAdapter} personalClient={personalClient} />)

    await waitFor(() => expect(readAdapter).toHaveBeenCalledWith({ path: '/api/health?include_counts=false' }))
    expect(readAdapter.mock.calls.map(([request]) => request.path)).toEqual([
      '/api/health?include_counts=false',
    ])
  })

  it('K 线先于证据预览，四态不用颜色区分，provider unavailable 不恢复记录保存', async () => {
    const personalClient = {
      openToday: vi.fn(() => Promise.resolve({ trace })),
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

    expect(screen.queryByRole('button', { name: /保存.*记录/ })).not.toBeInTheDocument()
  })
})
