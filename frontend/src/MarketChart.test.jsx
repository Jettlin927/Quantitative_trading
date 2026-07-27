import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { MarketChart } from './MarketChart.jsx'

const bars = [
  { time: '2025-07-17', open: 9, high: 10, low: 8, close: 9.5, volume: 900, amount: 1800, provenance: { kind: 'actual_market', label: '实际市场数据', source: 'PostgreSQL' } },
  { time: '2026-07-18', open: 10.5, high: 12, low: 10, close: 11, volume: 1200, amount: 2400, provenance: { kind: 'actual_market', label: '实际市场数据', source: 'PostgreSQL' } },
]

afterEach(cleanup)

describe('MarketChart', () => {
  it('通过 chart adapter 管理 create、setData、range、resize、dispose，并保留中文无障碍摘要', () => {
    const chart = {
      setData: vi.fn(),
      setRange: vi.fn(),
      resize: vi.fn(),
      dispose: vi.fn(),
    }
    const chartAdapter = { create: vi.fn(() => chart) }
    const view = render(<MarketChart bars={bars} chartAdapter={chartAdapter} />)

    expect(chartAdapter.create).toHaveBeenCalledOnce()
    expect(chart.setData).toHaveBeenCalledWith(expect.objectContaining({ bars }))
    expect(chart.setRange).toHaveBeenCalledWith(expect.any(Array), 'recent')
    expect(screen.getByRole('img', { name: /价格图。近 180 日：日 K 线共 2 个交易日，范围 2025-07-17 至 2026-07-18/ })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '近 1 年' }))
    expect(chart.setRange).toHaveBeenLastCalledWith(expect.any(Array), '1y')

    fireEvent(window, new Event('resize'))
    expect(chart.resize).toHaveBeenCalled()

    view.unmount()
    expect(chart.dispose).toHaveBeenCalledOnce()
  })
})
