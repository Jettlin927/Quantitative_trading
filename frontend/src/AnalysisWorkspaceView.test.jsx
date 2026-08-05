import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AnalysisWorkspaceView } from './AnalysisWorkspaceView.jsx'

afterEach(() => cleanup())


const preview = {
  draft_id: 'draft-1', status: 'ready', provider: 'deepseek', model: 'deepseek-v4-flash',
  config_revision: 'official-analysis-fixture-v1', included_fields: ['user_question', 'official_facts'],
  excluded_fields: [{ field: 'market_prices', reason_code: 'source_denied_for_ai' }],
  gaps: [], preview_sha256: 'a'.repeat(64),
  retention: 'DeepSeek 默认磁盘上下文缓存；输入/输出按当次政策处理', estimated_cost_usd: '0.0040',
  expires_at: '2026-08-03T04:30:00Z', consumed_at: null, evidence_ids: ['sec-1'],
  evidence: [{ evidence_id: 'sec-1', source: 'sec', field: 'official_facts', as_of: '2026-08-03T04:00:00Z' }],
}

describe('AI 影响分析工作台', () => {
  it('展示主动停用原因、真实持仓标的和历史运行，不把它误报成 Key 失败', async () => {
    const client = {
      openPortfolio: vi.fn().mockResolvedValue({ holdings: [{ holding_id: 'holding-net', symbol: 'NET', name: 'Cloudflare', state: 'active' }] }),
      listAnalysisCapabilities: vi.fn().mockResolvedValue({
        provider: 'disabled', model: 'deepseek-v4-flash', dispatch_enabled: false,
        reason_code: 'provider_disabled', credentials_scope: 'analysis_worker_only',
        credentials_visible_to_api: false, history_readable: true,
      }),
      listAnalyses: vi.fn().mockResolvedValue([{ run_id: 'run-old', status: 'completed', model: 'deepseek-v4-flash', claims: [], events: [], actual_cost_usd: '0.0002' }]),
      prepareAnalysis: vi.fn(), openAnalysis: vi.fn(), startAnalysis: vi.fn(), cancelAnalysis: vi.fn(),
    }

    render(<AnalysisWorkspaceView client={client} subjectId="SYNTH-001" />)

    expect(await screen.findByText('DeepSeek 由生产配置主动停用')).toBeInTheDocument()
    expect(screen.getByText(/不是 Key 校验失败/)).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '分析标的' })).toHaveValue('NET')
    expect(screen.getByText('COMPLETED · 已完成')).toBeInTheDocument()
    expect(screen.getByText(/run-old/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '生成外发预览' })).toBeDisabled()
  })

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

    expect(await screen.findByText('DeepSeek / deepseek-v4-flash')).toBeInTheDocument()
    expect(screen.getByText('official_facts')).toBeInTheDocument()
    expect(screen.getByText('market_prices')).toBeInTheDocument()
    expect(screen.getByText(/sec-1 · sec · official_facts/)).toBeInTheDocument()
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
    await screen.findByText('DeepSeek / deepseek-v4-flash')
    fireEvent.click(screen.getByRole('checkbox', { name: /确认 preview/ }))
    fireEvent.click(screen.getByRole('button', { name: '确认外发并开始分析' }))

    expect(await screen.findByText('Provider 不可用')).toBeInTheDocument()
    expect(screen.getByText(/行情、规则与证据检查仍可继续使用/)).toBeInTheDocument()
    expect(screen.getByText('official_facts')).toBeInTheDocument()
  })

  it('证据缺口存在时即使勾选确认也不能入队', async () => {
    const client = {
      prepareAnalysis: vi.fn().mockResolvedValue({
        ...preview,
        evidence_ids: [],
        evidence: [],
        gaps: ['official_evidence_config_stale'],
      }),
      startAnalysis: vi.fn(),
      openAnalysis: vi.fn(),
      cancelAnalysis: vi.fn(),
    }
    render(<AnalysisWorkspaceView client={client} subjectId="ACME" />)
    fireEvent.change(screen.getByLabelText('分析问题'), { target: { value: '过期配置必须失败关闭' } })
    fireEvent.click(screen.getByRole('button', { name: '生成外发预览' }))
    expect(await screen.findByText('official_evidence_config_stale')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('checkbox', { name: /确认 preview/ }))
    expect(screen.getByRole('button', { name: '确认外发并开始分析' })).toBeDisabled()
    expect(client.startAnalysis).not.toHaveBeenCalled()
  })

  it('agent 模式展示工具模式徽标与服务端执行说明', async () => {
    const client = {
      openPortfolio: vi.fn().mockResolvedValue({ holdings: [{ holding_id: 'h1', symbol: 'NVDA', name: 'NVIDIA', state: 'active' }] }),
      listAnalysisCapabilities: vi.fn().mockResolvedValue({
        provider: 'deepseek', model: 'deepseek-v4-flash', analysis_mode: 'agent',
        dispatch_enabled: true, reason_code: null, credentials_scope: 'analysis_worker_only',
        credentials_visible_to_api: false, history_readable: true,
      }),
      listAnalyses: vi.fn().mockResolvedValue([]),
      prepareAnalysis: vi.fn(), openAnalysis: vi.fn(), startAnalysis: vi.fn(), cancelAnalysis: vi.fn(),
    }
    render(<AnalysisWorkspaceView client={client} subjectId="NVDA" />)

    expect(await screen.findByText(/工具模式/)).toBeInTheDocument()
    expect(screen.getByText(/agent 在服务端按需调用持仓/)).toBeInTheDocument()
    expect(screen.getByText(/分析在服务端执行，agent 按需调用/)).toBeInTheDocument()
    expect(screen.getByText(/TOOL-USE AGENT/)).toBeInTheDocument()
  })

  it('agent 模式预览展示服务端工具，无冻结证据也能确认外发', async () => {
    const agentPreview = {
      draft_id: 'draft-agent-1', status: 'ready', provider: 'deepseek-agent', model: 'deepseek-v4-flash',
      config_revision: 'personal-agent-deepseek-v1',
      included_fields: ['user_question', 'get_holdings', 'get_kline', 'get_news'],
      excluded_fields: [], gaps: [], preview_sha256: 'b'.repeat(64),
      retention: 'DeepSeek 默认磁盘上下文缓存；输入/输出按当次政策处理',
      estimated_cost_usd: '0.0200', expires_at: '2026-08-10T04:30:00Z', consumed_at: null,
      evidence_ids: [], evidence: [],
    }
    const client = {
      prepareAnalysis: vi.fn().mockResolvedValue(agentPreview),
      startAnalysis: vi.fn().mockResolvedValue({ run_id: 'run-agent-1', status: 'queued', stage: 'queued', claims: [], events: [] }),
      openAnalysis: vi.fn(), cancelAnalysis: vi.fn(),
    }
    render(<AnalysisWorkspaceView client={client} subjectId="NVDA" />)
    fireEvent.change(screen.getByLabelText('分析问题'), { target: { value: 'NVDA 当前持仓与近期走势？' } })
    fireEvent.click(screen.getByRole('button', { name: '生成外发预览' }))

    expect(await screen.findByText('DeepSeek Agent / deepseek-v4-flash')).toBeInTheDocument()
    expect(screen.getByText('服务端工具')).toBeInTheDocument()
    expect(screen.getByText(/查当前持仓/)).toBeInTheDocument()
    expect(screen.getByText(/查目标标的日 K 线/)).toBeInTheDocument()
    expect(screen.getByText(/查产业新闻/)).toBeInTheDocument()
    expect(screen.queryByText('冻结官方证据')).not.toBeInTheDocument()

    const start = screen.getByRole('button', { name: '确认外发并开始分析' })
    expect(start).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: /确认 preview/ }))
    expect(start).toBeEnabled()
    fireEvent.click(start)

    await waitFor(() => expect(client.startAnalysis).toHaveBeenCalledWith(expect.objectContaining({
      draftId: 'draft-agent-1', previewSha256: 'b'.repeat(64),
    })))
    expect(await screen.findByText('QUEUED · 已入队')).toBeInTheDocument()
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

    expect(await screen.findAllByText(/已确认事实/)).toHaveLength(1)
    expect(screen.getAllByText(/◇ 推断/)).toHaveLength(1)
    expect(screen.getAllByText(/条件情景/)).toHaveLength(1)
    expect(screen.getAllByText(/未知项/)).toHaveLength(1)
    expect(screen.getAllByText('未来两个季度')).toHaveLength(4)
    expect(screen.getByText('反对证据 macro-1')).toBeInTheDocument()
    expect(screen.getAllByText('失效：新增官方披露')).toHaveLength(4)
  })

})
