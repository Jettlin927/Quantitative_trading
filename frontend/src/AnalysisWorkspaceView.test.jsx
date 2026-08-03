import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AnalysisWorkspaceView } from './AnalysisWorkspaceView.jsx'

afterEach(() => cleanup())


const preview = {
  draft_id: 'draft-1', status: 'ready', provider: 'openai', model: 'gpt-5.6-sol',
  config_revision: 'personal-impact-v1', included_fields: ['user_question', 'official_facts'],
  excluded_fields: [{ field: 'market_prices', reason_code: 'source_denied_for_ai' }],
  gaps: ['missing_current_guidance'], preview_sha256: 'a'.repeat(64),
  retention: 'store=false；服务端仅保存本地审计', estimated_cost_usd: '0.0040',
  expires_at: '2026-08-03T04:30:00Z', consumed_at: null, evidence_ids: ['sec-1'],
}

describe('AI 影响分析工作台', () => {
  it('先展示外发预览，只有勾选确认后才能入队', async () => {
    const client = {
      prepareAnalysis: vi.fn().mockResolvedValue(preview),
      startAnalysis: vi.fn().mockResolvedValue({ run_id: 'run-1', status: 'queued', stage: 'queued', claims: [], events: [] }),
      openAnalysis: vi.fn(),
      cancelAnalysis: vi.fn(),
    }
    render(<AnalysisWorkspaceView client={client} subjectId="ACME" />)

    fireEvent.change(screen.getByLabelText('分析问题'), { target: { value: '官方事实可能如何影响公司？' } })
    fireEvent.click(screen.getByRole('button', { name: '生成外发预览' }))

    expect(await screen.findByText('openai / gpt-5.6-sol')).toBeInTheDocument()
    expect(screen.getByText('official_facts')).toBeInTheDocument()
    expect(screen.getByText('market_prices')).toBeInTheDocument()
    expect(screen.getByText('missing_current_guidance')).toBeInTheDocument()
    const start = screen.getByRole('button', { name: '确认外发并开始分析' })
    expect(start).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: /确认 preview/ }))
    expect(start).toBeEnabled()
    fireEvent.click(start)

    await waitFor(() => expect(client.startAnalysis).toHaveBeenCalledWith(expect.objectContaining({
      draftId: 'draft-1', previewSha256: 'a'.repeat(64),
    })))
    expect(await screen.findByText('QUEUED · 已入队')).toBeInTheDocument()
  })

  it('provider 不可用时保留预览与确定性工作台边界', async () => {
    const client = {
      prepareAnalysis: vi.fn().mockResolvedValue(preview),
      startAnalysis: vi.fn().mockRejectedValue({ code: 'provider_unavailable', message: 'AI 分析当前不可用。' }),
      openAnalysis: vi.fn(),
      cancelAnalysis: vi.fn(),
    }
    render(<AnalysisWorkspaceView client={client} subjectId="ACME" />)
    fireEvent.change(screen.getByLabelText('分析问题'), { target: { value: '降级验证' } })
    fireEvent.click(screen.getByRole('button', { name: '生成外发预览' }))
    await screen.findByText('openai / gpt-5.6-sol')
    fireEvent.click(screen.getByRole('checkbox', { name: /确认 preview/ }))
    fireEvent.click(screen.getByRole('button', { name: '确认外发并开始分析' }))

    expect(await screen.findByText('Provider 不可用')).toBeInTheDocument()
    expect(screen.getByText(/行情、规则与证据检查仍可继续使用/)).toBeInTheDocument()
    expect(screen.getByText('official_facts')).toBeInTheDocument()
  })

  it('按四类身份展示证据、反对证据、假设、期限和失效条件', async () => {
    const claims = ['confirmed_fact', 'inference', 'conditional_scenario', 'unknown'].map((kind, index) => ({
      claim_id: `claim-${index}`, kind, statement: `${kind} 主张`, evidence_ids: ['sec-1'],
      opposing_evidence_ids: index === 1 ? ['macro-1'] : [], assumptions: index ? ['假设 A'] : [],
      horizon: '未来两个季度', invalidation_conditions: ['新增官方披露'],
    }))
    const client = {
      prepareAnalysis: vi.fn(), startAnalysis: vi.fn(), cancelAnalysis: vi.fn(),
      openAnalysis: vi.fn().mockResolvedValue({
        run_id: 'run-1', status: 'completed', stage: 'completed', claims,
        events: [{ sequence: 1, stage: 'completed', status: 'completed', occurred_at: '2026-08-03T04:00:00Z' }],
      }),
    }
    render(<AnalysisWorkspaceView client={client} subjectId="ACME" initialRunId="run-1" />)

    expect(await screen.findByText(/已确认事实/)).toBeInTheDocument()
    expect(screen.getByText(/◇ 推断/)).toBeInTheDocument()
    expect(screen.getByText(/条件情景/)).toBeInTheDocument()
    expect(screen.getByText(/未知项/)).toBeInTheDocument()
    expect(screen.getAllByText('未来两个季度')).toHaveLength(4)
    expect(screen.getByText('反对证据 macro-1')).toBeInTheDocument()
    expect(screen.getAllByText('失效：新增官方披露')).toHaveLength(4)
  })
})
