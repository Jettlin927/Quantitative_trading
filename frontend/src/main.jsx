import { useCallback, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Activity,
  BookOpenCheck,
  BriefcaseBusiness,
  ChevronRight,
  Clock3,
  Database,
  Globe2,
  LayoutDashboard,
  ListChecks,
  RefreshCw,
  Server,
} from 'lucide-react'
import { AnalysisWorkspaceView } from './AnalysisWorkspaceView.jsx'
import { OperationsView } from './OperationsView.jsx'
import { InstrumentWorkspaceView } from './InstrumentWorkspaceView.jsx'
import { PersonalTodayView } from './PersonalTodayView.jsx'
import { PortfolioView } from './PortfolioView.jsx'
import { ResearchCockpitView } from './ResearchCockpitView.jsx'
import { RulesView } from './RulesView.jsx'
import { browserPersonalJourneyClient } from './personalJourneyClient.js'
import { browserReadAdapter, systemClock } from './readAdapter.js'
import { Notice, translateStatus } from './viewSupport.jsx'
import './styles.css'

const NAV_ITEMS = [
  { id: 'today', path: '/today', label: '今日工作台', eyebrow: '持仓事项优先', icon: LayoutDashboard },
  { id: 'portfolio', path: '/portfolio', label: '我的持仓', eyebrow: '私有手工账本', icon: BriefcaseBusiness },
  { id: 'markets', path: '/markets/us', label: '市场与标的', eyebrow: '标的与证据', icon: Globe2 },
  { id: 'rules', path: '/rules', label: '规则与策略', eyebrow: '确定性四态', icon: ListChecks },
  { id: 'research', path: '/research', label: '研究驾驶舱', eyebrow: '正式研究隔离', icon: BookOpenCheck },
  { id: 'system', path: '/system', label: '数据与系统', eyebrow: '授权与健康', icon: Server },
]

/** @param {any} [appProps] */
export function App(appProps = {}) {
  const { initialPath = '/today', ...props } = appProps
  return <WorkspaceApp {...props} initialPath={initialPath} />
}

