import { FileText, RefreshCw } from 'lucide-react'
import {
  Badge,
  DomainFailure,
  EmptyRow,
  EvidenceList,
  Fact,
  FactGroup,
  Panel,
  SummaryMetric,
  conclusionTone,
  evaluationForPublication,
  formatDateTime,
  formatInt,
  formatStructuredItem,
  latestVersion,
  shortHash,
  translateEventType,
  translateStatus,
} from './viewSupport.jsx'

export function ResearchCockpitView({
  strategies,
  selectedStrategyId,
  setSelectedStrategyId,
  strategyProfile,
  selectedResearchId,
  setSelectedResearchId,
  researchDetail,
  publication,
  analytics,
  loading,
  error,
}) {
  const formalResearches = strategyProfile?.formal_researches || []
  const publishedRecord = latestVersion((researchDetail?.publications || []).filter((item) => item.status === 'published'))
  const evaluation = evaluationForPublication(researchDetail, publication)
  const publicationFact = publication || publishedRecord
  const proposals = strategyProfile?.follow_up_proposals || []
  const runningResearches = (researchDetail?.runs || []).filter((item) => ['running', 'retrying'].includes(item.status)).length
  const missingEvidence = evaluation?.missing_evidence?.length || 0
  const latestPublishedAt = publication?.published_at || publishedRecord?.published_at
  const legacyOnly = strategyProfile?.metadata_json?.archiveClass === 'legacy'
  return (
    <div className="view-stack enter research-cockpit">
      <section className="section-heading cockpit-heading">
        <div>
          <span>结构化研究事实</span>
          <h2>研究驾驶舱</h2>
          <p>运行事实与研究结论分开呈现；结构化评价是主入口，原始 HTML 只作为可追溯证据。</p>
        </div>
        <Badge value={loading ? '加载中' : '只读'} />
      </section>
      {error ? <DomainFailure title="研究档案读取失败" detail={error} /> : null}

      <section className="cockpit-metrics" aria-label="研究摘要">
        <SummaryMetric label="待批准研究" value="功能债" detail="编排聚合投影尚未接入" />
        <SummaryMetric label="运行中" value={formatInt(runningResearches)} detail="当前研究的 running / retrying 运行" />
        <SummaryMetric label="受阻研究" value="功能债" detail="编排受阻聚合尚未接入" />
        <SummaryMetric label="最近发布" value={formatDateTime(latestPublishedAt)} detail={publicationFact?.conclusion || '当前研究暂无已发布结论'} />
        <SummaryMetric label="尚缺证据" value={formatInt(missingEvidence)} detail="当前已发布评价版本" />
        <SummaryMetric label="后续提案" value={formatInt(proposals.length)} detail="不等于已批准研究" />
      </section>

      <section className="cockpit-grid">
        <aside className="strategy-rail">
          <header><span>策略档案</span><h2>策略档案</h2></header>
          <div className="strategy-list">
            {strategies.map((strategy) => (
              <button
                className={strategy.strategy_id === selectedStrategyId ? 'active' : ''}
                key={strategy.strategy_id}
                onClick={() => setSelectedStrategyId(strategy.strategy_id)}
              >
                <span><b>{strategy.display_name}</b><small>{strategy.strategy_id}</small></span>
                <span><Badge value={strategy.lifecycle_status} /><small>{formatInt(strategy.formal_research_count)} 项研究</small></span>
              </button>
            ))}
            {!strategies.length && !error ? <div className="empty-state">暂无已登记策略</div> : null}
          </div>
        </aside>

        <div className="research-workspace">
          {strategyProfile ? (
            <>
              <section className="strategy-profile-card">
                <div>
                  <span>{strategyProfile.strategy_id}</span>
                  <h2>{strategyProfile.display_name}</h2>
                  <p>{strategyProfile.economic_thesis || '尚未登记经济假设。'}</p>
                </div>
                <dl>
                  <dt>生命周期</dt><dd><Badge value={strategyProfile.lifecycle_status} /></dd>
                  <dt>登记版本</dt><dd className="mono">{strategyProfile.registry_version}</dd>
                  <dt>代码提交</dt><dd className="mono">{shortHash(strategyProfile.code_commit)}</dd>
                </dl>
              </section>

              <nav className="research-tabs" aria-label="正式研究列表">
                {formalResearches.map((research) => (
                  <button
                    className={research.id === selectedResearchId ? 'active' : ''}
                    key={research.id}
                    onClick={() => setSelectedResearchId(research.id)}
                  >
                    <span>#{research.id.slice(0, 8)}</span>
                    <b>{translateStatus(research.phase)}</b>
                    <small>{research.latest_publication_conclusion || `${formatInt(research.run_count)} 次运行`}</small>
                  </button>
                ))}
                {!formalResearches.length ? <div className="research-empty">该策略暂无正式研究记录</div> : null}
              </nav>

              {legacyOnly ? (
                <LegacyArchiveNotice profile={strategyProfile} />
              ) : researchDetail ? (
                <ResearchDetailView detail={researchDetail} evaluation={evaluation} publication={publicationFact} analytics={analytics} />
              ) : formalResearches.length && !error ? <div className="loading-state"><RefreshCw className="spin" size={18} />正在读取研究时间线…</div> : null}
            </>
          ) : !error ? <div className="loading-state">选择策略后查看结构化研究档案</div> : null}
        </div>
      </section>
    </div>
  )
}

