import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from './main.jsx'

const RESEARCH_ID = '11111111-1111-4111-8111-111111111111'
const PUBLICATION_ID = '22222222-2222-4222-8222-222222222222'

const coreResponses = {
  '/api/health?include_counts=false': {
    status: 'ok', database: 'ok', worker: { status: 'ok', ageSeconds: 3, stale: false }, queue: { status: 'ok', active: 0, queued: 0 },
  },
  '/api/tushare/sync-progress?include_coverage=false': { runs: [] },
  '/api/research/readiness?scope=a_share_cross_section': { level: 'inventory', status: 'inventory_available', blockers: [] },
  '/api/research/readiness?scope=etf_time_series': { level: 'inventory', status: 'inventory_available', blockers: [] },
  '/api/db/overview': { aShare: {} },
  '/api/stocks/screen?limit=50&offset=0': { items: [], total: 0, limit: 50, offset: 0 },
  '/api/indices?limit=80': [],
  '/api/funds?limit=80': [],
  '/api/industries?limit=80': [],
  '/api/us-research/db-overview': { counts: { assets: 1, assetDailyPrices: 1 }, assets: [{ symbol: 'SAMPLE', name: '样例资产', instrumentType: 'sample' }] },
}

const strategySummary = {
  strategy_id: 'momentum-v1', display_name: '横截面动量', lifecycle_status: '活跃', registry_version: '1', code_commit: 'a'.repeat(40),
  formal_research_count: 1, latest_publication_status: 'published', latest_publication_conclusion: '研究通过',
}

const strategyProfile = {
  ...strategySummary,
  economic_thesis: '收益延续可能在有限持有期内存在。', metadata_json: {}, follow_up_proposals: [],
  formal_researches: [{ id: RESEARCH_ID, plan_id: '33333333-3333-4333-8333-333333333333', origin: 'native', phase: 'published', run_count: 1, latest_publication_status: 'published', latest_publication_conclusion: '研究通过' }],
}

const researchDetail = {
  id: RESEARCH_ID, origin: 'native', phase: 'published', created_at: '2026-07-20T00:00:00Z', completed_at: '2026-07-20T01:00:00Z',
  plan: { id: '33333333-3333-4333-8333-333333333333', strategy_id: 'momentum-v1', issue_number: 37, version: 1, schema_version: '1', plan_sha256: 'b'.repeat(64), code_commit: 'a'.repeat(40), plan_json: {} },
  approval: { action: 'approved', actor_login: 'Jettlin927', created_at: '2026-07-20T00:01:00Z' },
  runs: [{ run_id: 'run-001', status: 'succeeded', stage: 'completed', result_fingerprint: 'c'.repeat(64), finished_at: '2026-07-20T00:30:00Z' }],
  events: [{ id: '44444444-4444-4444-8444-444444444444', sequence_no: 1, event_type: 'run_succeeded', payload_json: { summary: '执行完成' }, occurred_at: '2026-07-20T00:30:00Z' }],
  evaluations: [{ id: '55555555-5555-4555-8555-555555555555', version: 1, conclusion: '研究通过', supporting_evidence: [{ title: 'OOS 净收益通过' }], opposing_evidence: [], missing_evidence: [], limitations: [{ title: '仅覆盖既定区间' }], follow_up_recommendations: [] }],
  publications: [{ id: PUBLICATION_ID, version: 1, status: 'published' }], follow_up_proposals: [],
}

function ok(data) {
  return Promise.resolve(new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } }))
}

function installFetch({ failStrategies = false } = {}) {
  globalThis.fetch = vi.fn((input) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, '')
    if (path === '/api/research/strategies') {
      if (failStrategies) return Promise.resolve(new Response(JSON.stringify({ detail: '研究表尚不可用' }), { status: 503, headers: { 'Content-Type': 'application/json' } }))
      return ok([strategySummary])
    }
    if (path === '/api/research/strategies/momentum-v1') return ok(strategyProfile)
    if (path === `/api/research/formal-researches/${RESEARCH_ID}`) return ok(researchDetail)
    if (path === `/api/research/publications/${PUBLICATION_ID}`) {
      return ok({ status: 'published', conclusion: '研究通过', evaluation_version: 1, report_url: '/api/research/evaluations/report' })
    }
    return ok(coreResponses[path] ?? {})
  })
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
    expect(screen.getByRole('link', { name: /打开原始 HTML 证据/ })).toHaveAttribute('href', '/api/research/evaluations/report')
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
})
