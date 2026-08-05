import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Database } from 'lucide-react'

import { MarketChart } from './MarketChart.jsx'

const TRACK_LABELS = {
  corporate: '公司事件',
  macro: '宏观事件',
  data_gap: '数据缺口',
  personal_rule: '个人规则',
}

const ISSUE_LABELS = {
  asset_identity_unavailable: '资产名称暂时不可用',
  corporate_actions_unavailable: '公司行动来源读取失败',
  daily_bars_unavailable: '日线来源读取失败',
  official_events_unavailable: '官方 SEC / IR 事件来源读取失败',
  provider_adjusted_bars_unavailable: 'Provider adjusted 日线暂时不可用',
}

function eventSourceLabel(status) {
  if (status.source === 'alpaca_corporate_actions') {
    return status.availability === 'available'
      ? `公司行动来源正常，当前区间 ${status.event_count} 条`
      : '公司行动来源读取失败'
  }
  if (status.source === 'official_events') {
    if (status.availability === 'not_configured') return '官方 SEC / IR 事件尚未配置'
    return status.availability === 'available'
      ? `官方 SEC / IR 事件来源正常，当前区间 ${status.event_count} 条`
      : '官方 SEC / IR 事件来源读取失败'
  }
  return `${status.source} · ${status.availability}`
}

export function InstrumentWorkspaceView({ client, symbol, chartAdapter = undefined, onNavigate = (_path) => {} }) {
  const [workspace, setWorkspace] = useState(null)
  const [series, setSeries] = useState('raw')
  const [error, setError] = useState(null)
  const [holdings, setHoldings] = useState(null)
  const [requestedSymbol, setRequestedSymbol] = useState(symbol || '')

  useEffect(() => {
    if (typeof client.openPortfolio !== 'function') return undefined
    const controller = new AbortController()
    client.openPortfolio({ signal: controller.signal })
      .then((portfolio) => setHoldings((portfolio.holdings || []).filter((holding) => holding.state === 'active')))
      .catch((reason) => reason?.name !== 'AbortError' && setHoldings([]))
    return () => controller.abort()
  }, [client])

  useEffect(() => {
    if (symbol || !holdings?.length) return
    onNavigate(`/markets/us/${encodeURIComponent(holdings[0].symbol)}`)
  }, [holdings, onNavigate, symbol])

  useEffect(() => {
    if (!symbol) return undefined
    const controller = new AbortController()
    client.openInstrument(symbol, { signal: controller.signal })
      .then(setWorkspace)
      .catch((reason) => reason?.name !== 'AbortError' && setError(reason))
    return () => controller.abort()
  }, [client, symbol])

  function openSymbol(event) {
    event.preventDefault()
    const normalized = requestedSymbol.trim().toUpperCase()
    if (normalized) onNavigate(`/markets/us/${encodeURIComponent(normalized)}`)
  }

  const bars = useMemo(() => {
    const values = series === 'adjusted'
      ? workspace?.provider_adjusted_bars
      : workspace?.raw_bars
    return values || []
  }, [series, workspace])

  if (!symbol) return <section className="personal-empty"><Database /><h2>选择一个真实标的</h2><p>{holdings === null ? '正在读取你的活跃持仓…' : holdings.length ? '正在打开首个活跃持仓。' : '当前没有活跃持仓，也可以直接输入代码。'}</p><form className="instrument-picker" onSubmit={openSymbol}><label htmlFor="instrument-symbol-empty">美股代码</label><input id="instrument-symbol-empty" value={requestedSymbol} onChange={(event) => setRequestedSymbol(event.target.value)} placeholder="例如 NET" /><button>打开标的</button></form></section>
  if (error) return <section className="personal-empty"><AlertTriangle /><h2>标的工作台读取失败</h2><p>{error.message}</p><form className="instrument-picker" onSubmit={openSymbol}><input aria-label="切换美股代码" value={requestedSymbol} onChange={(event) => setRequestedSymbol(event.target.value)} /><button>切换标的</button></form></section>
  if (!workspace) return <section className="personal-empty"><Database /><h2>正在读取标的证据</h2></section>

  return <div className="instrument-workspace enter">
    <section className="instrument-head">
      <div><span>INSTRUMENT / {workspace.identity.asset_class}</span><h2>{workspace.identity.symbol} · {workspace.identity.name}</h2></div>
      <form className="instrument-picker" onSubmit={openSymbol}>
        <label htmlFor="instrument-symbol">切换标的</label>
        {holdings?.length ? <select aria-label="从活跃持仓选择" value={holdings.some((holding) => holding.symbol === requestedSymbol) ? requestedSymbol : ''} onChange={(event) => event.target.value && onNavigate(`/markets/us/${encodeURIComponent(event.target.value)}`)}><option value="">活跃持仓</option>{holdings.map((holding) => <option value={holding.symbol} key={holding.holding_id}>{holding.symbol} · {holding.name}</option>)}</select> : null}
        <input id="instrument-symbol" value={requestedSymbol} onChange={(event) => setRequestedSymbol(event.target.value)} placeholder="代码" />
        <button>打开</button>
      </form>
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
          <div className="event-source-statuses">{(workspace.event_source_statuses || []).map((status) => <p data-availability={status.availability} key={status.source}>{status.availability === 'unavailable' ? <AlertTriangle size={14} /> : null}{eventSourceLabel(status)}</p>)}</div>
          {workspace.event_tracks.length ? workspace.event_tracks.map((track) => <article key={track.track}><strong>{TRACK_LABELS[track.track] || track.track}</strong>{track.events.map((event) => <div key={event.event_id}><time>{event.occurred_at.slice(0, 10)}</time><span>{event.label}</span><small>{event.confirmation_state}</small></div>)}</article>) : <p className="event-empty">当前区间没有已确认事件；各来源状态如上。</p>}
        </div>
      </section>
      <section className="personal-panel">
        <header><div><span>03 / EVIDENCE</span><h2>证据检查器</h2></div><Database size={17} /></header>
        <dl className="evidence-list"><div><dt>选中日期</dt><dd>{workspace.evidence_inspector.selected_date || '-'}</dd></div><div><dt>授权快照</dt><dd>{workspace.evidence_inspector.authorization_snapshot_ids.length ? `授权快照 ${workspace.evidence_inspector.authorization_snapshot_ids.length} 条` : '不可用'}{workspace.evidence_inspector.authorization_snapshot_ids.length ? <details><summary>原始 ID</summary><code>{workspace.evidence_inspector.authorization_snapshot_ids.join(' · ')}</code></details> : null}</dd></div></dl>
        <div className="evidence-items">{(workspace.evidence_inspector.items || []).length ? workspace.evidence_inspector.items.map((item) => <article key={item.evidence_id}><strong>{item.label}</strong><span>{item.dataset} · {item.observed_date} · {item.source_health}</span><details><summary>技术身份</summary><code>{item.evidence_id}</code></details></article>) : <p>所选日期暂无可检查证据。</p>}</div>
        {workspace.evidence_inspector.issues.map((issue) => <p className="data-gap" key={issue}><AlertTriangle size={14} />{ISSUE_LABELS[issue] || issue}</p>)}
      </section>
    </div>

  </div>
}