function ResearchDetailView({ detail, evaluation, publication, analytics }) {
  const plan = detail.plan || {}
  const runs = detail.runs || []
  const events = [...(detail.events || [])].sort((left, right) => left.sequence_no - right.sequence_no)
  const publications = [...(detail.publications || [])].sort((left, right) => Number(right.version) - Number(left.version))
  const proposals = [...(detail.follow_up_proposals || [])].sort((left, right) => String(right.created_at || '').localeCompare(String(left.created_at || '')))
  return (
    <div className="research-detail-grid">
      <ResearchAnalyticsView analytics={analytics} />
      <Panel title="计划与授权" eyebrow={`议题 #${plan.issue_number || '-'}`} className="research-plan-panel">
        <div className="research-facts">
          <FactGroup title="冻结计划">
            <Fact label="研究阶段" value={translateStatus(detail.phase)} strong />
            <Fact label="来源" value={detail.origin === 'historical_import' ? '历史迁移' : '原生研究'} />
            <Fact label="计划版本" value={`v${plan.version || '-'}`} />
            <Fact label="计划哈希" value={shortHash(plan.plan_sha256)} />
          </FactGroup>
          <FactGroup title="批准事实">
            <Fact label="动作" value={translateStatus(detail.approval?.action)} strong />
            <Fact label="批准人" value={detail.approval?.actor_login || '-'} />
            <Fact label="批准时间" value={formatDateTime(detail.approval?.created_at)} />
            <Fact label="代码提交" value={shortHash(plan.code_commit)} />
          </FactGroup>
        </div>
      </Panel>

      <Panel title="运行事实" eyebrow={`${formatInt(runs.length)} 次运行`} className="research-runs-panel">
        <div className="table-scroll">
          <table className="data-table">
            <thead><tr><th>运行</th><th>状态</th><th>阶段</th><th>结果指纹</th><th>完成时间</th><th>失败解释</th></tr></thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.run_id}>
                  <td className="mono strong">{run.run_id}</td>
                  <td><Badge value={run.status} /></td>
                  <td>{translateStatus(run.stage)}</td>
                  <td className="mono">{shortHash(run.result_fingerprint)}</td>
                  <td>{formatDateTime(run.finished_at)}</td>
                  <td>{run.error || '-'}</td>
                </tr>
              ))}
              {!runs.length ? <EmptyRow colSpan={6} /> : null}
            </tbody>
          </table>
        </div>
      </Panel>

      <section className="conclusion-panel">
        <header><span>研究评价</span><h2>研究评价</h2></header>
        <div className="conclusion-hero">
          <small>强制结论状态</small>
          <strong className={`conclusion ${conclusionTone(publication?.conclusion || evaluation?.conclusion)}`}>
            {publication?.conclusion || evaluation?.conclusion || '尚未形成结论'}
          </strong>
          <span>评价 v{publication?.evaluation_version || evaluation?.version || '-'} · {publication?.status === 'published' ? '已发布' : publication?.status || '未发布'}</span>
          {publication?.report_url ? (
            <a className="evidence-link" href={publication.report_url} target="_blank" rel="noreferrer"><FileText size={15} />打开原始 HTML 证据</a>
          ) : <span className="evidence-unavailable">当前没有可读的已发布 HTML 工件</span>}
        </div>
        <div className="evidence-matrix">
          <EvidenceList title="支持证据" items={evaluation?.supporting_evidence} tone="support" />
          <EvidenceList title="反对证据" items={evaluation?.opposing_evidence} tone="oppose" />
          <EvidenceList title="尚缺证据" items={evaluation?.missing_evidence} tone="missing" />
        </div>
      </section>

      <Panel title="限制与后续研究" eyebrow="限制与后续建议" className="research-limits-panel">
        <div className="limits-grid">
          <EvidenceList title="限制项" items={evaluation?.limitations} tone="missing" />
          <EvidenceList title="后续建议" items={evaluation?.follow_up_recommendations} tone="support" />
        </div>
      </Panel>

      <Panel title="发布与后续提案" eyebrow="带时间的只读事实" className="research-publications-panel">
        <div className="publication-proposal-grid">
          <div className="table-scroll"><table className="data-table"><thead><tr><th>发布版本</th><th>状态</th><th>评价</th><th>发布时间</th></tr></thead><tbody>
            {publications.map((item) => <tr key={item.id}><td className="mono strong">v{item.version}</td><td><Badge value={item.status} /></td><td className="mono">{shortHash(item.evaluation_id)}</td><td>{formatDateTime(item.published_at || item.created_at)}</td></tr>)}
            {!publications.length ? <EmptyRow colSpan={4} /> : null}
          </tbody></table></div>
          <div className="table-scroll"><table className="data-table"><thead><tr><th>后续提案</th><th>状态</th><th>形成时间</th></tr></thead><tbody>
            {proposals.map((item) => <tr key={item.id}><td><b>{item.title}</b><small className="table-note">{item.rationale}</small></td><td><Badge value={item.status} /></td><td>{formatDateTime(item.created_at)}</td></tr>)}
            {!proposals.length ? <EmptyRow colSpan={3} /> : null}
          </tbody></table></div>
        </div>
      </Panel>

      <Panel title="研究时间线" eyebrow={`${formatInt(events.length)} 个事件`} className="research-timeline-panel">
        <ol className="research-timeline">
          {events.map((event) => (
            <li key={event.id}>
              <span>{formatInt(event.sequence_no)}</span>
              <div><b>{translateEventType(event.event_type)}</b><small>{formatStructuredItem(event.payload_json)}</small></div>
              <time>{formatDateTime(event.occurred_at)}</time>
            </li>
          ))}
          {!events.length ? <li className="timeline-empty">暂无时间线事件</li> : null}
        </ol>
      </Panel>
    </div>
  )
}

