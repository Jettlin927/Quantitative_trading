import { useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Activity,
  AlertTriangle,
  BarChart3,
  BookOpenCheck,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Database,
  FileText,
  Globe2,
  ListChecks,
  RefreshCw,
  Search,
  Server,
  ShieldCheck,
  Table2,
} from 'lucide-react'
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  createChart,
} from 'lightweight-charts'
import './styles.css'

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

const STOCK_PAGE_SIZE = 50
const CHART_RANGES = [
  { id: 'recent', label: '近 180 日' },
  { id: '1y', label: '近 1 年' },
  { id: '3y', label: '近 3 年' },
  { id: '5y', label: '近 5 年' },
  { id: 'all', label: '全部历史' },
]

const NAV_ITEMS = [
  { id: 'research', label: '研究驾驶舱', eyebrow: '结构化研究', icon: BookOpenCheck },
  { id: 'a-share', label: 'A 股数据', eyebrow: '实际市场数据', icon: BarChart3 },
  { id: 'us-data', label: '美股数据', eyebrow: '数据边界', icon: Globe2 },
  { id: 'operations', label: '系统运维', eyebrow: '只读运行事实', icon: Server },
]

export function App() {
  const [activeView, setActiveView] = useState('research')
  const [health, setHealth] = useState(null)
  const [overview, setOverview] = useState(null)
  const [syncProgress, setSyncProgress] = useState(null)
  const [readiness, setReadiness] = useState({ stocks: null, etf: null })
  const [stockPage, setStockPage] = useState({ items: [], total: 0, limit: STOCK_PAGE_SIZE, offset: 0 })
  const [catalogs, setCatalogs] = useState({ indices: [], funds: [], industries: [] })
  const [selectedCatalog, setSelectedCatalog] = useState({ kind: '', code: '' })
  const [catalogDetail, setCatalogDetail] = useState({ kind: '', code: '', bars: [], adjustments: [], members: [] })
  const [catalogLoading, setCatalogLoading] = useState(false)
  const [catalogError, setCatalogError] = useState('')
  const [usDb, setUsDb] = useState(null)
  const [strategies, setStrategies] = useState([])
  const [selectedStrategyId, setSelectedStrategyId] = useState('')
  const [strategyProfile, setStrategyProfile] = useState(null)
  const [selectedResearchId, setSelectedResearchId] = useState('')
  const [researchDetail, setResearchDetail] = useState(null)
  const [publication, setPublication] = useState(null)
  const [query, setQuery] = useState('')
  const [selectedCode, setSelectedCode] = useState('')
  const [stockBars, setStockBars] = useState([])
  const [stockDetail, setStockDetail] = useState(null)
  const [stockDataCode, setStockDataCode] = useState('')
  const [loading, setLoading] = useState(false)
  const [researchLoading, setResearchLoading] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [globalError, setGlobalError] = useState('')
  const [researchError, setResearchError] = useState('')
  const [stockError, setStockError] = useState('')
  const [lastUpdated, setLastUpdated] = useState(null)

  async function refreshAll(refreshCoverage = false) {
    setLoading(true)
    setResearchLoading(true)
    setGlobalError('')
    setResearchError('')
    const requests = [
      ['health', '/api/health?include_counts=false'],
      ['progress', '/api/tushare/sync-progress?include_coverage=false'],
      ['stockReadiness', '/api/research/readiness?scope=a_share_cross_section'],
      ['etfReadiness', '/api/research/readiness?scope=etf_time_series'],
      ['overview', `/api/db/overview${refreshCoverage ? '?refresh=true' : ''}`],
      ['stocks', buildStockScreenPath(query, 0)],
      ['indices', '/api/indices?limit=80'],
      ['funds', '/api/funds?limit=80'],
      ['industries', '/api/industries?limit=80'],
      ['usDb', '/api/us-research/db-overview'],
      ['strategies', '/api/research/strategies'],
    ]
    const results = await Promise.allSettled(requests.map(([, path]) => fetchJson(path)))
    const failures = []
    results.forEach((result, index) => {
      const key = requests[index][0]
      if (result.status === 'rejected') {
        if (key === 'strategies') {
          setResearchError(errorMessage(result.reason))
          setResearchLoading(false)
        }
        else failures.push(`${key}: ${errorMessage(result.reason)}`)
        return
      }
      const value = result.value
      if (key === 'health') setHealth(value)
      if (key === 'progress') setSyncProgress(value)
      if (key === 'stockReadiness') setReadiness((current) => ({ ...current, stocks: value }))
      if (key === 'etfReadiness') setReadiness((current) => ({ ...current, etf: value }))
      if (key === 'overview') setOverview(value)
      if (key === 'stocks') applyStockPage(value)
      if (key === 'indices') {
        setCatalogs((current) => ({ ...current, indices: value }))
        setSelectedCatalog((current) => current.code || !value.length ? current : { kind: 'index', code: value[0].tsCode })
      }
      if (key === 'funds') {
        setCatalogs((current) => ({ ...current, funds: value }))
        setSelectedCatalog((current) => current.code || !value.length ? current : { kind: 'fund', code: value[0].tsCode })
      }
      if (key === 'industries') {
        setCatalogs((current) => ({ ...current, industries: value }))
        setSelectedCatalog((current) => current.code || !value.length ? current : { kind: 'industry', code: value[0].indexCode })
      }
      if (key === 'usDb') setUsDb(value)
      if (key === 'strategies') {
        setStrategies(value)
        setSelectedStrategyId((current) => value.some((item) => item.strategy_id === current) ? current : value[0]?.strategy_id || '')
        if (!value.length) {
          setResearchLoading(false)
          setStrategyProfile(null)
          setSelectedResearchId('')
          setResearchDetail(null)
          setPublication(null)
        }
      }
    })
    if (failures.length) setGlobalError(`部分只读数据读取失败：${failures.join('；')}`)
    if (results.some((result) => result.status === 'fulfilled')) {
      setLastUpdated(new Date())
    }
    setResearchLoading(false)
    setLoading(false)
  }

  function applyStockPage(page) {
    setStockPage(page)
    const nextCode = page.items.some((item) => item.ts_code === selectedCode) ? selectedCode : page.items[0]?.ts_code || ''
    selectStock(nextCode)
  }

  function selectStock(tsCode) {
    if (tsCode === selectedCode) return
    setSelectedCode(tsCode)
    setStockDataCode('')
    setStockBars([])
    setStockDetail(null)
    setStockError('')
  }

  function selectCatalog(kind, code) {
    if (selectedCatalog.kind === kind && selectedCatalog.code === code) return
    setSelectedCatalog({ kind, code })
    setCatalogDetail({ kind, code, bars: [], adjustments: [], members: [] })
    setCatalogError('')
  }

  async function loadStocks(offset = 0) {
    setLoading(true)
    setStockError('')
    try {
      applyStockPage(await fetchJson(buildStockScreenPath(query, offset)))
    } catch (err) {
      setStockError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  function selectStrategy(strategyId) {
    setResearchLoading(true)
    setResearchError('')
    setStrategyProfile(null)
    setSelectedResearchId('')
    setResearchDetail(null)
    setPublication(null)
    setSelectedStrategyId(strategyId)
  }

  function selectResearch(researchId) {
    setResearchLoading(true)
    setResearchError('')
    setResearchDetail(null)
    setPublication(null)
    setSelectedResearchId(researchId)
  }

  useEffect(() => {
    const timer = window.setTimeout(() => refreshAll(), 0)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!selectedCode) return undefined
    let ignore = false
    const requestedCode = selectedCode
    async function loadSelectedStock() {
      setDetailLoading(true)
      setStockDataCode('')
      setStockBars([])
      setStockDetail(null)
      try {
        const [barsRes, detailRes] = await Promise.all([
          fetchJson(`/api/daily-bars?ts_code=${encodeURIComponent(requestedCode)}`),
          fetchJson(`/api/stocks/${encodeURIComponent(requestedCode)}/detail`),
        ])
        if (!ignore) {
          setStockDataCode(requestedCode)
          setStockBars(barsRes)
          setStockDetail(detailRes)
          setStockError('')
        }
      } catch (err) {
        if (!ignore) setStockError(errorMessage(err))
      } finally {
        if (!ignore) setDetailLoading(false)
      }
    }
    loadSelectedStock()
    return () => {
      ignore = true
    }
  }, [selectedCode])

  useEffect(() => {
    if (!selectedCatalog.code) return undefined
    let ignore = false
    const requested = selectedCatalog
    async function loadSelectedCatalog() {
      setCatalogLoading(true)
      setCatalogError('')
      setCatalogDetail({ ...requested, bars: [], adjustments: [], members: [] })
      try {
        const { startDate, endDate } = recentCatalogRange()
        let detail
        if (requested.kind === 'index') {
          const bars = await fetchJson(`/api/indices/${encodeURIComponent(requested.code)}/daily-bars?start_date=${startDate}&end_date=${endDate}`)
          detail = { ...requested, bars, adjustments: [], members: [] }
        } else if (requested.kind === 'fund') {
          const [bars, adjustments] = await Promise.all([
            fetchJson(`/api/funds/${encodeURIComponent(requested.code)}/daily-bars?start_date=${startDate}&end_date=${endDate}`),
            fetchJson(`/api/funds/${encodeURIComponent(requested.code)}/adjust-factors?start_date=${startDate}&end_date=${endDate}`),
          ])
          detail = { ...requested, bars, adjustments, members: [] }
        } else {
          const members = await fetchJson(`/api/industries/${encodeURIComponent(requested.code)}/members?trade_date=${endDate}`)
          detail = { ...requested, bars: [], adjustments: [], members }
        }
        if (!ignore) setCatalogDetail(detail)
      } catch (err) {
        if (!ignore) setCatalogError(errorMessage(err))
      } finally {
        if (!ignore) setCatalogLoading(false)
      }
    }
    loadSelectedCatalog()
    return () => { ignore = true }
  }, [selectedCatalog])

  useEffect(() => {
    if (!selectedStrategyId) {
      return undefined
    }
    let ignore = false
    fetchJson(`/api/research/strategies/${encodeURIComponent(selectedStrategyId)}`)
      .then((profile) => {
        if (ignore) return
        setResearchError('')
        setStrategyProfile(profile)
        setSelectedResearchId((current) => profile.formal_researches.some((item) => item.id === current) ? current : profile.formal_researches[0]?.id || '')
        if (!profile.formal_researches.length) {
          setResearchDetail(null)
          setPublication(null)
        }
      })
      .catch((err) => { if (!ignore) setResearchError(errorMessage(err)) })
      .finally(() => { if (!ignore) setResearchLoading(false) })
    return () => { ignore = true }
  }, [selectedStrategyId])

  useEffect(() => {
    if (!selectedResearchId) {
      return undefined
    }
    let ignore = false
    fetchJson(`/api/research/formal-researches/${encodeURIComponent(selectedResearchId)}`)
      .then(async (detail) => {
        if (ignore) return
        setResearchError('')
        setResearchDetail(detail)
        const latest = [...detail.publications]
          .filter((item) => item.status === 'published')
          .sort((left, right) => right.version - left.version)[0]
        const projection = latest
          ? await fetchJson(`/api/research/publications/${encodeURIComponent(latest.id)}`)
          : null
        if (!ignore) setPublication(projection)
      })
      .catch((err) => { if (!ignore) setResearchError(errorMessage(err)) })
      .finally(() => { if (!ignore) setResearchLoading(false) })
    return () => { ignore = true }
  }, [selectedResearchId])

  const stocks = stockPage.items
  const coverageRows = useMemo(() => buildCoverageRows(overview), [overview])
  const syncRuns = useMemo(() => syncProgress?.runs || [], [syncProgress])
  const selectedStock = stocks.find((stock) => stock.ts_code === selectedCode) || stocks[0] || null
  const selectedStockBars = stockDataCode === selectedCode ? stockBars : []
  const selectedStockDetail = stockDataCode === selectedCode ? stockDetail : null
  const selectedLatestBar = selectedStockBars[selectedStockBars.length - 1] || selectedStock || null

  return (
    <div className="app-frame">
      <Sidebar activeView={activeView} onNavigate={setActiveView} readiness={readiness} />

      <div className="workspace">
        <Topbar
          activeView={activeView}
          health={health}
          loading={loading}
          lastUpdated={lastUpdated}
          onRefresh={() => refreshAll(true)}
        />

        <main className="workspace-main">
          {globalError ? <Notice tone="warning" title="部分数据暂不可用" text={globalError} /> : null}

          {activeView === 'research' ? (
            <ResearchCockpitView
              strategies={strategies}
              selectedStrategyId={selectedStrategyId}
              setSelectedStrategyId={selectStrategy}
              strategyProfile={strategyProfile}
              selectedResearchId={selectedResearchId}
              setSelectedResearchId={selectResearch}
              researchDetail={researchDetail}
              publication={publication}
              loading={researchLoading}
              error={researchError}
            />
          ) : null}

          {activeView === 'a-share' ? (
            <AShareDataView
              coverageRows={coverageRows}
              readiness={readiness}
              stocks={stocks}
              stockPage={stockPage}
              query={query}
              setQuery={setQuery}
              onSearch={() => loadStocks(0)}
              onPage={loadStocks}
              selectedCode={selectedCode}
              setSelectedCode={selectStock}
              selectedStock={selectedStock}
              selectedLatestBar={selectedLatestBar}
              stockBars={selectedStockBars}
              stockDetail={selectedStockDetail}
              catalogs={catalogs}
              selectedCatalog={selectedCatalog}
              setSelectedCatalog={selectCatalog}
              catalogDetail={catalogDetail}
              catalogLoading={catalogLoading}
              catalogError={catalogError}
              syncRuns={syncRuns}
              detailLoading={detailLoading}
              error={stockError}
            />
          ) : null}

          {activeView === 'us-data' ? (
            <USDataBoundaryView usDb={usDb} />
          ) : null}

          {activeView === 'operations' ? (
            <OperationsView health={health} readiness={readiness} coverageRows={coverageRows} syncRuns={syncRuns} strategies={strategies} />
          ) : null}
        </main>
      </div>
    </div>
  )
}

function Sidebar({ activeView, onNavigate, readiness }) {
  const inventoryCount = [readiness.stocks, readiness.etf].filter(isInventoryAvailable).length
  return (
    <aside className="sidebar">
      <div className="brand-lockup">
        <span className="brand-mark"><Activity size={18} /></span>
        <div>
          <b>量化研究</b>
          <small>量化研究工作台</small>
        </div>
      </div>

      <nav className="side-nav" aria-label="主导航">
        {NAV_ITEMS.map(({ id, label, eyebrow, icon: Icon }) => (
          <button
            className={activeView === id ? 'active' : ''}
            key={id}
            onClick={() => onNavigate(id)}
            aria-current={activeView === id ? 'page' : undefined}
          >
            <Icon size={18} />
            <span><small>{eyebrow}</small>{label}</span>
            <ChevronRight size={14} />
          </button>
        ))}
      </nav>

      <div className="sidebar-foot">
        <div className="sidebar-readiness">
          <span><ShieldCheck size={15} /> 数据库存</span>
          <strong>{inventoryCount}/2 可用</strong>
          <div className="mini-track"><i style={{ width: `${inventoryCount * 50}%` }} /></div>
        </div>
        <p><i /> 仅限研究</p>
        <small>无券商连接 · 无真实交易</small>
      </div>
    </aside>
  )
}

function Topbar({ activeView, health, loading, lastUpdated, onRefresh }) {
  const current = NAV_ITEMS.find((item) => item.id === activeView) || NAV_ITEMS[0]
  const healthy = health?.status === 'ok'
  return (
    <header className="topbar">
      <div className="page-identity">
        <span>{current.eyebrow}</span>
        <h1>{current.label}</h1>
      </div>
      <div className="system-strip" aria-label="系统状态">
        <SystemState label="API" value={translateStatus(health?.status)} healthy={healthy} icon={Server} />
        <SystemState label="PostgreSQL" value={translateStatus(health?.database)} healthy={['connected', 'ok'].includes(health?.database)} icon={Database} />
        <SystemState
          label="Worker"
          value={health?.worker ? `${translateStatus(health.worker.status)} · ${health.worker.ageSeconds ?? '-'} 秒` : '未知'}
          healthy={health?.worker?.status === 'ok' && !health.worker.stale}
          icon={Activity}
        />
        <SystemState
          label="队列"
          value={health?.queue ? `${health.queue.active} 个运行中` : '未知'}
          healthy={Boolean(health?.queue) && health.queue.status !== 'stalled'}
          icon={ListChecks}
        />
        <span className="updated-at"><Clock3 size={14} /> {lastUpdated ? lastUpdated.toLocaleTimeString() : '尚未刷新'}</span>
        <button className="primary-action" onClick={onRefresh} disabled={loading}>
          <RefreshCw size={15} className={loading ? 'spin' : ''} />
          全局刷新
        </button>
      </div>
    </header>
  )
}

function SystemState({ label, value, healthy, icon: Icon }) {
  return (
    <span className="system-state">
      <Icon size={14} />
      <b>{label}</b>
      <i className={healthy ? 'good' : 'bad'} />
      {String(value)}
    </span>
  )
}

function ResearchCockpitView({
  strategies,
  selectedStrategyId,
  setSelectedStrategyId,
  strategyProfile,
  selectedResearchId,
  setSelectedResearchId,
  researchDetail,
  publication,
  loading,
  error,
}) {
  const formalResearches = strategyProfile?.formal_researches || []
  const publishedRecord = latestVersion((researchDetail?.publications || []).filter((item) => item.status === 'published'))
  const evaluation = evaluationForPublication(researchDetail, publication)
  const publicationFact = publication || publishedRecord
  const publishedStrategies = strategies.filter((item) => item.latest_publication_status === 'published').length
  const proposals = strategyProfile?.follow_up_proposals || []
  const activeResearches = formalResearches.filter((item) => ['active', 'evaluating'].includes(item.phase)).length
  const missingEvidence = evaluation?.missing_evidence?.length || 0
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
        <SummaryMetric label="运行中" value={formatInt(activeResearches)} detail="当前策略 active / evaluating" />
        <SummaryMetric label="受阻研究" value="功能债" detail="编排受阻聚合尚未接入" />
        <SummaryMetric label="最近发布" value={formatInt(publishedStrategies)} detail="有最新已发布结论的策略" />
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

              {researchDetail ? (
                <ResearchDetailView detail={researchDetail} evaluation={evaluation} publication={publicationFact} />
              ) : formalResearches.length && !error ? <div className="loading-state"><RefreshCw className="spin" size={18} />正在读取研究时间线…</div> : null}
            </>
          ) : !error ? <div className="loading-state">选择策略后查看结构化研究档案</div> : null}
        </div>
      </section>
    </div>
  )
}

