import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RulesView } from './RulesView.jsx'

describe('RulesView', () => {
  it('展示八模板、用户确认启用和非仅颜色的四态结果', async () => {
    const templates = Array.from({ length: 8 }, (_, index) => ({ template_id: `template-${index}`, version: 1, name: `模板 ${index + 1}`, description: '确定性本地计算', research_eligible: false, default_parameters: {} }))
    const rule = { rule_id: 'rule-1', template_id: 'template-0', template_version: 1, symbol: 'ACME', state: 'draft', revision: 1, parameters: {}, latest_evaluation: null }
    const client = {
      listRuleTemplates: vi.fn().mockResolvedValue(templates),
      openRules: vi.fn().mockResolvedValue({ rules: [rule], evaluations: [] }),
      submitRuleCommand: vi.fn(({ command }) => command.type === 'evaluate_rules'
        ? Promise.resolve({ symbol: 'ACME', status: 'completed', evaluations: [{ rule_id: 'rule-1', result: 'insufficient_data', reason_code: 'price_unavailable' }] })
        : Promise.resolve({ ...rule, state: 'enabled', revision: 2 })),
    }
    render(<RulesView client={client} />)

    expect(await screen.findByText('模板 8 套')).toBeTruthy()
    expect(screen.getByText('DRAFT · 草稿')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '确认启用 ACME' }))
    await waitFor(() => expect(client.submitRuleCommand).toHaveBeenCalledWith(expect.objectContaining({ command: expect.objectContaining({ type: 'set_rule_state', state: 'enabled' }) })))
    fireEvent.click(screen.getByRole('button', { name: '立即评估 ACME' }))
    await waitFor(() => expect(client.submitRuleCommand).toHaveBeenCalledWith(expect.objectContaining({ command: expect.objectContaining({ type: 'evaluate_rules', symbol: 'ACME' }) })))
    expect(await screen.findByText('数据不足 △')).toBeTruthy()
    expect(screen.getByText('price_unavailable')).toBeTruthy()
  })
})
