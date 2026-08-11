import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ArrowRight, Bot, BriefcaseBusiness, Check, Circle, Clock3, FlaskConical, ShieldX, Triangle, X } from 'lucide-react'

import { AnalysisWorkspaceView } from './AnalysisWorkspaceView.jsx'
import { MarketChart } from './MarketChart.jsx'
import { CandidateEvidenceSummary, StateMarks } from './WatchDiscoveryView.jsx'

const RULE_STATES = {
  hit: { label: '命中 ◆', icon: Check },
  not_hit: { label: '未命中 ○', icon: Circle },
  insufficient_data: { label: '数据不足 △', icon: Triangle },
  calculation_failed: { label: '计算失败 ×', icon: X },
}

export function PersonalTodayView({ client, chartAdapter = undefined, onNavigate = (_path) => {} }) {
  const [workspace, setWorkspace] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [analysisContext, setAnalysisContext] = useState(null)

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
      setWorkspace({ trace: created })
    } catch (reason) {
      setError(reason)
    } finally {
      setLoading(false)
    }
  }

  if (loading) return <section className="personal-empty"><FlaskConical size={22} /><h2>正在读取今日工作台</h2><p>汇总当前组合、行情覆盖和待验证事项。</p></section>
  if (error && !workspace) return <TodayLoadFailure error={error} />
  const overview = <TodayDesk workspace={workspace} onNavigate={onNavigate} onAnalyze={setAnalysisContext} />
  if (!trace) return (
    <div className="personal-today enter">
      {overview}
      <section className="synthetic-lab-note">
        <FlaskConical size={18} />
        <div><strong>合成验收旅程已与真实工作台分开</strong><p>它只验证隐私和降级边界，不再占据每日入口。</p></div>
        {!error && !workspace?.portfolio ? <button onClick={createTrace}>创建合成测试旅程</button> : null}
      </section>
      {analysisContext ? <div key={analysisContext.contextId} className="context-analysis"><button className="context-close" onClick={() => setAnalysisContext(null)}><X size={14} />关闭上下文分析</button><AnalysisWorkspaceView client={client} subjectId={analysisContext.subjectId} initialQuestion={analysisContext.question} contextLabel={analysisContext.label} /></div> : null}
    </div>
  )

  const providerUnavailable = trace.issues?.includes('provider_unavailable')
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

      {error ? <div className="notice error"><AlertTriangle size={16} /><span><b>{error.code || 'personal_request_failed'}</b><small>{error.message}</small></span></div> : null}
    </div>
  )
}

