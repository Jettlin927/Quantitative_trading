import { describe, expect, it, vi } from 'vitest'

import { PersonalJourneyClient, PersonalJourneyError } from './personalJourneyClient.js'

describe('PersonalJourneyClient', () => {
  it('只向同源私有端点发送 JSON 和命令证明，不接收或发送 gateway token', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ synthetic: true }), {
      status: 201,
      headers: { 'Content-Type': 'application/json' },
    }))
    const client = new PersonalJourneyClient({ fetcher })

    await client.createSyntheticTrace({ question: '合成问题', idempotencyKey: 'trace-001' })

    expect(fetcher).toHaveBeenCalledWith('/api/personal/synthetic-traces', expect.objectContaining({
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': 'trace-001',
        'X-Personal-Request': '1',
      },
      body: JSON.stringify({ question: '合成问题' }),
    }))
    expect(JSON.stringify(fetcher.mock.calls)).not.toMatch(/gateway|token/i)
  })

  it('保留服务端稳定错误码并支持 AbortSignal', async () => {
    const controller = new AbortController()
    const fetcher = vi.fn(async (_path, options) => {
      expect(options.signal).toBe(controller.signal)
      return new Response(JSON.stringify({ detail: { code: 'provider_unavailable', message: '模型不可用' } }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      })
    })
    const client = new PersonalJourneyClient({ fetcher })

    await expect(client.openToday({ signal: controller.signal })).rejects.toEqual(
      expect.objectContaining({ name: 'PersonalJourneyError', code: 'provider_unavailable', status: 503 }),
    )
    await client.openToday({ signal: controller.signal }).catch((error) => {
      expect(error).toBeInstanceOf(PersonalJourneyError)
    })
  })

  it('持仓读写只发送服务端封闭命令和 expected revision', async () => {
    const responses = [
      { portfolio_revision: 0, holdings: [] },
      { portfolio_revision: 1, holdings: [{ holding_id: 'holding-001', symbol: 'ACME' }] },
    ]
    const fetcher = vi.fn(async () => new Response(JSON.stringify(responses.shift()), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    const client = new PersonalJourneyClient({ fetcher })

    await client.openPortfolio()
    await client.submitPortfolioCommand({
      command: {
        type: 'add_holding',
        symbol: 'ACME',
        name: 'Acme Holdings',
        quantity: '2',
        average_cost: '100.25',
        expected_portfolio_revision: 0,
      },
      idempotencyKey: 'portfolio-add-001',
    })

    const firstCall = /** @type {any[]} */ (fetcher.mock.calls[0])
    expect(firstCall[0]).toBe('/api/personal/portfolio')
    expect(fetcher.mock.calls[1]).toEqual([
      '/api/personal/portfolio/commands',
      expect.objectContaining({
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': 'portfolio-add-001',
          'X-Personal-Request': '1',
        },
        body: JSON.stringify({
          type: 'add_holding',
          symbol: 'ACME',
          name: 'Acme Holdings',
          quantity: '2',
          average_cost: '100.25',
          expected_portfolio_revision: 0,
        }),
      }),
    ])
  })

  it('权益历史只请求 limit 参数并返回同一端点投影', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({
      currency: 'USD',
      snapshots: [
        { market_day: '2026-08-03', total_equity: '241.0000', after_close: true },
      ],
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    const client = new PersonalJourneyClient({ fetcher })

    const result = await client.openEquityHistory({ limit: 30 })

    const firstCall = /** @type {any[]} */ (fetcher.mock.calls[0])
    expect(firstCall[0]).toBe('/api/personal/portfolio/equity-history?limit=30')
    expect(result.snapshots).toHaveLength(1)
    expect(result.snapshots[0].market_day).toBe('2026-08-03')
  })

  it('AI 只发送问题、对象 ID 与 preview hash，不回传行情或模型正文', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ status: 'ready' }), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    }))
    const client = new PersonalJourneyClient({ fetcher })

    await client.prepareAnalysis({
      question: '官方事实可能如何影响公司？',
      subjectIds: ['ACME'],
      selectedPrivateFields: [],
      idempotencyKey: 'prepare-ai-1',
    })
    await client.startAnalysis({
      draftId: 'draft-1',
      previewSha256: 'a'.repeat(64),
      idempotencyKey: 'start-ai-1',
    })

    const prepareCall = /** @type {any[]} */ (fetcher.mock.calls[0])
    const startCall = /** @type {any[]} */ (fetcher.mock.calls[1])
    expect(prepareCall[0]).toBe('/api/personal/analysis-drafts')
    expect(JSON.parse(prepareCall[1].body)).toEqual({
      question: '官方事实可能如何影响公司？',
      subject_ids: ['ACME'],
      selected_private_fields: [],
    })
    expect(JSON.parse(startCall[1].body)).toEqual({
      draft_id: 'draft-1',
      preview_sha256: 'a'.repeat(64),
    })
    expect(JSON.stringify(fetcher.mock.calls)).not.toMatch(/market_prices|portfolio_weight|model_output/)
  })

  it('个人记录只提交 analysis 与 claim ID，不提交模型正文', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ record_id: 'record-1' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    const client = new PersonalJourneyClient({ fetcher })

    await client.commitRecord({
      command: {
        type: 'save_analysis',
        analysis_id: 'run-1',
        accepted_claim_ids: ['claim-1'],
        user_supplement: '用户说明',
        private_fragments: [],
        verification_drafts: [],
      },
      idempotencyKey: 'record-save-1',
    })

    const call = /** @type {any[]} */ (fetcher.mock.calls[0])
    expect(call[0]).toBe('/api/personal/records/commands')
    expect(JSON.parse(call[1].body)).toEqual(expect.objectContaining({
      analysis_id: 'run-1',
      accepted_claim_ids: ['claim-1'],
    }))
    expect(call[1].body).not.toMatch(/model_output|statement|market_prices|portfolio_weight/)
  })
})
