import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { WatchDiscoveryView } from './WatchDiscoveryView.jsx'

afterEach(() => cleanup())

const projection = {
  revision: 3,
  items: [
    { symbol: 'NVDA', is_holding: true, is_followed: true, candidate_status: 'active', preset_reasons: ['财报观察'], relation_evidence_ids: ['r1'], fact_evidence_ids: ['f1'] },
    { symbol: 'AMD', is_holding: false, is_followed: true, candidate_status: null, preset_reasons: ['行业映射'], custom_reason: '等待新品', relation_evidence_ids: [], fact_evidence_ids: [] },
    { symbol: 'INTC', is_holding: false, is_followed: false, candidate_status: 'archived', relation_evidence_ids: ['r2'], fact_evidence_ids: ['f2'], candidate_archived_at: '2026-08-01T00:00:00Z' },
  ],
  followed_items: [],
  watch_observations: [],
  active_candidates: [],
  archived_candidates: [],
}
projection.followed_items = [projection.items[0], projection.items[1]]
projection.watch_observations = [projection.items[1]]
projection.active_candidates = [projection.items[0]]
projection.archived_candidates = [projection.items[2]]

describe('关注与发现', () => {
  it('三个页签按服务端状态投影，状态身份不只依赖颜色', async () => {
    const client = { openWatchlist: vi.fn().mockResolvedValue(projection) }
    render(<WatchDiscoveryView client={client} />)

    expect(await screen.findByText('NVDA')).toBeInTheDocument()
    expect(screen.getByLabelText('NVDA 状态')).toHaveTextContent('持仓')
    expect(screen.getByLabelText('NVDA 状态')).toHaveTextContent('自选')
    expect(screen.getByLabelText('NVDA 状态')).toHaveTextContent('AI 候选')
    fireEvent.click(screen.getByRole('tab', { name: 'AI 候选' }))
    expect(screen.getByText('NVDA')).toBeInTheDocument()
    expect(screen.queryByText('AMD')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: '已归档' }))
    expect(screen.getByText('INTC')).toBeInTheDocument()
    expect(screen.getByLabelText('INTC 状态')).toHaveTextContent('已归档')
  })

  it('页签使用 roving focus，并支持方向键与 Home/End', async () => {
    const client = { openWatchlist: vi.fn().mockResolvedValue(projection) }
    render(<WatchDiscoveryView client={client} />)
    await screen.findByText('NVDA')
    const followed = screen.getByRole('tab', { name: '自选' })
    const candidates = screen.getByRole('tab', { name: 'AI 候选' })
    const archived = screen.getByRole('tab', { name: '已归档' })

    followed.focus()
    fireEvent.keyDown(followed, { key: 'ArrowRight' })
    expect(candidates).toHaveFocus()
    expect(candidates).toHaveAttribute('aria-selected', 'true')
    expect(followed).toHaveAttribute('tabindex', '-1')
    expect(candidates).toHaveAttribute('aria-controls', 'watch-tabpanel')
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'watch-tab-candidates')
    fireEvent.keyDown(candidates, { key: 'End' })
    expect(archived).toHaveFocus()
    fireEvent.keyDown(archived, { key: 'Home' })
    expect(followed).toHaveFocus()
    expect(followed).toHaveAttribute('aria-selected', 'true')
  })

  it('新增自选提交预设原因多选、自定义原因和乐观修订', async () => {
    const submit = vi.fn().mockResolvedValue({ ...projection, revision: 4 })
    const client = { openWatchlist: vi.fn().mockResolvedValue(projection), submitWatchlistCommand: submit }
    render(<WatchDiscoveryView client={client} />)
    await screen.findByText('NVDA')

    fireEvent.change(screen.getByLabelText('自选代码'), { target: { value: ' msft ' } })
    fireEvent.click(screen.getByRole('checkbox', { name: '财报观察' }))
    fireEvent.click(screen.getByRole('checkbox', { name: '事件催化' }))
    fireEvent.change(screen.getByLabelText('自定义原因'), { target: { value: '等待指引' } })
    fireEvent.click(screen.getByRole('button', { name: '加入自选' }))

    await waitFor(() => expect(submit).toHaveBeenCalledWith(expect.objectContaining({ command: {
      type: 'follow_symbol', symbol: 'MSFT', preset_reasons: ['财报观察', '事件催化'], custom_reason: '等待指引', expected_revision: 3,
    } })))
  })

  it('空、未授权和失败状态不会伪造成候选', async () => {
    const emptyClient = { openWatchlist: vi.fn().mockResolvedValue({ revision: 0, items: [], followed_items: [], watch_observations: [], active_candidates: [], archived_candidates: [] }) }
    render(<WatchDiscoveryView client={emptyClient} />)
    expect(await screen.findByText('尚无自选标的')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'AI 候选' }))
    expect(screen.getByText('当前没有满足证据门槛的 AI 候选')).toBeInTheDocument()

    cleanup()
    const deniedClient = { openWatchlist: vi.fn().mockRejectedValue({ code: 'personal_access_denied', message: '无权限' }) }
    render(<WatchDiscoveryView client={deniedClient} />)
    expect(await screen.findByText('来源未授权')).toBeInTheDocument()
    expect(screen.queryByText('尚无自选标的')).not.toBeInTheDocument()
    expect(screen.queryByText('当前没有满足证据门槛的 AI 候选')).not.toBeInTheDocument()

    cleanup()
    const failedClient = { openWatchlist: vi.fn().mockRejectedValue({ code: 'personal_request_failed', message: '读取失败' }) }
    render(<WatchDiscoveryView client={failedClient} />)
    expect(await screen.findByText('关注投影读取失败')).toBeInTheDocument()
    expect(screen.queryByText('尚无自选标的')).not.toBeInTheDocument()
  })

  it('写入期间取消自选保持单飞，不并发复用旧修订', async () => {
    /** @type {(value: typeof projection) => void} */
    let finish = () => {}
    const submit = vi.fn(() => new Promise((resolve) => { finish = resolve }))
    const client = { openWatchlist: vi.fn().mockResolvedValue(projection), submitWatchlistCommand: submit }
    render(<WatchDiscoveryView client={client} />)
    await screen.findByText('AMD')

    const cancel = screen.getByRole('button', { name: '取消自选' })
    fireEvent.click(cancel)
    fireEvent.click(cancel)
    expect(submit).toHaveBeenCalledTimes(1)
    expect(cancel).toBeDisabled()

    finish({ ...projection, revision: 4 })
    await waitFor(() => expect(cancel).not.toBeDisabled())
  })

  it('写入修订冲突不会误报为读取失败，也不清空旧投影', async () => {
    const client = {
      openWatchlist: vi.fn().mockResolvedValue(projection),
      submitWatchlistCommand: vi.fn().mockRejectedValue({ code: 'revision_conflict', message: 'expected revision 3' }),
    }
    render(<WatchDiscoveryView client={client} />)
    await screen.findByText('AMD')

    fireEvent.click(screen.getByRole('button', { name: '取消自选' }))

    expect(await screen.findByText('关注修订冲突，请刷新后重试')).toBeInTheDocument()
    expect(screen.queryByText('关注投影读取失败')).not.toBeInTheDocument()
    expect(screen.getByText('AMD')).toBeInTheDocument()
    expect(screen.queryByText('尚无自选标的')).not.toBeInTheDocument()
  })
})
