import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Database, Layers3 } from 'lucide-react'

import { MarketChart } from './MarketChart.jsx'

const TRACK_LABELS = {
  corporate: '公司事件',
  macro: '宏观事件',
  data_gap: '数据缺口',
  personal_rule: '个人规则',
}

export function InstrumentWorkspaceView({ client, symbol, chartAdapter }) {
  const [workspace, setWorkspace] = useState(null)
  const [series, setSeries] = useState('raw')
  const [error, setError] = useState(null)

  useEffect(() => {
    const controller = new AbortController()
    client.openInstrument(symbol, { signal: controller.signal })
      .then(setWorkspace)
      .catch((reason) => reason?.name !== 'AbortError' && setError(reason))
    return () => controller.abort()
  }, [client, symbol])

  const bars = useMemo(() => {
    const values = series === 'adjusted'
      ? workspace?.provider_adjusted_bars
      : workspace?.raw_bars
    return values || []
  }, [series, workspace])

  if (error) return <section className="personal-empty"><AlertTriangle /><h2>标的工作台读取失败</h2><p>{error.message}</p></section>
  if (!workspace) return <section className="personal-empty"><Database /><h2>正在读取标的证据</h2></section>

  return <div className="instrument-workspace enter">
    <section className="instrument-head">
      <div><span>INSTRUMENT / {workspace.identity.asset_class}</span><h2>{workspace.identity.symbol} · {workspace.identity.name}</h2></div>
      <div className="series-toggle" aria-label="价格序列">
        <button aria-pressed={series === 'raw'} onClick={() => setSeries('raw')}>Raw</button>
        <button aria-pressed={series === 'adjusted'} onClick={() => setSeries('adjusted')}>Provider adjusted</button>
      </div>
    </section>

    <section className="personal-panel chart-first">
      <header><div><span>01 / PRICE CANVAS</span><h2>日 K 主画布</h2></div><b>{workspace.evidence_inspector.source_health}</b></header>
      <MarketChart bars={bars} chartAdapter={chartAdapter} />
      <div className="cost-reference"><strong>当前成本参考线</strong><span>{workspace.cost_reference.value || '不可用'}</span><em>不是历史持仓轨迹</em></div>
    </section>

    <div className="instrument-lower-grid">
      <section className="personal-panel">
        <header><div><span>02 / EVENT RAILS</span><h2>独立事件轨</h2></div><b>不混入 K 线</b></header>
        <div className="event-rails">
          {workspace.event_tracks.length ? workspace.event_tracks.map((track) => <article key={track.track}><strong>{TRACK_LABELS[track.track] || track.track}</strong>{track.events.map((event) => <div key={event.event_id}><time>{event.occurred_at.slice(0, 10)}</time><span>{event.label}</span><small>{event.confirmation_state}</small></div>)}</article>) : <p>当前无事件；来源失败不会伪装为无事件。</p>}
        </div>
      </section>
      <section className="personal-panel">
        <header><div><span>03 / EVIDENCE</span><h2>证据检查器</h2></div><Database size={17} /></header>
        <dl className="evidence-list"><div><dt>选中日期</dt><dd>{workspace.evidence_inspector.selected_date || '-'}</dd></div><div><dt>证据身份</dt><dd>{workspace.evidence_inspector.evidence_ids.join(' · ') || '不可用'}</dd></div><div><dt>授权快照</dt><dd>{workspace.evidence_inspector.authorization_snapshot_ids.join(' · ') || '不可用'}</dd></div></dl>
        {workspace.evidence_inspector.issues.map((issue) => <p className="data-gap" key={issue}><AlertTriangle size={14} />{issue}</p>)}
      </section>
    </div>

    <section className="formal-overlay"><Layers3 size={16} /><div><strong>{workspace.formal_research_overlay.label}</strong><span>只读归一化叠加 · 不改变个人观察规则的研究资格</span></div><b>research_eligible = false</b></section>
  </div>
}
