import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PortfolioView } from './PortfolioView.jsx'

afterEach(() => cleanup())

const available = (value, extras = {}) => ({
  availability: 'available', value, reason_code: null, source_health: 'fresh',
  as_of: '2026-08-03T02:45:00Z', source_ids: ['alpaca-acme'], feed: 'delayed_sip', delay_seconds: 900,
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
  active_holding_count: 0,
  priced_holding_count: 0,
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
  active_holding_count: 1,
  priced_holding_count: 1,
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
    const table = await screen.findByRole('table')
    const row = within(table).getByText('ACME').closest('tr')
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

  it('单一标的无行情时展示已覆盖估值而不冒充完整总值', async () => {
    const partial = available('241.0000', {
      reason_code: 'partial_valuation', source_health: 'degraded',
    })
    const portfolio = {
      ...activePortfolio,
      holdings: [
        activePortfolio.holdings[0],
        {
          ...activePortfolio.holdings[0], holding_id: 'holding-002', symbol: 'BETA', name: 'Beta',
          market_price: unavailable, market_value: unavailable, unrealized_profit_loss: unavailable,
          unrealized_return: unavailable, weight: unavailable,
        },
      ],
      total_market_value: partial,
      total_equity: partial,
      active_holding_count: 2,
      priced_holding_count: 1,
      issues: ['provider_unavailable', 'partial_valuation'],
    }
    const client = { openPortfolio: vi.fn(() => Promise.resolve(portfolio)) }

    render(<PortfolioView client={client} />)

    expect(await screen.findByText('已覆盖组合值')).toBeInTheDocument()
    expect(screen.getByText('已覆盖持仓市值')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('行情覆盖 1 / 2')
    expect(screen.getByRole('status')).toHaveTextContent('其余标的保持不可用')
    expect(screen.getAllByText('241.0000')).toHaveLength(3)
  })

  it('权益日线与概览：折线、KPI、仓位分布、盈亏分解与日表', async () => {
    const history = {
      currency: 'USD',
      snapshots: [
        { market_day: '2026-08-03', total_equity: '200.0000', total_market_value: '180.0000', usd_cash: '20.0000', holdings_count: 1, priced_count: 1, after_close: true, observed_at: '2026-08-03T20:05:00Z' },
        { market_day: '2026-08-04', total_equity: '250.0000', total_market_value: '230.0000', usd_cash: '20.0000', holdings_count: 1, priced_count: 1, after_close: false, observed_at: '2026-08-04T15:00:00Z' },
      ],
    }
    const fakeAdapter = {
      create: vi.fn(() => ({ setData: vi.fn(), setRange: vi.fn(), resize: vi.fn(), dispose: vi.fn() })),
    }
    const client = {
      openPortfolio: vi.fn(() => Promise.resolve(activePortfolio)),
      openEquityHistory: vi.fn(() => Promise.resolve(history)),
    }

    render(<PortfolioView client={client} chartAdapter={fakeAdapter} />)

    expect(await screen.findByText('权益日线与概览')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /组合权益日线，共 2 个观测点/ })).toBeInTheDocument()
    expect(fakeAdapter.create).toHaveBeenCalledTimes(1)
    // 今日变动 = 250 - 200 = +50.00 / 25.00%（KPI 与日表各出现一次）
    expect(screen.getAllByText('+50.00').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('25.00%').length).toBeGreaterThanOrEqual(1)
    // KPI：总成本、未实现盈亏、盈亏率、现金占比、最大持仓
    expect(screen.getByText('总成本')).toBeInTheDocument()
    expect(screen.getByText('200.50')).toBeInTheDocument()
    expect(screen.getAllByText('+40.50').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('20.20%').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('0.00%')).toBeInTheDocument()
    expect(screen.getByText('最大持仓')).toBeInTheDocument()
    // 仓位分布与盈亏分解
    expect(screen.getByText('仓位分布')).toBeInTheDocument()
    expect(screen.getByText('未实现盈亏分解')).toBeInTheDocument()
    expect(screen.getAllByText('100.00%').length).toBeGreaterThanOrEqual(1)
    // 日表：最新在前，含收盘/盘中标记
    const table = await screen.findByRole('table', { name: /组合权益日线/ })
    const rows = within(table).getAllByRole('row')
    expect(rows[1]).toHaveTextContent('2026-08-04')
    expect(within(rows[1]).getByText('盘中')).toBeInTheDocument()
    expect(rows[2]).toHaveTextContent('2026-08-03')
    expect(within(rows[2]).getByText('收盘')).toBeInTheDocument()
  })

  it('实时行情失败时标记上次落盘并显示回退提示', async () => {
    const cachedPortfolio = {
      ...activePortfolio,
      holdings: [{
        ...activePortfolio.holdings[0],
        market_price: available('120.5000', { cached: true, source_health: 'stale' }),
        market_value: available('241.0000', { cached: true, source_health: 'stale' }),
        unrealized_profit_loss: available('40.5000', { cached: true, source_health: 'stale' }),
        unrealized_return: available('0.201995', { cached: true, source_health: 'stale' }),
        weight: available('1.000000', { cached: true, source_health: 'stale' }),
      }],
    }
    const client = { openPortfolio: vi.fn(() => Promise.resolve(cachedPortfolio)) }

    render(<PortfolioView client={client} />)

    expect((await screen.findAllByText(/上次落盘/)).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('1 个标的使用上次落盘行情。')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('实时行情获取失败，已回退到最近一次成功落盘的价格')
  })
})
