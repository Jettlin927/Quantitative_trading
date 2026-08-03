import { useEffect, useState } from 'react'
import { AlertTriangle, Ban, Check, CircleDot, Clock3, FileClock, Play, Send, ShieldCheck, Square } from 'lucide-react'


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

export function AnalysisWorkspaceView({ client, subjectId, initialRunId = '' }) {
  const [question, setQuestion] = useState('')
  const [preview, setPreview] = useState(null)
  const [confirmed, setConfirmed] = useState(false)
  const [run, setRun] = useState(null)
  const [busy, setBusy] = useState(Boolean(initialRunId))
  const [error, setError] = useState(null)
  const [acceptedClaimIds, setAcceptedClaimIds] = useState([])
  const [userSupplement, setUserSupplement] = useState('')
  const [savedRecord, setSavedRecord] = useState(null)

  useEffect(() => {
    if (!initialRunId) return undefined
    const controller = new AbortController()
    client.openAnalysis(initialRunId, { signal: controller.signal })
      .then((value) => {
        setRun(value)
        setAcceptedClaimIds(saveableClaimIds(value))
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
        subjectIds: [subjectId],
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
      setAcceptedClaimIds(saveableClaimIds(value))
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

  async function saveRecord() {
    setBusy(true)
    setError(null)
    try {
      setSavedRecord(await client.commitRecord({
        command: {
          type: 'save_analysis',
          analysis_id: run.run_id,
          accepted_claim_ids: acceptedClaimIds,
          user_supplement: userSupplement,
          private_fragments: [],
          verification_drafts: [],
        },
        idempotencyKey: crypto.randomUUID(),
      }))
    } catch (reason) {
      setError(reason)
    } finally {
      setBusy(false)
    }
  }

  const providerUnavailable = error?.code === 'provider_unavailable'
  return (
    <section className="analysis-workspace enter" aria-label="AI 影响分析">
      <header className="analysis-command-header">
        <div><span>AI IMPACT / READ-ONLY EVIDENCE</span><h2>可审计影响分析</h2></div>
        <b><ShieldCheck size={15} /> 先预览，后外发</b>
      </header>

      {!initialRunId ? <div className="analysis-question-panel">
        <label htmlFor="analysis-question">分析问题</label>
        <textarea
          id="analysis-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="只问传导机制、证据边界与未知项，不生成交易建议。"
          rows={4}
        />
        <div><small>上下文标的 {subjectId} · 浏览器不上传行情、组合权重或规则结果</small><button className="primary-action" disabled={busy || !question.trim()} onClick={prepare}><Send size={15} />生成外发预览</button></div>
      </div> : null}

      {preview ? <section className="analysis-preview" aria-label="外发预览">
        <header><div><span>PREVIEW HASH</span><code>{preview.preview_sha256.slice(0, 16)}…</code></div><strong>{preview.provider} / {preview.model}</strong></header>
        <div className="analysis-preview-meta">
          <span><Clock3 size={14} />过期 {new Date(preview.expires_at).toLocaleString('zh-CN')}</span>
          <span>估算 ${preview.estimated_cost_usd}</span>
          <span>{preview.retention}</span>
        </div>
        <div className="preview-columns">
          <div><h3>允许外发字段</h3><ul>{preview.included_fields.map((field) => <li key={field}><Check size={14} />{field}</li>)}</ul></div>
          <div><h3>强制排除字段</h3><ul>{preview.excluded_fields.map((item) => <li key={item.field}><Ban size={14} /><span>{item.field}<small>{item.reason_code}</small></span></li>)}</ul></div>
          <div><h3>仍缺证据</h3><ul>{preview.gaps.length ? preview.gaps.map((gap) => <li key={gap}><AlertTriangle size={14} />{gap}</li>) : <li><Check size={14} />未识别缺口</li>}</ul></div>
        </div>
        <div className="analysis-confirm-row">
          <label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />确认 preview {preview.preview_sha256.slice(0, 8)} 的 provider、字段、排除项、保留政策与费用</label>
          <button className="primary-action" disabled={!confirmed || busy || Boolean(run)} onClick={start}><Play size={15} />确认外发并开始分析</button>
        </div>
      </section> : null}

      {providerUnavailable ? <div className="analysis-degraded" role="status"><AlertTriangle size={18} /><span><strong>Provider 不可用</strong>本次未入队且不会自动切换模型；行情、规则与证据检查仍可继续使用。</span></div> : null}
      {error && !providerUnavailable ? <div className="notice error"><AlertTriangle size={16} /><span><b>{error.code || 'personal_request_failed'}</b><small>{error.message}</small></span></div> : null}

      {run ? <section className="analysis-run" aria-label="分析运行">
        <header><div><span>RUN {run.run_id?.slice(0, 8)}</span><h3>{RUN_IDENTITIES[run.status] || run.status}</h3></div>{run.cancellable ? <button disabled={busy} onClick={cancel}><Square size={14} />取消</button> : null}</header>
        <ol className="analysis-stage-line">{(run.events || []).map((event) => <li key={event.sequence}><CircleDot size={13} /><span>{event.stage}</span></li>)}</ol>
        {run.failure_code ? <p className="analysis-failure"><AlertTriangle size={15} />{run.failure_code} · 失败输出不能保存</p> : null}
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
        {(run.status === 'completed' || run.status === 'evidence_insufficient') && !savedRecord ? <section className="record-save-panel" aria-label="保存个人记录">
          <header><div><span>EXPLICIT SAVE</span><h3>六类确认卡</h3></div><b><FileClock size={15} />不会自动进入正式研究</b></header>
          <div className="record-save-cards">
            {Object.entries(CLAIM_IDENTITIES).map(([kind, identity]) => {
              const claims = run.claims.filter((claim) => claim.kind === kind)
              return <article key={kind}><b>{identity.mark} {identity.label}</b>{claims.length ? claims.map((claim) => <label key={claim.claim_id}><input type="checkbox" checked={acceptedClaimIds.includes(claim.claim_id)} onChange={(event) => setAcceptedClaimIds((current) => event.target.checked ? [...current, claim.claim_id] : current.filter((id) => id !== claim.claim_id))} />{claim.claim_id.slice(0, 8)}</label>) : <small>本次为空</small>}</article>
            })}
            <article><b>+ 用户补充</b><small>{userSupplement.trim() ? '将保存' : '本次为空'}</small></article>
            <article><b>○ 待验证事项</b><small>可在记录页追加</small></article>
          </div>
          <label className="record-supplement">用户补充<textarea value={userSupplement} onChange={(event) => setUserSupplement(event.target.value)} rows={3} placeholder="只补充你的判断边界；精确持仓值需单独进入私有片段。" /></label>
          <button className="primary-action" disabled={busy || !acceptedClaimIds.length} onClick={saveRecord}><FileClock size={15} />保存所选主张为个人记录</button>
        </section> : null}
        {savedRecord ? <div className="record-saved" role="status"><Check size={17} /><span><strong>已保存个人记录 v{savedRecord.current_version}</strong>{savedRecord.record_id} · 个人记录不等于正式研究结论。</span></div> : null}
      </section> : null}
    </section>
  )
}

function saveableClaimIds(run) {
  if (run?.status !== 'completed' && run?.status !== 'evidence_insufficient') return []
  const allowedKinds = run.status === 'evidence_insufficient' ? new Set(['confirmed_fact', 'unknown']) : null
  return (run.claims || []).filter((claim) => !allowedKinds || allowedKinds.has(claim.kind)).map((claim) => claim.claim_id)
}