function TodayDesk({ workspace, onNavigate, onAnalyze }) {
  const model = workspace?.read_model
  const portfolio = model?.portfolio
  const attention = model?.attention_items || []
  const followed = model?.watch_observations || []
  const candidates = model?.active_candidates || []
  const facts = model?.fact_events || []
  const gaps = model?.gaps || []
  const newsGap = gaps.find((gap) => gap.subject === 'structured_news')

  return <section className="today-desk" aria-label="今日组合总览">
    <header className="today-masthead">
      <div><span>TODAY / {periodLabel(model?.period)}</span><h2>今天先处理什么</h2><p>先读可核验事实与缺口，再决定是否需要 AI 解释；这里不生成买卖评级。</p></div>
      <b>{model?.as_of ? `投影 ${new Date(model.as_of).toLocaleString('zh-CN')}` : '投影时点不可用'}</b>
    </header>

    <div className="today-layout">
      <div className="today-reading-column">
        <DeskSection index="01" title="需要处理" meta={`${attention.length} 项`}>
          {attention.length ? <div className="today-action-list">{attention.map((item) => <article key={item.attention_id}><span className="action-priority">{RULE_STATES[item.result]?.label || item.result}</span><div><strong>{item.symbol} · {item.label}</strong><small>{item.reason_code}</small></div><button onClick={() => onAnalyze({ contextId: `attention-${item.attention_id}`, subjectId: item.symbol, label: `事项 / ${item.symbol}`, question: `${item.symbol} 的“${item.label}”事项有哪些可核验事实、传导机制和未知项？` })}><Bot size={14} />深度分析</button></article>)}</div> : <EmptyLine text="当前没有必须处理的规则命中或数据缺口" />}
        </DeskSection>

        <DeskSection index="02" title="影响持仓的事实变化" meta={`${facts.length} 个去重事件`}>
          {facts.length ? <div className="fact-ledger">{facts.map((event) => <article key={event.event_id}><header><span>■ 来源摘要 · 待核验</span><time>{formatTime(event.published_at)}</time></header><h3>{event.title}</h3><p>{event.summary}</p><div className="fact-scope"><span>{event.related_symbols?.join(' · ')}</span><small>{event.source} · 抓取 {formatTime(event.fetched_at)}</small></div><footer><a href={event.url} target="_blank" rel="noreferrer">查看来源</a><button onClick={() => onAnalyze({ contextId: `event-${event.event_id}`, subjectId: event.related_symbols?.[0] || portfolio?.active_holding_symbols?.[0] || '', label: '事件 / 待核验摘要', question: `请基于证据核验事件“${event.title}”，并区分确认事实、推断与未知项。` })}><Bot size={14} />分析此事件</button></footer></article>)}</div> : <EmptyLine text={factEmptyText(newsGap, portfolio)} tone={newsGap ? 'warning' : 'neutral'} />}
          <div className="inference-lane"><header><span>◇ AI 推断</span><b>与事实分栏</b></header><p>尚未生成上下文解释。只有从标的、事件或事项主动进入后，才展示带证据引用的推断。</p></div>
        </DeskSection>

        <div className="today-observation-grid">
          <DeskSection index="03" title="自选观察" meta={`${followed.length} 个`}>
            {followed.length ? <InstrumentRows items={followed} onNavigate={onNavigate} onAnalyze={onAnalyze} /> : <EmptyLine text="当前没有持仓或手动自选标的" />}
          </DeskSection>
          <DeskSection index="04" title="AI 候选" meta={`${candidates.length} 个`}>
            {candidates.length ? <InstrumentRows items={candidates} onNavigate={onNavigate} onAnalyze={onAnalyze} /> : <EmptyLine text="没有满足关系证据与近期事实门槛的候选" />}
          </DeskSection>
        </div>

        <DeskSection index="05" title="数据状态" meta={statusLabel(model?.status)}>
          <div className={`today-source-state ${model?.status || 'unavailable'}`}><Clock3 size={17} /><span><strong>{statusLabel(model?.status)}</strong><small>证据覆盖 {model?.field_coverage ?? '—'} · 新鲜度 {model?.freshness_seconds == null ? '—' : `${model.freshness_seconds}s`}</small></span></div>
          {gaps.length ? <ul className="today-gaps">{gaps.map((gap) => <li key={`${gap.code}-${gap.subject}`}>{gap.code} · {gap.subject}</li>)}</ul> : null}
        </DeskSection>
      </div>

      <PortfolioFloatCard portfolio={portfolio} onNavigate={onNavigate} />
    </div>
  </section>
}

function DeskSection({ index, title, meta, children }) {
  return <section className="desk-section"><header><span>{index}</span><h3>{title}</h3><b>{meta}</b></header>{children}</section>
}

function InstrumentRows({ items, onNavigate, onAnalyze }) {
  return <div className="instrument-rows">{items.map((item) => <article key={item.symbol}><div><strong>{item.symbol}</strong><StateMarks item={item} /></div><small>{item.preset_reasons?.join(' · ') || item.custom_reason || (item.is_holding ? '持仓自动关注' : '证据关系观察')}</small>{item.candidate_status ? <CandidateEvidenceSummary item={item} compact /> : null}<footer><button onClick={() => onNavigate(`/markets/us/${encodeURIComponent(item.symbol)}`)}>进入标的</button><button onClick={() => onAnalyze({ contextId: `instrument-${item.symbol}`, subjectId: item.symbol, label: `标的 / ${item.symbol}`, question: `${item.symbol} 当前有哪些可核验事实、影响机制与证据缺口？` })}><Bot size={13} />深度分析</button></footer></article>)}</div>
}

