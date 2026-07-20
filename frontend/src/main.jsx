import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Activity,
  BarChart3,
  BookOpenCheck,
  ChevronRight,
  Clock3,
  Database,
  Globe2,
  ListChecks,
  RefreshCw,
  Server,
  ShieldCheck,
} from 'lucide-react'
import { AShareDataView } from './AShareDataView.jsx'
import { OperationsView } from './OperationsView.jsx'
import { ResearchCockpitView } from './ResearchCockpitView.jsx'
import { USDataBoundaryView } from './USDataBoundaryView.jsx'
import { Notice, isInventoryAvailable, translateStatus } from './viewSupport.jsx'
import './styles.css'

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
const STOCK_PAGE_SIZE = 50

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
  const stockRequestId = useRef(0)
  const catalogRequestId = useRef(0)
  const strategyRequestId = useRef(0)
  const researchRequestId = useRef(0)
  const selectedCodeRef = useRef('')
  const selectedCatalogRef = useRef({ kind: '', code: '' })
  const selectedStrategyIdRef = useRef('')
  const selectedResearchIdRef = useRef('')

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
      ['indices', '/api/indices?limit=1000'],
      ['funds', '/api/funds?limit=1000'],
      ['industries', '/api/industries?limit=1000'],
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
        failures.push(`${key}: ${errorMessage(result.reason)}`)
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
        if (!selectedCatalogRef.current.code && value.length) selectCatalog('index', value[0].tsCode)
      }
      if (key === 'funds') {
        setCatalogs((current) => ({ ...current, funds: value }))
        if (!selectedCatalogRef.current.code && value.length) selectCatalog('fund', value[0].tsCode)
      }
      if (key === 'industries') {
        setCatalogs((current) => ({ ...current, industries: value }))
        if (!selectedCatalogRef.current.code && value.length) selectCatalog('industry', value[0].indexCode)
      }
      if (key === 'usDb') setUsDb(value)
      if (key === 'strategies') {
        setStrategies(value)
        const current = selectedStrategyIdRef.current
        const next = value.some((item) => item.strategy_id === current) ? current : value[0]?.strategy_id || ''
        if (next !== current) selectStrategy(next)
        if (!value.length) {
          setResearchLoading(false)
          setStrategyProfile(null)
          setResearchDetail(null)
          setPublication(null)
        }
      }
    })
    const detailResults = refreshCoverage ? await Promise.all([
      selectedCodeRef.current ? loadSelectedStockData(selectedCodeRef.current) : Promise.resolve(true),
      selectedCatalogRef.current.code ? loadSelectedCatalogData(selectedCatalogRef.current) : Promise.resolve(true),
      refreshSelectedResearchData(selectedStrategyIdRef.current),
    ]) : [true]
    if (failures.length) setGlobalError(`部分只读数据读取失败：${failures.join('；')}`)
    if (refreshCoverage && !failures.length && detailResults.every(Boolean)) setLastUpdated(new Date())
    setResearchLoading(false)
    setLoading(false)
  }

  function applyStockPage(page) {
    setStockPage(page)
    const current = selectedCodeRef.current
    const nextCode = page.items.some((item) => item.ts_code === current) ? current : page.items[0]?.ts_code || ''
    selectStock(nextCode)
  }

  function selectStock(tsCode) {
    if (tsCode === selectedCodeRef.current) return
    selectedCodeRef.current = tsCode
    stockRequestId.current += 1
    setSelectedCode(tsCode)
    setStockDataCode('')
    setStockBars([])
    setStockDetail(null)
    setStockError('')
  }

  function selectCatalog(kind, code) {
    if (selectedCatalogRef.current.kind === kind && selectedCatalogRef.current.code === code) return
    const next = { kind, code }
    selectedCatalogRef.current = next
    catalogRequestId.current += 1
    setSelectedCatalog(next)
    setCatalogDetail({ ...next, bars: [], adjustments: [], members: [] })
    setCatalogError('')
  }

  const loadSelectedStockData = useCallback(async (requestedCode) => {
    const requestId = stockRequestId.current + 1
    stockRequestId.current = requestId
    setDetailLoading(true)
    setStockDataCode('')
    setStockBars([])
    setStockDetail(null)
    try {
      const [barsRes, detailRes] = await Promise.all([
        fetchJson(`/api/daily-bars?ts_code=${encodeURIComponent(requestedCode)}`),
        fetchJson(`/api/stocks/${encodeURIComponent(requestedCode)}/detail`),
      ])
      if (requestId !== stockRequestId.current || requestedCode !== selectedCodeRef.current) return false
      setStockDataCode(requestedCode)
      setStockBars(barsRes)
      setStockDetail(detailRes)
      setStockError('')
      return true
    } catch (err) {
      if (requestId === stockRequestId.current) setStockError(errorMessage(err))
      return false
    } finally {
      if (requestId === stockRequestId.current) setDetailLoading(false)
    }
  }, [])

  const loadSelectedCatalogData = useCallback(async (requested) => {
    const requestId = catalogRequestId.current + 1
    catalogRequestId.current = requestId
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
      const isCurrent = requestId === catalogRequestId.current
        && requested.kind === selectedCatalogRef.current.kind
        && requested.code === selectedCatalogRef.current.code
      if (isCurrent) setCatalogDetail(detail)
      return isCurrent
    } catch (err) {
      if (requestId === catalogRequestId.current) setCatalogError(errorMessage(err))
      return false
    } finally {
      if (requestId === catalogRequestId.current) setCatalogLoading(false)
    }
  }, [])

  const loadStrategyProfileData = useCallback(async (strategyId) => {
    const requestId = strategyRequestId.current + 1
    strategyRequestId.current = requestId
    try {
      const profile = await fetchJson(`/api/research/strategies/${encodeURIComponent(strategyId)}`)
      if (requestId !== strategyRequestId.current || strategyId !== selectedStrategyIdRef.current) return { ok: false, researchId: '' }
      setResearchError('')
      setStrategyProfile(profile)
      const currentResearchId = selectedResearchIdRef.current
      const nextResearchId = profile.formal_researches.some((item) => item.id === currentResearchId)
        ? currentResearchId
        : profile.formal_researches[0]?.id || ''
      if (nextResearchId !== currentResearchId) {
        selectedResearchIdRef.current = nextResearchId
        researchRequestId.current += 1
        setSelectedResearchId(nextResearchId)
        setResearchDetail(null)
        setPublication(null)
      }
      if (!profile.formal_researches.length) {
        setResearchDetail(null)
        setPublication(null)
      }
      return { ok: true, researchId: nextResearchId }
    } catch (err) {
      if (requestId === strategyRequestId.current) setResearchError(errorMessage(err))
      return { ok: false, researchId: '' }
    } finally {
      if (requestId === strategyRequestId.current) setResearchLoading(false)
    }
  }, [])

  const loadResearchDetailData = useCallback(async (researchId) => {
    const requestId = researchRequestId.current + 1
    researchRequestId.current = requestId
    try {
      const detail = await fetchJson(`/api/research/formal-researches/${encodeURIComponent(researchId)}`)
      if (requestId !== researchRequestId.current || researchId !== selectedResearchIdRef.current) return false
      const latest = [...detail.publications]
        .filter((item) => item.status === 'published')
        .sort((left, right) => right.version - left.version)[0]
      const projection = latest ? await fetchJson(`/api/research/publications/${encodeURIComponent(latest.id)}`) : null
      if (requestId !== researchRequestId.current || researchId !== selectedResearchIdRef.current) return false
      setResearchError('')
      setResearchDetail(detail)
      setPublication(projection)
      return true
    } catch (err) {
      if (requestId === researchRequestId.current) setResearchError(errorMessage(err))
      return false
    } finally {
      if (requestId === researchRequestId.current) setResearchLoading(false)
    }
  }, [])

  async function refreshSelectedResearchData(strategyId) {
    if (!strategyId) return true
    const profileResult = await loadStrategyProfileData(strategyId)
    if (!profileResult.ok || !profileResult.researchId) return profileResult.ok
    return loadResearchDetailData(profileResult.researchId)
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
    if (strategyId === selectedStrategyIdRef.current) return
    selectedStrategyIdRef.current = strategyId
    selectedResearchIdRef.current = ''
    strategyRequestId.current += 1
    researchRequestId.current += 1
    setResearchLoading(true)
    setResearchError('')
    setStrategyProfile(null)
    setSelectedResearchId('')
    setResearchDetail(null)
    setPublication(null)
    setSelectedStrategyId(strategyId)
  }

  function selectResearch(researchId) {
    if (researchId === selectedResearchIdRef.current) return
    selectedResearchIdRef.current = researchId
    researchRequestId.current += 1
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
    loadSelectedStockData(selectedCode)
    return () => { stockRequestId.current += 1 }
  }, [loadSelectedStockData, selectedCode])

  useEffect(() => {
    if (!selectedCatalog.code) return undefined
    loadSelectedCatalogData(selectedCatalog)
    return () => { catalogRequestId.current += 1 }
  }, [loadSelectedCatalogData, selectedCatalog])

  useEffect(() => {
    if (!selectedStrategyId) return undefined
    const timer = window.setTimeout(() => loadStrategyProfileData(selectedStrategyId), 0)
    return () => {
      window.clearTimeout(timer)
      strategyRequestId.current += 1
    }
  }, [loadStrategyProfileData, selectedStrategyId])

  useEffect(() => {
    if (!selectedResearchId) return undefined
    const timer = window.setTimeout(() => loadResearchDetailData(selectedResearchId), 0)
    return () => {
      window.clearTimeout(timer)
      researchRequestId.current += 1
    }
  }, [loadResearchDetailData, selectedResearchId])

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
        <Topbar activeView={activeView} health={health} loading={loading} lastUpdated={lastUpdated} onRefresh={() => refreshAll(true)} />
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
          {activeView === 'us-data' ? <USDataBoundaryView usDb={usDb} /> : null}
          {activeView === 'operations' ? <OperationsView health={health} readiness={readiness} coverageRows={coverageRows} syncRuns={syncRuns} /> : null}
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
        <div><b>量化研究</b><small>量化研究工作台</small></div>
      </div>
      <nav className="side-nav" aria-label="主导航">
        {NAV_ITEMS.map(({ id, label, eyebrow, icon: Icon }) => (
          <button className={activeView === id ? 'active' : ''} key={id} onClick={() => onNavigate(id)} aria-current={activeView === id ? 'page' : undefined}>
            <Icon size={18} /><span><small>{eyebrow}</small>{label}</span><ChevronRight size={14} />
          </button>
        ))}
      </nav>
      <div className="sidebar-foot">
        <div className="sidebar-readiness">
          <span><ShieldCheck size={15} /> 数据库存</span><strong>{inventoryCount}/2 可用</strong>
          <div className="mini-track"><i style={{ width: `${inventoryCount * 50}%` }} /></div>
        </div>
        <p><i /> 仅限研究</p><small>无券商连接 · 无真实交易</small>
      </div>
    </aside>
  )
}