function ResearchDetailView({ detail, evaluation, publication }) {
  const plan = detail.plan || {}
  const runs = detail.runs || []
  const events = [...(detail.events || [])].sort((left, right) => left.sequence_no - right.sequence_no)
  return (
    <div className="research-detail-grid">
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
            <thead><tr><th>运行</th><th>状态</th><th>阶段</th><th>结果指纹</th><th>完成时间</th></tr></thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.run_id}>
                  <td className="mono strong">{run.run_id}</td>
                  <td><Badge value={run.status} /></td>
                  <td>{translateStatus(run.stage)}</td>
                  <td className="mono">{shortHash(run.result_fingerprint)}</td>
                  <td>{formatDateTime(run.finished_at)}</td>
                </tr>
              ))}
              {!runs.length ? <EmptyRow colSpan={5} /> : null}
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

function EvidenceList({ title, items = [], tone }) {
  return (
    <section className={`evidence-list ${tone}`}>
      <h3>{title}<b>{formatInt(items?.length || 0)}</b></h3>
      <ul>
        {(items || []).slice(0, 8).map((item, index) => <li key={`${title}-${index}`}>{formatStructuredItem(item)}</li>)}
        {!items?.length ? <li className="muted">暂无记录</li> : null}
      </ul>
    </section>
  )
}