function WorkspaceApp({ readAdapter = browserReadAdapter, personalClient = browserPersonalJourneyClient, clock = systemClock, chartAdapter = undefined, initialPath = null, browserHistory = false } = {}) {
  const { pathname, navigate } = useWorkspaceRoute({ initialPath, browserHistory })
  const activeView = routeView(pathname)
  const [health, setHealth] = useState(null)
  const [strategies, setStrategies] = useState([])
  const [selectedStrategyId, setSelectedStrategyId] = useState('')
  const [strategyProfile, setStrategyProfile] = useState(null)
  const [selectedResearchId, setSelectedResearchId] = useState('')
  const [researchDetail, setResearchDetail] = useState(null)
  const [publication, setPublication] = useState(null)
  const [publicationAnalytics, setPublicationAnalytics] = useState(null)
  const [loading, setLoading] = useState(false)
  const [researchLoading, setResearchLoading] = useState(false)
  const [globalError, setGlobalError] = useState('')
  const [researchError, setResearchError] = useState('')
  const [lastUpdated, setLastUpdated] = useState(null)
  const strategyRequestId = useRef(0)
  const researchRequestId = useRef(0)
  const selectedStrategyIdRef = useRef('')
  const selectedResearchIdRef = useRef('')

  async function refreshAll(refreshSelected = false) {
    setLoading(true)
    setResearchLoading(true)
    setGlobalError('')
    setResearchError('')
    const requests = [['health', '/api/health?include_counts=false']]
    if (activeView === 'research') requests.push(['strategies', '/api/research/strategies'])
    const results = await Promise.allSettled(requests.map(([, path]) => readAdapter({ path })))
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
          setPublicationAnalytics(null)
        }
      }
    })
    const detailRequests = []
    if (refreshSelected && activeView === 'research') {
      detailRequests.push(refreshSelectedResearchData(selectedStrategyIdRef.current))
    }
    const detailResults = refreshSelected ? await Promise.all(detailRequests) : [true]
    if (failures.length) setGlobalError(`部分只读数据读取失败：${failures.join('；')}`)
    const detailComplete = detailResults.every(Boolean)
    if (refreshSelected && !failures.length && detailComplete) setLastUpdated(clock.now())
    setResearchLoading(false)
    setLoading(false)
  }

  const loadStrategyProfileData = useCallback(async (strategyId) => {
    const requestId = strategyRequestId.current + 1
    strategyRequestId.current = requestId
    try {
      const profile = await readAdapter({ path: `/api/research/strategies/${encodeURIComponent(strategyId)}` })
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
        setPublicationAnalytics(null)
      }
      if (!profile.formal_researches.length) {
        setResearchDetail(null)
        setPublication(null)
        setPublicationAnalytics(null)
      }
      return { ok: true, researchId: nextResearchId }
    } catch (err) {
      if (requestId === strategyRequestId.current) setResearchError(errorMessage(err))
      return { ok: false, researchId: '' }
    } finally {
      if (requestId === strategyRequestId.current) setResearchLoading(false)
    }
  }, [readAdapter])

  const loadResearchDetailData = useCallback(async (researchId) => {
    const requestId = researchRequestId.current + 1
    researchRequestId.current = requestId
    try {
      const detail = await readAdapter({ path: `/api/research/formal-researches/${encodeURIComponent(researchId)}` })
      if (requestId !== researchRequestId.current || researchId !== selectedResearchIdRef.current) return false
      const latest = [...detail.publications]
        .filter((item) => item.status === 'published')
        .sort((left, right) => right.version - left.version)[0]
      const projection = latest ? await readAdapter({ path: `/api/research/publications/${encodeURIComponent(latest.id)}` }) : null
      let analytics = null
      let analyticsError = ''
      if (projection?.analytics_url) {
        try {
          analytics = await readAdapter({ path: projection.analytics_url })
        } catch (err) {
          analyticsError = errorMessage(err)
        }
      }
      if (requestId !== researchRequestId.current || researchId !== selectedResearchIdRef.current) return false
      setResearchError(analyticsError)
      setResearchDetail(detail)
      setPublication(projection)
      setPublicationAnalytics(analytics)
      return !analyticsError
    } catch (err) {
      if (requestId === researchRequestId.current) setResearchError(errorMessage(err))
      return false
    } finally {
      if (requestId === researchRequestId.current) setResearchLoading(false)
    }
  }, [readAdapter])

  async function refreshSelectedResearchData(strategyId) {
    if (!strategyId) return true
    const profileResult = await loadStrategyProfileData(strategyId)
    if (!profileResult.ok || !profileResult.researchId) return profileResult.ok
    return loadResearchDetailData(profileResult.researchId)
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
    setPublicationAnalytics(null)
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
    setPublicationAnalytics(null)
    setSelectedResearchId(researchId)
  }

  useEffect(() => {
    const timer = window.setTimeout(() => refreshAll(), 0)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeView, pathname])

  useEffect(() => {
    if (activeView !== 'research' || !selectedStrategyId) return undefined
    const timer = window.setTimeout(() => loadStrategyProfileData(selectedStrategyId), 0)
    return () => {
      window.clearTimeout(timer)
      strategyRequestId.current += 1
    }
  }, [activeView, loadStrategyProfileData, selectedStrategyId])

  useEffect(() => {
    if (activeView !== 'research' || !selectedResearchId) return undefined
    const timer = window.setTimeout(() => loadResearchDetailData(selectedResearchId), 0)
    return () => {
      window.clearTimeout(timer)
      researchRequestId.current += 1
    }
  }, [activeView, loadResearchDetailData, selectedResearchId])

  return (
    <div className="app-frame">
      <Sidebar activeView={activeView} onNavigate={navigate} />
      <div className="workspace">
        <Topbar activeView={activeView} health={health} loading={loading} lastUpdated={lastUpdated} onRefresh={() => refreshAll(true)} />
        <main className="workspace-main">
          {globalError ? <Notice tone="warning" title="部分数据暂不可用" text={globalError} /> : null}
          {activeView === 'today' ? <div className="today-stack"><PersonalTodayView client={personalClient} chartAdapter={chartAdapter} onNavigate={navigate} /><AnalysisWorkspaceView client={personalClient} subjectId="" /></div> : null}
          {activeView === 'portfolio' ? <PortfolioView client={personalClient} /> : null}
          {activeView === 'rules' ? <RulesView client={personalClient} /> : null}
          {activeView === 'markets' ? <div key={personalInstrumentSymbol(pathname)}><InstrumentWorkspaceView client={personalClient} symbol={personalInstrumentSymbol(pathname)} chartAdapter={chartAdapter} onNavigate={navigate} /></div> : null}
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
              analytics={publicationAnalytics}
              loading={researchLoading}
              error={researchError}
            />
          ) : null}
          {activeView === 'system' ? <OperationsView health={health} /> : null}
        </main>
      </div>
    </div>
  )
}

