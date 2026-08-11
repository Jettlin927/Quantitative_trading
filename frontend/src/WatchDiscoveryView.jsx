import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Archive, Bot, Eye, Plus, Search, Sparkles, X } from 'lucide-react'

import { AnalysisWorkspaceView } from './AnalysisWorkspaceView.jsx'

const REASONS = ['财报观察', '行业映射', '估值跟踪', '事件催化', '技术结构']
const TABS = [
  { id: 'followed', label: '自选', icon: Eye },
  { id: 'candidates', label: 'AI 候选', icon: Sparkles },
  { id: 'archived', label: '已归档', icon: Archive },
]

export function WatchDiscoveryView({ client, onNavigate = (_path) => {} }) {
  const [projection, setProjection] = useState(null)
  const [activeTab, setActiveTab] = useState('followed')
  const [loading, setLoading] = useState(true)
  const [writing, setWriting] = useState(false)
  const [error, setError] = useState(null)
  const [errorOperation, setErrorOperation] = useState(null)
  const [symbol, setSymbol] = useState('')
  const [reasons, setReasons] = useState([])
  const [customReason, setCustomReason] = useState('')
  const [analysisContext, setAnalysisContext] = useState(null)
  const mutationLock = useRef(false)

  useEffect(() => {
    const controller = new AbortController()
    client.openWatchlist({ signal: controller.signal })
      .then(setProjection)
      .catch((reason) => {
        if (reason?.name !== 'AbortError') {
          setErrorOperation('read')
          setError(reason)
        }
      })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [client])

  const items = useMemo(() => {
    if (activeTab === 'candidates') return projection?.active_candidates || []
    if (activeTab === 'archived') return projection?.archived_candidates || []
    return projection?.followed_items || []
  }, [activeTab, projection])

  function toggleReason(reason) {
    setReasons((current) => current.includes(reason) ? current.filter((item) => item !== reason) : [...current, reason])
  }

  function moveTabFocus(event, currentIndex) {
    const offsets = { ArrowLeft: -1, ArrowRight: 1 }
    if (!(event.key in offsets) && event.key !== 'Home' && event.key !== 'End') return
    event.preventDefault()
    const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? TABS.length - 1 : (currentIndex + offsets[event.key] + TABS.length) % TABS.length
    setActiveTab(TABS[nextIndex].id)
    event.currentTarget.parentElement?.querySelectorAll('[role="tab"]')[nextIndex]?.focus()
  }

  async function follow(event) {
    event.preventDefault()
    if (mutationLock.current || !projection || !symbol.trim() || (!reasons.length && !customReason.trim())) return
    mutationLock.current = true
    setWriting(true)
    setErrorOperation(null)
    setError(null)
    try {
      const next = await client.submitWatchlistCommand({
        command: {
          type: 'follow_symbol',
          symbol: symbol.trim().toUpperCase(),
          preset_reasons: reasons,
          custom_reason: customReason.trim() || null,
          expected_revision: projection?.revision || 0,
        },
        idempotencyKey: crypto.randomUUID(),
      })
      setProjection(next)
      setSymbol('')
      setReasons([])
      setCustomReason('')
      setActiveTab('followed')
    } catch (reason) {
      setErrorOperation('write')
      setError(reason)
    } finally {
      mutationLock.current = false
      setWriting(false)
    }
  }

  async function unfollow(item) {
    if (mutationLock.current || !projection) return
    mutationLock.current = true
    setWriting(true)
    setErrorOperation(null)
    setError(null)
    try {
      setProjection(await client.submitWatchlistCommand({
        command: { type: 'unfollow_symbol', symbol: item.symbol, expected_revision: projection.revision },
        idempotencyKey: crypto.randomUUID(),
      }))
    } catch (reason) {
      setErrorOperation('write')
      setError(reason)
    } finally {
      mutationLock.current = false
      setWriting(false)
    }
  }

  return <div className="watch-discovery enter">
    <header className="watch-hero">
      <div><span>WATCH & DISCOVERY / PRIVATE SCOPE</span><h2>关注与发现</h2><p>持仓、自选与 AI 候选可以并存；候选不会自动升级为自选。</p></div>
      <b>R{projection?.revision ?? 0} · 私有修订</b>
    </header>

    <form className="watch-addition" onSubmit={follow}>
      <label>新增自选<input aria-label="自选代码" value={symbol} onChange={(event) => setSymbol(event.target.value)} placeholder="例如 AMD" /></label>
      <fieldset><legend>关注原因（可多选）</legend>{REASONS.map((reason) => <label key={reason}><input type="checkbox" checked={reasons.includes(reason)} onChange={() => toggleReason(reason)} />{reason}</label>)}</fieldset>
      <label>自定义原因<input aria-label="自定义原因" value={customReason} onChange={(event) => setCustomReason(event.target.value)} placeholder="写下继续观察的条件" /></label>
      <button className="primary-action" disabled={loading || writing || !projection || !symbol.trim() || (!reasons.length && !customReason.trim())}><Plus size={15} />加入自选</button>
    </form>

    {error ? <div className="watch-status error" role="status"><AlertTriangle size={18} /><div><strong>{watchErrorTitle(error, errorOperation)}</strong><p>{error.message || error.code}</p></div></div> : null}
    {loading && !projection ? <div className="watch-status"><Search size={18} /><div><strong>正在读取私有关注投影</strong><p>浏览器不推导候选或持仓状态。</p></div></div> : null}

    {error && !projection ? null : <><div className="watch-tabs" role="tablist" aria-label="关注分类">{TABS.map(({ id, label, icon: Icon }, index) => <button id={`watch-tab-${id}`} role="tab" aria-controls="watch-tabpanel" aria-selected={activeTab === id} tabIndex={activeTab === id ? 0 : -1} className={activeTab === id ? 'active' : ''} key={id} onClick={() => setActiveTab(id)} onKeyDown={(event) => moveTabFocus(event, index)}><Icon size={15} />{label}</button>)}</div>
    <section id="watch-tabpanel" role="tabpanel" aria-labelledby={`watch-tab-${activeTab}`} className="watch-ledger">
      {items.length ? items.map((item) => <article key={`${activeTab}-${item.symbol}`}>
        <div className="watch-symbol"><span>{item.symbol}</span><StateMarks item={item} /></div>
        <div className="watch-reasons"><strong>{item.preset_reasons?.join(' · ') || (item.candidate_status ? '关系证据 + 近期事实' : '持仓自动关注')}</strong><p>{item.custom_reason || candidateNote(item)}</p></div>
        <CandidateEvidenceSummary item={item} />
        <div className="watch-actions"><button onClick={() => onNavigate(`/markets/us/${encodeURIComponent(item.symbol)}`)}>进入标的</button><button onClick={() => setAnalysisContext({ subjectId: item.symbol, question: `${item.symbol} 当前事实与证据可能通过什么机制影响公司？` })}><Bot size={14} />深度分析</button>{item.is_followed && !item.is_holding ? <button disabled={writing} onClick={() => unfollow(item)}><X size={14} />取消自选</button> : null}</div>
      </article>) : <div className="watch-empty"><Archive size={20} /><strong>{activeTab === 'followed' ? '尚无自选标的' : activeTab === 'candidates' ? '当前没有满足证据门槛的 AI 候选' : '没有已归档候选'}</strong><p>空状态不会被填充为推荐列表。</p></div>}
    </section></>}

    {analysisContext ? <div key={analysisContext.subjectId} className="context-analysis"><button className="context-close" onClick={() => setAnalysisContext(null)}><X size={14} />关闭上下文分析</button><AnalysisWorkspaceView client={client} subjectId={analysisContext.subjectId} initialQuestion={analysisContext.question} contextLabel={`候选 / ${analysisContext.subjectId}`} /></div> : null}
  </div>
}

export function CandidateEvidenceSummary({ item, compact = false }) {
  const relation = item.relation_evidence || []
  const facts = item.fact_evidence || []
  const relationCount = item.relation_evidence_ids?.length || 0
  const factCount = item.fact_evidence_ids?.length || 0
  if (!relationCount && !factCount) return <div className="watch-evidence evidence-empty"><small>尚无候选证据</small></div>
  return <div className={`watch-evidence ${compact ? 'compact' : ''}`} aria-label={`${item.symbol} 候选证据`}>
    <EvidenceGroup title="关系证据" count={relationCount} items={relation} compact={compact} />
    <EvidenceGroup title="关键事实证据 · 待核验" count={factCount} items={facts} compact={compact} />
  </div>
}

function EvidenceGroup({ title, count, items, compact }) {
  return <section className="candidate-evidence-group">
    <header><strong>{title}</strong><span>{items.length ? `${items.length} 条重点 / 共 ${count} 条` : `${count} 条`}</span></header>
    {items.length ? items.map((evidence) => <article key={evidence.evidence_id}>
      <strong>{evidence.title}</strong>
      {!compact ? <p>{evidence.summary}</p> : null}
      <footer><span>{evidence.source} · {formatTime(evidence.as_of)}</span>{evidence.url ? <a href={evidence.url} target="_blank" rel="noreferrer">查看原文</a> : null}</footer>
    </article>) : <p className="evidence-legacy-note">历史候选仅保留证据身份；等待下一次来源刷新补齐摘要。</p>}
  </section>
}

export function StateMarks({ item }) {
  return <span className="state-marks" aria-label={`${item.symbol} 状态`}>
    {item.is_holding ? <b>■ 持仓</b> : null}
    {item.is_followed ? <b>◎ 自选</b> : null}
    {item.candidate_status === 'active' ? <b>◇ AI 候选</b> : null}
    {item.candidate_status === 'archived' ? <b>□ 已归档</b> : null}
  </span>
}

function candidateNote(item) {
  if (item.candidate_status === 'archived') return `归档于 ${formatTime(item.candidate_archived_at)}`
  if (item.candidate_status === 'active') return `最近证据 ${formatTime(item.candidate_refreshed_at)}`
  return '尚未填写手工原因'
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString('zh-CN') : '时点不可用'
}

function watchErrorTitle(error, operation) {
  if (operation === 'write') return error.code === 'revision_conflict' ? '关注修订冲突，请刷新后重试' : '关注更新失败'
  return error.code === 'personal_access_denied' ? '来源未授权' : '关注投影读取失败'
}