function AShareDataView(props) {
  const {
    readiness,
    coverageRows,
    catalogs,
    stockDetail,
    selectedCatalog,
    setSelectedCatalog,
    catalogDetail,
    catalogLoading,
    catalogError,
    syncRuns,
    error,
  } = props
  const failedSyncRuns = syncRuns.filter((run) => ['failed', 'error'].includes(String(run.status).toLowerCase())).length
  return (
    <div className="view-stack enter">
      <section className="section-heading actual-data-heading">
        <div><span>PostgreSQL 实际市场数据</span><h2>A 股实际市场数据</h2><p>所有行情、估值、财务、指数、ETF 与行业记录均来自 PostgreSQL；页面不生成评级或实盘指令。</p></div>
        <div className="data-boundary-badges"><Badge value="实际数据" /><Badge value="只读" /></div>
      </section>
      {error ? <DomainFailure title="A 股数据读取失败" detail={error} /> : null}
      <section className="inventory-strip">
        <SummaryMetric label="A 股横截面库存" value={inventoryLabel(readiness.stocks)} detail="库存可用不等于研究通过" />
        <SummaryMetric label="ETF 时序库存" value={inventoryLabel(readiness.etf)} detail="质量运行另行判定" />
        <SummaryMetric label="估值历史" value={formatInt(stockDetail?.valuation_history?.length)} detail="当前股票返回记录" />
        <SummaryMetric label="财务历史" value={formatInt(stockDetail?.financial_history?.length)} detail="按公告日 point-in-time" />
        <SummaryMetric label="同步失败" value={formatInt(failedSyncRuns)} detail="最近同步运行中的失败事实" />
      </section>
      <StockLabView {...props} />
      <FundamentalsHistory detail={stockDetail} />
      <section className="catalog-grid">
        <CatalogPanel title="指数目录" eyebrow="指数基准" kind="index" rows={catalogs.indices} codeKey="tsCode" metaKey="category" selected={selectedCatalog} onSelect={setSelectedCatalog} />
        <CatalogPanel title="ETF 目录" eyebrow="ETF 范围" kind="fund" rows={catalogs.funds} codeKey="tsCode" metaKey="fundType" selected={selectedCatalog} onSelect={setSelectedCatalog} />
        <CatalogPanel title="行业分类" eyebrow="申万 2021 行业" kind="industry" rows={catalogs.industries} codeKey="indexCode" nameKey="industryName" metaKey="level" selected={selectedCatalog} onSelect={setSelectedCatalog} />
      </section>
      {catalogError ? <DomainFailure title="目录明细读取失败" detail={catalogError} /> : null}
      <CatalogDetailPanel selection={selectedCatalog} detail={catalogDetail} loading={catalogLoading} />
      <Panel title="实际数据覆盖与研究用途" eyebrow="覆盖与质量"><CoverageMatrix rows={coverageRows} detailed /></Panel>
    </div>
  )
}

