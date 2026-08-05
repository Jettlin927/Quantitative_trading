import { useCallback, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Activity,
  BriefcaseBusiness,
  ChevronRight,
  Clock3,
  Database,
  Globe2,
  LayoutDashboard,
  RefreshCw,
  Server,
} from 'lucide-react'
import { AnalysisWorkspaceView } from './AnalysisWorkspaceView.jsx'
import { OperationsView } from './OperationsView.jsx'
import { InstrumentWorkspaceView } from './InstrumentWorkspaceView.jsx'
import { PersonalTodayView } from './PersonalTodayView.jsx'
import { PortfolioView } from './PortfolioView.jsx'
import { browserPersonalJourneyClient } from './personalJourneyClient.js'
import { browserReadAdapter, systemClock } from './readAdapter.js'
import { Notice, translateStatus } from './viewSupport.jsx'
import './styles.css'

const NAV_ITEMS = [
  { id: 'today', path: '/today', label: '今日工作台', eyebrow: '持仓事项优先', icon: LayoutDashboard },
  { id: 'portfolio', path: '/portfolio', label: '我的持仓', eyebrow: '私有手工账本', icon: BriefcaseBusiness },
  { id: 'markets', path: '/markets/us', label: '市场与标的', eyebrow: '标的与证据', icon: Globe2 },
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
  const [loading, setLoading] = useState(false)
  const [globalError, setGlobalError] = useState('')
  const [lastUpdated, setLastUpdated] = useState(null)

  async function refreshAll(markUpdated = false) {
    setLoading(true)
    setGlobalError('')
    const requests = [['health', '/api/health?include_counts=false']]
    const results = await Promise.allSettled(requests.map(([, path]) => readAdapter({ path })))
    const failures = []
    results.forEach((result, index) => {
      const key = requests[index][0]
      if (result.status === 'rejected') {
        failures.push(`${key}: ${errorMessage(result.reason)}`)
        return
      }
      const value = result.value
      if (key === 'health') setHealth(value)
    })
    if (failures.length) setGlobalError(`部分只读数据读取失败：${failures.join('；')}`)
    if (markUpdated && !failures.length) setLastUpdated(clock.now())
    setLoading(false)
  }

  useEffect(() => {
    const timer = window.setTimeout(() => refreshAll(), 0)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeView, pathname])

  return (
    <div className="app-frame">
      <Sidebar activeView={activeView} onNavigate={navigate} />
      <div className="workspace">
        <Topbar activeView={activeView} health={health} loading={loading} lastUpdated={lastUpdated} onRefresh={() => refreshAll(true)} />
        <main className="workspace-main">
          {globalError ? <Notice tone="warning" title="部分数据暂不可用" text={globalError} /> : null}
          {activeView === 'today' ? <div className="today-stack"><PersonalTodayView client={personalClient} chartAdapter={chartAdapter} onNavigate={navigate} /><AnalysisWorkspaceView client={personalClient} subjectId="" /></div> : null}
          {activeView === 'portfolio' ? <PortfolioView client={personalClient} /> : null}
          {activeView === 'markets' ? <div key={personalInstrumentSymbol(pathname)}><InstrumentWorkspaceView client={personalClient} symbol={personalInstrumentSymbol(pathname)} chartAdapter={chartAdapter} onNavigate={navigate} /></div> : null}
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
  if (pathname.startsWith('/rules')) return 'today'
  if (pathname.startsWith('/research') || pathname.startsWith('/strategies')) return 'today'
  if (pathname.startsWith('/system')) return 'system'
  return 'today'
}

function personalInstrumentSymbol(pathname) {
  const value = pathname.split('/')[3] || ''
  return decodeURIComponent(value)
}

function useWorkspaceRoute({ initialPath, browserHistory }) {
  const requestedPath = initialPath || window.location.pathname
  const [pathname, setPathname] = useState(canonicalWorkspacePath(requestedPath))

  useEffect(() => {
    if (!browserHistory) return undefined
    const canonicalPath = canonicalWorkspacePath(window.location.pathname)
    if (window.location.pathname !== canonicalPath) window.history.replaceState(null, '', canonicalPath)
    const handlePopState = () => {
      const nextPath = canonicalWorkspacePath(window.location.pathname)
      if (window.location.pathname !== nextPath) window.history.replaceState(null, '', nextPath)
      setPathname(nextPath)
    }
    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [browserHistory])

  const navigate = useCallback((path) => {
    if (browserHistory && window.location.pathname !== path) window.history.pushState(null, '', path)
    setPathname(path)
  }, [browserHistory])

  return { pathname, navigate }
}

function canonicalWorkspacePath(pathname) {
  return routeView(pathname) === 'today' && !pathname.startsWith('/today') ? '/today' : pathname
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
