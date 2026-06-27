import React, { useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Activity, Database, DownloadCloud, RefreshCw, Search, Server, Table2 } from 'lucide-react'
import './styles.css'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:18000'

function App() {
  const [health, setHealth] = useState(null)
  const [overview, setOverview] = useState(null)
  const [syncProgress, setSyncProgress] = useState(null)
  const [stocks, setStocks] = useState([])
  const [usDb, setUsDb] = useState(null)
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [lastUpdated, setLastUpdated] = useState(null)

  async function refreshAll() {
    setLoading(true)
    setError('')
    try {
      const [healthRes, overviewRes, progressRes, stocksRes, usDbRes] = await Promise.all([
        fetchJson('/api/health'),
        fetchJson('/api/db/overview'),
        fetchJson('/api/tushare/sync-progress'),
        fetchJson(`/api/stocks/screen?limit=80${query.trim() ? `&q=${encodeURIComponent(query.trim())}` : ''}`),
        fetchJson('/api/us-research/db-overview'),
      ])
      setHealth(healthRes)
      setOverview(overviewRes)
      setSyncProgress(progressRes)
      setStocks(stocksRes)
      setUsDb(usDbRes)
      setLastUpdated(new Date())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  async function importUsSample() {
    setLoading(true)
    setError('')
    try {
      await fetchJson('/api/us-research/import-sample', { method: 'POST' })
      await refreshAll()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setLoading(false)
    }
  }

  useEffect(() => {
    refreshAll()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const aShare = overview?.aShare || {}
  const usCounts = usDb?.counts || {}
  const tableRows = useMemo(() => buildTableRows(overview, usDb), [overview, usDb])

  return (
    <main className="shell">
      <aside className="rail">
        <div className="brand">
          <Database size={22} />
          <span>Quant DB</span>
        </div>
        <nav>
          <a href="#overview"><Server size={18} />概览</a>
          <a href="#ashare"><Table2 size={18} />A股</a>
          <a href="#us"><Activity size={18} />美股</a>
        </nav>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Data Source to PostgreSQL</p>
            <h1>数据源与数据库工作台</h1>
          </div>
          <div className="actions">
            <div className="search">
              <Search size={16} />
              <input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && refreshAll()} placeholder="代码 / 名称" />
            </div>
            <button onClick={refreshAll} disabled={loading}>
              <RefreshCw size={16} />
              刷新
            </button>
          </div>
        </header>

        {error ? <div className="notice error">{error}</div> : null}

        <section id="overview" className="metric-grid">
          <Metric label="API" value={health?.status || '-'} note={health?.database || ''} />
          <Metric label="A股股票" value={formatInt(aShare.stocks)} note="stocks" />
          <Metric label="日线覆盖" value={dateRange(aShare.dailyBars)} note={`${formatInt(aShare.dailyBars?.rows)} rows`} />
          <Metric label="daily_basic" value={dateRange(aShare.dailyBasic)} note={`${formatInt(aShare.dailyBasic?.rows)} rows`} />
          <Metric label="财务指标" value={dateRange(aShare.financialIndicators)} note={`${formatInt(aShare.financialIndicators?.rows)} rows`} />
          <Metric label="美股 sample" value={formatInt(usCounts.assets)} note={`${formatInt(usCounts.assetDailyPrices)} prices`} />
        </section>

        <section className="panel" id="ashare">
          <div className="panel-head">
            <div>
              <h2>A股数据表</h2>
              <p>这里只展示已入库数据、覆盖状态和同步记录。</p>
            </div>
            <span>{lastUpdated ? `刷新于 ${lastUpdated.toLocaleTimeString()}` : ''}</span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>表</th>
                  <th>行数</th>
                  <th>标的数</th>
                  <th>日期范围</th>
                </tr>
              </thead>
              <tbody>
                {tableRows.map((row) => (
                  <tr key={row.name}>
                    <td>{row.name}</td>
                    <td>{formatInt(row.rows)}</td>
                    <td>{formatInt(row.symbols)}</td>
                    <td>{row.range || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <div>
              <h2>股票样本</h2>
              <p>来自 PostgreSQL 的最新行情行。</p>
            </div>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>代码</th>
                  <th>名称</th>
                  <th>市场</th>
                  <th>行业</th>
                  <th>最新日期</th>
                  <th>收盘</th>
                  <th>涨跌幅</th>
                  <th>bar 数</th>
                </tr>
              </thead>
              <tbody>
                {stocks.map((stock) => (
                  <tr key={stock.ts_code}>
                    <td>{stock.ts_code}</td>
                    <td>{stock.name}</td>
                    <td>{stock.market || '-'}</td>
                    <td>{stock.industry || '-'}</td>
                    <td>{stock.latest_date || '-'}</td>
                    <td>{formatNumber(stock.close)}</td>
                    <td>{formatPercent(stock.pct_chg)}</td>
                    <td>{formatInt(stock.data_bars)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel" id="us">
          <div className="panel-head">
            <div>
              <h2>美股 sample DB</h2>
              <p>仅 sample / 脱敏数据；未接入券商，未导入真实持仓。</p>
            </div>
            <button onClick={importUsSample} disabled={loading}>
              <DownloadCloud size={16} />
              导入 sample
            </button>
          </div>
          <div className="asset-grid">
            {(usDb?.assets || []).map((asset) => (
              <article className="asset" key={asset.naturalKey}>
                <strong>{asset.symbol}</strong>
                <span>{asset.name}</span>
                <small>{asset.instrumentType || '-'} · {asset.riskTag || '-'}</small>
                <b>{formatNumber(asset.latestPrice?.close)}</b>
              </article>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <div>
              <h2>最近同步</h2>
              <p>同步日志来自 `data_sync_runs`。</p>
            </div>
          </div>
          <div className="table-wrap compact">
            <table>
              <thead>
                <tr>
                  <th>target</th>
                  <th>status</th>
                  <th>rows</th>
                  <th>date</th>
                  <th>created</th>
                </tr>
              </thead>
              <tbody>
                {(syncProgress?.runs || []).slice(0, 10).map((run) => (
                  <tr key={run.id}>
                    <td>{run.target}</td>
                    <td>{run.status}</td>
                    <td>{formatInt(run.rowsUpserted)}</td>
                    <td>{[run.startDate, run.endDate].filter(Boolean).join(' - ') || '-'}</td>
                    <td>{run.createdAt ? new Date(run.createdAt).toLocaleString() : '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </section>
    </main>
  )
}

function Metric({ label, value, note }) {
  return (
    <article className="metric">
      <span>{label}</span>
      <strong>{value ?? '-'}</strong>
      <small>{note || '-'}</small>
    </article>
  )
}

async function fetchJson(path, options) {
  const response = await fetch(`${API_BASE}${path}`, options)
  if (!response.ok) {
    throw new Error(`${path} ${response.status}`)
  }
  return response.json()
}

function buildTableRows(overview, usDb) {
  const aShare = overview?.aShare || {}
  return [
    { name: 'stocks', rows: overview?.tables?.stocks, symbols: aShare.stocks, range: '-' },
    { name: 'stock_daily_bars', ...(coverageRow(aShare.dailyBars)) },
    { name: 'stock_daily_basic', ...(coverageRow(aShare.dailyBasic)) },
    { name: 'stock_financial_indicators', ...(coverageRow(aShare.financialIndicators)) },
    { name: 'assets', rows: usDb?.counts?.assets, symbols: usDb?.counts?.assets, range: '-' },
    { name: 'asset_daily_prices', rows: usDb?.counts?.assetDailyPrices, symbols: usDb?.marketSnapshot?.symbolCount, range: '-' },
  ]
}

function coverageRow(coverage) {
  return {
    rows: coverage?.rows,
    symbols: coverage?.symbols,
    range: dateRange(coverage),
  }
}

function dateRange(coverage) {
  if (!coverage?.minDate && !coverage?.maxDate) return '-'
  return `${coverage.minDate || '?'} - ${coverage.maxDate || '?'}`
}

function formatInt(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return Number(value).toLocaleString()
}

function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return `${Number(value).toFixed(2)}%`
}

createRoot(document.getElementById('root')).render(<App />)
