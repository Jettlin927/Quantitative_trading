import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Archive, CheckCircle2, FileClock, History, RotateCcw, SearchCheck, Trash2 } from 'lucide-react'


const CARD_MARKS = {
  confirmed_fact: '■',
  inference: '◇',
  conditional_scenario: '△',
  unknown: '?',
  user_supplement: '+',
  verification_item: '○',
}

export function RecordsView({ client, recordId = '' }) {
  const [records, setRecords] = useState([])
  const [selected, setSelected] = useState(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    const controller = new AbortController()
    client.openRecords({ signal: controller.signal })
      .then(async (list) => {
        setRecords(list)
        const targetId = recordId || list[0]?.record_id
        setSelected(targetId ? await client.openRecord(targetId, { signal: controller.signal }) : null)
      })
      .catch((reason) => {
        if (reason?.name !== 'AbortError') setError(reason)
      })
      .finally(() => setBusy(false))
    return () => controller.abort()
  }, [client, recordId])

  async function command(type) {
    if (!selected) return
    setBusy(true)
    setError(null)
    try {
      const next = await client.commitRecord({
        command: {
          type,
          record_id: selected.record_id,
          expected_version: selected.current_version,
        },
        idempotencyKey: crypto.randomUUID(),
      })
      setSelected(next)
      setRecords((current) => current.map((item) => item.record_id === next.record_id ? next : item))
    } catch (reason) {
      setError(reason)
    } finally {
      setBusy(false)
    }
  }

  const latest = selected?.versions?.[selected.versions.length - 1]
  const availableActions = useMemo(() => {
    if (!selected || selected.state === 'purged') return []
    if (selected.state === 'trashed') return [['restore', '恢复记录', RotateCcw]]
    return [
      ['start_reasoning_audit', '推理审计', SearchCheck],
      ...(selected.state === 'active' ? [['archive', '归档', Archive]] : [['restore', '恢复记录', RotateCcw]]),
      ['trash', '移入回收站', Trash2],
    ]
  }, [selected])

  return (
    <section className="records-workspace enter" aria-label="个人研究记录">
      <header className="records-header">
        <div><span>PRIVATE NOTEBOOK / APPEND ONLY</span><h1>个人研究记录</h1></div>
        <b><FileClock size={16} />不可变版本 · 非正式研究</b>
      </header>
      {error ? <div className="notice error"><AlertTriangle size={16} /><span><b>{error.code || 'personal_request_failed'}</b><small>{error.message}</small></span></div> : null}
      <div className="records-layout">
        <aside className="record-index" aria-label="记录列表">
          <header><span>RECORD INDEX</span><b>{records.length}</b></header>
          {records.length ? records.map((item) => (
            <button key={item.record_id} className={selected?.record_id === item.record_id ? 'active' : ''} onClick={() => client.openRecord(item.record_id).then(setSelected)}>
              <strong>{item.title}</strong><span>v{item.current_version} · {stateLabel(item.state)}</span>
            </button>
          )) : <p>{busy ? '读取中…' : '尚无已确认记录。AI 运行不会自动保存。'}</p>}
        </aside>
        <main className="record-detail">
          {selected ? <>
            <header className="record-title-row">
              <div><span>RECORD {selected.record_id.slice(0, 8)}</span><h2>{selected.title}</h2><small>个人记录 · 永不映射为正式研究结论</small></div>
              <div className="record-actions">{availableActions.map(([type, label, Icon]) => <button key={type} disabled={busy} onClick={() => command(type)}><Icon size={14} />{label}</button>)}</div>
            </header>
            {selected.redactions?.length ? <div className="record-tombstone" role="status"><AlertTriangle size={17} /><span><strong>源私有对象已删除</strong>精确值已遮蔽；备份副本最长在 {new Date(selected.backup_expires_at).toLocaleDateString('zh-CN')} 前自然过期。</span></div> : null}
            {latest ? <>
              <section className="confirmation-cards" aria-label="六类确认卡">
                {latest.cards.map((card) => <article key={card.kind} className={card.status}><b>{CARD_MARKS[card.kind]} {card.label}</b><span>{card.status === 'accepted' ? '已确认保存' : '本版本为空'}</span><small>{card.claim_ids.join(' · ') || '无主张 ID'}</small></article>)}
              </section>
              <section className="record-context"><span>CONTEXT</span><h3>{latest.question}</h3><p>分析 {latest.analysis_id} · evidence pack {latest.evidence_pack_identity} · {latest.config_revision}</p></section>
              <section className="record-claims" aria-label="已保存主张">
                {latest.claims.map((claim) => <article key={claim.claim_id}><header><b>{CARD_MARKS[claim.kind]} {claim.kind}</b><span>{claim.horizon}</span></header><p>{claim.statement}</p><small>证据 {claim.evidence_ids.join(' · ')} · 失效 {claim.invalidation_conditions.join('；')}</small></article>)}
              </section>
              {latest.reasoning_audit?.length ? <section className="reasoning-audit"><h3>推理审计</h3>{latest.reasoning_audit.map((item) => <p key={item}><CheckCircle2 size={14} />{item}</p>)}</section> : null}
            </> : null}
            <section className="verification-ledger"><header><h3>待验证闭环</h3><span>{selected.verification_items.length} 项</span></header>{selected.verification_items.length ? selected.verification_items.map((item) => <article key={item.item_id}><b>{item.state.toUpperCase()} · {item.question}</b><p>{item.target} · {item.source} · {item.criterion}</p><small>{item.observations.length ? `${item.observations.length} 条追加观察` : '尚无观察；不会改写原主张'}</small></article>) : <p>本记录尚无待验证事项。</p>}</section>
            <ol className="version-chain" aria-label="不可变版本链">{selected.versions.map((version) => <li key={version.version_id}><History size={14} /><span>v{version.version} · {version.derived_relation}</span><code>{version.content_sha256.slice(0, 12)}…</code></li>)}</ol>
          </> : <div className="record-empty"><FileClock size={24} /><h2>选择一条个人记录</h2><p>只有显式确认的主张会进入不可变版本链。</p></div>}
        </main>
      </div>
    </section>
  )
}

function stateLabel(state) {
  return { active: '当前', archived: '已归档', trashed: '回收站', purged: '已永久删除' }[state] || state
}
