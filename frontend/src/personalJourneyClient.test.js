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
})