function FundamentalsHistory({ detail }) {
  const valuation = detail?.valuation_history || []
  const financial = detail?.financial_history || []
  return (
    <section className="history-grid">
      <Panel title="估值历史" eyebrow="每日基础估值">
        <div className="table-scroll compact-history"><table className="data-table"><thead><tr><th>交易日</th><th>PE TTM</th><th>PB</th><th>换手率</th><th>总市值</th></tr></thead><tbody>
          {valuation.slice(-12).reverse().map((row) => <tr key={row.tradeDate}><td>{row.tradeDate}</td><td>{formatNumber(row.peTtm)}</td><td>{formatNumber(row.pb)}</td><td>{formatPercent(row.turnoverRate)}</td><td>{formatWanYi(row.totalMv)}</td></tr>)}
          {!valuation.length ? <EmptyRow colSpan={5} /> : null}
        </tbody></table></div>
      </Panel>
      <Panel title="财务历史" eyebrow="公告日时点可见">
        <div className="table-scroll compact-history"><table className="data-table"><thead><tr><th>公告日</th><th>报告期</th><th>EPS</th><th>ROE</th><th>营收同比</th></tr></thead><tbody>
          {financial.slice(-12).reverse().map((row) => <tr key={`${row.annDate}-${row.endDate}`}><td>{row.annDate}</td><td>{row.endDate}</td><td>{formatNumber(row.eps)}</td><td>{formatPercent(row.roe)}</td><td>{formatPercent(row.trYoy)}</td></tr>)}
          {!financial.length ? <EmptyRow colSpan={5} /> : null}
        </tbody></table></div>
      </Panel>
    </section>
  )
}

function CatalogPanel({ title, eyebrow, kind, rows, codeKey, nameKey = 'name', metaKey, selected, onSelect }) {
  return (
    <Panel title={title} eyebrow={`${eyebrow} · ${formatInt(rows.length)}`}>
      <div className="catalog-list">
        {rows.slice(0, 12).map((row) => (
          <button className={selected.kind === kind && selected.code === row[codeKey] ? 'active' : ''} key={row[codeKey]} onClick={() => onSelect(kind, row[codeKey])}>
            <span><b>{row[nameKey] || '-'}</b><small>{row[codeKey]}</small></span><em>{row[metaKey] || '-'}</em>
          </button>
        ))}
        {!rows.length ? <div className="empty-state">暂无目录记录</div> : null}
      </div>
    </Panel>
  )
}

