import { useEffect, useState } from 'react'
import { AlertTriangle, Check, Circle, Triangle, X } from 'lucide-react'

const STATE_LABELS = {
  draft: 'DRAFT · 草稿',
  enabled: 'ENABLED · 已启用',
  paused: 'PAUSED · 已暂停',
  archived: 'ARCHIVED · 已归档',
}
const RESULT_LABELS = {
  hit: ['命中 ◆', Check],
  not_hit: ['未命中 ○', Circle],
  insufficient_data: ['数据不足 △', Triangle],
  calculation_failed: ['计算失败 ×', X],
}

export function RulesView({ client }) {
  const [templates, setTemplates] = useState([])
  const [rules, setRules] = useState([])
  const [error, setError] = useState(null)
  const [symbol, setSymbol] = useState('')
  const [templateId, setTemplateId] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      client.listRuleTemplates({ signal: controller.signal }),
      client.openRules({ signal: controller.signal }),
    ]).then(([templateValues, workspace]) => {
      setTemplates(templateValues)
      setTemplateId(templateValues[0]?.template_id || '')
      setRules(workspace.rules)
    }).catch((reason) => reason?.name !== 'AbortError' && setError(reason))
    return () => controller.abort()
  }, [client])

  async function changeState(rule, state) {
    try {
      const updated = await client.submitRuleCommand({
        command: { type: 'set_rule_state', rule_id: rule.rule_id, expected_revision: rule.revision, state },
        idempotencyKey: crypto.randomUUID(),
      })
      setRules((current) => current.map((item) => item.rule_id === updated.rule_id ? updated : item))
    } catch (reason) {
      setError(reason)
    }
  }

  async function createRule(event) {
    event.preventDefault()
    const template = templates.find((item) => item.template_id === templateId)
    if (!template) return
    try {
      const created = await client.submitRuleCommand({
        command: { type: 'create_rule', template_id: template.template_id, symbol, parameters: template.default_parameters },
        idempotencyKey: crypto.randomUUID(),
      })
      setRules((current) => [...current, created])
      setSymbol('')
    } catch (reason) {
      setError(reason)
    }
  }

  return <div className="rules-workspace enter">
    <section className="rules-summary"><div><span>DETERMINISTIC OBSERVATION</span><h2>观察规则登记簿</h2><p>只生成个人注意事项；不输出买卖评级或正式研究结论。</p></div><strong>模板 {templates.length} 套</strong></section>
    {error ? <div className="notice error"><AlertTriangle size={16} /><span>{error.message}</span></div> : null}
    <form className="rule-create" onSubmit={createRule}>
      <label>标的代码<input value={symbol} onChange={(event) => setSymbol(event.target.value.toUpperCase())} maxLength={15} placeholder="例如 ACME" required /></label>
      <label>规则模板<select value={templateId} onChange={(event) => setTemplateId(event.target.value)}>{templates.map((template) => <option key={template.template_id} value={template.template_id}>{template.name}</option>)}</select></label>
      <button className="primary-action" disabled={!templateId}>创建草稿</button>
      <small>使用模板默认参数创建；创建后仍需单独确认启用。</small>
    </form>
    <section className="template-strip" aria-label="观察规则模板">{templates.map((template) => <article key={template.template_id}><small>v{template.version}</small><strong>{template.name}</strong><p>{template.description}</p><em>非正式研究</em></article>)}</section>
    <section className="personal-panel"><header><div><span>RULE INSTANCES</span><h2>我的规则</h2></div><b>显式确认后启用</b></header>
      <div className="rule-register">{rules.length ? rules.map((rule) => {
        const result = rule.latest_evaluation ? RESULT_LABELS[rule.latest_evaluation.result] : null
        const ResultIcon = result?.[1]
        return <article key={rule.rule_id}><div><small>{rule.template_id}@{rule.template_version}</small><h3>{rule.symbol}</h3><span>{STATE_LABELS[rule.state]}</span></div><code>revision {rule.revision}</code>{result ? <strong className="rule-result"><ResultIcon size={16} />{result[0]}</strong> : <em>尚未评估</em>}<div className="rule-actions">{rule.state === 'draft' ? <button className="primary-action" onClick={() => changeState(rule, 'enabled')} aria-label={`确认启用 ${rule.symbol}`}>确认启用</button> : null}{rule.state === 'enabled' ? <button onClick={() => changeState(rule, 'paused')}>暂停</button> : null}{rule.state === 'paused' ? <button onClick={() => changeState(rule, 'enabled')}>重新启用</button> : null}{rule.state !== 'archived' ? <button onClick={() => changeState(rule, 'archived')}>归档</button> : null}</div></article>
      }) : <p>尚未创建个人观察规则。</p>}</div>
    </section>
  </div>
}
