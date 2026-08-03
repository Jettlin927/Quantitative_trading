import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PortfolioView } from './PortfolioView.jsx'

afterEach(() => cleanup())

const available = (value, extras = {}) => ({
  availability: 'available', value, reason_code: null, source_health: 'fresh',
  as_of: '2026-08-03T02:45:00Z', source_ids: ['alpaca-acme'], feed: 'sip', delay_seconds: 900,
  ...extras,
})

const unavailable = {
  availability: 'not_available', value: null, reason_code: 'provider_unavailable',
  source_health: 'unavailable', as_of: null, source_ids: [], feed: null, delay_seconds: null,
}

const emptyPortfolio = {
  workspace_id: null,
  portfolio_revision: 0,
  currency: 'USD',
  usd_cash: '0.0000',
  holdings: [],
  total_market_value: available('0.0000', { as_of: null, source_ids: [], feed: null, delay_seconds: null }),
  total_equity: available('0.0000', { as_of: null, source_ids: [], feed: null, delay_seconds: null }),
  issues: [],
}

const activePortfolio = {
  ...emptyPortfolio,
  workspace_id: 'workspace-001',
  portfolio_revision: 1,
  holdings: [{
    holding_id: 'holding-001', symbol: 'ACME', name: 'Acme Holdings', state: 'active', revision: 1,
    quantity: '2.0000', average_cost: '100.2500', cost_amount: '200.5000', currency: 'USD',
    verification_status: 'pending_verification',
    market_price: available('120.5000'), market_value: available('241.0000'),
    unrealized_profit_loss: available('40.5000'), unrealized_return: available('0.201995'),
    weight: available('1.000000'),
  }],
  total_market_value: available('241.0000'),
  total_equity: available('241.0000'),
}

describe('手工美股持仓工作台', () => {
  it('从空状态提交手工字段，并只展示服务端返回的七个字段与派生值', async () => {
    const client = {
      openPortfolio: vi.fn(() => Promise.resolve(emptyPortfolio)),
      submitPortfolioCommand: vi.fn(() => Promise.resolve(activePortfolio)),
    }
    render(<PortfolioView client={client} />)

    expect(await screen.findByText('尚未添加持仓')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('标的代码'), { target: { value: 'ACME' } })
    fireEvent.change(screen.getByLabelText('标的名称'), { target: { value: 'Acme Holdings' } })
    fireEvent.change(screen.getByLabelText('持股数量'), { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText('平均买入价'), { target: { value: '100.25' } })
    fireEvent.click(screen.getByRole('button', { name: '添加持仓' }))

    await waitFor(() => expect(client.submitPortfolioCommand).toHaveBeenCalledWith(expect.objectContaining({
      command: {
        type: 'add_holding', symbol: 'ACME', name: 'Acme Holdings', quantity: '2', average_cost: '100.25',
        expected_portfolio_revision: 0,
      },
    })))
    const row = (await screen.findByText('ACME')).closest('tr')
    for (const value of ['2.0000', '100.2500', '200.5000', '241.0000', '+40.5000', '20.20%', '100.00%']) {
      expect(within(row).getByText(value)).toBeInTheDocument()
    }
    expect(screen.getByText('延迟 SIP · 15 分钟')).toBeInTheDocument()
  })

  it('行情不可用显示明确缺值，移出可恢复，永久删除必须二次 challenge', async () => {
    const unavailablePortfolio = {
      ...activePortfolio,
      portfolio_revision: 2,
      holdings: [{
        ...activePortfolio.holdings[0],
        market_price: unavailable, market_value: unavailable, unrealized_profit_loss: unavailable,
        unrealized_return: unavailable, weight: unavailable,
      }],
      total_market_value: unavailable,
      total_equity: unavailable,
      issues: ['provider_unavailable'],
    }
    const removedPortfolio = {
      ...unavailablePortfolio,
      portfolio_revision: 3,
      holdings: [{ ...unavailablePortfolio.holdings[0], state: 'removed' }],
    }
    const challenge = {
      holding_id: 'holding-001', portfolio_revision: 3, challenge: 'signed-challenge',
      expires_at: '2026-08-03T03:10:00Z',
    }
    const client = {
      openPortfolio: vi.fn(() => Promise.resolve(unavailablePortfolio)),
      submitPortfolioCommand: vi.fn(({ command }) => {
        if (command.type === 'remove_holding') return Promise.resolve(removedPortfolio)
        if (command.type === 'request_purge') return Promise.resolve(challenge)
        if (command.type === 'confirm_purge') return Promise.resolve({
          holding_id: 'holding-001', status: 'purged', portfolio_revision: 4,
          backup_status: 'expires_within_window', backup_expires_at: '2026-09-02T03:00:00Z',
        })
        return Promise.resolve(unavailablePortfolio)
      }),
    }
    render(<PortfolioView client={client} />)

    expect(await screen.findAllByText('不可用')).toHaveLength(6)
    expect(screen.getByText('行情来源不可用，手工持仓仍可编辑。')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '移出 ACME' }))
    expect(await screen.findByRole('button', { name: '恢复 ACME' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '永久删除 ACME' }))
    expect(await screen.findByRole('dialog', { name: '永久删除 ACME' })).toBeInTheDocument()
    expect(client.submitPortfolioCommand).toHaveBeenCalledWith(expect.objectContaining({
      command: { type: 'request_purge', holding_id: 'holding-001', expected_portfolio_revision: 3 },
    }))
    fireEvent.click(screen.getByRole('button', { name: '确认永久删除' }))
    await waitFor(() => expect(client.submitPortfolioCommand).toHaveBeenCalledWith(expect.objectContaining({
      command: {
        type: 'confirm_purge', holding_id: 'holding-001', expected_portfolio_revision: 3, challenge: 'signed-challenge',
      },
    })))
    expect(await screen.findByText(/备份副本最迟于/)).toBeInTheDocument()
  })
})
