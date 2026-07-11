import { useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Activity,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Database,
  DownloadCloud,
  Gauge,
  History,
  ListChecks,
  RefreshCw,
  Search,
  Server,
  ShieldCheck,
  Table2,
  XCircle,
} from 'lucide-react'
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  createChart,
} from 'lightweight-charts'
import './styles.css'

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

const TERMINAL_JOB_STATUSES = new Set(['ok', 'partial', 'failed'])
const CHART_RANGES = [
  { id: 'recent', label: '近 180 日' },
  { id: '1y', label: '近 1 年' },
  { id: '3y', label: '近 3 年' },
  { id: '5y', label: '近 5 年' },
  { id: 'all', label: '全部历史' },
]

const NAV_ITEMS = [
  { id: 'overview', label: '研究总览', eyebrow: 'OVERVIEW', icon: Gauge },
  { id: 'data', label: '数据资产', eyebrow: 'DATA ASSETS', icon: Database },
  { id: 'stocks', label: '标的研究', eyebrow: 'SECURITY LAB', icon: BarChart3 },
  { id: 'runs', label: '运行记录', eyebrow: 'RUN LOG', icon: History },
]

function App() {
  const [activeView, setActiveView] = useState('overview')
  const [health, setHealth] = useState(null)
  const [overview, setOverview] = useState(null)
  const [syncProgress, setSyncProgress] = useState(null)
  const [readiness, setReadiness] = useState({ stocks: null, etf: null })
  const [stocks, setStocks] = useState([])
  const [usDb, setUsDb] = useState(null)
  const [query, setQuery] = useState('')
  const [selectedCode, setSelectedCode] = useState('')
  const [stockBars, setStockBars] = useState([])
  const [fundamentals, setFundamentals] = useState(null)
  const [loading, setLoading] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [actionJobs, setActionJobs] = useState({})
  const [actionResult, setActionResult] = useState(null)
  const [error, setError] = useState('')
  const [lastUpdated, setLastUpdated] = useState(null)
  const pollingJobsRef = useRef(new Set())

  async function refreshAll(refreshCoverage = false) {
    setLoading(true)
    setError('')
    try {
      const q = query.trim()
      const [healthRes, progressRes, stockReadyRes, etfReadyRes, stockListRes, usDbRes, jobsRes] = await Promise.all([
        fetchJson('/api/health?include_counts=false'),
        fetchJson('/api/tushare/sync-progress?include_coverage=false'),
        fetchJson('/api/research/readiness?scope=a_share_cross_section'),
        fetchJson('/api/research/readiness?scope=etf_time_series'),
        fetchJson(`/api/stocks?limit=120${q ? `&q=${encodeURIComponent(q)}` : ''}`),
        fetchJson('/api/us-research/db-overview'),
        fetchJson('/api/sync-jobs?limit=50'),
      ])
      setHealth(healthRes)
      setSyncProgress(progressRes)
      setReadiness({ stocks: stockReadyRes, etf: etfReadyRes })
      setStocks(stockListRes)
      setUsDb(usDbRes)
      setActionJobs(latestJobsByAction(jobsRes))
      setLastUpdated(new Date())
      if (!selectedCode && stockListRes[0]) setSelectedCode(stockListRes[0].ts_code)
      for (const job of jobsRes) {
        if (isActiveJob(job)) void pollSyncJob(job.action, job.id)
      }
      void fetchJson(`/api/db/overview${refreshCoverage ? '?refresh=true' : ''}`)
        .then((overviewRes) => setOverview(overviewRes))
        .catch((err) => setError(errorMessage(err)))
      void fetchJson(`/api/stocks/screen?limit=120${q ? `&q=${encodeURIComponent(q)}` : ''}`)
        .then((stocksRes) => setStocks(stocksRes))
        .catch((err) => setError(errorMessage(err)))
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  async function searchStocks() {
    setLoading(true)
    setError('')
    try {
      const q = query.trim()
      const result = await fetchJson(`/api/stocks/screen?limit=120${q ? `&q=${encodeURIComponent(q)}` : ''}`)
      setStocks(result)
      if (result[0] && !result.some((item) => item.ts_code === selectedCode)) setSelectedCode(result[0].ts_code)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  async function runDataAction(actionId) {
    setActionResult(null)
    setError('')
    try {
      const request = buildSyncJobRequest(actionId)
      const job = await fetchJson('/api/sync-jobs', { method: 'POST', body: JSON.stringify(request) })
      setActionJobs((current) => ({ ...current, [actionId]: job }))
      void pollSyncJob(actionId, job.id)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  async function pollSyncJob(actionId, jobId) {
    if (!jobId || pollingJobsRef.current.has(jobId)) return
    pollingJobsRef.current.add(jobId)
    try {
      let job
      do {
        await delay(1500)
        job = await fetchJson(`/api/sync-jobs/${encodeURIComponent(jobId)}`)
        setActionJobs((current) => ({ ...current, [actionId]: job }))
      } while (!TERMINAL_JOB_STATUSES.has(String(job.status).toLowerCase()))
      setActionResult(job)
      await refreshAll(true)
    } catch (err) {
      setActionJobs((current) => ({
        ...current,
        [actionId]: { ...current[actionId], status: 'poll-error', message: `任务状态读取失败：${errorMessage(err)}` },
      }))
      setError(errorMessage(err))
    } finally {
      pollingJobsRef.current.delete(jobId)
    }
  }

  useEffect(() => {
    refreshAll()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!selectedCode) return undefined
    let ignore = false
    async function loadSelectedStock() {
      setDetailLoading(true)
      try {
        const [barsRes, fundamentalsRes] = await Promise.all([
          fetchJson(`/api/daily-bars?ts_code=${encodeURIComponent(selectedCode)}`),
          fetchJson(`/api/stocks/${encodeURIComponent(selectedCode)}/fundamentals`),
        ])
        if (!ignore) {
          setStockBars(barsRes)
          setFundamentals(fundamentalsRes)
        }
      } catch (err) {
        if (!ignore) setError(errorMessage(err))
      } finally {
        if (!ignore) setDetailLoading(false)
      }
    }
    loadSelectedStock()
    return () => {
      ignore = true
    }
  }, [selectedCode, stocks])

  const coverageRows = useMemo(() => buildCoverageRows(overview), [overview])
  const syncRuns = useMemo(() => syncProgress?.runs || [], [syncProgress])
  const auditItems = useMemo(() => buildAuditItems(readiness, syncRuns), [readiness, syncRuns])
  const selectedStock = stocks.find((stock) => stock.ts_code === selectedCode) || stocks[0] || null
  const selectedLatestBar = stockBars[stockBars.length - 1] || selectedStock || null

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
          {error ? <Notice tone="error" title="操作未完成" text={error} /> : null}
          {actionResult ? (
            <Notice
              tone={actionResult.status === 'ok' ? 'success' : actionResult.status === 'failed' ? 'error' : 'warning'}
              title={actionResult.status === 'ok' ? '同步任务已完成' : actionResult.status === 'failed' ? '同步任务失败' : '同步任务部分完成'}
              text={actionResult.message || `写入 ${formatInt(actionResult.rowsUpserted)} 行；覆盖与 readiness 已刷新。`}
            />
          ) : null}

          {activeView === 'overview' ? (
            <OverviewView
              readiness={readiness}
              coverageRows={coverageRows}
              auditItems={auditItems}
              syncRuns={syncRuns}
              overview={overview}
              onOpenData={() => setActiveView('data')}
              onOpenStocks={() => setActiveView('stocks')}
            />
          ) : null}

          {activeView === 'data' ? (
            <DataAssetsView
              coverageRows={coverageRows}
              syncRuns={syncRuns}
              actionJobs={actionJobs}
              onRunAction={runDataAction}
            />
          ) : null}

          {activeView === 'stocks' ? (
            <StockLabView
              stocks={stocks}
              query={query}
              setQuery={setQuery}
              onSearch={searchStocks}
              selectedCode={selectedCode}
              setSelectedCode={setSelectedCode}
              selectedStock={selectedStock}
              selectedLatestBar={selectedLatestBar}
              stockBars={stockBars}
              fundamentals={fundamentals}
              detailLoading={detailLoading}
            />
          ) : null}

          {activeView === 'runs' ? (
            <RunLogView syncRuns={syncRuns} usDb={usDb} actionJobs={actionJobs} onRunAction={runDataAction} />
          ) : null}
        </main>
      </div>
    </div>
  )
}

function Sidebar({ activeView, onNavigate, readiness }) {
  const readyCount = [readiness.stocks, readiness.etf].filter((item) => item?.status === 'ready').length
  return (
    <aside className="sidebar">
      <div className="brand-lockup">
        <span className="brand-mark"><Activity size={18} /></span>
        <div>
          <b>QUANT RESEARCH</b>
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
          <span><ShieldCheck size={15} /> 研究门禁</span>
          <strong>{readyCount}/2 READY</strong>
          <div className="mini-track"><i style={{ width: `${readyCount * 50}%` }} /></div>
        </div>
        <p><i /> RESEARCH ONLY</p>
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
        <SystemState label="API" value={health?.status || 'UNKNOWN'} healthy={healthy} icon={Server} />
        <SystemState label="PostgreSQL" value={health?.database || 'UNKNOWN'} healthy={['connected', 'ok'].includes(health?.database)} icon={Database} />
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
      {String(value).toUpperCase()}
    </span>
  )
}

function OverviewView({ readiness, coverageRows, auditItems, syncRuns, overview, onOpenData, onOpenStocks }) {
  const aShare = overview?.aShare || {}
  return (
    <div className="view-stack enter">
      <section className="readiness-grid">
        <ReadinessCard
          title="A股横截面"
          eyebrow="CROSS-SECTION"
          data={readiness.stocks}
          accent="cyan"
          onAction={onOpenData}
        />
        <ReadinessCard
          title="ETF 时序"
          eyebrow="TIME-SERIES"
          data={readiness.etf}
          accent="amber"
          onAction={onOpenData}
        />
        <AuditCard items={auditItems} />
      </section>

      <section className="overview-grid">
        <Panel title="数据覆盖矩阵" eyebrow="DATA COVERAGE" action="查看全部" onAction={onOpenData} className="coverage-panel">
          <CoverageMatrix rows={coverageRows.slice(0, 8)} />
        </Panel>
        <Panel title="最近同步任务" eyebrow="SYNC QUEUE" action="运行记录" onAction={onOpenData} className="queue-panel">
          <RunTable runs={syncRuns.slice(0, 7)} compact />
        </Panel>
      </section>

      <section className="summary-ribbon" aria-label="数据库摘要">
        <SummaryMetric label="A股标的" value={formatInt(aShare.stocks)} detail="stocks" />
        <SummaryMetric label="日线记录" value={formatCompact(aShare.dailyBars?.rows)} detail={dateRange(aShare.dailyBars)} />
        <SummaryMetric label="复权因子" value={formatCompact(aShare.adjustFactors?.rows)} detail={`${formatInt(aShare.adjustFactors?.symbols)} symbols`} />
        <SummaryMetric label="指数日线" value={formatCompact(aShare.indexDailyBars?.rows)} detail={`${formatInt(aShare.indices?.rows)} indices`} />
        <SummaryMetric label="ETF 日线" value={formatCompact(aShare.fundDailyBars?.rows)} detail={`${formatInt(aShare.funds?.rows)} funds`} />
        <button className="summary-cta" onClick={onOpenStocks}>
          进入标的研究 <ChevronRight size={16} />
        </button>
      </section>
    </div>
  )
}

function ReadinessCard({ title, eyebrow, data, accent, onAction }) {
  const ready = data?.status === 'ready'
  const required = data?.requiredTables?.length || 0
  const blockers = data?.blockers?.length || 0
  const progress = required ? Math.round(((required - blockers) / required) * 100) : 0
  const issues = [...(data?.missingTables || []), ...(data?.emptyTables || [])]
  return (
    <article className={`readiness-card ${accent} ${ready ? 'ready' : 'blocked'}`}>
      <header>
        <div className="readiness-icon">{ready ? <CheckCircle2 size={23} /> : <AlertTriangle size={23} />}</div>
        <div><span>{eyebrow}</span><h2>{title}</h2></div>
        <Badge value={ready ? 'READY' : 'BLOCKED'} />
      </header>
      <div className="readiness-progress">
        <div><span>数据门禁完成度</span><strong>{progress}%</strong></div>
        <div className="progress-track"><i style={{ width: `${progress}%` }} /></div>
      </div>
      <div className="readiness-detail">
        <span>{ready ? '研究数据合同已满足' : `发现 ${issues.length} 个阻断项`}</span>
        <ul>
          {(issues.length ? issues : ['复权、基准与时间轴已就绪']).slice(0, 3).map((item) => (
            <li key={item}><i /> {formatTableName(item)}</li>
          ))}
        </ul>
      </div>
      <button className="card-action" onClick={onAction}>
        {ready ? '检查覆盖明细' : '补齐缺失数据'} <ChevronRight size={15} />
      </button>
    </article>
  )
}

function AuditCard({ items }) {
  const problems = items.filter((item) => item.tone !== 'good')
  return (
    <article className="audit-card">
      <header><div><span>AUDIT & ANOMALY</span><h2>异常与审计</h2></div><Badge value={problems.length ? `${problems.length} ISSUES` : 'CLEAR'} /></header>
      <div className="audit-list">
        {items.slice(0, 5).map((item) => (
          <div className={`audit-item ${item.tone}`} key={item.id}>
            {item.tone === 'good' ? <CheckCircle2 size={15} /> : item.tone === 'bad' ? <XCircle size={15} /> : <AlertTriangle size={15} />}
            <span><b>{item.title}</b><small>{item.detail}</small></span>
          </div>
        ))}
      </div>
    </article>
  )
}

function DataAssetsView({ coverageRows, syncRuns, actionJobs, onRunAction }) {
  return (
    <div className="view-stack enter">
      <section className="section-heading">
        <div><span>DATA OPERATIONS</span><h2>数据资产与同步控制</h2><p>先补齐门禁数据，再开始研究。按钮使用服务器环境中的 Tushare token，不会在浏览器中读取凭据。</p></div>
      </section>

      <section className="action-grid">
        <SyncAction
          id="trade_calendar"
          title="刷新交易日历"
          detail="同步最近 3 个月交易日，校准所有研究时间轴。"
          icon={Clock3}
          job={actionJobs.trade_calendar}
          onRun={onRunAction}
        />
        <SyncAction
          id="stock_listings"
          title="刷新历史上市状态"
          detail="同步 L / D / P / G，避免只看当前上市股票。"
          icon={ListChecks}
          job={actionJobs.stock_listings}
          onRun={onRunAction}
        />
        <SyncAction
          id="market_bundle"
          title="补齐近 10 日市场数据"
          detail="批量补日线、涨跌停价格和停复牌事件。"
          icon={Activity}
          job={actionJobs.market_bundle}
          onRun={onRunAction}
        />
      </section>

      <section className="data-detail-grid">
        <Panel title="全部数据覆盖" eyebrow="POSTGRESQL TABLES" className="full-coverage">
          <CoverageMatrix rows={coverageRows} detailed />
        </Panel>
        <Panel title="同步执行记录" eyebrow="RECENT WRITES" className="full-runs">
          <RunTable runs={syncRuns} />
        </Panel>
      </section>
    </div>
  )
}

function SyncAction({ id, title, detail, icon: Icon, job, onRun }) {
  const running = isActiveJob(job)
  return (
    <article className={`sync-action ${running ? 'running' : ''}`}>
      <span className="sync-icon"><Icon size={20} /></span>
      <div><h3>{title}</h3><p>{detail}</p></div>
      {job ? (
        <div className="sync-job-state">
          <Badge value={job.status} />
          <span>{job.message || (job.status === 'queued' ? '任务已进入后台队列' : `最近写入 ${formatInt(job.rowsUpserted)} 行`)}</span>
        </div>
      ) : null}
      <button onClick={() => onRun(id)} disabled={running}>
        <RefreshCw size={14} className={running ? 'spin' : ''} /> {jobButtonText(job)}
      </button>
    </article>
  )
}

function StockLabView({
  stocks,
  query,
  setQuery,
  onSearch,
  selectedCode,
  setSelectedCode,
  selectedStock,
  selectedLatestBar,
  stockBars,
  fundamentals,
  detailLoading,
}) {
  const historyStart = stockBars[0]?.trade_date
  const historyEnd = stockBars[stockBars.length - 1]?.trade_date
  return (
    <div className="stock-lab enter">
      <aside className="security-browser">
        <header><div><span>SECURITY UNIVERSE</span><h2>股票浏览</h2></div><b>{formatInt(stocks.length)}</b></header>
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
      </aside>

      <section className="market-chart-panel">
        <header className="security-title">
          <div><span>{selectedStock?.ts_code || 'NO SECURITY'}</span><h2>{selectedStock?.name || '请选择股票'}</h2></div>
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
        <header><span>POINT-IN-TIME FACTS</span><h2>估值与财务</h2></header>
        <FactGroup title={selectedLatestBar?.trade_date || selectedStock?.latest_date || '最新行情'}>
          <Fact label="开 / 高" value={`${formatNumber(selectedLatestBar?.open)} / ${formatNumber(selectedLatestBar?.high)}`} />
          <Fact label="低 / 收" value={`${formatNumber(selectedLatestBar?.low)} / ${formatNumber(selectedLatestBar?.close)}`} strong />
          <Fact label="成交额" value={formatDailyAmount(selectedLatestBar?.amount)} />
        </FactGroup>
        <FactGroup title="估值">
          <Fact label="总市值" value={formatWanYi(fundamentals?.valuation?.totalMv)} />
          <Fact label="PE TTM" value={formatNumber(fundamentals?.valuation?.peTtm)} />
          <Fact label="PB" value={formatNumber(fundamentals?.valuation?.pb)} />
          <Fact label="换手率" value={formatPercent(fundamentals?.valuation?.turnoverRate)} />
        </FactGroup>
        <FactGroup title="财务（公告后可见）">
          <Fact label="公告日" value={fundamentals?.financial?.annDate || '-'} />
          <Fact label="ROE" value={formatPercent(fundamentals?.financial?.roe)} />
          <Fact label="毛利率" value={formatPercent(fundamentals?.financial?.grossprofitMargin)} />
          <Fact label="营收同比" value={formatPercent(fundamentals?.financial?.trYoy)} />
          <Fact label="净利同比" value={formatPercent(fundamentals?.financial?.netprofitYoy)} />
        </FactGroup>
      </aside>
    </div>
  )
}

function RunLogView({ syncRuns, usDb, actionJobs, onRunAction }) {
  const usAssets = usDb?.assets || []
  const counts = usDb?.counts || {}
  const usJob = actionJobs.us_sample
  const usJobRunning = isActiveJob(usJob)
  return (
    <div className="view-stack enter">
      <section className="section-heading run-heading">
        <div><span>RUN HISTORY</span><h2>同步与样本运行记录</h2><p>这里记录数据写入事实，不展示策略评级、收益承诺或真实账户。</p></div>
        <div className="run-job-action">
          {usJob ? <span><Badge value={usJob.status} /> {usJob.message || `最近写入 ${formatInt(usJob.rowsUpserted)} 行`}</span> : null}
          <button className="primary-action" onClick={() => onRunAction('us_sample')} disabled={usJobRunning}>
            <DownloadCloud size={15} /> {usJobRunning ? jobButtonText(usJob) : '导入美股 sample'}
          </button>
        </div>
      </section>
      <Panel title="最近 20 次同步" eyebrow="DATA SYNC RUNS"><RunTable runs={syncRuns} /></Panel>
      <Panel title="美股 Sample 资产" eyebrow={`${formatInt(counts.assets)} ASSETS · ${formatInt(counts.assetDailyPrices)} PRICES`}>
        <div className="table-scroll">
          <table className="data-table">
            <thead><tr><th>SYMBOL</th><th>名称</th><th>类型</th><th>风险标签</th><th>最新收盘</th></tr></thead>
            <tbody>
              {usAssets.map((asset) => (
                <tr key={asset.naturalKey || asset.symbol}>
                  <td className="mono strong">{asset.symbol}</td><td>{asset.name || '-'}</td><td>{asset.instrumentType || '-'}</td><td>{asset.riskTag || '-'}</td><td>{formatNumber(asset.latestPrice?.close)}</td>
                </tr>
              ))}
              {!usAssets.length ? <EmptyRow colSpan={5} /> : null}
            </tbody>
          </table>
        </div>
      </Panel>
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
  const text = String(value || 'UNKNOWN').toUpperCase()
  const tone = ['READY', 'OK', 'SUCCESS', 'CONNECTED', 'AVAILABLE'].includes(text)
    ? 'good'
    : ['BLOCKED', 'FAILED', 'FAIL', 'ERROR', 'EMPTY'].includes(text)
      ? 'bad'
      : 'warn'
  return <span className={`badge ${tone}`}><i />{text}</span>
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

function buildSyncJobRequest(action) {
  const endDate = todayString()
  if (action === 'stock_listings') {
    return { action, payload: { statuses: ['L', 'D', 'P', 'G'] } }
  }
  if (action === 'trade_calendar') {
    return { action, payload: { start_date: dateMonthsBefore(endDate, 3), end_date: endDate, exchange: '' } }
  }
  if (action === 'market_bundle') {
    return {
      action,
      payload: {
        start_date: dateDaysBefore(endDate, 10),
        end_date: endDate,
        skip_existing: true,
        min_existing_rows: 4000,
        max_trade_dates: 10,
      },
    }
  }
  if (action === 'us_sample') return { action, payload: {} }
  throw new Error(`未知同步动作：${action}`)
}

function latestJobsByAction(jobs) {
  const latest = {}
  for (const job of jobs || []) {
    const current = latest[job.action]
    if (!current || String(job.createdAt || '') > String(current.createdAt || '')) latest[job.action] = job
  }
  return latest
}

function isActiveJob(job) {
  return ['queued', 'running'].includes(String(job?.status || '').toLowerCase())
}

function jobButtonText(job) {
  const status = String(job?.status || '').toLowerCase()
  if (status === 'queued') return '已排队'
  if (status === 'running') return '后台同步中'
  return '立即执行'
}

function delay(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
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

function buildAuditItems(readiness, syncRuns) {
  const items = []
  for (const [scope, data] of [['A股横截面', readiness.stocks], ['ETF 时序', readiness.etf]]) {
    if (!data) continue
    if (data.status === 'ready') {
      items.push({ id: `${scope}-ready`, title: `${scope}门禁通过`, detail: '关键表存在且非空', tone: 'good' })
    } else {
      for (const blocker of (data.blockers || []).slice(0, 3)) {
        items.push({ id: `${scope}-${blocker}`, title: `${scope}被阻断`, detail: blocker.replace(':', ' · '), tone: 'warn' })
      }
    }
  }
  const latestByTarget = new Map()
  for (const run of syncRuns) {
    if (!latestByTarget.has(run.target)) latestByTarget.set(run.target, run)
  }
  const failed = [...latestByTarget.values()].filter((run) => !['ok', 'success'].includes(String(run.status).toLowerCase()))
  if (failed.length) items.push({ id: 'sync-failed', title: '存在非成功同步', detail: `${failed.length} 个最近任务需要检查`, tone: 'bad' })
  if (!items.length) items.push({ id: 'waiting', title: '等待数据检查', detail: '刷新后生成审计结果', tone: 'warn' })
  return items
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

function formatTableName(value) {
  const labels = {
    stock_listings: '历史上市状态', stock_limit_prices: '每日涨跌停', stock_suspend_events: '停复牌事件',
    stock_adjust_factors: '股票复权因子', fund_adjust_factors: 'ETF 复权因子', index_daily_bars: '指数基准日线',
  }
  return labels[value] || String(value).replaceAll('_', ' ')
}

function dateRange(value) {
  if (!value?.minDate && !value?.maxDate) return '-'
  return `${value.minDate || '?'} → ${value.maxDate || '?'}`
}

function todayString() {
  return new Date().toISOString().slice(0, 10)
}

function dateMonthsBefore(dateText, months) {
  const date = new Date(`${dateText}T00:00:00`)
  date.setMonth(date.getMonth() - months)
  return date.toISOString().slice(0, 10)
}

function dateDaysBefore(dateText, days) {
  const date = new Date(`${dateText}T00:00:00`)
  date.setDate(date.getDate() - days)
  return date.toISOString().slice(0, 10)
}

function formatInt(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return Number(value).toLocaleString('zh-CN')
}

function formatCompact(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return new Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 1 }).format(Number(value))
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

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error)
}

const rootElement = document.getElementById('root')
const appRoot = Reflect.get(window, '__quantResearchRoot') || createRoot(rootElement)
Reflect.set(window, '__quantResearchRoot', appRoot)
appRoot.render(<App />)
