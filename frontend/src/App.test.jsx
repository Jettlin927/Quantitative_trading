import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from './main.jsx'

vi.mock('lightweight-charts', () => ({
  CandlestickSeries: 'candlestick',
  HistogramSeries: 'histogram',
  LineSeries: 'line',
  createChart: vi.fn(() => {
    const timeScale = {
      fitContent: vi.fn(),
      setVisibleLogicalRange: vi.fn(),
      subscribeVisibleLogicalRangeChange: vi.fn(),
    }
    return {
      addSeries: vi.fn(() => ({ setData: vi.fn() })),
      applyOptions: vi.fn(),
      remove: vi.fn(),
      timeScale: () => timeScale,
    }
  }),
}))

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
  events: [
    { id: '44444444-4444-4444-8444-444444444441', sequence_no: 1, event_type: 'research_queued', payload_json: { workItemId: 'work-001', maxAttempts: 3, resourceBudget: { wallClockSeconds: 3600, cpuCores: '1.0', memoryMiB: 1024, artifactMiB: 2048, maxRetries: 0 } }, occurred_at: '2026-07-20T00:10:00Z' },
    { id: '44444444-4444-4444-8444-444444444442', sequence_no: 2, event_type: 'research_run_succeeded', payload_json: { runId: 'run-001', artifactRoot: '/artifacts/run-001' }, occurred_at: '2026-07-20T00:30:00Z' },
    { id: '44444444-4444-4444-8444-444444444443', sequence_no: 3, event_type: 'research_publication_failed', payload_json: { publicationId: PUBLICATION_ID, error: '读回失败', retryable: true, publicationStatus: 'published' }, occurred_at: '2026-07-20T00:35:00Z' },
  ],
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
    expect(screen.getByText('组合模拟')).toBeInTheDocument()
    expect(screen.getByText('研究运行完成')).toBeInTheDocument()
    expect(screen.getByText(/运行：run-001；工件目录：\/artifacts\/run-001/)).toBeInTheDocument()
    expect(screen.getByText(/工作项：work-001.*最长运行秒数=3600.*CPU 核数=1.0.*内存上限 MiB=1024.*工件上限 MiB=2048.*最大重试次数=0/)).toBeInTheDocument()
    expect(screen.getByText(/发布状态：已发布/)).toBeInTheDocument()
    expect(screen.getByText('扩大市场环境复核')).toBeInTheDocument()
    expect(screen.getAllByText('已提议').length).toBeGreaterThan(0)
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

  it('全局刷新会重读策略、研究与发布投影，并在全部成功后更新时间', async () => {
    render(<App />)
    await screen.findByRole('link', { name: /打开原始 HTML 证据/ })
    const fetchMock = vi.mocked(globalThis.fetch)
    const before = {
      profile: fetchMock.mock.calls.filter(([path]) => path === '/api/research/strategies/momentum-v1').length,
      detail: fetchMock.mock.calls.filter(([path]) => path === `/api/research/formal-researches/${RESEARCH_ID}`).length,
      publication: fetchMock.mock.calls.filter(([path]) => path === `/api/research/publications/${PUBLICATION_ID}`).length,
    }

    fireEvent.click(screen.getByRole('button', { name: /全局刷新/ }))
    await waitFor(() => expect(document.querySelector('.updated-at')).not.toHaveTextContent('尚未刷新'))
    expect(fetchMock.mock.calls.filter(([path]) => path === '/api/research/strategies/momentum-v1').length).toBeGreaterThan(before.profile)
    expect(fetchMock.mock.calls.filter(([path]) => path === `/api/research/formal-researches/${RESEARCH_ID}`).length).toBeGreaterThan(before.detail)
    expect(fetchMock.mock.calls.filter(([path]) => path === `/api/research/publications/${PUBLICATION_ID}`).length).toBeGreaterThan(before.publication)
  })

  it('发布投影刷新失败时保留一致旧事实且不伪造刷新时间', async () => {
    let failPublication = false
    installFetch({
      route: (path) => {
        if (failPublication && path === `/api/research/publications/${PUBLICATION_ID}`) {
          return Promise.resolve(new Response(JSON.stringify({ detail: '发布投影暂不可用' }), { status: 503, headers: { 'Content-Type': 'application/json' } }))
        }
        return null
      },
    })
    render(<App />)
    await screen.findByRole('link', { name: /打开原始 HTML 证据/ })
    failPublication = true

    fireEvent.click(screen.getByRole('button', { name: /全局刷新/ }))
    await screen.findByText('发布投影暂不可用')
    expect(document.querySelector('.updated-at')).toHaveTextContent('尚未刷新')
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
    const refreshBars = deferred()
    const refreshDetail = deferred()
    let delayStockRefresh = false
    let oldStockResolved = false
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
        if (path === '/api/daily-bars?ts_code=000001.SZ') return oldStockResolved ? ok([]) : oldBars.promise
        if (path === '/api/stocks/000001.SZ/detail') return oldStockResolved ? ok({ listing: { listStatus: '甲股当前状态' }, valuation_history: [], financial_history: [] }) : oldDetail.promise
        if (path === '/api/daily-bars?ts_code=000002.SZ') return delayStockRefresh ? refreshBars.promise : ok([])
        if (path === '/api/stocks/000002.SZ/detail') return delayStockRefresh ? refreshDetail.promise : ok({ listing: { listStatus: '乙股状态' }, valuation_history: [], financial_history: [] })
        return null
      },
    })
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: /A 股数据/ }))
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledWith('/api/stocks/000001.SZ/detail', expect.anything()))

    fireEvent.click(await screen.findByRole('button', { name: /000002.*乙公司/ }))
    await screen.findByText('乙股状态')
    expect(screen.getByText('暂无可绘制的日线数据')).toBeInTheDocument()

    oldStockResolved = true
    oldBars.resolve(new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    oldDetail.resolve(new Response(JSON.stringify({ listing: { listStatus: '甲股旧状态' }, valuation_history: [], financial_history: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await waitFor(() => expect(screen.queryByText('甲股旧状态')).not.toBeInTheDocument())
    expect(screen.getByText('乙股状态')).toBeInTheDocument()

    const fetchMock = vi.mocked(globalThis.fetch)
    const detailCallsBeforeRefresh = fetchMock.mock.calls.filter(([path]) => path === '/api/stocks/000002.SZ/detail').length
    delayStockRefresh = true
    fireEvent.click(screen.getByRole('button', { name: /全局刷新/ }))
    await waitFor(() => expect(fetchMock.mock.calls.filter(([path]) => path === '/api/stocks/000002.SZ/detail').length).toBeGreaterThan(detailCallsBeforeRefresh))
    fireEvent.click(screen.getByRole('button', { name: /000001.*甲公司/ }))
    await screen.findByText('甲股当前状态')

    refreshBars.resolve(new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    refreshDetail.resolve(new Response(JSON.stringify({ listing: { listStatus: '乙股刷新旧状态' }, valuation_history: [], financial_history: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await waitFor(() => expect(screen.queryByText('乙股刷新旧状态')).not.toBeInTheDocument())
    expect(screen.getByText('甲股当前状态')).toBeInTheDocument()
  })

  it('连续搜索只接受最新查询，空结果会结束旧明细加载', async () => {
    const firstSearch = deferred()
    const secondSearch = deferred()
    const initialDetail = deferred()
    const globalHealth = deferred()
    let delayGlobalHealth = false
    const initialPage = {
      items: [{ ts_code: '000001.SZ', symbol: '000001', name: '初始股票', close: 10, pct_chg: 0 }],
      total: 1,
      limit: 50,
      offset: 0,
    }
    installFetch({
      coreOverrides: { '/api/stocks/screen?limit=50&offset=0': initialPage },
      route: (path) => {
        if (path === '/api/health?include_counts=false' && delayGlobalHealth) return globalHealth.promise
        if (path === '/api/stocks/screen?limit=50&offset=0&q=first') return firstSearch.promise
        if (path === '/api/stocks/screen?limit=50&offset=0&q=second') return secondSearch.promise
        if (path === '/api/stocks/screen?limit=50&offset=0&q=failure') {
          return Promise.resolve(new Response(JSON.stringify({ detail: '股票列表暂不可用' }), { status: 503, headers: { 'Content-Type': 'application/json' } }))
        }
        if (path === '/api/stocks/screen?limit=50&offset=0&q=empty') return ok({ items: [], total: 0, limit: 50, offset: 0 })
        if (path === '/api/daily-bars?ts_code=000001.SZ') return initialDetail.promise
        if (path === '/api/stocks/000001.SZ/detail') return initialDetail.promise
        if (path === '/api/daily-bars?ts_code=000003.SZ') return ok([])
        if (path === '/api/stocks/000003.SZ/detail') return ok({ listing: { listStatus: '最新查询状态' }, valuation_history: [], financial_history: [] })
        return null
      },
    })
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: /A 股数据/ }))
    await screen.findByRole('button', { name: /000001.*初始股票/ })
    const input = screen.getByPlaceholderText('代码 / 名称 / 拼音')

    fireEvent.change(input, { target: { value: 'first' } })
    fireEvent.click(screen.getByRole('button', { name: '查询' }))
    fireEvent.change(input, { target: { value: 'second' } })
    fireEvent.click(screen.getByRole('button', { name: '查询' }))
    secondSearch.resolve(new Response(JSON.stringify({ items: [{ ts_code: '000003.SZ', symbol: '000003', name: '最新股票', close: 30, pct_chg: 3 }], total: 1, limit: 50, offset: 0 }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await screen.findByText('最新查询状态')

    firstSearch.resolve(new Response(JSON.stringify({ items: [{ ts_code: '000002.SZ', symbol: '000002', name: '过期股票', close: 20, pct_chg: 2 }], total: 1, limit: 50, offset: 0 }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await waitFor(() => expect(screen.queryByText('过期股票')).not.toBeInTheDocument())
    expect(screen.getByRole('heading', { name: '最新股票' })).toBeInTheDocument()

    delayGlobalHealth = true
    const refreshButton = screen.getByRole('button', { name: /全局刷新/ })
    fireEvent.click(refreshButton)
    expect(refreshButton).toBeDisabled()

    fireEvent.change(input, { target: { value: 'failure' } })
    fireEvent.click(screen.getByRole('button', { name: '查询' }))
    await screen.findByText('股票列表暂不可用')
    expect(refreshButton).toBeDisabled()

    globalHealth.resolve(new Response(JSON.stringify(coreResponses['/api/health?include_counts=false']), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await waitFor(() => expect(refreshButton).toBeEnabled())
    expect(screen.getByText('股票列表暂不可用')).toBeInTheDocument()

    fireEvent.change(input, { target: { value: 'empty' } })
    fireEvent.click(screen.getByRole('button', { name: '查询' }))
    await screen.findByText('没有匹配的股票')
    expect(document.querySelector('.security-meta')).not.toHaveTextContent('加载中')
    expect(screen.getByText('未选择股票')).toBeInTheDocument()
  })

  it('为图形行情提供中文摘要与可展开的数据表', async () => {
    const stockPage = {
      items: [{ ts_code: '000001.SZ', symbol: '000001', name: '甲公司', close: 11, pct_chg: 1 }],
      total: 1,
      limit: 50,
      offset: 0,
    }
    const bars = [
      { trade_date: '2024-01-02', open: 8, high: 9, low: 7, close: 8.5, vol: 800, amount: 1600 },
      { trade_date: '2025-07-17', open: 9, high: 10, low: 8, close: 9.5, vol: 900, amount: 1800 },
      { trade_date: '2025-07-18', open: 9.5, high: 10.5, low: 9, close: 10, vol: 950, amount: 1900 },
      { trade_date: '2026-07-17', open: 10, high: 11, low: 9, close: 10.5, vol: 1000, amount: 2000 },
      { trade_date: '2026-07-18', open: 10.5, high: 12, low: 10, close: 11, vol: 1200, amount: 2400 },
    ]
    installFetch({
      coreOverrides: { '/api/stocks/screen?limit=50&offset=0': stockPage },
      route: (path) => {
        if (path === '/api/daily-bars?ts_code=000001.SZ') return ok(bars)
        if (path === '/api/stocks/000001.SZ/detail') return ok({ listing: {}, valuation_history: [], financial_history: [] })
        return null
      },
    })
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: /A 股数据/ }))

    await screen.findByRole('img', { name: /价格图。近 180 日：日 K 线共 5 个交易日/ })
    expect(screen.getByRole('button', { name: '近 180 日' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText(/当前只读 API 尚未提供按日期与标的定位的局部缺口质量结果/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('查看近 180 日行情数据表（5 条）'))
    expect(screen.getByRole('table', { name: /近 180 日：日 K 线共 5 个交易日/ })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '近 1 年' }))
    expect(screen.getByRole('button', { name: '近 1 年' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('img', { name: /价格图。近 1 年：日 K 线共 3 个交易日/ })).toBeInTheDocument()
    expect(screen.getByRole('table', { name: /近 1 年：日 K 线共 3 个交易日/ })).toBeInTheDocument()
    expect(screen.queryByText('2025-07-17')).not.toBeInTheDocument()
    expect(screen.getByText('2025-07-18')).toBeInTheDocument()
  })

  it('指数、ETF 与行业目录可读取历史和当前成员，单域失败不阻断页面', async () => {
    const indices = [{ tsCode: '000001.SH', name: '上证指数', category: '综合指数' }]
    const funds = [{ tsCode: '510300.SH', name: '沪深 300 ETF', fundType: '股票型' }]
    const industries = [{ indexCode: '801081.SI', industryName: '半导体', level: 'L2' }]
    let failIndustry = false
    let delayFundRefresh = false
    const fundBars = deferred()
    const fundAdjustments = deferred()
    installFetch({
      coreOverrides: {
        '/api/indices?limit=1000': indices,
        '/api/funds?limit=1000': funds,
        '/api/industries?limit=1000': industries,
        '/api/tushare/sync-progress?include_coverage=false': { runs: [{ id: 9, target: 'fund_daily', status: 'partial', message: '2 个标的失败', createdAt: '2026-07-20T00:00:00Z' }] },
      },
      route: (path) => {
        if (path.startsWith('/api/indices/000001.SH/daily-bars?')) return ok([{ tradeDate: '2026-07-18', close: 3210.12, pctChg: 0.5, amount: 100000 }])
        if (path.startsWith('/api/funds/510300.SH/daily-bars?')) return delayFundRefresh ? fundBars.promise : ok([{ tradeDate: '2026-07-18', close: 4.56, pctChg: -0.4, amount: 80000 }])
        if (path.startsWith('/api/funds/510300.SH/adjust-factors?')) return delayFundRefresh ? fundAdjustments.promise : ok([{ tradeDate: '2026-07-18', adjFactor: 1.23 }])
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
    const fetchMock = vi.mocked(globalThis.fetch)
    const fundCallsBeforeRefresh = fetchMock.mock.calls.filter(([path]) => String(path).startsWith('/api/funds/510300.SH/daily-bars?')).length
    delayFundRefresh = true
    fireEvent.click(screen.getByRole('button', { name: /全局刷新/ }))
    await waitFor(() => expect(fetchMock.mock.calls.filter(([path]) => String(path).startsWith('/api/funds/510300.SH/daily-bars?')).length).toBeGreaterThan(fundCallsBeforeRefresh))
    fireEvent.click(screen.getByRole('button', { name: /半导体.*801081.SI/ }))
    await screen.findByText('600703.SH')
    fundBars.resolve(new Response(JSON.stringify([{ tradeDate: '2026-07-18', close: 9.99, pctChg: 9.9, amount: 99999 }]), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    fundAdjustments.resolve(new Response(JSON.stringify([{ tradeDate: '2026-07-18', adjFactor: 9.99 }]), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await waitFor(() => expect(screen.queryByText('9.99')).not.toBeInTheDocument())
    expect(screen.getByText('600703.SH')).toBeInTheDocument()

    failIndustry = true
    fireEvent.click(screen.getByRole('button', { name: /上证指数.*000001.SH/ }))
    await screen.findByText('3,210.12')
    fireEvent.click(screen.getByRole('button', { name: /半导体.*801081.SI/ }))
    await screen.findByText('行业成员暂不可用')
    expect(screen.getByRole('heading', { name: 'A 股实际市场数据' })).toBeInTheDocument()
  })
})