function PortfolioFloatCard({ portfolio, onNavigate }) {
  const snapshots = portfolio?.equity_snapshots || []
  return <aside className="portfolio-float" aria-label="悬浮组合卡">
    <header><span>PORTFOLIO / PRIVATE</span><BriefcaseBusiness size={18} /></header>
    <strong className="portfolio-total">{portfolio?.total_equity_availability === 'available' ? `$${portfolio.total_equity_value}` : '估值不可用'}</strong>
    <small>{portfolio?.total_equity_as_of ? `as-of ${formatTime(portfolio.total_equity_as_of)}` : '缺少可用行情时点'}</small>
    <div className="equity-window"><header><span>近期权益波动</span><b>最近 {snapshots.length} 个真实快照</b></header>{portfolio?.equity_snapshot_status === 'available' ? <EquitySparkline snapshots={snapshots} /> : <p>{portfolio?.equity_snapshot_status === 'failed' ? '权益快照读取失败，未伪装为空。' : '快照不足，不用现价拼接伪历史。'}</p>}</div>
    <dl><div><dt>活跃持仓</dt><dd>{portfolio?.active_holding_count ?? 0}</dd></div><div><dt>行情覆盖</dt><dd>{portfolio?.priced_holding_count ?? 0}/{portfolio?.active_holding_count ?? 0}</dd></div><div><dt>组合修订</dt><dd>R{portfolio?.portfolio_revision ?? 0}</dd></div></dl>
    <button className="primary-action" onClick={() => onNavigate('/portfolio')}>查看全部持仓<ArrowRight size={14} /></button>
  </aside>
}

function EquitySparkline({ snapshots }) {
  const values = snapshots.map((item) => Number(item.total_equity)).filter(Number.isFinite)
  const minimum = Math.min(...values)
  const maximum = Math.max(...values)
  const spread = maximum - minimum || 1
  const points = values.map((value, index) => `${(index / Math.max(values.length - 1, 1)) * 180},${54 - ((value - minimum) / spread) * 44}`).join(' ')
  return <svg className="equity-sparkline" viewBox="0 0 180 64" role="img" aria-label={`最近 ${values.length} 个真实权益快照波动图`}><polyline points={points} /></svg>
}

function EmptyLine({ text, tone = 'neutral' }) {
  return <div className={`today-empty-line ${tone}`}><Circle size={15} /><span>{text}</span></div>
}

function factEmptyText(newsGap, portfolio) {
  const symbols = portfolio?.active_holding_symbols || []
  const scope = symbols.length ? `，尚未读取 ${symbols.join(' · ')} 的事实变化` : ''
  if (newsGap?.code === 'source_unavailable') return `结构化新闻源不可用${scope}`
  if (newsGap) return `结构化新闻存在数据缺口（${newsGap.code}）${scope}`
  return symbols.length ? `已检查 ${symbols.join(' · ')}，当前没有匹配的去重事件` : '当前没有活跃持仓可检查'
}

function TodayLoadFailure({ error }) {
  return <section className="personal-empty today-load-failure" role="status"><AlertTriangle size={22} /><h2>{error.code === 'personal_access_denied' ? '来源未授权' : '今日投影读取失败'}</h2><p>{error.message || error.code}</p><small>未知状态不会显示为空工作台。</small></section>
}

function periodLabel(period) {
  return ({ pre_market: '盘前', regular: '盘中', after_hours: '盘后', market_closed: '休市' })[period] || '当前时段'
}

function statusLabel(status) {
  return ({ success: '正常', partial: '部分数据', stale: '来源过期', unavailable: '来源不可用', failed: '读取失败' })[status] || '来源不可用'
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString('zh-CN') : '时点不可用'
}

export function PersonalPlaceholder({ title, description }) {
  return <section className="personal-empty"><h2>{title}</h2><p>{description}</p><small>T0 仅交付 synthetic trust slice；真实数据与写入将在后续票据启用。</small></section>
}
