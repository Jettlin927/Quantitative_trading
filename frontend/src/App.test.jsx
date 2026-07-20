import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from './main.jsx'

const RESEARCH_ID = '11111111-1111-4111-8111-111111111111'
const PUBLICATION_ID = '22222222-2222-4222-8222-222222222222'
const EVALUATION_ID = '55555555-5555-4555-8555-555555555555'
const PROPOSAL_ID = '77777777-7777-4777-8777-777777777777'

const coreResponses = {
  '/api/health?include_counts=false': {
    status: 'ok', database: 'ok', worker: { status: 'ok', ageSeconds: 3, stale: false }, queue: { status: 'ok', active: 0, queued: 0 },
  },
  '/api/tushare/sync-progress?include_coverage=false': { runs: [] },
  '/api/research/readiness?scope=a_share_cross_section': { level: 'inventory', status: 'inventory_available', blockers: [] },
  '/api/research/readiness?scope=etf_time_series': { level: 'inventory', status: 'inventory_available', blockers: [] },
  '/api/db/overview': { aShare: {} },
  '/api/stocks/screen?limit=50&offset=0': { items: [], total: 0, limit: 50, offset: 0 },
  '/api/indices?limit=1000': [],
  '/api/funds?limit=1000': [],
  '/api/industries?limit=1000': [],
  '/api/us-research/db-overview': { counts: { assets: 1, assetDailyPrices: 1 }, assets: [{ symbol: 'SAMPLE', name: '样例资产', instrumentType: 'sample' }] },
}

const strategySummary = {
  strategy_id: 'momentum-v1', display_name: '横截面动量', lifecycle_status: '活跃', registry_version: '1', code_commit: 'a'.repeat(40),
  formal_research_count: 1, latest_publication_status: 'published', latest_publication_conclusion: '研究通过',
}

const strategyProfile = {
  ...strategySummary,
  economic_thesis: '收益延续可能在有限持有期内存在。', metadata_json: {},
  follow_up_proposals: [{ id: PROPOSAL_ID, title: '扩大市场环境复核', rationale: '补足极端环境证据。', status: 'proposed', created_at: '2026-07-20T00:45:00Z' }],
  formal_researches: [{ id: RESEARCH_ID, plan_id: '33333333-3333-4333-8333-333333333333', origin: 'native', phase: 'published', run_count: 1, latest_publication_status: 'published', latest_publication_conclusion: '研究通过' }],
}

const researchDetail = {
  id: RESEARCH_ID, origin: 'native', phase: 'published', created_at: '2026-07-20T00:00:00Z', completed_at: '2026-07-20T01:00:00Z',
  plan: { id: '33333333-3333-4333-8333-333333333333', strategy_id: 'momentum-v1', issue_number: 37, version: 1, schema_version: '1', plan_sha256: 'b'.repeat(64), code_commit: 'a'.repeat(40), plan_json: {} },
  approval: { action: 'approved', actor_login: 'Jettlin927', created_at: '2026-07-20T00:01:00Z' },
  runs: [
    { run_id: 'run-001', status: 'succeeded', stage: 'completed', result_fingerprint: 'c'.repeat(64), finished_at: '2026-07-20T00:30:00Z', error: null },
    { run_id: 'run-000', status: 'failed', stage: 'simulation', result_fingerprint: null, finished_at: '2026-07-20T00:20:00Z', error: '冻结输入缺失' },
  ],
  events: [{ id: '44444444-4444-4444-8444-444444444444', sequence_no: 1, event_type: 'run_succeeded', payload_json: { summary: '执行完成' }, occurred_at: '2026-07-20T00:30:00Z' }],
  evaluations: [{ id: EVALUATION_ID, version: 1, conclusion: '研究通过', supporting_evidence: [{ statement: 'OOS 净收益通过' }], opposing_evidence: [], missing_evidence: [], limitations: [{ statement: '仅覆盖既定区间' }], follow_up_recommendations: [] }],
  publications: [{ id: PUBLICATION_ID, evaluation_id: EVALUATION_ID, version: 1, status: 'published', created_at: '2026-07-20T00:40:00Z', published_at: '2026-07-20T00:41:00Z' }],
  follow_up_proposals: [{ id: PROPOSAL_ID, title: '扩大市场环境复核', rationale: '补足极端环境证据。', status: 'proposed', created_at: '2026-07-20T00:45:00Z' }],
}