function CatalogDetailPanel({ selection, detail, loading }) {
  const isIndustry = selection.kind === 'industry'
  const adjustmentByDate = new Map((detail.adjustments || []).map((item) => [item.tradeDate, item.adjFactor]))
  return (
    <Panel
      title={selection.code || '请选择目录标的'}
      eyebrow={isIndustry ? '当前交易日行业成员' : `${selection.kind === 'fund' ? 'ETF' : '指数'}近一年日线历史`}
    >
      {loading ? <div className="loading-state"><RefreshCw className="spin" size={18} />正在读取目录明细…</div> : (
        <div className="table-scroll compact-history">
          {isIndustry ? (
            <table className="data-table"><thead><tr><th>成分代码</th><th>名称</th><th>纳入日</th><th>移出日</th><th>最新成员</th></tr></thead><tbody>
              {(detail.members || []).slice(0, 50).map((row) => <tr key={`${row.conCode}-${row.inDate}`}><td className="mono strong">{row.conCode}</td><td>{row.conName || '-'}</td><td>{row.inDate}</td><td>{row.outDate || '-'}</td><td><Badge value={row.isNew ? '是' : '否'} /></td></tr>)}
              {!detail.members?.length ? <EmptyRow colSpan={5} /> : null}
            </tbody></table>
          ) : (
            <table className="data-table"><thead><tr><th>交易日</th><th>收盘</th><th>涨跌幅</th><th>成交额</th>{selection.kind === 'fund' ? <th>复权因子</th> : null}</tr></thead><tbody>
              {(detail.bars || []).slice(-20).reverse().map((row) => <tr key={row.tradeDate}><td>{row.tradeDate}</td><td className="mono strong">{formatNumber(row.close)}</td><td className={priceTone(row.pctChg)}>{formatSignedPercent(row.pctChg)}</td><td>{formatDailyAmount(row.amount)}</td>{selection.kind === 'fund' ? <td>{formatNumber(adjustmentByDate.get(row.tradeDate))}</td> : null}</tr>)}
              {!detail.bars?.length ? <EmptyRow colSpan={selection.kind === 'fund' ? 5 : 4} /> : null}
            </tbody></table>
          )}
        </div>
      )}
    </Panel>
  )
}

function USDataBoundaryView({ usDb }) {
  const usAssets = usDb?.assets || []
  const counts = usDb?.counts || {}
  return (
    <div className="view-stack enter">
      <section className="functional-debt-card">
        <div className="debt-icon"><Globe2 size={25} /></div>
        <div><span>功能债与样例边界</span><h2>美股研究级实际数据尚未接入</h2><p>当前内容仅是开发用样例，不具备时点可见、历史标的范围、复权与研究发布资格。不得将其解释为实际持仓、真实账户或研究结论。</p></div>
        <a href="https://github.com/Jettlin927/Quantitative_trading/issues/27" target="_blank" rel="noreferrer">查看待补信息 #27 <ChevronRight size={14} /></a>
      </section>
      <section className="sample-ribbon"><Badge value="仅样例" /><span>开发夹具只读投影</span><strong>{formatInt(counts.assets)} 个资产 · {formatInt(counts.assetDailyPrices)} 条价格</strong></section>
      <Panel title="美股样例资产" eyebrow="非研究级开发夹具">
        <div className="table-scroll"><table className="data-table"><thead><tr><th>代码</th><th>名称</th><th>类型</th><th>风险标签</th><th>样例收盘</th></tr></thead><tbody>
          {usAssets.map((asset) => <tr key={asset.naturalKey || asset.symbol}><td className="mono strong">{asset.symbol}</td><td>{asset.name || '-'}</td><td>{asset.instrumentType || '-'}</td><td>{asset.riskTag || '-'}</td><td>{formatNumber(asset.latestPrice?.close)}</td></tr>)}
          {!usAssets.length ? <EmptyRow colSpan={5} /> : null}
        </tbody></table></div>
      </Panel>
    </div>
  )
}

function OperationsView({ health, readiness, coverageRows, syncRuns, strategies }) {
  const publishedStrategies = strategies.filter((item) => item.latest_publication_status === 'published').length
  return (
    <div className="view-stack enter">
      <section className="section-heading"><div><span>只读系统事实</span><h2>系统运维</h2><p>仅展示 API、数据库、Worker、队列、数据覆盖与同步历史；不在驾驶舱执行写入或生产操作。</p></div><Badge value="无写入控制" /></section>
      <section className="operations-grid">
        <OperationCard icon={Server} title="API / PostgreSQL" value={`${translateStatus(health?.status)} / ${translateStatus(health?.database)}`} healthy={health?.status === 'ok' && health?.database === 'ok'} />
        <OperationCard icon={Activity} title="Worker 心跳" value={health?.worker ? `${translateStatus(health.worker.status)} · ${health.worker.ageSeconds ?? '-'} 秒` : '未知'} healthy={health?.worker?.status === 'ok' && !health?.worker?.stale} />
        <OperationCard icon={ListChecks} title="队列" value={health?.queue ? `${health.queue.active} 个运行中 · ${health.queue.queued} 个排队中` : '未知'} healthy={Boolean(health?.queue) && health.queue.status !== 'stalled'} />
        <OperationCard icon={ShieldCheck} title="研究库存" value={`${inventoryLabel(readiness.stocks)} / ${inventoryLabel(readiness.etf)}`} healthy={isInventoryAvailable(readiness.stocks) && isInventoryAvailable(readiness.etf)} />
        <OperationCard icon={FileText} title="发布健康" value={`${formatInt(publishedStrategies)} 个策略有已发布结论；队列聚合待接入`} healthy={publishedStrategies > 0} />
        <OperationCard icon={Database} title="磁盘" value="功能债：只读磁盘探针尚未接入" healthy={false} />
        <OperationCard icon={ShieldCheck} title="备份" value="人工核验门禁：跟踪议题 #28" healthy={false} />
      </section>
      <section className="data-detail-grid">
        <Panel title="PostgreSQL 数据覆盖" eyebrow="数据库实时计数" className="full-coverage"><CoverageMatrix rows={coverageRows} detailed /></Panel>
        <Panel title="最近同步事实" eyebrow="最近数据写入" className="full-runs"><RunTable runs={syncRuns} /></Panel>
      </section>
    </div>
  )
}

function OperationCard({ icon: Icon, title, value, healthy }) {
  return <article className={`operation-card ${healthy ? 'healthy' : 'attention'}`}><Icon size={20} /><span><b>{title}</b><small>{String(value)}</small></span><i /></article>
}

function DomainFailure({ title, detail }) {
  return <div className="domain-failure" role="alert"><AlertTriangle size={19} /><span><b>{title}</b><small>{detail}</small><em>其他只读区域仍可继续浏览。</em></span></div>
}

