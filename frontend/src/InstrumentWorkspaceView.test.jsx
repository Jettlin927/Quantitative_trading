import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { InstrumentWorkspaceView } from './InstrumentWorkspaceView.jsx'

const workspace = {
  identity: { symbol: 'ACME', name: 'Acme Holdings', asset_class: 'us_equity' },
  raw_bars: [{ time: '2026-08-01', open: '100', high: '103', low: '99', close: '102', volume: 1000 }],
  provider_adjusted_bars: [{ time: '2026-08-01', open: '50', high: '51.5', low: '49.5', close: '51', volume: 1000 }],
  cost_reference: { availability: 'available', value: '100.2500', identity: 'current_manual_average_cost', historical_position_track: false },
  event_tracks: [{ track: 'corporate', events: [{ event_id: 'sec-1', label: '10-Q 已确认', occurred_at: '2026-08-01T12:00:00Z', confirmation_state: 'confirmed', evidence_ids: ['sec-1'] }] }],
  event_source_statuses: [
    { source: 'alpaca_corporate_actions', availability: 'available', event_count: 0 },
    { source: 'official_events', availability: 'not_configured', event_count: 0 },
  ],
  evidence_inspector: {
    selected_date: '2026-08-01',
    evidence_ids: ['bar-1', 'sec-1'],
    source_health: 'fresh',
    authorization_snapshot_ids: ['auth-display'],
    issues: [],
    items: [{ label: 'Alpaca 原始日线', source: 'Alpaca', dataset: 'alpaca_daily_bars', observed_date: '2026-08-01', source_health: 'fresh', evidence_id: 'bar-1' }],
  },
  formal_research_overlay: { research_eligible: false, label: '正式研究发布投影', scale_identity: 'normalized_readonly', events: [] },
  issues: [],
}

describe('InstrumentWorkspaceView', () => {
  it('K 线先于事件和证据，并明确成本线不是历史持仓轨迹', async () => {
    const client = { openInstrument: vi.fn().mockResolvedValue(workspace) }
    const chartAdapter = { create: vi.fn(() => ({ setData() {}, setRange() {}, resize() {}, dispose() {} })) }
    render(<InstrumentWorkspaceView client={client} symbol="ACME" chartAdapter={chartAdapter} />)

    expect(await screen.findByRole('heading', { name: 'ACME · Acme Holdings' })).toBeTruthy()
    expect(screen.getByText('当前成本参考线')).toBeTruthy()
    expect(screen.getByText('不是历史持仓轨迹')).toBeTruthy()
    expect(screen.getByRole('heading', { name: '独立事件轨' })).toBeTruthy()
    expect(screen.getByText('公司行动来源正常，当前区间 0 条')).toBeTruthy()
    expect(screen.getByText('官方 SEC / IR 事件尚未配置')).toBeTruthy()
    expect(screen.getByRole('heading', { name: '证据检查器' })).toBeTruthy()
    expect(screen.getByText('Alpaca 原始日线')).toBeTruthy()
    expect(screen.getByText('alpaca_daily_bars · 2026-08-01 · fresh')).toBeTruthy()
    expect(screen.getByText('授权快照 1 条')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Provider adjusted' }))
    await waitFor(() => expect(chartAdapter.create).toHaveBeenCalled())
  })

  it('默认入口从真实活跃持仓选择，并允许手工输入代码', async () => {
    const onNavigate = vi.fn()
    const client = {
      openPortfolio: vi.fn().mockResolvedValue({
        holdings: [{ holding_id: 'holding-net', symbol: 'NET', name: 'Cloudflare', state: 'active' }],
      }),
      openInstrument: vi.fn(),
    }

    render(<InstrumentWorkspaceView client={client} symbol="" onNavigate={onNavigate} />)

    expect(screen.getByRole('heading', { name: '选择一个真实标的' })).toBeTruthy()
    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith('/markets/us/NET'))
    expect(client.openInstrument).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('美股代码'), { target: { value: 'glw' } })
    fireEvent.click(screen.getByRole('button', { name: '打开标的' }))
    expect(onNavigate).toHaveBeenLastCalledWith('/markets/us/GLW')
  })
})
