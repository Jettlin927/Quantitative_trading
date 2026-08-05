import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from './main.jsx'

afterEach(() => cleanup())

const health = { status: 'ok', database: 'ok' }

function createReadAdapter(overrides = {}) {
  return vi.fn(({ path }) => {
    if (path === '/api/health?include_counts=false') return Promise.resolve(health)
    if (path in overrides) return Promise.resolve(overrides[path])
    return Promise.reject(new Error(`未声明只读端点：${path}`))
  })
}

const personalClient = {
  openToday: vi.fn(() => Promise.resolve({ trace: null, portfolio: { portfolio_revision: 0, holdings: [], issues: [] }, attention_items: [] })),
  openPortfolio: vi.fn(() => Promise.resolve({ portfolio_revision: 0, holdings: [], issues: [] })),
  listAnalysisCapabilities: vi.fn(() => Promise.resolve(null)),
  listAnalyses: vi.fn(() => Promise.resolve([])),
}

describe('美股优先工作台路由', () => {
  it('只保留五个美股工作区，不再展示 A 股、旧实验美股、个人记录或旧策略驾驶舱', async () => {
    render(<App readAdapter={createReadAdapter()} personalClient={personalClient} />)

    for (const name of ['今日工作台', '我的持仓', '市场与标的', '规则与策略', '数据与系统']) {
      expect(screen.getByRole('button', { name: new RegExp(name) })).toBeInTheDocument()
    }
    for (const name of ['A 股数据', '美股数据', '研究记录', '研究驾驶舱']) {
      expect(screen.queryByRole('button', { name: new RegExp(name) })).not.toBeInTheDocument()
    }
    expect(await screen.findByRole('heading', { name: '今天先看组合、数据缺口与待验证事项' })).toBeInTheDocument()
  })

  it('旧路由回落今日工作台且不会请求已退役端点', async () => {
    const readAdapter = createReadAdapter()
    render(<App initialPath="/markets/a-share/overview" readAdapter={readAdapter} personalClient={personalClient} />)

    await waitFor(() => expect(readAdapter).toHaveBeenCalledWith({ path: '/api/health?include_counts=false' }))
    expect(readAdapter.mock.calls.map(([request]) => request.path)).toEqual(['/api/health?include_counts=false'])
    expect(screen.queryByText(/A 股实际市场数据/)).not.toBeInTheDocument()

    cleanup()
    readAdapter.mockClear()
    render(<App initialPath="/legacy/us-data" readAdapter={readAdapter} personalClient={personalClient} />)
    await waitFor(() => expect(readAdapter).toHaveBeenCalledWith({ path: '/api/health?include_counts=false' }))
    expect(readAdapter.mock.calls.map(([request]) => request.path)).toEqual(['/api/health?include_counts=false'])
    expect(await screen.findByRole('heading', { name: '今天先看组合、数据缺口与待验证事项' })).toBeInTheDocument()

    cleanup()
    readAdapter.mockClear()
    render(<App initialPath="/research" readAdapter={readAdapter} personalClient={personalClient} />)
    await waitFor(() => expect(readAdapter).toHaveBeenCalledWith({ path: '/api/health?include_counts=false' }))
    expect(readAdapter.mock.calls.map(([request]) => request.path)).toEqual(['/api/health?include_counts=false'])
    expect(await screen.findByRole('heading', { name: '今天先看组合、数据缺口与待验证事项' })).toBeInTheDocument()
  })

  it('系统页仅展示通用健康与个人工作台边界', async () => {
    const readAdapter = createReadAdapter()
    render(<App initialPath="/system" readAdapter={readAdapter} personalClient={personalClient} />)

    expect(await screen.findByRole('heading', { name: '系统运维' })).toBeInTheDocument()
    expect(screen.getByText('个人工作台访问')).toBeInTheDocument()
    expect(screen.getByText('手工美股持仓 · 无券商连接 · 无下单')).toBeInTheDocument()
    expect(screen.queryByText('同步 Worker 心跳')).not.toBeInTheDocument()
    expect(screen.queryByText('同步队列')).not.toBeInTheDocument()
    expect(screen.queryByText('PostgreSQL 数据覆盖')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /全局刷新/ }))
    await waitFor(() => expect(document.querySelector('.updated-at')).toHaveTextContent('界面刷新'))
  })
})