function Sidebar({ activeView, onNavigate }) {
  return (
    <aside className="sidebar">
      <div className="brand-lockup">
        <span className="brand-mark"><Activity size={18} /></span>
        <div><b>量化研究</b><small>量化研究工作台</small></div>
      </div>
      <nav className="side-nav" aria-label="主导航">
        {NAV_ITEMS.map(({ id, path, label, eyebrow, icon: Icon }) => (
          <button className={activeView === id ? 'active' : ''} key={id} onClick={() => onNavigate(path)} aria-current={activeView === id ? 'page' : undefined}>
            <Icon size={18} /><span><small>{eyebrow}</small>{label}</span><ChevronRight size={14} />
          </button>
        ))}
      </nav>
      <div className="sidebar-foot">
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
        <span className="updated-at"><Clock3 size={14} /> {lastUpdated ? `界面刷新 ${lastUpdated.toLocaleTimeString()}` : '尚未刷新'}</span>
        <button className="primary-action" onClick={onRefresh} disabled={loading} title="只刷新当前页面所需数据"><RefreshCw size={15} className={loading ? 'spin' : ''} />全局刷新</button>
      </div>
    </header>
  )
}

function routeView(pathname) {
  if (pathname === '/') return 'today'
  if (pathname.startsWith('/today')) return 'today'
  if (pathname.startsWith('/portfolio')) return 'portfolio'
  if (pathname.startsWith('/markets/a-share')) return 'today'
  if (pathname.startsWith('/markets/')) return 'markets'
  if (pathname.startsWith('/rules')) return 'rules'
  if (pathname.startsWith('/research') || pathname.startsWith('/strategies')) return 'research'
  if (pathname.startsWith('/system')) return 'system'
  return 'today'
}

function personalInstrumentSymbol(pathname) {
  const value = pathname.split('/')[3] || ''
  return decodeURIComponent(value)
}

function useWorkspaceRoute({ initialPath, browserHistory }) {
  const requestedPath = initialPath || window.location.pathname
  const [pathname, setPathname] = useState(requestedPath === '/' ? '/today' : requestedPath)

  useEffect(() => {
    if (!browserHistory) return undefined
    if (window.location.pathname === '/') window.history.replaceState(null, '', '/today')
    const handlePopState = () => setPathname(window.location.pathname)
    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [browserHistory])

  const navigate = useCallback((path) => {
    if (browserHistory && window.location.pathname !== path) window.history.pushState(null, '', path)
    setPathname(path)
  }, [browserHistory])

  return { pathname, navigate }
}

function SystemState({ label, value, healthy, icon: Icon }) {
  return <span className="system-state"><Icon size={14} /><b>{label}</b><i className={healthy ? 'good' : 'bad'} />{String(value)}</span>
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error)
}

const rootElement = document.getElementById('root')
if (rootElement) {
  const appRoot = Reflect.get(window, '__quantResearchRoot') || createRoot(rootElement)
  Reflect.set(window, '__quantResearchRoot', appRoot)
  appRoot.render(<WorkspaceApp browserHistory />)
}