function ok(data) {
  return Promise.resolve(new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } }))
}

/**
 * @param {{
 *   failStrategies?: boolean,
 *   detail?: Record<string, unknown>,
 *   coreOverrides?: Record<string, unknown>,
 *   route?: (path: string) => Promise<Response> | null,
 * }} options
 */
function installFetch(options = {}) {
  const { failStrategies = false, detail = researchDetail, coreOverrides = {}, route } = options
  globalThis.fetch = vi.fn((input) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, '')
    const routed = route?.(path)
    if (routed) return routed
    if (path === '/api/research/strategies') {
      if (failStrategies) return Promise.resolve(new Response(JSON.stringify({ detail: '研究表尚不可用' }), { status: 503, headers: { 'Content-Type': 'application/json' } }))
      return ok([strategySummary])
    }
    if (path === '/api/research/strategies/momentum-v1') return ok(strategyProfile)
    if (path === `/api/research/formal-researches/${RESEARCH_ID}`) return ok(detail)
    if (path === `/api/research/publications/${PUBLICATION_ID}`) {
      return ok({ status: 'published', conclusion: '研究通过', evaluation_id: EVALUATION_ID, evaluation_version: 1, published_at: '2026-07-20T00:41:00Z', report_url: '/api/research/evaluations/report' })
    }
    return ok(coreOverrides[path] ?? coreResponses[path] ?? {})
  })
}