function StockLabView({
  stocks,
  stockPage,
  query,
  setQuery,
  onSearch,
  onPage,
  selectedCode,
  setSelectedCode,
  selectedStock,
  selectedLatestBar,
  stockBars,
  stockDetail,
  detailLoading,
}) {
  const historyStart = stockBars[0]?.trade_date
  const historyEnd = stockBars[stockBars.length - 1]?.trade_date
  const pageNumber = Math.floor(stockPage.offset / stockPage.limit) + 1
  const pageCount = Math.max(1, Math.ceil(stockPage.total / stockPage.limit))
  return (
    <div className="stock-lab enter">
      <aside className="security-browser">
        <header><div><span>股票范围</span><h2>股票浏览</h2></div><b>{formatInt(stockPage.total)}</b></header>
        <label className="security-search">
          <Search size={15} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && onSearch()}
            placeholder="代码 / 名称 / 拼音"
          />
          <button onClick={onSearch}>查询</button>
        </label>
        <div className="security-list">
          {stocks.map((stock) => (
            <button
              className={stock.ts_code === selectedCode ? 'active' : ''}
              key={stock.ts_code}
              onClick={() => setSelectedCode(stock.ts_code)}
            >
              <span><b>{stock.symbol || stock.ts_code}</b><small>{stock.name}</small></span>
              <span className={priceTone(stock.pct_chg)}>{formatNumber(stock.close)}<small>{formatSignedPercent(stock.pct_chg)}</small></span>
            </button>
          ))}
          {!stocks.length ? <div className="empty-state">没有匹配的股票</div> : null}
        </div>
        <div className="security-pagination" aria-label="股票分页">
          <button disabled={stockPage.offset <= 0} onClick={() => onPage(Math.max(0, stockPage.offset - stockPage.limit))}>上一页</button>
          <span>{pageNumber} / {pageCount}</span>
          <button disabled={stockPage.offset + stockPage.limit >= stockPage.total} onClick={() => onPage(stockPage.offset + stockPage.limit)}>下一页</button>
        </div>
      </aside>

      <section className="market-chart-panel">
        <header className="security-title">
          <div><span>{selectedStock?.ts_code || '未选择股票'}</span><h2>{selectedStock?.name || '请选择股票'}</h2></div>
          <div className="security-quote">
            <strong className={priceTone(selectedLatestBar?.pct_chg)}>{formatNumber(selectedLatestBar?.close)}</strong>
            <span className={priceTone(selectedLatestBar?.pct_chg)}>{formatSignedPercent(selectedLatestBar?.pct_chg)}</span>
          </div>
          <div className="security-meta">
            <span>行业 <b>{selectedStock?.industry || '-'}</b></span>
            <span>市场 <b>{selectedStock?.market || '-'}</b></span>
            <span>交易日 <b>{detailLoading ? '加载中' : formatInt(stockBars.length)}</b></span>
            <span>完整区间 <b>{historyStart && historyEnd ? `${historyStart} → ${historyEnd}` : '-'}</b></span>
          </div>
        </header>
        <TechnicalChart bars={stockBars} />
      </section>

      <aside className="facts-panel">
        <header><span>时点可见事实</span><h2>估值与财务</h2></header>
        <FactGroup title={selectedLatestBar?.trade_date || selectedStock?.latest_date || '最新行情'}>
          <Fact label="开 / 高" value={`${formatNumber(selectedLatestBar?.open)} / ${formatNumber(selectedLatestBar?.high)}`} />
          <Fact label="低 / 收" value={`${formatNumber(selectedLatestBar?.low)} / ${formatNumber(selectedLatestBar?.close)}`} strong />
          <Fact label="成交额" value={formatDailyAmount(selectedLatestBar?.amount)} />
        </FactGroup>
        <FactGroup title="估值">
          <Fact label="总市值" value={formatWanYi(stockDetail?.valuation?.totalMv)} />
          <Fact label="PE TTM" value={formatNumber(stockDetail?.valuation?.peTtm)} />
          <Fact label="PB" value={formatNumber(stockDetail?.valuation?.pb)} />
          <Fact label="换手率" value={formatPercent(stockDetail?.valuation?.turnoverRate)} />
        </FactGroup>
        <FactGroup title="财务（公告后可见）">
          <Fact label="公告日" value={stockDetail?.financial?.annDate || '-'} />
          <Fact label="ROE" value={formatPercent(stockDetail?.financial?.roe)} />
          <Fact label="毛利率" value={formatPercent(stockDetail?.financial?.grossprofitMargin)} />
          <Fact label="营收同比" value={formatPercent(stockDetail?.financial?.trYoy)} />
          <Fact label="净利同比" value={formatPercent(stockDetail?.financial?.netprofitYoy)} />
        </FactGroup>
        <FactGroup title="上市与可交易性">
          <Fact label="上市状态" value={stockDetail?.listing?.listStatus || '-'} strong />
          <Fact label="上市日" value={stockDetail?.listing?.listDate || '-'} />
          <Fact label="最新涨停" value={formatNumber(stockDetail?.latest_limit_price?.upLimit)} />
          <Fact label="最新跌停" value={formatNumber(stockDetail?.latest_limit_price?.downLimit)} />
          <Fact label="复权因子" value={formatNumber(stockDetail?.latest_adjust_factor?.adjFactor)} />
          <Fact label="最近停牌事件" value={stockDetail?.latest_suspend_event?.tradeDate || '无记录'} />
        </FactGroup>
      </aside>
    </div>
  )
}

function TechnicalChart({ bars }) {
  const priceRef = useRef(null)
  const volumeRef = useRef(null)
  const [range, setRange] = useState('recent')
  const series = useMemo(() => buildTechnicalSeries(bars), [bars])

  useEffect(() => {
    if (!series.candles.length) return undefined
    const charts = [renderPriceChart(priceRef.current, series), renderVolumeChart(volumeRef.current, series)].filter(Boolean)
    applyChartRange(charts, series.candles, range)
    synchronizeChartRanges(charts)
    const resize = () => charts.forEach(({ chart, element }) => chart.applyOptions({ width: element.clientWidth }))
    window.addEventListener('resize', resize)
    return () => {
      window.removeEventListener('resize', resize)
      charts.forEach(({ chart }) => chart.remove())
    }
  }, [range, series])

  if (!series.candles.length) return <div className="chart-empty">暂无可绘制的日线数据</div>
  return (
    <div className="chart-stack">
      <div className="chart-toolbar">
        <div className="chart-legend"><span><i className="ma10" />MA10</span><span><i className="ma20" />MA20</span><span><i className="volume" />成交量</span></div>
        <div className="chart-ranges" aria-label="行情显示区间">
          {CHART_RANGES.map((item) => (
            <button className={range === item.id ? 'active' : ''} key={item.id} onClick={() => setRange(item.id)}>{item.label}</button>
          ))}
        </div>
      </div>
      <div className="chart-pane price" ref={priceRef} />
      <div className="chart-pane volume" ref={volumeRef} />
    </div>
  )
}

