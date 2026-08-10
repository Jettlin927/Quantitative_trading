import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PersonalTodayView } from './PersonalTodayView.jsx'

afterEach(() => cleanup())

const available = { availability: 'available', value: '1250.0000', as_of: '2026-08-10T04:00:00Z' }

function richToday(status = 'partial') {
  const attention = [{ attention_id: 'a-1', symbol: 'ACME', label: '价格阈值命中', result: 'hit', reason_code: 'threshold_reached' }]
  const factEvents = [{
    event_id: 'event-1', evidence_id: 'evidence-1', title: 'Acme 发布季度更新',
    summary: '来源给出的摘要仍需正文核验。', source: 'investment-news', source_type: 'industry_news', sector: 'semis', content_sha256: 'a'.repeat(64),
    url: 'https://example.com/acme', published_at: '2026-08-10T01:00:00Z', fetched_at: '2026-08-10T02:00:00Z',
    related_symbols: ['ACME', 'AMD'], confirmation_state: 'source_summary_unconfirmed',
  }]
  const states = [
    { symbol: 'ACME', is_holding: true, is_followed: true, candidate_status: 'active', preset_reasons: ['财报观察'], relation_evidence_ids: ['rel-1'], fact_evidence_ids: ['fact-1'] },
    { symbol: 'AMD', is_holding: false, is_followed: true, candidate_status: null, preset_reasons: ['行业映射'], relation_evidence_ids: [], fact_evidence_ids: [] },
  ]
  return {
    trace: null,
    portfolio: {
      portfolio_revision: 8,
      total_equity: available,
      issues: [],
      holdings: [{ holding_id: 'h-acme', symbol: 'ACME', name: 'Acme', state: 'active', market_value: available }],
    },
    attention_items: attention,
    read_model: {
      status, as_of: '2026-08-10T04:00:00Z', period: 'pre_market', attention_items: attention,
      fact_events: factEvents, watch_observations: [states[1]], active_candidates: [states[0]], archived_candidates: [],
      gaps: status === 'success' ? [] : [{ code: 'structured_news_stale', subject: 'structured_news' }],
      field_coverage: '0.67', freshness_seconds: 900,
      portfolio: {
        portfolio_revision: 8, total_equity_availability: 'available', total_equity_value: '1250.0000', total_equity_as_of: '2026-08-10T04:00:00Z',
        active_holding_count: 1, active_holding_symbols: ['ACME'], priced_holding_count: 1, issues: [], equity_snapshot_status: 'available',
        equity_snapshots: [{ market_day: '2026-08-08', total_equity: '1200.0000' }, { market_day: '2026-08-09', total_equity: '1250.0000' }],
      },
    },
  }
}

