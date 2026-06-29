import React, { useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Activity,
  Database,
  DownloadCloud,
  RefreshCw,
  Search,
  Server,
  Table2,
} from 'lucide-react'
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  createChart,
} from 'lightweight-charts'
import './styles.css'

const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:18000').replace(/\/$/, '')

function App() {
  const [health, setHealth] = useState(null)
  const [overview, setOverview] = useState(null)
  const [syncProgress, setSyncProgress] = useState(null)
  const [stocks, setStocks] = useState([])
  const [usDb, setUsDb] = useState(null)
  const [query, setQuery] = useState('')
  const [selectedCode, setSelectedCode] = useState('')
  const [stockBars, setStockBars] = useState([])
  const [fundamentals, setFundamentals] = useState(null)
  const [loading, setLoading] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState('')
  const [lastUpdated, setLastUpdated] = useState(null)

  async function refreshAll() {
    setLoading(true)
    setError('')
    try {
      const q = query.trim()
      const [healthRes, overviewRes, progressRes, stocksRes, usDbRes] = await Promise.all([
        fetchJson('/api/health'),
        fetchJson('/api/db/overview'),
        fetchJson('/api/tushare/sync-progress'),
        fetchJson(`/api/stocks/screen?limit=80${q ? `&q=${encodeURIComponent(q)}` : ''}`),
        fetchJson('/api/us-research/db-overview'),
      ])
      setHealth(healthRes)
      setOverview(overviewRes)
      setSyncProgress(progressRes)
      setStocks(stocksRes)
      setUsDb(usDbRes)
      setLastUpdated(new Date())
      if (!selectedCode && stocksRes[0]) {
        setSelectedCode(stocksRes[0].ts_code)
      }
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

  useEffect(() => {
    if (!selectedCode) return
    let ignore = false
    async function loadSelectedStock() {
      setDetailLoading(true)
      setError('')
      try {
        const selected = stocks.find((stock) => stock.ts_code === selectedCode)
        const endDate = selected?.latest_date || todayString()
        const startDate = dateMonthsBefore(endDate, 8)
        const [barsRes, fundamentalsRes] = await Promise.all([
          fetchJson(`/api/daily-bars?ts_code=${encodeURIComponent(selectedCode)}&start_date=${startDate}&end_date=${endDate}`),
          fetchJson(`/api/stocks/${encodeURIComponent(selectedCode)}/fundamentals`),
        ])
        if (!ignore) {
          setStockBars(barsRes)
          setFundamentals(fundamentalsRes)
        }
      } catch (err) {
        if (!ignore) setError(err instanceof Error ? err.message : String(err))
      } finally {
        if (!ignore) setDetailLoading(false)
      }
    }
    loadSelectedStock()
    return () => {
      ignore = true
    }
  }, [selectedCode, stocks])

  const aShare = overview?.aShare || {}
  const usCounts = usDb?.counts || {}
  const tableRows = useMemo(() => buildTableRows(overview, usDb), [overview, usDb])
  const syncRuns = syncProgress?.runs || []
  const usAssets = usDb?.assets || []
  const selectedStock = stocks.find((stock) => stock.ts_code === selectedCode) || stocks[0] || null
  const selectedLatestBar = stockBars[stockBars.length - 1] || selectedStock || null

  return (
    <main className="terminal-shell">
      <header className="terminal-header">
        <div className="title-lockup">
          <Database size={22} />
          <div>
            <span>LOCAL DATA WORKBENCH</span>
            <h1>本地数据工作台</h1>
          </div>
        </div>
        <div className="status-strip" aria-label="系统状态">
          <StatusItem label="API" value={health?.status || '-'} tone={health?.status === 'ok' ? 'good' : 'idle'} />
          <StatusItem label="DB" value={health?.database || '-'} />
          <StatusItem label="刷新" value={lastUpdated ? lastUpdated.toLocaleTimeString() : '-'} />
        </div>
      </header>

      <section className="command-bar" aria-label="工作台操作">
        <span className="mode-chip">只读数据 / 样本 / P0 覆盖</span>
        <button onClick={refreshAll} disabled={loading}>
          <RefreshCw size={16} className={loading ? 'spin' : ''} />
          刷新数据
        </button>
        <button onClick={importUsSample} disabled={loading}>
          <DownloadCloud size={16} />
          导入 sample
        </button>
      </section>

      {error ? <div className="notice error">{error}</div> : null}

      <section className="metric-strip" aria-label="核心覆盖">
        <Metric label="A股股票" value={formatInt(aShare.stocks)} note="stocks" icon={Server} />
        <Metric label="日线覆盖" value={dateRange(aShare.dailyBars)} note={`${formatInt(aShare.dailyBars?.rows)} rows`} icon={Table2} />
        <Metric label="交易日历" value={aShare.tradeCalendar?.latestOpenDate || dateRange(aShare.tradeCalendar)} note={`${formatInt(aShare.tradeCalendar?.rows)} rows`} icon={Activity} />
        <Metric label="复权因子" value={dateRange(aShare.adjustFactors)} note={`${formatInt(aShare.adjustFactors?.rows)} rows`} icon={Table2} />
        <Metric label="指数日线" value={dateRange(aShare.indexDailyBars)} note={`${formatInt(aShare.indices?.rows)} indices`} icon={Table2} />
        <Metric label="ETF 日线" value={dateRange(aShare.fundDailyBars)} note={`${formatInt(aShare.funds?.rows)} funds`} icon={Table2} />
        <Metric label="行业成分" value={formatInt(aShare.industryMembers)} note={`${formatInt(aShare.industries?.rows)} industries`} icon={Database} />
        <Metric label="美股 sample" value={formatInt(usCounts.assets)} note={`${formatInt(usCounts.assetDailyPrices)} prices`} icon={Database} />
      </section>

      <section className="stock-workbench" aria-label="股票样本分析台">
        <aside className="stock-browser">
          <div className="stock-browser-head">
            <h2>股票样本</h2>
            <span>{formatInt(stocks.length)} 条</span>
          </div>
          <label className="stock-query">
            <Search size={15} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && refreshAll()}
              placeholder="代码 / 名称"
            />
          </label>
          <div className="stock-list">
            {stocks.map((stock, index) => (
              <button
                className={`stock-row ${stock.ts_code === selectedCode ? 'active' : ''}`}
                key={stock.ts_code}
                onClick={() => setSelectedCode(stock.ts_code)}
              >
                <span>{index + 1}. <b>{stock.symbol || stock.ts_code}</b></span>
                <em>{stock.name}</em>
              </button>
            ))}
            {stocks.length === 0 ? <div className="empty-box">暂无股票样本</div> : null}
          </div>
        </aside>

        <section className="chart-surface">
          <div className="chart-title">
            <div>
              <span className="mono">{selectedStock?.ts_code || '-'}</span>
              <h2>{selectedStock?.name || '未选择股票'}</h2>
            </div>
            <div className="chart-tags">
              <span>{selectedStock?.industry || '-'}</span>
              <span>{selectedStock?.market || '-'}</span>
              <span>{detailLoading ? '加载中' : `${formatInt(stockBars.length)} bars`}</span>
            </div>
          </div>
          <TechnicalCharts bars={stockBars} />
        </section>

        <aside className="fundamental-panel">
          <h2>基本面</h2>
          <InfoBlock title={selectedLatestBar?.trade_date || selectedStock?.latest_date || '-'}>
            <InfoRow label="开" value={formatNumber(selectedLatestBar?.open)} />
            <InfoRow label="高" value={formatNumber(selectedLatestBar?.high)} />
            <InfoRow label="低" value={formatNumber(selectedLatestBar?.low)} />
            <InfoRow label="收" value={formatNumber(selectedLatestBar?.close)} strong />
            <InfoRow label="涨跌幅" value={formatPercent(selectedLatestBar?.pct_chg)} tone={Number(selectedLatestBar?.pct_chg) > 0 ? 'positive' : Number(selectedLatestBar?.pct_chg) < 0 ? 'negative' : ''} />
            <InfoRow label="成交额" value={formatDailyAmount(selectedLatestBar?.amount)} />
          </InfoBlock>
          <InfoBlock title="估值">
            <InfoRow label="总市值" value={formatWanYi(fundamentals?.valuation?.totalMv)} />
            <InfoRow label="流通市值" value={formatWanYi(fundamentals?.valuation?.circMv)} />
            <InfoRow label="PE TTM" value={formatNumber(fundamentals?.valuation?.peTtm)} />
            <InfoRow label="PB" value={formatNumber(fundamentals?.valuation?.pb)} />
            <InfoRow label="换手率" value={formatPercent(fundamentals?.valuation?.turnoverRate)} />
          </InfoBlock>
          <InfoBlock title="财务">
            <InfoRow label="公告日" value={fundamentals?.financial?.annDate || '-'} />
            <InfoRow label="EPS" value={formatNumber(fundamentals?.financial?.eps)} />
            <InfoRow label="ROE" value={formatPercent(fundamentals?.financial?.roe)} />
            <InfoRow label="毛利率" value={formatPercent(fundamentals?.financial?.grossprofitMargin)} />
            <InfoRow label="营收同比" value={formatPercent(fundamentals?.financial?.trYoy)} />
            <InfoRow label="净利同比" value={formatPercent(fundamentals?.financial?.netprofitYoy)} />
          </InfoBlock>
        </aside>
      </section>

      <section className="data-grid">
        <Panel title="A股覆盖">
          <DataTable columns={['表', '行数', '标的数', '日期范围']}>
            {tableRows.map((row) => (
              <tr key={row.name}>
                <td className="mono">{row.name}</td>
                <td>{formatInt(row.rows)}</td>
                <td>{formatInt(row.symbols)}</td>
                <td>{row.range || '-'}</td>
              </tr>
            ))}
          </DataTable>
        </Panel>

        <Panel title="同步记录" compact>
          <DataTable columns={['target', 'status', 'rows', 'date', 'created']}>
            {syncRuns.slice(0, 8).map((run) => (
              <tr key={run.id || `${run.target}-${run.createdAt}`}>
                <td className="mono">{run.target}</td>
                <td><Badge value={run.status} /></td>
                <td>{formatInt(run.rowsUpserted)}</td>
                <td>{[run.startDate, run.endDate].filter(Boolean).join(' - ') || '-'}</td>
                <td>{run.createdAt ? new Date(run.createdAt).toLocaleString() : '-'}</td>
              </tr>
            ))}
            {syncRuns.length === 0 ? <EmptyRow colSpan={5} /> : null}
          </DataTable>
        </Panel>

        <Panel title="美股 sample" wide>
          <DataTable columns={['symbol', 'name', 'type', 'risk', 'latest close']}>
            {usAssets.map((asset) => (
              <tr key={asset.naturalKey || asset.symbol}>
                <td className="mono strong">{asset.symbol}</td>
                <td>{asset.name || '-'}</td>
                <td>{asset.instrumentType || '-'}</td>
                <td>{asset.riskTag || '-'}</td>
                <td>{formatNumber(asset.latestPrice?.close)}</td>
              </tr>
            ))}
            {usAssets.length === 0 ? <EmptyRow colSpan={5} /> : null}
          </DataTable>
        </Panel>
      </section>
    </main>
  )
}

function TechnicalCharts({ bars }) {
  const priceRef = useRef(null)
  const volumeRef = useRef(null)
  const kdjRef = useRef(null)
  const macdRef = useRef(null)
  const series = useMemo(() => buildTechnicalSeries(bars), [bars])

  useEffect(() => {
    if (!series.candles.length) return undefined
    const charts = [
      renderPriceChart(priceRef.current, series),
      renderVolumeChart(volumeRef.current, series),
      renderLineChart(kdjRef.current, series.kdj, [
        ['k', '#2b8fe8'],
        ['d', '#ff9f2e'],
        ['j', '#dd3265'],
      ], 118),
      renderMacdChart(macdRef.current, series),
    ].filter(Boolean)

    const resize = () => {
      charts.forEach(({ chart, element }) => chart.applyOptions({ width: element.clientWidth }))
    }
    window.addEventListener('resize', resize)
    return () => {
      window.removeEventListener('resize', resize)
      charts.forEach(({ chart }) => chart.remove())
    }
  }, [series])

  if (!series.candles.length) {
    return <div className="chart-empty">暂无 K 线数据</div>
  }

  return (
    <div className="chart-stack">
      <div className="chart-pane price" ref={priceRef} />
      <div className="chart-pane volume" ref={volumeRef} />
      <div className="chart-pane indicator" ref={kdjRef} />
      <div className="chart-pane indicator" ref={macdRef} />
    </div>
  )
}

function renderPriceChart(element, series) {
  if (!element) return null
  element.replaceChildren()
  const chart = createBaseChart(element, 330)
  chart.addSeries(CandlestickSeries, {
    upColor: '#2d7d3c',
    downColor: '#d84040',
    borderUpColor: '#2d7d3c',
    borderDownColor: '#d84040',
    wickUpColor: '#2d7d3c',
    wickDownColor: '#d84040',
  }).setData(series.candles)
  chart.addSeries(LineSeries, { color: '#6f58ff', lineWidth: 2, priceLineVisible: false }).setData(series.ma10)
  chart.addSeries(LineSeries, { color: '#f29f2d', lineWidth: 2, priceLineVisible: false }).setData(series.ma20)
  chart.timeScale().fitContent()
  return { chart, element }
}

function renderVolumeChart(element, series) {
  if (!element) return null
  element.replaceChildren()
  const chart = createBaseChart(element, 100)
  chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' } }).setData(series.volume)
  chart.addSeries(LineSeries, { color: '#2b8fe8', lineWidth: 1, priceLineVisible: false }).setData(series.volumeMa10)
  chart.addSeries(LineSeries, { color: '#f29f2d', lineWidth: 1, priceLineVisible: false }).setData(series.volumeMa20)
  chart.timeScale().fitContent()
  return { chart, element }
}

function renderLineChart(element, rows, lines, height) {
  if (!element) return null
  element.replaceChildren()
  const chart = createBaseChart(element, height)
  lines.forEach(([key, color]) => {
    chart.addSeries(LineSeries, { color, lineWidth: key === 'j' ? 2 : 1, priceLineVisible: false })
      .setData(rows.map((row) => ({ time: row.time, value: row[key] })).filter((row) => Number.isFinite(row.value)))
  })
  chart.timeScale().fitContent()
  return { chart, element }
}

function renderMacdChart(element, series) {
  if (!element) return null
  element.replaceChildren()
  const chart = createBaseChart(element, 128)
  chart.addSeries(HistogramSeries, { priceLineVisible: false }).setData(series.macdHistogram)
  chart.addSeries(LineSeries, { color: '#6f58ff', lineWidth: 1, priceLineVisible: false }).setData(series.macdDiff)
  chart.addSeries(LineSeries, { color: '#f29f2d', lineWidth: 1, priceLineVisible: false }).setData(series.macdDea)
  chart.timeScale().fitContent()
  return { chart, element }
}

function createBaseChart(element, height) {
  return createChart(element, {
    width: element.clientWidth,
    height,
    layout: { background: { color: '#ffffff' }, textColor: '#68737d' },
    grid: {
      vertLines: { color: '#edf1f4' },
      horzLines: { color: '#e4e9ed' },
    },
    rightPriceScale: { borderColor: '#d6dde3' },
    timeScale: { borderColor: '#d6dde3', timeVisible: false },
    crosshair: { mode: 1 },
  })
}

function StatusItem({ label, value, tone = 'idle' }) {
  return (
    <span className="status-item">
      <i className={tone} />
      <b>{label}</b>
      {value}
    </span>
  )
}

function Metric({ label, value, note, icon: Icon }) {
  return (
    <article className="metric">
      <div>
        <Icon size={16} />
        <span>{label}</span>
      </div>
      <strong>{value ?? '-'}</strong>
      <small>{note || '-'}</small>
    </article>
  )
}

function Panel({ title, children, compact = false, wide = false }) {
  return (
    <section className={`panel ${compact ? 'compact' : ''} ${wide ? 'wide' : ''}`}>
      <header className="panel-title">
        <h2>{title}</h2>
      </header>
      <div className="table-wrap">{children}</div>
    </section>
  )
}

function DataTable({ columns, children }) {
  return (
    <table>
      <thead>
        <tr>
          {columns.map((column) => <th key={column}>{column}</th>)}
        </tr>
      </thead>
      <tbody>{children}</tbody>
    </table>
  )
}

function InfoBlock({ title, children }) {
  return (
    <section className="info-block">
      <h3>{title}</h3>
      <dl>{children}</dl>
    </section>
  )
}

function InfoRow({ label, value, strong = false, tone = '' }) {
  return (
    <>
      <dt>{label}</dt>
      <dd className={`${strong ? 'strong' : ''} ${tone}`}>{value || '-'}</dd>
    </>
  )
}

function Badge({ value }) {
  const text = value || '-'
  const tone = ['success', 'ok', 'pass'].includes(text) ? 'good' : ['failed', 'error', 'fail'].includes(text) ? 'bad' : 'idle'
  return <span className={`badge ${tone}`}>{text}</span>
}

function EmptyRow({ colSpan }) {
  return (
    <tr>
      <td className="empty" colSpan={colSpan}>暂无数据</td>
    </tr>
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
    { name: 'stock_daily_bars', ...coverageRow(aShare.dailyBars) },
    { name: 'stock_daily_basic', ...coverageRow(aShare.dailyBasic) },
    { name: 'stock_financial_indicators', ...coverageRow(aShare.financialIndicators) },
    { name: 'trade_calendars', ...coverageRow(aShare.tradeCalendar) },
    { name: 'stock_adjust_factors', ...coverageRow(aShare.adjustFactors) },
    { name: 'indices', rows: aShare.indices?.rows, symbols: aShare.indices?.symbols, range: '-' },
    { name: 'index_daily_bars', ...coverageRow(aShare.indexDailyBars) },
    { name: 'funds', rows: aShare.funds?.rows, symbols: aShare.funds?.symbols, range: '-' },
    { name: 'fund_daily_bars', ...coverageRow(aShare.fundDailyBars) },
    { name: 'industry_classifications', rows: aShare.industries?.rows, symbols: aShare.industries?.symbols, range: '-' },
    { name: 'industry_members', rows: aShare.industryMembers, symbols: aShare.industryMembers, range: '-' },
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

function buildTechnicalSeries(bars) {
  const candles = bars
    .filter((bar) => [bar.open, bar.high, bar.low, bar.close].every((value) => Number.isFinite(Number(value))))
    .map((bar) => ({
      time: bar.trade_date,
      open: Number(bar.open),
      high: Number(bar.high),
      low: Number(bar.low),
      close: Number(bar.close),
    }))
  const closes = candles.map((bar) => bar.close)
  const volumes = bars.map((bar, index) => ({
    time: bar.trade_date,
    value: Number(bar.vol || 0),
    color: candles[index]?.close >= candles[index]?.open ? '#2d7d3c' : '#d84040',
  }))
  const volumeValues = volumes.map((row) => row.value)
  const macd = calcMacd(candles)
  return {
    candles,
    ma10: calcSma(candles, closes, 10),
    ma20: calcSma(candles, closes, 20),
    volume: volumes,
    volumeMa10: calcSma(candles, volumeValues, 10),
    volumeMa20: calcSma(candles, volumeValues, 20),
    kdj: calcKdj(candles),
    macdHistogram: macd.map((row) => ({ time: row.time, value: row.hist, color: row.hist >= 0 ? '#d84040' : '#2d7d3c' })),
    macdDiff: macd.map((row) => ({ time: row.time, value: row.diff })),
    macdDea: macd.map((row) => ({ time: row.time, value: row.dea })),
  }
}

function calcSma(candles, values, period) {
  return values
    .map((_, index) => {
      if (index + 1 < period) return null
      const slice = values.slice(index + 1 - period, index + 1)
      return { time: candles[index].time, value: slice.reduce((sum, value) => sum + value, 0) / period }
    })
    .filter(Boolean)
}

function calcKdj(candles) {
  let k = 50
  let d = 50
  return candles.map((bar, index) => {
    const slice = candles.slice(Math.max(0, index - 8), index + 1)
    const low = Math.min(...slice.map((row) => row.low))
    const high = Math.max(...slice.map((row) => row.high))
    const rsv = high === low ? 50 : ((bar.close - low) / (high - low)) * 100
    k = (2 * k + rsv) / 3
    d = (2 * d + k) / 3
    return { time: bar.time, k, d, j: 3 * k - 2 * d }
  })
}

function calcMacd(candles) {
  const closes = candles.map((bar) => bar.close)
  const ema12 = calcEma(closes, 12)
  const ema26 = calcEma(closes, 26)
  const diff = closes.map((_, index) => ema12[index] - ema26[index])
  const dea = calcEma(diff, 9)
  return candles.map((bar, index) => ({
    time: bar.time,
    diff: diff[index],
    dea: dea[index],
    hist: diff[index] - dea[index],
  }))
}

function calcEma(values, period) {
  const alpha = 2 / (period + 1)
  const result = []
  values.forEach((value, index) => {
    result[index] = index === 0 ? value : alpha * value + (1 - alpha) * result[index - 1]
  })
  return result
}

function dateRange(coverage) {
  if (!coverage?.minDate && !coverage?.maxDate) return '-'
  return `${coverage.minDate || '?'} - ${coverage.maxDate || '?'}`
}

function todayString() {
  return new Date().toISOString().slice(0, 10)
}

function dateMonthsBefore(dateText, months) {
  const date = new Date(`${dateText}T00:00:00`)
  date.setMonth(date.getMonth() - months)
  return date.toISOString().slice(0, 10)
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

function formatWanYi(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  if (Math.abs(number) >= 10000) return `${(number / 10000).toFixed(2)}亿`
  return `${number.toFixed(2)}万`
}

function formatDailyAmount(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  const thousandYuan = Number(value)
  if (Math.abs(thousandYuan) >= 100000) return `${(thousandYuan / 100000).toFixed(2)}亿`
  return `${(thousandYuan / 10).toFixed(0)}万`
}

createRoot(document.getElementById('root')).render(<App />)