function renderPriceChart(element, series) {
  if (!element) return null
  element.replaceChildren()
  const chart = createBaseChart(element, 430)
  chart.addSeries(CandlestickSeries, {
    upColor: '#d84b4b', downColor: '#078761', borderUpColor: '#d84b4b', borderDownColor: '#078761', wickUpColor: '#d84b4b', wickDownColor: '#078761',
  }).setData(series.candles)
  chart.addSeries(LineSeries, { color: '#087ea4', lineWidth: 2, priceLineVisible: false }).setData(series.ma10)
  chart.addSeries(LineSeries, { color: '#d78a17', lineWidth: 2, priceLineVisible: false }).setData(series.ma20)
  return { chart, element }
}

function renderVolumeChart(element, series) {
  if (!element) return null
  element.replaceChildren()
  const chart = createBaseChart(element, 125)
  chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceLineVisible: false }).setData(series.volume)
  return { chart, element }
}

function applyChartRange(charts, candles, range) {
  if (!candles.length) return
  if (range === 'all') {
    charts.forEach(({ chart }) => chart.timeScale().fitContent())
    return
  }
  const years = { '1y': 1, '3y': 3, '5y': 5 }[range]
  let from = Math.max(0, candles.length - 180)
  if (years) {
    const cutoff = new Date(`${candles[candles.length - 1].time}T00:00:00`)
    cutoff.setFullYear(cutoff.getFullYear() - years)
    const cutoffText = cutoff.toISOString().slice(0, 10)
    const firstVisible = candles.findIndex((bar) => bar.time >= cutoffText)
    from = firstVisible < 0 ? 0 : firstVisible
  }
  const visibleRange = { from: Math.max(-0.5, from - 0.5), to: candles.length - 0.5 }
  charts.forEach(({ chart }) => chart.timeScale().setVisibleLogicalRange(visibleRange))
}

function synchronizeChartRanges(charts) {
  if (charts.length < 2) return
  let syncing = false
  for (const source of charts) {
    source.chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range || syncing) return
      syncing = true
      for (const target of charts) {
        if (target !== source) target.chart.timeScale().setVisibleLogicalRange(range)
      }
      syncing = false
    })
  }
}

function createBaseChart(element, height) {
  return createChart(element, {
    width: element.clientWidth,
    height,
    layout: { background: { color: '#f8fafb' }, textColor: '#61707c', fontFamily: 'IBM Plex Mono, Consolas, monospace', fontSize: 11 },
    grid: { vertLines: { color: '#e9eef1' }, horzLines: { color: '#e3e9ed' } },
    rightPriceScale: { borderColor: '#ccd5db', scaleMargins: { top: 0.08, bottom: 0.08 } },
    timeScale: { borderColor: '#ccd5db', timeVisible: false },
    crosshair: { mode: 1 },
  })
}

function Panel({ title, eyebrow, action = '', onAction = undefined, className = '', children }) {
  return (
    <section className={`panel ${className}`}>
      <header className="panel-head">
        <div><span>{eyebrow}</span><h2>{title}</h2></div>
        {action ? <button onClick={onAction}>{action}<ChevronRight size={14} /></button> : null}
      </header>
      {children}
    </section>
  )
}

function CoverageMatrix({ rows, detailed = false }) {
  return (
    <div className="table-scroll">
      <table className="data-table coverage-table">
        <thead><tr><th>数据集</th><th>状态</th><th>记录</th><th>标的</th><th>日期范围</th>{detailed ? <th>研究用途</th> : null}</tr></thead>
        <tbody>
          {rows.length ? rows.map((row) => (
            <tr key={row.name}>
              <td><span className="dataset-name"><Table2 size={14} /><b>{row.label}</b><small>{row.name}</small></span></td>
              <td><Badge value={row.status} /></td>
              <td className="mono">{formatInt(row.rows)}</td>
              <td className="mono">{formatInt(row.symbols)}</td>
              <td className="mono date-cell">{row.range}</td>
              {detailed ? <td>{row.purpose}</td> : null}
            </tr>
          )) : <tr><td className="empty-cell" colSpan={detailed ? 6 : 5}>正在读取 PostgreSQL 精确覆盖统计…</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

function RunTable({ runs, compact = false }) {
  return (
    <div className="table-scroll">
      <table className={`data-table ${compact ? 'compact' : ''}`}>
        <thead><tr><th>目标</th><th>状态</th><th>写入行</th><th>数据范围</th><th>执行时间</th></tr></thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id || `${run.target}-${run.createdAt}`}>
              <td className="mono strong">{run.target}</td><td><Badge value={run.status} /></td><td className="mono">{formatInt(run.rowsUpserted)}</td><td className="mono date-cell">{[run.startDate, run.endDate].filter(Boolean).join(' → ') || '-'}</td><td>{formatDateTime(run.createdAt)}</td>
            </tr>
          ))}
          {!runs.length ? <EmptyRow colSpan={5} /> : null}
        </tbody>
      </table>
    </div>
  )
}

function SummaryMetric({ label, value, detail }) {
  return <div className="summary-metric"><span>{label}</span><strong>{value}</strong><small>{detail || '-'}</small></div>
}

function FactGroup({ title, children }) {
  return <section className="fact-group"><h3>{title}</h3><dl>{children}</dl></section>
}

function Fact({ label, value, strong = false }) {
  return <><dt>{label}</dt><dd className={strong ? 'strong' : ''}>{value || '-'}</dd></>
}

function Badge({ value }) {
  const text = String(value || 'unknown')
  const normalized = text.toLowerCase()
  const tone = ['ready', 'ok', 'success', 'succeeded', 'connected', 'available', 'published', '实际数据', '只读'].includes(normalized)
    ? 'good'
    : ['blocked', 'failed', 'fail', 'error', 'empty', '受阻', '不通过'].includes(normalized)
      ? 'bad'
      : 'warn'
  return <span className={`badge ${tone}`}><i />{translateStatus(text)}</span>
}

function Notice({ tone, title, text }) {
  return <div className={`notice ${tone}`}>{tone === 'success' ? <CheckCircle2 size={17} /> : <AlertTriangle size={17} />}<span><b>{title}</b><small>{text}</small></span></div>
}

function EmptyRow({ colSpan }) {
  return <tr><td className="empty-cell" colSpan={colSpan}>暂无记录</td></tr>
}

async function fetchJson(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail || `${path} 返回 ${response.status}`)
  }
  return response.json()
}