describe('90 秒今日分诊工作台', () => {
  it('按优先级展示去重事实、状态并存、AI 分栏与真实权益快照', async () => {
    const client = {
      openToday: vi.fn().mockResolvedValue(richToday()),
      openPortfolio: vi.fn().mockResolvedValue(richToday().portfolio),
      listAnalysisCapabilities: vi.fn().mockResolvedValue({ dispatch_enabled: false, reason_code: 'provider_disabled', model: 'deepseek-v4-flash' }),
      listAnalyses: vi.fn().mockResolvedValue([]),
    }
    render(<PersonalTodayView client={client} />)

    expect(await screen.findByRole('heading', { name: '今天先处理什么' })).toBeInTheDocument()
    for (const heading of ['需要处理', '影响持仓的事实变化', '自选观察', 'AI 候选', '数据状态']) expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument()
    expect(screen.getAllByText('Acme 发布季度更新')).toHaveLength(1)
    expect(screen.getByText('■ 来源摘要 · 待核验')).toBeInTheDocument()
    expect(screen.getByText('◇ AI 推断')).toBeInTheDocument()
    expect(screen.getByText(/尚未生成上下文解释/)).toBeInTheDocument()
    expect(screen.getByLabelText('ACME 状态')).toHaveTextContent('持仓')
    expect(screen.getByLabelText('ACME 状态')).toHaveTextContent('自选')
    expect(screen.getByLabelText('ACME 状态')).toHaveTextContent('AI 候选')
    expect(screen.getByRole('img', { name: '最近 2 个真实权益快照波动图' })).toBeInTheDocument()
    expect(screen.getAllByText('部分数据').length).toBeGreaterThan(0)
    expect(screen.getByText(/structured_news_stale/)).toBeInTheDocument()
  })

  it('从事项进入上下文分析，不在今日页常驻通用输入框', async () => {
    const client = {
      openToday: vi.fn().mockResolvedValue(richToday('success')),
      openPortfolio: vi.fn().mockResolvedValue(richToday().portfolio),
      listAnalysisCapabilities: vi.fn().mockResolvedValue({ dispatch_enabled: false, reason_code: 'provider_disabled', model: 'deepseek-v4-flash' }),
      listAnalyses: vi.fn().mockResolvedValue([]),
    }
    render(<PersonalTodayView client={client} />)
    await screen.findByRole('heading', { name: '今天先处理什么' })
    expect(screen.queryByLabelText('分析问题')).not.toBeInTheDocument()

    const actionSection = screen.getByRole('heading', { name: '需要处理' }).closest('section')
    fireEvent.click(within(actionSection).getByRole('button', { name: '深度分析' }))

    expect(await screen.findByLabelText('分析问题')).toHaveValue('ACME 的“价格阈值命中”事项有哪些可核验事实、传导机制和未知项？')
    expect(screen.getByRole('combobox', { name: '分析标的' })).toHaveValue('ACME')
  })

  it.each([
    ['stale', '来源过期'],
    ['unavailable', '来源不可用'],
  ])('明确展示 %s 降级状态', async (status, label) => {
    const client = { openToday: vi.fn().mockResolvedValue(richToday(status)) }
    render(<PersonalTodayView client={client} />)
    expect((await screen.findAllByText(label)).length).toBeGreaterThan(0)
  })

  it('读取失败或未授权时保留明确失败身份', async () => {
    const client = { openToday: vi.fn().mockRejectedValue({ code: 'personal_access_denied', message: '无权读取私有投影' }) }
    render(<PersonalTodayView client={client} />)
    expect(await screen.findByText('来源未授权')).toBeInTheDocument()
    expect(screen.getByText('无权读取私有投影')).toBeInTheDocument()
    expect(screen.queryByText('当前没有必须处理的规则命中或数据缺口')).not.toBeInTheDocument()
    expect(screen.queryByText('当前没有通过结构化来源进入投影的事件')).not.toBeInTheDocument()
  })

  it('空投影与普通失败都有独立身份，不填充伪事实', async () => {
    const emptyClient = { openToday: vi.fn().mockResolvedValue({
      trace: null, portfolio: { portfolio_revision: 0, holdings: [], issues: [] }, attention_items: [],
      read_model: {
        status: 'success', as_of: null, period: null, attention_items: [], fact_events: [], watch_observations: [], active_candidates: [], archived_candidates: [],
        gaps: [], field_coverage: '1', freshness_seconds: 0,
        portfolio: { portfolio_revision: 0, total_equity_availability: 'not_available', total_equity_value: null, total_equity_as_of: null, active_holding_count: 0, active_holding_symbols: [], priced_holding_count: 0, issues: [], equity_snapshot_status: 'available', equity_snapshots: [{ market_day: '2026-08-08', total_equity: '1000' }, { market_day: '2026-08-09', total_equity: '1000' }] },
      },
    }) }
    render(<PersonalTodayView client={emptyClient} />)
    expect(await screen.findByText('当前没有必须处理的规则命中或数据缺口')).toBeInTheDocument()
    expect(screen.getByText('当前没有通过结构化来源进入投影的事件')).toBeInTheDocument()
    expect(screen.getAllByText('正常').length).toBeGreaterThan(0)

    cleanup()
    const failedClient = { openToday: vi.fn().mockRejectedValue({ code: 'personal_request_failed', message: '读取超时' }) }
    render(<PersonalTodayView client={failedClient} />)
    expect(await screen.findByText('今日投影读取失败')).toBeInTheDocument()
    expect(screen.getByText('读取超时')).toBeInTheDocument()
    expect(screen.queryByText('当前没有必须处理的规则命中或数据缺口')).not.toBeInTheDocument()
    expect(screen.queryByText('当前没有通过结构化来源进入投影的事件')).not.toBeInTheDocument()
  })

  it('同一标的连续处理不同事项时重建上下文问题', async () => {
    const today = richToday('success')
    today.read_model.attention_items = [
      ...today.read_model.attention_items,
      { attention_id: 'a-2', symbol: 'ACME', label: '财报数据缺口', result: 'insufficient_data', reason_code: 'filing_missing' },
    ]
    const client = {
      openToday: vi.fn().mockResolvedValue(today),
      listAnalysisCapabilities: vi.fn().mockResolvedValue({ dispatch_enabled: false, reason_code: 'provider_disabled', model: 'deepseek-v4-flash' }),
      listAnalyses: vi.fn().mockResolvedValue([]),
    }
    render(<PersonalTodayView client={client} />)

    const section = (await screen.findByRole('heading', { name: '需要处理' })).closest('section')
    const actions = within(section).getAllByRole('button', { name: '深度分析' })
    fireEvent.click(actions[0])
    expect(await screen.findByLabelText('分析问题')).toHaveValue('ACME 的“价格阈值命中”事项有哪些可核验事实、传导机制和未知项？')
    fireEvent.click(actions[1])
    expect(await screen.findByLabelText('分析问题')).toHaveValue('ACME 的“财报数据缺口”事项有哪些可核验事实、传导机制和未知项？')
  })

  it('权益快照读取失败会进入部分状态并明确显示失败', async () => {
    const today = richToday('partial')
    today.read_model.portfolio.equity_snapshot_status = 'failed'
    today.read_model.portfolio.equity_snapshots = []
    today.read_model.gaps = [{ code: 'equity_snapshots_failed', subject: 'portfolio_equity_history' }]
    const client = { openToday: vi.fn().mockResolvedValue(today) }
    render(<PersonalTodayView client={client} />)

    expect(await screen.findByText('权益快照读取失败，未伪装为空。')).toBeInTheDocument()
    expect(screen.getByText(/equity_snapshots_failed/)).toBeInTheDocument()
    expect(screen.getAllByText('部分数据').length).toBeGreaterThan(0)
  })
})