function LegacyArchiveNotice({ profile }) {
  return (
    <section className="legacy-archive-notice">
      <div>
        <span>LEGACY PROVENANCE ONLY</span>
        <h2>仅追溯，未按当前标准评价</h2>
        <p>{profile.economic_thesis}</p>
      </div>
      <dl>
        <dt>结构化结论</dt><dd>not_available</dd>
        <dt>指标与图表</dt><dd>legacy_provenance_only</dd>
        <dt>展示规则</dt><dd>缺失指标不会显示为 0，也不会推断为策略有效</dd>
      </dl>
    </section>
  )
}

function ResearchAnalyticsView({ analytics }) {
  if (!analytics) {
    return (
      <section className="analytics-unavailable">
        <b>规范指标投影暂不可用</b>
        <span>结论与证据仍可查看；缺失数字不会被填成 0。</span>
      </section>
    )
  }
  if (analytics.data_status !== 'complete') {
    return (
      <section className="analytics-unavailable">
        <b>当前评价没有完整的规范指标</b>
        <span>{availabilityReason(analytics.availability?.metrics)}</span>
      </section>
    )
  }
  const metrics = analytics.metrics || {}
  const chart = analytics.chart_series || {}
  const robustness = analytics.robustness || {}
  const capacity = analytics.capacity || {}
  const metricAvailability = analytics.availability?.metricFields || {}
  const hasExcessReturn = finite(metrics.excessTotalReturn)
  const activeMetric = metrics.excessTotalReturn ?? metrics.relativeWealth
  const activeLabel = hasExcessReturn ? '累计超额收益' : '相对财富差'
  return (
    <section className="research-analytics" aria-label="量化评价指标与图表">
      <header className="analytics-header">
        <div><span>QUANTITATIVE EVIDENCE</span><h2>数字指标与图表</h2></div>
        <div><b>{analytics.primary_label || analytics.primary_run_id}</b><small>评价 v{analytics.evaluation_version} · 同一冻结证据投影</small></div>
      </header>

      <div className="analytics-kpis">
        <AnalyticsKpi label="累计净收益" value={formatRatio(metrics.totalReturn)} tone={numberTone(metrics.totalReturn)} />
        <AnalyticsKpi label="年化收益（CAGR）" value={formatRatio(metrics.cagr)} tone={numberTone(metrics.cagr)} />
        <AnalyticsKpi label="基准累计收益" value={formatRatio(metrics.benchmarkTotalReturn)} tone="benchmark" />
        <AnalyticsKpi label={activeLabel} value={formatRatio(activeMetric)} tone={numberTone(activeMetric)} />
        <AnalyticsKpi label="最大回撤" value={formatRatio(metrics.maxDrawdown)} tone="bad" />
        <AnalyticsKpi label="夏普比率（Sharpe）" value={formatDecimal(metrics.sharpe)} tone={numberTone(metrics.sharpe)} />
        <AnalyticsKpi label="索提诺比率（Sortino）" value={formatDecimal(metrics.sortino)} tone={numberTone(metrics.sortino)} />
        <AnalyticsKpi label="预期损失（ES95）" value={formatPercent(metrics.es95)} tone="risk" />
      </div>

      <div className="analytics-chart-grid">
        <AnalyticsChartPanel title="净值与基准" eyebrow="WEALTH PATH" availability={analytics.availability?.nav}>
          <LineChart
            ariaLabel="策略与基准净值对比图"
            series={[
              { label: analytics.primary_label || '策略净值', values: chart.nav, color: '#087ea4' },
              { label: analytics.benchmark?.label || '匹配基准', values: chart.benchmarkNav, color: '#d78a17' },
            ]}
          />
          {analytics.availability?.benchmarkNav?.status !== 'complete' ? <ChartNote text={availabilityReason(analytics.availability?.benchmarkNav)} /> : null}
        </AnalyticsChartPanel>
        <AnalyticsChartPanel title="回撤曲线" eyebrow="DRAWDOWN" availability={analytics.availability?.drawdown}>
          <LineChart ariaLabel="策略回撤曲线图" series={[{ label: '策略回撤', values: chart.drawdown, color: '#d84b4b' }]} zeroLine />
        </AnalyticsChartPanel>
        <AnalyticsChartPanel title="换手与成本" eyebrow="FRICTION" availability={analytics.availability?.turnoverCost}>
          <LineChart
            ariaLabel="累计换手与交易成本图"
            series={[
              { label: '累计单边换手', values: chart.cumulativeTurnover, color: '#087ea4' },
              { label: '累计成本率', values: chart.cumulativeCost, color: '#d78a17' },
            ]}
          />
        </AnalyticsChartPanel>
        <YearlyReturnChart rows={analytics.yearly || []} />
      </div>

      <div className="analytics-detail-grid">
        <MetricGroup title="收益与风险调整" rows={[
          ['年化波动', formatMetric(metrics.annualizedVolatility, metricAvailability.annualizedVolatility, formatPercent)],
          ['下行波动', formatMetric(metrics.downsideVolatility, metricAvailability.downsideVolatility, formatPercent)],
          ['Calmar', formatMetric(metrics.calmar, metricAvailability.calmar, formatDecimal)],
          ['信息比率', formatMetric(metrics.informationRatio, metricAvailability.informationRatio, formatDecimal)],
          ['跟踪误差', formatMetric(metrics.trackingError, metricAvailability.trackingError, formatPercent)],
          ['Beta', formatMetric(metrics.beta, metricAvailability.beta, formatDecimal)],
        ]} />
        <MetricGroup title="尾部与形态" rows={[
          ['VaR95', formatMetric(metrics.var95, metricAvailability.var95, formatPercent)],
          ['ES95', formatMetric(metrics.es95, metricAvailability.es95, formatPercent)],
          ['偏度', formatMetric(metrics.skew, metricAvailability.skew, formatDecimal)],
          ['超额峰度', formatMetric(metrics.excessKurtosis, metricAvailability.excessKurtosis, formatDecimal)],
          ['最长回撤', formatMetric(metrics.maxDrawdownDuration, metricAvailability.maxDrawdownDuration, formatDays)],
          ['最大回撤', formatMetric(metrics.maxDrawdown, metricAvailability.maxDrawdown, formatRatio)],
        ]} />
        <MetricGroup title="交易、暴露与容量" rows={[
          ['平均单边换手', formatMetric(metrics.averageOneWayTurnover, metricAvailability.averageOneWayTurnover, formatPercent)],
          ['累计单边换手', formatMetric(metrics.cumulativeOneWayTurnover, metricAvailability.cumulativeOneWayTurnover, formatMultiple)],
          ['累计成本率', formatMetric(metrics.cumulativeTransactionCostRate, metricAvailability.cumulativeTransactionCostRate, formatPercent)],
          ['平均暴露', formatMetric(metrics.averageExposure, metricAvailability.averageExposure, formatPercent)],
          ['最大权重', formatMetric(metrics.maximumWeight, metricAvailability.maximumWeight, formatPercent)],
          ['平均 HHI', formatMetric(metrics.averageHhi, metricAvailability.averageHhi, formatDecimal)],
          ['阻塞率', formatMetric(metrics.blockedRequestRate, metricAvailability.blockedRequestRate, formatPercent)],
          ['ADV 参与率 P95', formatMetric(metrics.advParticipationP95, metricAvailability.advParticipationP95, formatPercent)],
          ['容量证据', formatEvidenceStatus(capacity)],
        ]} />
        <MetricGroup title="稳健性与过拟合" rows={[
          ['Walk-forward', formatWalkForward(robustness.walkForward)],
          ['参数邻域', formatEvidenceStatus(robustness.parameterNeighborhood)],
          ['成本压力', formatEvidenceStatus(robustness.costStress)],
          ['DSR', formatProbabilityEvidence(robustness.dsr)],
          ['PBO', formatProbabilityEvidence(robustness.pbo)],
        ]} />
      </div>

      <RegimeMatrix rows={analytics.regimes || []} />
      <footer className="analytics-provenance">
        <span>数据状态：{analytics.data_status}</span>
        <span>运行：{analytics.primary_run_id || 'not_available'}</span>
        <span>来源指纹：{shortHash(analytics.provenance?.sha256 || analytics.provenance?.manifestSha256)}</span>
        <span>结果指纹：{shortHash(analytics.provenance?.resultFingerprint)}</span>
      </footer>
    </section>
  )
}

