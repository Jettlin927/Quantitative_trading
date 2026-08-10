import { useEffect, useState } from 'react'
import { AlertTriangle, Ban, Check, CircleDot, Clock3, Play, Send, ShieldCheck, Square } from 'lucide-react'


const CLAIM_IDENTITIES = {
  confirmed_fact: { label: '已确认事实', mark: '■' },
  inference: { label: '推断', mark: '◇' },
  conditional_scenario: { label: '条件情景', mark: '△' },
  unknown: { label: '未知项', mark: '?' },
}

const RUN_IDENTITIES = {
  queued: 'QUEUED · 已入队',
  running: 'RUNNING · 分析中',
  completed: 'COMPLETED · 已完成',
  failed: 'FAILED · 未保存主张',
  cancelled: 'CANCELLED · 已取消',
}

const FAILURE_IDENTITIES = {
  provider_disabled: '生产配置主动停用',
  provider_auth_failed: 'API Key 认证失败',
  provider_balance_unavailable: '账户余额不足',
  provider_model_unavailable: '模型不可用',
  provider_rate_limited: '官方接口限流',
  provider_upstream_error: '官方接口 5xx',
  provider_timeout: '官方接口超时',
  provider_request_unsafe: '本地请求安全校验失败',
  provider_claims_invalid_schema: '官方响应结构不合格',
  provider_response_invalid_json: '官方响应不是合法 JSON',
}

const PROVIDER_CALL_LABELS = {
  not_started: '未外发',
  started: '已外发，等待结果',
  completed: '已外发并完成',
  outcome_unknown: '已外发，结果未知',
  legacy_unknown: '旧记录未保存外发状态',
}

const PROVIDER_LABELS = {
  'deepseek-agent': 'DeepSeek Agent',
  deepseek: 'DeepSeek',
}

const TOOL_LABELS = {
  get_holdings: { label: '查当前持仓', mark: '◈' },
  get_kline: { label: '查目标标的日 K 线', mark: '▤' },
  get_news: { label: '查产业新闻', mark: '✦' },
}

