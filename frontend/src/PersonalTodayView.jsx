import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Check, Circle, FlaskConical, Save, ShieldX, Triangle, X } from 'lucide-react'

import { MarketChart } from './MarketChart.jsx'

const RULE_STATES = {
  hit: { label: '命中 ◆', icon: Check },
  not_hit: { label: '未命中 ○', icon: Circle },
  insufficient_data: { label: '数据不足 △', icon: Triangle },
  calculation_failed: { label: '计算失败 ×', icon: X },
}

export function PersonalTodayView({ client, chartAdapter }) {
  const [workspace, setWorkspace] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const [savedRecord, setSavedRecord] = useState(null)

  useEffect(() => {
    const controller = new AbortController()
    client.openToday({ signal: controller.signal })
      .then(setWorkspace)
      .catch((reason) => {
        if (reason?.name !== 'AbortError') setError(reason)
      })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [client])

  const trace = workspace?.trace ?? (workspace?.synthetic ? workspace : null)
  const bars = useMemo(() => (trace?.market?.bars || []).map((bar) => ({
    time: bar.date,
    open: Number(bar.open),
    high: Number(bar.high),
    low: Number(bar.low),
    close: Number(bar.close),
    volume: Number(bar.volume),
    amount: null,
    provenance: { kind: 'synthetic', label: '合成数据', source: 'SYNTH-T0', sample: true },
  })), [trace])

  async function createTrace() {
    setLoading(true)
    setError(null)
    try {
      const created = await client.createSyntheticTrace({
        question: '这个虚构事件可能通过什么机制影响合成标的？',
        idempotencyKey: crypto.randomUUID(),
      })
      setWorkspace({ trace: created, record: null })
    } catch (reason) {
      setError(reason)
    } finally {
      setLoading(false)
    }
  }

  async function saveRecord() {
    setSaving(true)
    setError(null)
    try {
      const record = await client.saveSyntheticRecord({
        analysisId: trace.analysis_id,
        previewSha256: trace.analysis_preview.preview_sha256,
        idempotencyKey: crypto.randomUUID(),
      })
      setSavedRecord(record)
    } catch (reason) {
      setError(reason)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <section className="personal-empty"><FlaskConical size={22} /><h2>正在读取合成工作台</h2><p>只读取当前会话，不写浏览器存储。</p></section>
  if (!trace) return (
    <section className="personal-empty">
      <FlaskConical size={24} />
      <h2>合成信任纵切尚未创建</h2>
      <p>{error?.code === 'personal_access_unconfigured' ? '私有网关尚未配置；公开研究与市场区域仍可使用。' : '使用虚构数据验证隐私、降级与显式保存边界。'}</p>
      {error ? <small>{error.code || 'personal_request_failed'} · {error.message}</small> : null}
      {error?.code !== 'personal_access_unconfigured' ? <button className="primary-action" onClick={createTrace}>启动合成旅程</button> : null}
    </section>
  )

  const providerUnavailable = trace.issues?.includes('provider_unavailable')
  const record = savedRecord || workspace?.record
  return (
    <div className="personal-today enter">
      <section className="synthetic-ribbon" aria-label="合成数据边界">
        <span><FlaskConical size={16} /> SYNTHETIC TRACE / T0</span>
        <strong>所有持仓、行情、证据与模型均为虚构夹具</strong>
        <em>不可用于正式研究</em>
      </section>

      <section className="personal-context">
        <div><span>当前标的</span><h2>{trace.holding.symbol}</h2><p>{trace.holding.name}</p></div>
        <dl>
          <div><dt>虚构数量</dt><dd>{trace.holding.quantity}</dd></div>
          <div><dt>虚构均价</dt><dd>{trace.holding.average_cost} {trace.holding.currency}</dd></div>
          <div><dt>市场来源</dt><dd>{trace.market.source_health === 'unavailable' ? '不可用 / 合成回放' : trace.market.source_health}</dd></div>
        </dl>
      </section>

      <section className="personal-panel chart-first">
        <header><div><span>01 / MARKET CANVAS</span><h2>日 K 主画布</h2></div><b>合成数据 · 非真实行情</b></header>
        <MarketChart bars={bars} chartAdapter={chartAdapter} />
      </section>

      {trace.analysis_claim ? <section className="personal-panel claim-summary">
        <header><div><span>02 / CLAIM SUMMARY</span><h2>合成主张摘要</h2></div><b>{trace.analysis_claim.kind === 'inference' ? '推断 ◇' : trace.analysis_claim.kind}</b></header>
        <blockquote>{trace.analysis_claim.statement}</blockquote>
        <footer>证据身份：{trace.analysis_claim.evidence_ids.join(' · ')}</footer>
      </section> : null}

      <section className="personal-panel">
        <header><div><span>03 / RULE STATES</span><h2>观察规则四态</h2></div><b>文字 + 形状双重身份</b></header>
        <div className="rule-state-grid">
          {trace.rule_evaluations.map((rule) => {
            const identity = RULE_STATES[rule.result] || RULE_STATES.calculation_failed
            const Icon = identity.icon
            return <article className={`rule-state ${rule.result}`} key={rule.rule_id}><Icon size={18} aria-hidden="true" /><strong>{identity.label}</strong><span>{rule.label}</span><p>{rule.reason}</p></article>
          })}
        </div>
      </section>

      <section className="personal-panel evidence-preview">
        <header><div><span>04 / AI EGRESS</span><h2>AI 外发排除预览</h2></div><b>{providerUnavailable ? 'Provider 不可用' : 'Synthetic provider'}</b></header>
        {providerUnavailable ? <div className="degraded-strip"><AlertTriangle size={17} /><span><strong>Provider 不可用</strong>确定性行情、规则与保存预览仍完整；不会自动切换模型。</span></div> : null}
        <div className="preview-columns">
          <div><h3>允许外发</h3><ul>{trace.analysis_preview.included_fields.map((field) => <li key={field}><Check size={14} />{field}</li>)}</ul></div>
          <div><h3>强制排除</h3><ul>{trace.analysis_preview.excluded_fields.map((item) => <li key={item.field}><ShieldX size={14} /><span>{item.field}<small>{item.reason_code}</small></span></li>)}</ul></div>
        </div>
        <footer><code>preview {trace.analysis_preview.preview_sha256.slice(0, 12)}…</code><span>{trace.analysis_preview.retention}</span></footer>
      </section>

      <section className="personal-panel followup-panel">
        <div><span>05 / FOLLOW-UP</span><h2>后续追问</h2><p>追问会创建新的 analysis ID，不修改当前主张或记录。</p></div>
        <button disabled title="Synthetic provider 当前不可用">Provider 恢复后可追问</button>
      </section>

      <section className="personal-panel save-panel">
        <div><span>06 / EXPLICIT COMMIT</span><h2>显式保存</h2><p>保存只提交 analysis ID 与当前 preview hash；服务端重新组装记录。</p></div>
        {record ? <strong className="saved-state"><Check size={16} />合成记录 v{record.version} 已保存</strong> : <button className="primary-action" disabled={saving} onClick={saveRecord}><Save size={15} />确认并保存合成记录</button>}
      </section>
      {error ? <div className="notice error"><AlertTriangle size={16} /><span><b>{error.code || 'personal_request_failed'}</b><small>{error.message}</small></span></div> : null}
    </div>
  )
}

export function PersonalPlaceholder({ title, description }) {
  return <section className="personal-empty"><h2>{title}</h2><p>{description}</p><small>T0 仅交付 synthetic trust slice；真实数据与写入将在后续票据启用。</small></section>
}