function Topbar({ activeView, health, loading, lastUpdated, onRefresh }) {
  const current = NAV_ITEMS.find((item) => item.id === activeView) || NAV_ITEMS[0]
  return (
    <header className="topbar">
      <div className="page-identity"><span>{current.eyebrow}</span><h1>{current.label}</h1></div>
      <div className="system-strip" aria-label="系统状态">
        <SystemState label="API" value={translateStatus(health?.status)} healthy={health?.status === 'ok'} icon={Server} />
        <SystemState label="PostgreSQL" value={translateStatus(health?.database)} healthy={['connected', 'ok'].includes(health?.database)} icon={Database} />
        <SystemState label="同步 Worker" value={health?.worker ? `${translateStatus(health.worker.status)} · ${health.worker.ageSeconds ?? '-'} 秒` : '未知'} healthy={health?.worker?.status === 'ok' && !health.worker.stale} icon={Activity} />
        <SystemState label="同步队列" value={health?.queue ? `${health.queue.active} 个运行中` : '未知'} healthy={Boolean(health?.queue) && health.queue.status !== 'stalled'} icon={ListChecks} />
        <span className="updated-at"><Clock3 size={14} /> {lastUpdated ? lastUpdated.toLocaleTimeString() : '尚未刷新'}</span>
        <button className="primary-action" onClick={onRefresh} disabled={loading}><RefreshCw size={15} className={loading ? 'spin' : ''} />全局刷新</button>
      </div>
    </header>
  )
}

function SystemState({ label, value, healthy, icon: Icon }) {
  return <span className="system-state"><Icon size={14} /><b>{label}</b><i className={healthy ? 'good' : 'bad'} />{String(value)}</span>
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
  return { name, label, rows, symbols: value?.symbols, range: dateRange(value), status: rows > 0 || mayBeEmpty ? 'available' : 'empty', purpose }
}

function dateRange(value) {
  if (!value?.minDate && !value?.maxDate) return '-'
  return `${value.minDate || '?'} → ${value.maxDate || '?'}`
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

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error)
}

const rootElement = document.getElementById('root')
if (rootElement) {
  const appRoot = Reflect.get(window, '__quantResearchRoot') || createRoot(rootElement)
  Reflect.set(window, '__quantResearchRoot', appRoot)
  appRoot.render(<App />)
}