function AnalyticsKpi({ label, value, tone = '' }) {
  return <article className={`analytics-kpi ${tone}`}><span>{label}</span><b>{value}</b></article>
}

function AnalyticsChartPanel({ title, eyebrow, availability, children }) {
  const ready = availability?.status === 'complete'
  return (
    <section className="analytics-chart-panel">
      <header><span>{eyebrow}</span><h3>{title}</h3></header>
      {ready ? children : <div className="analytics-chart-empty">{availabilityReason(availability)}</div>}
    </section>
  )
}

function LineChart({ ariaLabel, series, zeroLine = false }) {
  const available = (series || []).filter((item) => item.values?.length)
  if (!available.length) return <div className="analytics-chart-empty">not_available：没有冻结序列</div>
  const width = 720
  const height = 230
  const padding = { left: 34, right: 16, top: 18, bottom: 28 }
  const allValues = available.flatMap((item) => item.values.map((point) => Number(point.value))).filter(Number.isFinite)
  const low = Math.min(...allValues, zeroLine ? 0 : Number.POSITIVE_INFINITY)
  const high = Math.max(...allValues, zeroLine ? 0 : Number.NEGATIVE_INFINITY)
  const spread = high - low || 1
  const points = (values) => values.map((point, index) => {
    const x = padding.left + (index / Math.max(values.length - 1, 1)) * (width - padding.left - padding.right)
    const y = padding.top + ((high - Number(point.value)) / spread) * (height - padding.top - padding.bottom)
    return `${x.toFixed(2)},${y.toFixed(2)}`
  }).join(' ')
  const zeroY = padding.top + ((high - 0) / spread) * (height - padding.top - padding.bottom)
  return (
    <div className="analytics-line-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
        {[0, 1, 2, 3].map((index) => {
          const y = padding.top + index * ((height - padding.top - padding.bottom) / 3)
          return <line key={index} x1={padding.left} x2={width - padding.right} y1={y} y2={y} className="chart-grid-line" />
        })}
        {zeroLine && zeroY >= padding.top && zeroY <= height - padding.bottom ? <line x1={padding.left} x2={width - padding.right} y1={zeroY} y2={zeroY} className="chart-zero-line" /> : null}
        {available.map((item) => <polyline key={item.label} points={points(item.values)} fill="none" stroke={item.color} strokeWidth="2.4" vectorEffect="non-scaling-stroke" />)}
      </svg>
      <div className="analytics-chart-legend">
        {available.map((item) => <span key={item.label}><i style={{ background: item.color }} />{item.label}<b>{formatChartEnd(item.values)}</b></span>)}
      </div>
    </div>
  )
}