function deferred() {
  /** @type {(response: Response) => void} */
  let resolve = () => {}
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

describe('研究驾驶舱', () => {
  beforeEach(() => installFetch())
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('提供四个一级区域并显式标注美股功能债与 SAMPLE 边界', async () => {
    render(<App />)
    expect(screen.getByRole('button', { name: /研究驾驶舱/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /A 股数据/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /美股数据/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /系统运维/ })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /美股数据/ }))
    expect(screen.getByRole('heading', { name: '美股研究级实际数据尚未接入' })).toBeInTheDocument()
    expect(screen.getByText('仅样例')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /导入/ })).not.toBeInTheDocument()
  })

  it('分别展示运行成功事实、五态研究结论与原始 HTML 证据入口', async () => {
    render(<App />)
    await screen.findByRole('heading', { name: '横截面动量' })
    await screen.findByText('执行成功')

    expect(screen.getByRole('heading', { name: '运行事实' })).toBeInTheDocument()
    expect(screen.getAllByText('研究通过').length).toBeGreaterThan(0)
    expect(screen.getByText('冻结输入缺失')).toBeInTheDocument()
    expect(screen.getByText('扩大市场环境复核')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /打开原始 HTML 证据/ })).toHaveAttribute('href', '/api/research/evaluations/report')
  })

  it('已发布结论只绑定同一 evaluation_id 的证据，不拼接待发布新版本', async () => {
    const nextEvaluation = {
      ...researchDetail.evaluations[0],
      id: '66666666-6666-4666-8666-666666666666',
      version: 2,
      conclusion: '不通过',
      supporting_evidence: [{ statement: '待发布 v2 证据，不应展示' }],
    }
    installFetch({ detail: { ...researchDetail, evaluations: [...researchDetail.evaluations, nextEvaluation] } })
    render(<App />)

    await screen.findByText('OOS 净收益通过')
    expect(screen.queryByText('待发布 v2 证据，不应展示')).not.toBeInTheDocument()
    expect(screen.getAllByText('研究通过').length).toBeGreaterThan(0)
  })

  it('研究 API 失败时保留其他只读区域可访问', async () => {
    installFetch({ failStrategies: true })
    render(<App />)
    await screen.findByText('研究表尚不可用')
    expect(screen.getByText('其他只读区域仍可继续浏览。')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /系统运维/ }))
    await waitFor(() => expect(screen.getByRole('heading', { name: '系统运维', level: 2 })).toBeInTheDocument())
    expect(screen.getByText('无写入控制')).toBeInTheDocument()
  })

  it('切换股票时清空旧事实，并忽略较晚返回的旧股票响应', async () => {
    const oldBars = deferred()
    const oldDetail = deferred()
    const stockPage = {
      items: [
        { ts_code: '000001.SZ', symbol: '000001', name: '甲公司', close: 10, pct_chg: 1 },
        { ts_code: '000002.SZ', symbol: '000002', name: '乙公司', close: 20, pct_chg: 2 },
      ],
      total: 2,
      limit: 50,
      offset: 0,
    }
    installFetch({
      coreOverrides: { '/api/stocks/screen?limit=50&offset=0': stockPage },
      route: (path) => {
        if (path === '/api/daily-bars?ts_code=000001.SZ') return oldBars.promise
        if (path === '/api/stocks/000001.SZ/detail') return oldDetail.promise
        if (path === '/api/daily-bars?ts_code=000002.SZ') return ok([])
        if (path === '/api/stocks/000002.SZ/detail') return ok({ listing: { listStatus: '乙股状态' }, valuation_history: [], financial_history: [] })
        return null
      },
    })
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: /A 股数据/ }))
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledWith('/api/stocks/000001.SZ/detail', expect.anything()))

    fireEvent.click(await screen.findByRole('button', { name: /000002.*乙公司/ }))
    await screen.findByText('乙股状态')
    expect(screen.getByText('暂无可绘制的日线数据')).toBeInTheDocument()

    oldBars.resolve(new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    oldDetail.resolve(new Response(JSON.stringify({ listing: { listStatus: '甲股旧状态' }, valuation_history: [], financial_history: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await waitFor(() => expect(screen.queryByText('甲股旧状态')).not.toBeInTheDocument())
    expect(screen.getByText('乙股状态')).toBeInTheDocument()

    const fetchMock = vi.mocked(globalThis.fetch)
    const detailCallsBeforeRefresh = fetchMock.mock.calls.filter(([path]) => path === '/api/stocks/000002.SZ/detail').length
    fireEvent.click(screen.getByRole('button', { name: /全局刷新/ }))
    await waitFor(() => expect(fetchMock.mock.calls.filter(([path]) => path === '/api/stocks/000002.SZ/detail').length).toBeGreaterThan(detailCallsBeforeRefresh))
  })

  it('指数、ETF 与行业目录可读取历史和当前成员，单域失败不阻断页面', async () => {
    const indices = [{ tsCode: '000001.SH', name: '上证指数', category: '综合指数' }]
    const funds = [{ tsCode: '510300.SH', name: '沪深 300 ETF', fundType: '股票型' }]
    const industries = [{ indexCode: '801081.SI', industryName: '半导体', level: 'L2' }]
    let failIndustry = false
    installFetch({
      coreOverrides: {
        '/api/indices?limit=1000': indices,
        '/api/funds?limit=1000': funds,
        '/api/industries?limit=1000': industries,
        '/api/tushare/sync-progress?include_coverage=false': { runs: [{ id: 9, target: 'fund_daily', status: 'partial', message: '2 个标的失败', createdAt: '2026-07-20T00:00:00Z' }] },
      },
      route: (path) => {
        if (path.startsWith('/api/indices/000001.SH/daily-bars?')) return ok([{ tradeDate: '2026-07-18', close: 3210.12, pctChg: 0.5, amount: 100000 }])
        if (path.startsWith('/api/funds/510300.SH/daily-bars?')) return ok([{ tradeDate: '2026-07-18', close: 4.56, pctChg: -0.4, amount: 80000 }])
        if (path.startsWith('/api/funds/510300.SH/adjust-factors?')) return ok([{ tradeDate: '2026-07-18', adjFactor: 1.23 }])
        if (path.startsWith('/api/industries/801081.SI/members?')) {
          if (failIndustry) return Promise.resolve(new Response(JSON.stringify({ detail: '行业成员暂不可用' }), { status: 503, headers: { 'Content-Type': 'application/json' } }))
          return ok([{ conCode: '600703.SH', conName: '三安光电', inDate: '2020-01-01', outDate: null, isNew: true }])
        }
        return null
      },
    })
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: /A 股数据/ }))
    await screen.findByText('3,210.12')
    expect(screen.getByText('2 个标的失败')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /沪深 300 ETF.*510300.SH/ }))
    await screen.findByText('1.23')
    fireEvent.click(screen.getByRole('button', { name: /半导体.*801081.SI/ }))
    await screen.findByText('600703.SH')

    failIndustry = true
    fireEvent.click(screen.getByRole('button', { name: /上证指数.*000001.SH/ }))
    await screen.findByText('3,210.12')
    fireEvent.click(screen.getByRole('button', { name: /半导体.*801081.SI/ }))
    await screen.findByText('行业成员暂不可用')
    expect(screen.getByRole('heading', { name: 'A 股实际市场数据' })).toBeInTheDocument()
  })
})