function buildCoverageRows(overview) {
  if (!overview) return []
  const a = overview?.aShare || {}
  return [
    coverage('stock_daily_bars', 'A股日线', a.dailyBars, '收益、波动与成交基础'),
    coverage('stock_adjust_factors', '股票复权因子', a.adjustFactors, '消除除权除息跳变'),
    coverage('stock_listings', '历史上市状态', a.stockListings, '控制幸存者偏差'),
    coverage('stock_limit_prices', '每日涨跌停', a.limitPrices, '开盘可成交约束'),
    coverage('stock_suspend_events', '停复牌事件', a.suspendEvents, '停牌与复牌审计', true),
    coverage('stock_daily_basic', '每日估值', a.dailyBasic, '估值与流动性因子'),
    coverage('stock_financial_indicators', '财务指标', a.financialIndicators, '公告日 point-in-time'),
    coverage('trade_calendars', '交易日历', a.tradeCalendar, '统一时间轴'),
    coverage('index_daily_bars', '指数日线', a.indexDailyBars, '正式比较基准'),
    coverage('fund_daily_bars', 'ETF 日线', a.fundDailyBars, 'ETF 时序研究'),
    coverage('fund_adjust_factors', 'ETF 复权因子', a.fundAdjustFactors, 'ETF 分红复权'),
    { name: 'industry_members', label: '申万历史成分', rows: a.industryMembers, symbols: a.industries?.rows, range: '-', status: Number(a.industryMembers) > 0 ? 'available' : 'empty', purpose: '历史行业股票池' },
  ]
}

function coverage(name, label, value, purpose, mayBeEmpty = false) {
  const rows = Number(value?.rows || 0)
  return {
    name,
    label,
    rows,
    symbols: value?.symbols,
    range: dateRange(value),
    status: rows > 0 || mayBeEmpty ? 'available' : 'empty',
    purpose,
  }
}

function isInventoryAvailable(data) {
  return data?.level === 'inventory' && data?.status === 'inventory_available'
}

function buildTechnicalSeries(bars) {
  const candles = bars
    .filter((bar) => [bar.open, bar.high, bar.low, bar.close].every((value) => Number.isFinite(Number(value))))
    .map((bar) => ({ time: bar.trade_date, open: Number(bar.open), high: Number(bar.high), low: Number(bar.low), close: Number(bar.close) }))
  const closes = candles.map((bar) => bar.close)
  return {
    candles,
    ma10: calcSma(candles, closes, 10),
    ma20: calcSma(candles, closes, 20),
    volume: bars.map((bar, index) => ({ time: bar.trade_date, value: Number(bar.vol || 0), color: candles[index]?.close >= candles[index]?.open ? '#d84b4b99' : '#07876199' })),
  }
}

function calcSma(candles, values, period) {
  return values.map((_, index) => {
    if (index + 1 < period) return null
    const slice = values.slice(index + 1 - period, index + 1)
    return { time: candles[index].time, value: slice.reduce((sum, value) => sum + value, 0) / period }
  }).filter(Boolean)
}

function dateRange(value) {
  if (!value?.minDate && !value?.maxDate) return '-'
  return `${value.minDate || '?'} → ${value.maxDate || '?'}`
}

function formatInt(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return Number(value).toLocaleString('zh-CN')
}

function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return `${Number(value).toFixed(2)}%`
}

function formatSignedPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function priceTone(value) {
  const number = Number(value)
  return number > 0 ? 'price-up' : number < 0 ? 'price-down' : ''
}

function formatWanYi(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  if (Math.abs(number) >= 10000) return `${(number / 10000).toFixed(2)}亿`
  return `${number.toFixed(2)}万`
}

function formatDailyAmount(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  if (Math.abs(number) >= 100000) return `${(number / 100000).toFixed(2)}亿`
  return `${(number / 10).toFixed(0)}万`
}

function formatDateTime(value) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function buildStockScreenPath(query, offset) {
  const params = new URLSearchParams({ limit: String(STOCK_PAGE_SIZE), offset: String(Math.max(0, offset)) })
  if (query.trim()) params.set('q', query.trim())
  return `/api/stocks/screen?${params.toString()}`
}

function recentCatalogRange() {
  const end = new Date()
  const start = new Date(end)
  start.setUTCFullYear(start.getUTCFullYear() - 1)
  return { startDate: start.toISOString().slice(0, 10), endDate: end.toISOString().slice(0, 10) }
}

function latestVersion(items = []) {
  return [...(items || [])].sort((left, right) => Number(right.version || 0) - Number(left.version || 0))[0] || null
}

function evaluationForPublication(detail, projection) {
  const evaluations = detail?.evaluations || []
  const published = latestVersion((detail?.publications || []).filter((item) => item.status === 'published'))
  const evaluationId = projection?.evaluation_id || published?.evaluation_id
  if (evaluationId) {
    return evaluations.find((item) => String(item.id) === String(evaluationId)) || null
  }
  return published ? null : latestVersion(evaluations)
}

function shortHash(value) {
  if (!value) return '-'
  return String(value).slice(0, 12)
}

function formatStructuredItem(item) {
  if (item === null || item === undefined) return '-'
  if (typeof item === 'string' || typeof item === 'number') return String(item)
  const preferred = ['statement', 'title', 'label', 'summary', 'description', 'rationale', 'reason', 'metric', 'name']
  for (const key of preferred) {
    if (item[key]) return String(item[key])
  }
  return JSON.stringify(item)
}

function conclusionTone(value) {
  if (value === '研究通过') return 'passed'
  if (value === '不通过' || value === '受阻') return 'failed'
  return 'conditional'
}

function inventoryLabel(value) {
  return isInventoryAvailable(value) ? '可用' : translateStatus(value?.status)
}

function translateStatus(value) {
  const text = String(value || 'unknown')
  const labels = {
    unknown: '未知', ready: '就绪', ok: '正常', success: '成功', succeeded: '执行成功', connected: '已连接', available: '可用',
    blocked: '受阻', failed: '失败', fail: '失败', error: '错误', empty: '空', queued: '排队中', running: '运行中', retrying: '重试中',
    interrupted: '已中断', pending: '待发布', published: '已发布', active: '进行中', stopped: '已停止', approved: '已批准', invalidated: '已失效', historical_import: '历史导入', evaluating: '评价中', completed: '已完成',
    inventory_available: '库存可用', stalled: '停滞', partial: '部分完成', loading: '加载中',
  }
  return labels[text.toLowerCase()] || text
}

function translateEventType(value) {
  const labels = {
    run_queued: '研究运行已排队', run_started: '研究运行已开始', run_succeeded: '研究运行完成', run_failed: '研究运行失败',
    evaluation_created: '研究评价已形成', publication_published: '研究结论已发布', research_stopped: '研究已停止',
  }
  return labels[String(value || '').toLowerCase()] || String(value || '未知事件')
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error)
}

const rootElement = document.getElementById('root')
if (rootElement) {
  const appRoot = Reflect.get(window, '__quantResearchRoot') || createRoot(rootElement)
  Reflect.set(window, '__quantResearchRoot', appRoot)
  appRoot.render(<App />)
}