function YearlyReturnChart({ rows }) {
  const visible = rows.filter((row) => row.year && finite(row.strategyReturn)).slice(-12)
  return (
    <section className="analytics-chart-panel yearly-return-panel">
      <header><span>CALENDAR</span><h3>逐年收益</h3></header>
      {visible.length ? (
        <div className="yearly-bars" role="img" aria-label="策略与基准逐年收益对比图">
          {visible.map((row) => {
            const benchmark = row.benchmarkReturn ?? row.passiveReturn
            const scale = Math.max(Math.abs(Number(row.strategyReturn)), Math.abs(Number(benchmark || 0)), 0.01)
            return (
              <div className="yearly-bar-row" key={row.year}>
                <b>{row.year}</b>
                <span><i className={numberTone(row.strategyReturn)} style={{ width: `${Math.min(100, Math.abs(Number(row.strategyReturn)) / scale * 100)}%` }} />{formatRatio(row.strategyReturn)}</span>
                <span><i className="benchmark" style={{ width: `${Math.min(100, Math.abs(Number(benchmark || 0)) / scale * 100)}%` }} />{formatRatio(benchmark)}</span>
              </div>
            )
          })}
        </div>
      ) : <div className="analytics-chart-empty">not_available：没有冻结逐年结果</div>}
    </section>
  )
}