export function AnalysisWorkspaceView({ client, subjectId, initialRunId = '', initialQuestion = '', contextLabel = '' }) {
  const [question, setQuestion] = useState(initialQuestion)
  const [preview, setPreview] = useState(null)
  const [confirmed, setConfirmed] = useState(false)
  const [run, setRun] = useState(null)
  const [busy, setBusy] = useState(Boolean(initialRunId))
  const [error, setError] = useState(null)
  const [capability, setCapability] = useState(null)
  const [subjects, setSubjects] = useState([])
  const [selectedSubject, setSelectedSubject] = useState(subjectId)
  const [history, setHistory] = useState([])

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      typeof client.openPortfolio === 'function' ? client.openPortfolio({ signal: controller.signal }) : Promise.resolve(null),
      typeof client.listAnalysisCapabilities === 'function' ? client.listAnalysisCapabilities({ signal: controller.signal }) : Promise.resolve(null),
      typeof client.listAnalyses === 'function' ? client.listAnalyses({ signal: controller.signal }) : Promise.resolve([]),
    ]).then(([portfolio, providerCapability, runs]) => {
      const active = (portfolio?.holdings || []).filter((holding) => holding.state === 'active')
      if (subjectId && !subjectId.startsWith('SYNTH') && !active.some((holding) => holding.symbol === subjectId)) active.unshift({ holding_id: `context-${subjectId}`, symbol: subjectId, name: '上下文标的', state: 'context' })
      setSubjects(active)
      if (subjectId && !subjectId.startsWith('SYNTH')) setSelectedSubject(subjectId)
      else if (active.length) setSelectedSubject(active[0].symbol)
      setCapability(providerCapability)
      setHistory(runs || [])
    }).catch((reason) => {
      if (reason?.name !== 'AbortError') setError(reason)
    })
    return () => controller.abort()
  }, [client, subjectId])

  useEffect(() => {
    if (!initialRunId) return undefined
    const controller = new AbortController()
    client.openAnalysis(initialRunId, { signal: controller.signal })
      .then((value) => {
        setRun(value)
      })
      .catch((reason) => {
        if (reason?.name !== 'AbortError') setError(reason)
      })
      .finally(() => setBusy(false))
    return () => controller.abort()
  }, [client, initialRunId])

  async function prepare() {
    setBusy(true)
    setError(null)
    setConfirmed(false)
    setRun(null)
    try {
      setPreview(await client.prepareAnalysis({
        question,
        subjectIds: [selectedSubject],
        selectedPrivateFields: [],
        idempotencyKey: crypto.randomUUID(),
      }))
    } catch (reason) {
      setError(reason)
    } finally {
      setBusy(false)
    }
  }

  async function start() {
    setBusy(true)
    setError(null)
    try {
      const value = await client.startAnalysis({
        draftId: preview.draft_id,
        previewSha256: preview.preview_sha256,
        idempotencyKey: crypto.randomUUID(),
      })
      setRun(value)
    } catch (reason) {
      setError(reason)
    } finally {
      setBusy(false)
    }
  }

  async function cancel() {
    setBusy(true)
    setError(null)
    try {
      setRun(await client.cancelAnalysis({
        runId: run.run_id,
        idempotencyKey: crypto.randomUUID(),
      }))
    } catch (reason) {
      setError(reason)
    } finally {
      setBusy(false)
    }
  }

  const providerUnavailable = error?.code === 'provider_unavailable'
  const dispatchEnabled = capability?.dispatch_enabled !== false
  const disabledByConfig = capability?.reason_code === 'provider_disabled'
  const agentMode = capability?.analysis_mode === 'agent' || preview?.provider === 'deepseek-agent'
  const agentTools = (preview?.included_fields || []).filter((field) => field !== 'user_question')
  return (
    <section className="analysis-workspace enter" aria-label="AI 影响分析">
      <header className="analysis-command-header">
        <div><span>AI IMPACT / {contextLabel || (agentMode ? 'TOOL-USE AGENT' : 'READ-ONLY EVIDENCE')}</span><h2>可审计影响分析</h2></div>
        <b><ShieldCheck size={15} /> 先预览，后外发</b>
      </header>

      {capability ? <div className={`analysis-capability ${dispatchEnabled ? 'enabled' : 'disabled'}`} role="status"><ShieldCheck size={18} /><div><strong>{dispatchEnabled ? `DeepSeek 可外发 · ${capability.model}${agentMode ? ' · 工具模式' : ''}` : disabledByConfig ? 'DeepSeek 由生产配置主动停用' : FAILURE_IDENTITIES[capability.reason_code] || capability.reason_code}</strong><p>{disabledByConfig ? '这是配置门禁，不是 Key 校验失败；Key 只对分析 Worker 可见，API 无权读取。历史运行仍可查看。' : dispatchEnabled ? (agentMode ? 'agent 在服务端按需调用持仓 / K线 / 新闻工具后产出结构化影响分析；仍须先生成预览并逐次确认外发。' : '仍须先生成预览并逐次确认外发。') : '当前不会入队、重试或自动切换模型。'}</p></div></div> : null}

      {!initialRunId ? <div className="analysis-question-panel">
        <label htmlFor="analysis-subject">分析标的</label>
        <select id="analysis-subject" aria-label="分析标的" value={selectedSubject} onChange={(event) => setSelectedSubject(event.target.value)}>{subjects.length ? subjects.map((holding) => <option key={holding.holding_id} value={holding.symbol}>{holding.symbol} · {holding.name}</option>) : <option value={selectedSubject}>{selectedSubject}</option>}</select>
        <label htmlFor="analysis-question">分析问题</label>
        <textarea
          id="analysis-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="只问传导机制、证据边界与未知项，不生成交易建议。"
          rows={4}
        />
        <div><small>{agentMode ? `上下文标的 ${selectedSubject} · 分析在服务端执行，agent 按需调用持仓/K线/新闻工具，浏览器不上传私有字段` : `上下文标的 ${selectedSubject} · 浏览器不上传行情、组合权重或规则结果`}</small><button className="primary-action" disabled={busy || !question.trim() || !dispatchEnabled} onClick={prepare}><Send size={15} />生成外发预览</button></div>
      </div> : null}

      {history.length ? <section className="analysis-history" aria-label="个人分析运行历史"><header><span>RUN HISTORY</span><h3>最近个人分析</h3></header><div>{history.map((item) => <button key={item.run_id} onClick={() => setRun(item)}><strong>{RUN_IDENTITIES[item.status] || item.status}</strong><code>{item.run_id}</code>{item.question ? <span>{(item.subject_ids || []).join(' · ')} · {item.question}</span> : null}<small>{item.model} · {item.actual_cost_usd ? `$${item.actual_cost_usd}` : item.provider_call_state === 'not_started' ? '未外发、未计费' : '费用未知'}{item.failure_code ? ` · ${FAILURE_IDENTITIES[item.failure_code] || item.failure_code}` : ''}</small></button>)}</div></section> : null}

      {preview ? <section className="analysis-preview" aria-label="外发预览">
        <header><div><span>PREVIEW HASH</span><code>{preview.preview_sha256.slice(0, 16)}…</code></div><strong>{PROVIDER_LABELS[preview.provider] || preview.provider} / {preview.model}</strong></header>
        <div className="analysis-preview-meta">
          <span><Clock3 size={14} />过期 {new Date(preview.expires_at).toLocaleString('zh-CN')}</span>
          <span>估算 ${preview.estimated_cost_usd}</span>
          <span>{preview.retention}</span>
        </div>
        <div className="preview-columns">
          <div><h3>{agentMode ? '外发内容' : '允许外发字段'}</h3><ul>{preview.included_fields.map((field) => <li key={field}><Check size={14} />{field}</li>)}</ul></div>
          <div><h3>强制排除字段</h3><ul>{preview.excluded_fields.map((item) => <li key={item.field}><Ban size={14} /><span>{item.field}<small>{item.reason_code}</small></span></li>)}</ul></div>
          <div><h3>仍缺证据</h3><ul>{preview.gaps.length ? preview.gaps.map((gap) => <li key={gap}><AlertTriangle size={14} />{gap}</li>) : <li><Check size={14} />未识别缺口</li>}</ul></div>
        </div>
        {agentMode ? <div className="analysis-preview-evidence" aria-label="服务端工具">
          <h3>服务端工具</h3>
          <ul>{agentTools.length ? agentTools.map((tool) => {
            const identity = TOOL_LABELS[tool] || { label: tool, mark: '⚙' }
            return <li key={tool}><Check size={14} />{identity.mark} {identity.label}<small>{tool} · 数据由服务端在确认后按需获取</small></li>
          }) : <li><AlertTriangle size={14} />未配置任何工具</li>}</ul>
        </div> : <div className="analysis-preview-evidence">
          <h3>冻结官方证据</h3>
          <ul>{(preview.evidence || []).length ? preview.evidence.map((item) => <li key={item.evidence_id}><Check size={14} />{item.evidence_id} · {item.source} · {item.field} · as-of {new Date(item.as_of).toLocaleString('zh-CN')}</li>) : <li><AlertTriangle size={14} />没有可外发的合格官方证据</li>}</ul>
        </div>}
        <div className="analysis-confirm-row">
          <label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />确认 preview {preview.preview_sha256.slice(0, 8)} 的 provider、字段、排除项、保留政策与费用</label>
          <button className="primary-action" disabled={!confirmed || busy || Boolean(run) || preview.gaps.length > 0 || (!agentMode && !(preview.evidence || []).length)} onClick={start}><Play size={15} />确认外发并开始分析</button>
        </div>
      </section> : null}

      {providerUnavailable ? <div className="analysis-degraded" role="status"><AlertTriangle size={18} /><span><strong>Provider 不可用</strong>本次未入队且不会自动切换模型；行情、规则与证据检查仍可继续使用。</span></div> : null}
      {error && !providerUnavailable ? <div className="notice error"><AlertTriangle size={16} /><span><b>{error.code || 'personal_request_failed'}</b><small>{error.message}</small></span></div> : null}

      {run ? <section className="analysis-run" aria-label="分析运行">
        <header><div><span>RUN {run.run_id?.slice(0, 8)}</span><h3>{RUN_IDENTITIES[run.status] || run.status}</h3></div>{run.cancellable ? <button disabled={busy} onClick={cancel}><Square size={14} />取消</button> : null}</header>
        <div className="analysis-run-audit">
          <p><b>分析问题</b>{run.question || '旧记录未保存问题文本'}</p>
          <p><b>分析标的</b>{(run.subject_ids || []).join(' · ') || '旧记录未保存标的'}</p>
          <p><b>外发状态</b>{PROVIDER_CALL_LABELS[run.provider_call_state] || run.provider_call_state || '旧记录未知'}</p>
          <p><b>费用口径</b>预留上界 ${run.estimated_cost_usd || '未知'} · 实际 {run.actual_cost_usd == null ? '未知' : `$${run.actual_cost_usd}`} · 账本 {run.accounted_cost_usd == null ? '未知' : `$${run.accounted_cost_usd}`}</p>
        </div>
        {(run.planned_tools || []).length ? <div className="analysis-tool-audit"><h4>工具与证据链</h4><ul>{run.planned_tools.map((tool) => {
          const identity = TOOL_LABELS[tool] || { label: tool, mark: '⚙' }
          const executions = (run.tool_events || []).filter((event) => event.tool_name === tool)
          return <li key={tool}><b>{identity.mark} {identity.label}</b>{executions.length ? executions.map((event) => <span key={`${event.sequence}-${event.tool_call_id}`}>{event.status === 'completed' ? '已完成' : `失败：${event.error_code || 'unknown'}`} · {(event.evidence_ids || []).join(' · ') || '无证据'}</span>) : <span>本次未调用</span>}</li>
        })}</ul>{(run.tool_evidence || []).length ? <details><summary>查看冻结工具证据</summary>{run.tool_evidence.map((item) => <article key={item.evidence_id}><code>{item.evidence_id}</code><small>{item.source} · {item.field}</small><p>{item.excerpt}</p></article>)}</details> : null}</div> : null}
        <ol className="analysis-stage-line">{(run.events || []).map((event) => <li key={event.sequence}><CircleDot size={13} /><span>{event.stage}</span></li>)}</ol>
        {run.failure_code ? <p className="analysis-failure"><AlertTriangle size={15} />{FAILURE_IDENTITIES[run.failure_code] || run.failure_code} · 失败输出不能保存</p> : null}
        {run.claims?.length ? <div className="claim-ledger">{run.claims.map((claim) => {
          const identity = CLAIM_IDENTITIES[claim.kind] || { label: claim.kind, mark: '·' }
          return <article className={`analysis-claim ${claim.kind}`} key={claim.claim_id}>
            <header><b>{identity.mark} {identity.label}</b><span>{claim.horizon}</span></header>
            <p>{claim.statement}</p>
            <div className="claim-links"><span>证据 {claim.evidence_ids.join(' · ') || '无'}</span>{claim.opposing_evidence_ids.map((id) => <span key={id}>反对证据 {id}</span>)}</div>
            {claim.assumptions.length ? <small>假设：{claim.assumptions.join('；')}</small> : null}
            <small>失效：{claim.invalidation_conditions.join('；')}</small>
          </article>
        })}</div> : null}
      </section> : null}
    </section>
  )
}
