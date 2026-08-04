import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ArrowRight, BriefcaseBusiness, Check, Circle, FlaskConical, ListChecks, Save, ShieldX, Triangle, X } from 'lucide-react'

import { MarketChart } from './MarketChart.jsx'

const RULE_STATES = {
  hit: { label: '命中 ◆', icon: Check },
  not_hit: { label: '未命中 ○', icon: Circle },
  insufficient_data: { label: '数据不足 △', icon: Triangle },
  calculation_failed: { label: '计算失败 ×', icon: X },
}

export function PersonalTodayView({ client, chartAdapter, onNavigate = () => {} }) {
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

  if (loading) return <section className="personal-empty"><FlaskConical size={22} /><h2>正在读取今日工作台</h2><p>汇总当前组合、行情覆盖和待验证事项。</p></section>
  const overview = <TodayOverview workspace={workspace} error={error} onNavigate={onNavigate} />
  if (!trace) return (
    <div className="personal-today enter">
      {overview}
      <section className="synthetic-lab-note">
        <FlaskConical size={18} />
        <div><strong>合成验收旅程已与真实工作台分开</strong><p>它只验证隐私和降级边界，不再占据每日入口。</p></div>
        {!error && !workspace?.portfolio ? <button onClick={createTrace}>创建合成测试旅程</button> : null}
      </section>
    </div>
  )

  const providerUnavailable = trace.issues?.includes('provider_unavailable')
  const record = savedRecord || workspace?.record
  return (
    <div className="personal-today enter">
      {overview}
      <section className="synthetic-ribbon" aria-label="合成数据边界">
        <span><FlaskConical size={16} /> SYNTHETIC TRACE / T0</span>
        <strong>所有持仓、行情、证据与模型均为虚构夹具</strong>
        <em>不可用于正式研究</em>
      </section>

      {workspace?.attention_items?.length ? <section className="personal-panel">
        <header><div><span>ATTENTION QUEUE</span><h2>今日注意事项</h2></div><b>规则命中与数据缺口</b></header>
        <div className="rule-state-grid">{workspace.attention_items.map((item) => <article className={`rule-state ${item.result}`} key={item.attention_id}><strong>{RULE_STATES[item.result]?.label || item.result}</strong><span>{item.symbol} · {item.label}</span><p>{item.reason_code}</p></article>)}</div>
      </section> : null}

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

function TodayOverview({ workspace, error, onNavigate }) {
  const portfolio = workspace?.portfolio
  const active = portfolio?.holdings?.filter((holding) => holding.state === 'active') || []
  const covered = active.filter((holding) => holding.market_value?.availability === 'available').length
  const attention = workspace?.attention_items || []
  const total = portfolio?.total_equity

  return <section className="today-overview" aria-label="今日组合总览">
    <header className="today-overview-head">
      <div><span>TODAY / PRIVATE RESEARCH DESK</span><h2>今天先看组合、数据缺口与待验证事项</h2><p>这里汇总需要你处理的事实；不会生成买卖评级或自动发起 AI。</p></div>
      <div className="today-overview-actions">
        <button className="primary-action" onClick={() => onNavigate('/portfolio')}><BriefcaseBusiness size={15} />查看全部持仓</button>
        <button onClick={() => onNavigate('/rules')}><ListChecks size={15} />查看观察规则</button>
      </div>
    </header>

    {error ? <div className="notice error"><AlertTriangle size={16} /><span><b>{error.code || 'personal_request_failed'}</b><small>{error.message}</small></span></div> : null}

    <div className="today-metric-grid">
      <article><span>组合总值</span><strong>{total?.availability === 'available' ? `${total.value} USD` : '待行情恢复'}</strong><small>{total?.as_of ? `as-of ${new Date(total.as_of).toLocaleString('zh-CN')}` : '手工事实仍可查看'}</small></article>
      <article><span>持仓范围</span><strong>{active.length} 个活跃持仓</strong><small>组合修订 R{portfolio?.portfolio_revision ?? 0}</small></article>
      <article><span>行情覆盖</span><strong>行情覆盖 {covered}/{active.length}</strong><small>{portfolio?.issues?.length ? portfolio.issues.join(' · ') : '当前无来源缺口'}</small></article>
      <article><span>今日事项</span><strong>{attention.length} 项</strong><small>规则命中与数据缺口分列</small></article>
    </div>

    {active.length ? <div className="today-holding-strip" aria-label="活跃持仓快捷入口">{active.map((holding) => <button key={holding.holding_id} onClick={() => onNavigate(`/markets/us/${encodeURIComponent(holding.symbol)}`)}><span>{holding.symbol}</span><small>{holding.name}</small><ArrowRight size={14} /></button>)}</div> : <p className="today-empty-note">尚无活跃持仓；先在“我的持仓”维护手工事实。</p>}

    {attention.length ? <section className="today-attention-ledger"><header><span>ATTENTION LEDGER</span><b>{attention.length}</b></header><div>{attention.map((item) => <article key={item.attention_id}><strong>{RULE_STATES[item.result]?.label || item.result}</strong><span>{item.symbol} · {item.label}</span><small>{item.reason_code}</small></article>)}</div></section> : null}
  </section>
}

export function PersonalPlaceholder({ title, description }) {
  return <section className="personal-empty"><h2>{title}</h2><p>{description}</p><small>T0 仅交付 synthetic trust slice；真实数据与写入将在后续票据启用。</small></section>
}
