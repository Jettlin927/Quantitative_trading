import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { RecordsView } from './RecordsView.jsx'


afterEach(() => cleanup())

const cards = [
  ['confirmed_fact', '已确认事实', 'accepted'],
  ['inference', '推断', 'accepted'],
  ['conditional_scenario', '条件情景', 'empty'],
  ['unknown', '未知项', 'empty'],
  ['user_supplement', '用户补充', 'accepted'],
  ['verification_item', '待验证事项', 'accepted'],
].map(([kind, label, status]) => ({ kind, label, status, claim_ids: status === 'accepted' && kind === 'confirmed_fact' ? ['claim-1'] : [] }))

const record = {
  record_id: 'record-1', analysis_id: 'run-1', state: 'active', current_version: 1,
  title: '官方事实如何影响公司？', synthetic: false, formal_research_eligible: false,
  created_at: '2026-08-03T07:00:00Z', updated_at: '2026-08-03T07:00:00Z',
  versions: [{
    version_id: 'version-1', version: 1, parent_version_id: null,
    derived_relation: 'saved_analysis', state: 'active', content_sha256: 'a'.repeat(64),
    as_of: '2026-08-03T07:00:00Z', created_at: '2026-08-03T07:00:00Z',
    analysis_id: 'run-1', evidence_pack_identity: 'draft-1', question: '官方事实如何影响公司？',
    config_revision: 'personal-impact-v1', cards, user_supplement: '用户边界', privacy_level: 'private',
    reasoning_audit: [], claims: [{
      claim_id: 'claim-1', kind: 'confirmed_fact', statement: '公司已披露事实。',
      evidence_ids: ['sec-1'], opposing_evidence_ids: [], assumptions: [], horizon: '当前',
      invalidation_conditions: ['官方更正'],
    }],
  }],
  verification_items: [{
    item_id: 'verify-1', claim_id: 'claim-1', state: 'pending', question: '下一份披露是否支持？',
    target: '公司指引', expected_at: null, source: 'SEC', criterion: '官方披露', observations: [],
  }],
  private_fragments: [], redactions: [], backup_status: null, backup_expires_at: null,
}

describe('个人研究记录', () => {
  it('展示六类确认卡、不可变版本和正式研究隔离', async () => {
    const client = {
      openRecords: vi.fn().mockResolvedValue([record]),
      openRecord: vi.fn().mockResolvedValue(record),
      commitRecord: vi.fn(),
    }
    render(<RecordsView client={client} />)

    expect(await screen.findByText('■ 已确认事实')).toBeInTheDocument()
    expect(screen.getByText('◇ 推断')).toBeInTheDocument()
    expect(screen.getByText('+ 用户补充')).toBeInTheDocument()
    expect(screen.getByText('○ 待验证事项')).toBeInTheDocument()
    expect(screen.getByText(/永不映射为正式研究结论/)).toBeInTheDocument()
    expect(screen.getByText('v1 · saved_analysis')).toBeInTheDocument()
    expect(screen.getByText('PENDING · 下一份披露是否支持？')).toBeInTheDocument()
  })

  it('推理审计通过封闭命令追加版本', async () => {
    const audited = {
      ...record,
      current_version: 2,
      versions: [...record.versions, {
        ...record.versions[0], version_id: 'version-2', version: 2,
        parent_version_id: 'version-1', derived_relation: 'reasoning_audit',
        reasoning_audit: ['confirmed_fact:证据已绑定:含失效条件'],
      }],
    }
    const client = {
      openRecords: vi.fn().mockResolvedValue([record]),
      openRecord: vi.fn().mockResolvedValue(record),
      commitRecord: vi.fn().mockResolvedValue(audited),
    }
    render(<RecordsView client={client} />)
    const button = await screen.findByRole('button', { name: '推理审计' })
    fireEvent.click(button)

    await waitFor(() => expect(client.commitRecord).toHaveBeenCalledWith(expect.objectContaining({
      command: { type: 'start_reasoning_audit', record_id: 'record-1', expected_version: 1 },
    })))
    expect(await screen.findByText('confirmed_fact:证据已绑定:含失效条件')).toBeInTheDocument()
  })
})