function MetricGroup({ title, rows }) {
  return (
    <section className="analytics-metric-group"><h3>{title}</h3><dl>
      {rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
    </dl></section>
  )
}

function RegimeMatrix({ rows }) {
  return (
    <section className="regime-panel">
      <header><span>MARKET REGIMES</span><h3>方向 × 波动率</h3></header>
      {rows.length ? <div className="regime-grid">
        {rows.map((row, index) => {
          const strategyReturn = row.strategyReturn
          const benchmarkReturn = row.benchmarkReturn ?? row.passiveReturn
          const activeReturn = row.activeReturn
          return <article className={numberTone(strategyReturn)} key={`${row.direction}-${row.volatility}-${index}`}>
            <span>{row.direction || '未分类'} · {row.volatility || '未分类'}</span>
            <b>{formatRatio(strategyReturn)}</b>
            <small>基准 {formatRatio(benchmarkReturn)} · 主动 {formatRatio(activeReturn)}</small>
            <em>{formatInt(row.observations || row.months || 0)} 个观察</em>
          </article>
        })}
      </div> : <div className="analytics-chart-empty">not_available：没有冻结市场环境矩阵</div>}
    </section>
  )
}

function ChartNote({ text }) {
  return <p className="analytics-chart-note">{text}</p>
}

function availabilityReason(value) {
  return value?.reason ? `not_available：${value.reason}` : 'not_available：当前发布没有冻结该项证据'
}

function formatMetric(value, availability, formatter) {
  return availability?.status === 'complete' || (!availability && finite(value))
    ? formatter(value)
    : availabilityReason(availability)
}

function formatEvidenceStatus(value) {
  if (value?.status === 'complete') return '证据完整'
  if (value?.status === 'not_applicable') return `not_applicable：${value.reason || '该策略不适用'}`
  return `not_available：${value?.reason || '当前发布没有冻结该项证据'}`
}

function formatWalkForward(value) {
  if (value?.status !== 'complete') return formatEvidenceStatus(value)
  const count = value.windowCount
  const positiveRate = value.positiveWindowRate ?? value.positiveRate
  if (finite(count) && finite(positiveRate)) return `${formatInt(count)} 个窗口 · 正收益 ${formatPercent(positiveRate)}`
  if (finite(count)) return `${formatInt(count)} 个窗口`
  return '证据完整'
}

function formatProbabilityEvidence(value) {
  if (value?.status !== 'complete') return formatEvidenceStatus(value)
  return finite(value.probability) ? formatPercent(value.probability) : '证据完整'
}

function finite(value) {
  return value !== null && value !== undefined && Number.isFinite(Number(value))
}

function formatRatio(value) {
  if (!finite(value)) return 'not_available'
  const number = Number(value) * 100
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function formatDecimal(value) {
  return finite(value) ? Number(value).toFixed(3) : 'not_available'
}

function formatPercent(value) {
  return finite(value) ? `${(Number(value) * 100).toFixed(2)}%` : 'not_available'
}

function formatDays(value) {
  return finite(value) ? `${formatInt(value)} 日` : 'not_available'
}

function formatMultiple(value) {
  return finite(value) ? `${Number(value).toFixed(2)}×` : 'not_available'
}

function formatChartEnd(values) {
  const value = values?.[values.length - 1]?.value
  return finite(value) ? Number(value).toFixed(3) : 'not_available'
}

function numberTone(value) {
  if (!finite(value)) return 'neutral'
  return Number(value) > 0 ? 'good' : Number(value) < 0 ? 'bad' : 'neutral'
}
