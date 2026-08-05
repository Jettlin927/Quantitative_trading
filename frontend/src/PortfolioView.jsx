import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  ArchiveRestore,
  CircleDollarSign,
  Pencil,
  Plus,
  RefreshCw,
  ShieldAlert,
  TrendingUp,
  Trash2,
} from 'lucide-react'
import { lightweightEquityChartAdapter } from './equityChartAdapter.js'

const EMPTY_FORM = { symbol: '', name: '', quantity: '', averageCost: '' }

export function PortfolioView({ client, chartAdapter = lightweightEquityChartAdapter }) {
  const [portfolio, setPortfolio] = useState(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [editingId, setEditingId] = useState(null)
  const [cash, setCash] = useState('')
  const [purge, setPurge] = useState(null)
  const [deletionReceipt, setDeletionReceipt] = useState(null)
  const [equityHistory, setEquityHistory] = useState([])
  const [equityError, setEquityError] = useState(null)

  async function refresh({ signal } = { signal: undefined }) {
    const next = await client.openPortfolio({ signal })
    setPortfolio(next)
    setCash(next.usd_cash)
    return next
  }

  useEffect(() => {
    const controller = new AbortController()
    client.openPortfolio({ signal: controller.signal })
      .then((next) => {
        setPortfolio(next)
        setCash(next.usd_cash)
      })
      .catch((reason) => {
        if (reason?.name !== 'AbortError') setError(reason)
      })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [client])

  useEffect(() => {
    if (typeof client.openEquityHistory !== 'function') return undefined
    const controller = new AbortController()
    client.openEquityHistory({ signal: controller.signal })
      .then((next) => setEquityHistory(Array.isArray(next?.snapshots) ? next.snapshots : []))
      .catch((reason) => {
        if (reason?.name !== 'AbortError') setEquityError(reason)
      })
    return () => controller.abort()
  }, [client])

  const activeCount = useMemo(
    () => portfolio?.holdings?.filter((holding) => holding.state === 'active').length || 0,
    [portfolio],
  )

  const cachedCount = useMemo(
    () => portfolio?.holdings?.filter((holding) => holding.market_price?.cached).length || 0,
    [portfolio],
  )

  const equityStats = useMemo(() => {
    if (!portfolio) return null
    const holdings = portfolio.holdings.filter((holding) => holding.state === 'active')
    const available = (observed) => observed?.availability === 'available' && observed.value != null
    const priced = holdings.filter((holding) => available(holding.unrealized_profit_loss))
    const sumMoney = (list, pick) => list.reduce((total, item) => total + Number(pick(item)), 0)
    const totalCost = sumMoney(holdings, (holding) => holding.cost_amount || 0)
    const pricedCost = sumMoney(priced, (holding) => holding.cost_amount || 0)
    const totalProfitLoss = sumMoney(priced, (holding) => holding.unrealized_profit_loss.value)
    const totalEquity = portfolio.total_equity?.availability === 'available'
      ? Number(portfolio.total_equity.value)
      : null
    const cash = Number(portfolio.usd_cash)
    const weighted = holdings.filter((holding) => available(holding.weight))
    const largest = weighted.reduce(
      (current, holding) => (!current || Number(holding.weight.value) > Number(current.weight.value) ? holding : current),
      null,
    )
    const snapshots = equityHistory
    const latest = snapshots[snapshots.length - 1]
    const previous = snapshots[snapshots.length - 2]
    const dayChange = latest && previous
      ? Number(latest.total_equity) - Number(previous.total_equity)
      : null
    const dayChangePercent = latest && previous && Number(previous.total_equity)
      ? dayChange / Number(previous.total_equity)
      : null
    return {
      pricedCount: priced.length,
      holdingCount: holdings.length,
      totalCost,
      pricedCost,
      totalProfitLoss,
      returnRate: pricedCost ? totalProfitLoss / pricedCost : null,
      cashRatio: totalEquity ? cash / totalEquity : null,
      largest,
      topWeight: largest ? Number(largest.weight.value) : null,
      dayChange,
      dayChangePercent,
      latestSnapshot: latest || null,
      previousSnapshot: previous || null,
      equityAvailable: totalEquity != null,
    }
  }, [portfolio, equityHistory])

  async function execute(command) {
    setSubmitting(true)
    setError(null)
    try {
      const result = await client.submitPortfolioCommand({
        command,
        idempotencyKey: crypto.randomUUID(),
      })
      if (Array.isArray(result?.holdings)) {
        setPortfolio(result)
        setCash(result.usd_cash)
      }
      return result
    } catch (reason) {
      setError(reason)
      if (reason?.code === 'revision_conflict') await refresh().catch(() => {})
      throw reason
    } finally {
      setSubmitting(false)
    }
  }

  async function submitHolding(event) {
    event.preventDefault()
    const command = editingId
      ? {
          type: 'edit_holding',
          holding_id: editingId,
          name: form.name,
          quantity: form.quantity,
          average_cost: form.averageCost,
          expected_portfolio_revision: portfolio.portfolio_revision,
        }
      : {
          type: 'add_holding',
          symbol: form.symbol,
          name: form.name,
          quantity: form.quantity,
          average_cost: form.averageCost,
          expected_portfolio_revision: portfolio.portfolio_revision,
        }
    try {
      await execute(command)
      setForm(EMPTY_FORM)
      setEditingId(null)
    } catch {
      // execute 已记录稳定错误码，并在 revision conflict 时刷新投影。
    }
  }

  function beginEdit(holding) {
    setEditingId(holding.holding_id)
    setForm({
      symbol: holding.symbol,
      name: holding.name,
      quantity: holding.quantity,
      averageCost: holding.average_cost,
    })
  }

  async function updateState(holding, type) {
    try {
      await execute({
        type,
        holding_id: holding.holding_id,
        expected_portfolio_revision: portfolio.portfolio_revision,
      })
    } catch {
      // execute 已记录稳定错误码。
    }
  }

  async function setUsdCash(event) {
    event.preventDefault()
    try {
      await execute({
        type: 'set_usd_cash',
        usd_cash: cash,
        expected_portfolio_revision: portfolio.portfolio_revision,
      })
    } catch {
      // execute 已记录稳定错误码。
    }
  }

  async function requestPurge(holding) {
    try {
      const challenge = await execute({
        type: 'request_purge',
        holding_id: holding.holding_id,
        expected_portfolio_revision: portfolio.portfolio_revision,
      })
      setPurge({ holding, challenge })
    } catch {
      // execute 已记录稳定错误码。
    }
  }

  async function confirmPurge() {
    try {
      const receipt = await execute({
        type: 'confirm_purge',
        holding_id: purge.holding.holding_id,
        expected_portfolio_revision: purge.challenge.portfolio_revision,
        challenge: purge.challenge.challenge,
      })
      setDeletionReceipt(receipt)
      setPurge(null)
      await refresh().catch(() => {})
    } catch {
      // execute 已记录稳定错误码。
    }
  }

  if (loading) return (
    <section className="portfolio-loading" aria-live="polite">
      <RefreshCw size={20} className="spin" />
      <div><h2>正在解密持仓投影</h2><p>只保留当前会话内存，不写浏览器存储。</p></div>
    </section>
  )

  if (!portfolio) return (
    <section className="portfolio-loading error-state">
      <AlertTriangle size={22} />
      <div><h2>个人持仓暂不可用</h2><p>{error?.message || '请检查私有网关与存储配置。'}</p></div>
    </section>
  )

  const pricedCount = portfolio.priced_holding_count ?? portfolio.holdings.filter((holding) => holding.state === 'active' && holding.market_value?.availability === 'available').length
  const partialValuation = portfolio.total_market_value?.reason_code === 'partial_valuation'
  const sourceUnavailable = activeCount > 0 && (partialValuation || portfolio.total_market_value?.availability !== 'available')
  return (
    <div className="portfolio-workbench enter">
      <header className="portfolio-command-bar">
        <div>
          <span>PRIVATE PORTFOLIO / USD ONLY</span>
          <h1>手工美股持仓</h1>
          <p>手工事实与延迟估值分层展示；价格不可用不会阻断修订。</p>
        </div>
        <dl>
          <div><dt>组合修订</dt><dd>R{portfolio.portfolio_revision}</dd></div>
          <div><dt>在册 / 活跃</dt><dd>{portfolio.holdings.length} / {activeCount}</dd></div>
          <div><dt>行情覆盖</dt><dd>{pricedCount} / {activeCount}</dd></div>
          <div><dt>币种</dt><dd>USD</dd></div>
        </dl>
      </header>

      {sourceUnavailable ? (
        <div className="portfolio-source-warning" role="status">
          <AlertTriangle size={17} />
          <span>{partialValuation ? <><strong>行情覆盖 {pricedCount} / {activeCount}，当前展示已覆盖估值。</strong>其余标的保持不可用，不以 0 代替，也不计算组合权重。</> : <><strong>行情来源不可用，手工持仓仍可编辑。</strong>市值、盈亏和权重保持 Unavailable，不以 0 代替。</>}</span>
        </div>
      ) : null}

      {cachedCount > 0 && !sourceUnavailable ? (
        <div className="portfolio-cache-warning" role="status">
          <RefreshCw size={16} />
          <span><strong>{cachedCount} 个标的使用上次落盘行情。</strong>实时行情获取失败，已回退到最近一次成功落盘的价格（带时间标注），超过有效期后恢复不可用。</span>
        </div>
      ) : null}

      {error ? (
        <div className="portfolio-error" role="alert">
          <AlertTriangle size={16} />
          <span><strong>{error.code || 'personal_request_failed'}</strong>{error.message}</span>
        </div>
      ) : null}

      {deletionReceipt ? (
        <div className="portfolio-delete-receipt" role="status">
          <ShieldAlert size={16} />
          <span>持仓已永久删除。备份副本最迟于 {formatTime(deletionReceipt.backup_expires_at)} 自然过期。</span>
        </div>
      ) : null}

      <section className="portfolio-summary-grid" aria-label="组合摘要">
        <article><span>{partialValuation ? '已覆盖组合值' : '组合总值'}</span><ObservedValue observed={portfolio.total_equity} kind="money" /></article>
        <article><span>{partialValuation ? '已覆盖持仓市值' : '持仓市值'}</span><ObservedValue observed={portfolio.total_market_value} kind="money" /></article>
        <article><span>USD 现金</span><strong>{portfolio.usd_cash}</strong></article>
        <form onSubmit={setUsdCash}>
          <label htmlFor="portfolio-cash">修订现金</label>
          <div><input id="portfolio-cash" inputMode="decimal" value={cash} onChange={(event) => setCash(event.target.value)} /><button disabled={submitting}><CircleDollarSign size={15} />保存</button></div>
        </form>
      </section>

      <EquityOverview
        history={equityHistory}
        stats={equityStats}
        holdings={portfolio.holdings.filter((holding) => holding.state === 'active')}
        error={equityError}
        chartAdapter={chartAdapter}
      />

      <div className="portfolio-layout">
        <section className="portfolio-ledger" aria-labelledby="portfolio-ledger-heading">
          <header><div><span>01 / LEDGER</span><h2 id="portfolio-ledger-heading">当前持仓与回收区</h2></div><b>派生值只读</b></header>
          {!portfolio.holdings.length ? (
            <div className="portfolio-empty"><ArchiveRestore size={22} /><h3>尚未添加持仓</h3><p>先填写右侧手工事实；行情可稍后恢复。</p></div>
          ) : (
            <div className="portfolio-table-wrap">
              <table className="portfolio-table">
                <thead><tr><th>标的</th><th>数量</th><th>均价</th><th>成本</th><th>市值</th><th>未实现盈亏</th><th>盈亏率</th><th>权重</th><th>操作</th></tr></thead>
                <tbody>
                  {portfolio.holdings.map((holding) => (
                    <tr key={holding.holding_id} className={holding.state === 'removed' ? 'removed' : ''}>
                      <th scope="row"><strong>{holding.symbol}</strong><span>{holding.name}</span><small>{holding.state === 'removed' ? '已移出 / 可恢复' : sourceLabel(holding.market_price)}</small></th>
                      <td>{holding.quantity}</td>
                      <td>{holding.average_cost}</td>
                      <td>{holding.cost_amount}</td>
                      <td><ObservedValue observed={holding.market_value} kind="money" compact /></td>
                      <td><ObservedValue observed={holding.unrealized_profit_loss} kind="money" compact signed /></td>
                      <td><ObservedValue observed={holding.unrealized_return} kind="ratio" compact /></td>
                      <td><ObservedValue observed={holding.weight} kind="ratio" compact /></td>
                      <td><div className="portfolio-row-actions">
                        {holding.state === 'active' ? <>
                          <button aria-label={`编辑 ${holding.symbol}`} onClick={() => beginEdit(holding)}><Pencil size={14} /></button>
                          <button aria-label={`移出 ${holding.symbol}`} onClick={() => updateState(holding, 'remove_holding')}><ArchiveRestore size={14} /></button>
                        </> : <button aria-label={`恢复 ${holding.symbol}`} onClick={() => updateState(holding, 'restore_holding')}><RefreshCw size={14} /></button>}
                        <button className="danger" aria-label={`永久删除 ${holding.symbol}`} onClick={() => requestPurge(holding)}><Trash2 size={14} /></button>
                      </div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <form className="portfolio-editor" onSubmit={submitHolding}>
          <header><span>02 / MANUAL FACTS</span><h2>{editingId ? '编辑手工事实' : '添加持仓'}</h2><p>数量与均价进入私有修订；成本、市值和盈亏不能填写。</p></header>
          <label>标的代码<input required disabled={Boolean(editingId)} autoCapitalize="characters" value={form.symbol} onChange={(event) => setForm({ ...form, symbol: event.target.value.toUpperCase() })} placeholder="例如 ACME" /></label>
          <label>标的名称<input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="公司或 ETF 名称" /></label>
          <div className="portfolio-field-pair">
            <label>持股数量<input required inputMode="decimal" value={form.quantity} onChange={(event) => setForm({ ...form, quantity: event.target.value })} placeholder="0.0000" /></label>
            <label>平均买入价<input required inputMode="decimal" value={form.averageCost} onChange={(event) => setForm({ ...form, averageCost: event.target.value })} placeholder="USD" /></label>
          </div>
          <div className="portfolio-derived-lock"><ShieldAlert size={15} /><span>成本金额、快照市值、盈亏与权重由服务端 Decimal 计算。</span></div>
          <div className="portfolio-editor-actions">
            {editingId ? <button type="button" onClick={() => { setEditingId(null); setForm(EMPTY_FORM) }}>取消</button> : null}
            <button className="primary-action" disabled={submitting}><Plus size={15} />{editingId ? '保存修订' : '添加持仓'}</button>
          </div>
        </form>
      </div>

      {purge ? (
        <div className="portfolio-modal-backdrop">
          <section className="portfolio-purge-dialog" role="dialog" aria-modal="true" aria-label={`永久删除 ${purge.holding.symbol}`}>
            <ShieldAlert size={26} />
            <span>DESTRUCTIVE CHALLENGE</span>
            <h2>永久删除 {purge.holding.symbol}</h2>
            <p>将删除当前持仓及其全部修订。历史备份不会即时擦除，最长 30 天自然过期。</p>
            <small>Challenge 有效至 {formatTime(purge.challenge.expires_at)}，并绑定当前组合修订 R{purge.challenge.portfolio_revision}。</small>
            <div><button onClick={() => setPurge(null)}>取消</button><button className="danger-action" disabled={submitting} onClick={confirmPurge}><Trash2 size={15} />确认永久删除</button></div>
          </section>
        </div>
      ) : null}
    </div>
  )
}

function ObservedValue({ observed, kind, compact = false, signed = false }) {
  if (!observed || observed.availability !== 'available' || observed.value == null) {
    return <strong className={`observed-unavailable ${compact ? 'compact' : ''}`} title={observed?.reason_code || 'not_available'}>不可用</strong>
  }
  let value = observed.value
  if (kind === 'ratio') value = `${(Number(value) * 100).toFixed(2)}%`
  if (signed && Number(observed.value) > 0) value = `+${value}`
  return <strong className={Number(observed.value) < 0 ? 'negative' : ''}>{value}</strong>
}

function sourceLabel(observed) {
  if (!observed || observed.availability !== 'available') return '行情不可用'
  if (observed.cached) return `上次落盘 · ${formatTime(observed.as_of)}`
  if (observed.feed === 'sip' || observed.feed === 'delayed_sip') return `延迟 SIP · ${Math.round((observed.delay_seconds || 0) / 60)} 分钟`
  if (observed.feed === 'eod') return `EOD 回退 · ${formatTime(observed.as_of)}`
  return observed.source_health || '来源未知'
}

function EquityOverview({ history, stats, holdings, error, chartAdapter }) {
  const points = history.map((item) => ({ time: item.market_day, value: Number(item.total_equity), afterClose: item.after_close }))
  return (
    <section className="portfolio-equity" aria-label="权益日线与概览">
      <header className="portfolio-equity-header">
        <div>
          <span>03 / EQUITY</span>
          <h2>权益日线与概览</h2>
          <p>每个美股交易日（ET）写入当日权益快照；实时行情失败自动回退到最近落盘价格。</p>
        </div>
        {stats ? <EquityKpis stats={stats} /> : null}
      </header>
      <div className="portfolio-equity-grid">
        <div className="portfolio-equity-chart-card">
          <div className="portfolio-equity-chart-head"><TrendingUp size={15} /><span>组合权益日线</span><small>USD</small></div>
          {history.length ? <EquityChart points={points} chartAdapter={chartAdapter} /> : (
            <div className="portfolio-equity-empty">
              <TrendingUp size={20} />
              <span>暂无权益历史。每个美股工作日打开本页后自动记录当日快照；盘中和收盘后打开都会刷新当天值。</span>
            </div>
          )}
          {error ? <p className="portfolio-equity-error">权益历史暂不可用：{error.message}</p> : null}
        </div>
        <div className="portfolio-equity-bars">
          <PositionBars holdings={holdings} />
          <ProfitLossBars holdings={holdings} />
        </div>
      </div>
      {history.length ? <EquityTable history={history} /> : null}
    </section>
  )
}

function EquityKpis({ stats }) {
  const items = [
    { label: '今日变动', value: stats.dayChange == null ? '—' : formatSignedMoney(stats.dayChange), sub: stats.dayChangePercent == null ? '' : `${(stats.dayChangePercent * 100).toFixed(2)}%`, negative: (stats.dayChange || 0) < 0 },
    { label: '总成本', value: formatMoney(stats.totalCost) },
    { label: '总未实现盈亏', value: stats.totalProfitLoss == null ? '—' : formatSignedMoney(stats.totalProfitLoss), sub: stats.pricedCount < stats.holdingCount ? `已覆盖 ${stats.pricedCount}/${stats.holdingCount}` : '', negative: (stats.totalProfitLoss || 0) < 0 },
    { label: '盈亏率', value: stats.returnRate == null ? '—' : `${(stats.returnRate * 100).toFixed(2)}%` },
    { label: '现金占比', value: stats.cashRatio == null ? '—' : `${(stats.cashRatio * 100).toFixed(2)}%` },
    { label: '最大持仓', value: stats.largest ? stats.largest.symbol : '—', sub: stats.topWeight == null ? '' : `${(stats.topWeight * 100).toFixed(2)}%` },
  ]
  return (
    <dl className="portfolio-equity-kpis">
      {items.map((item) => (
        <div key={item.label}>
          <dt>{item.label}</dt>
          <dd className={item.negative ? 'negative' : ''}>
            <strong>{item.value}</strong>
            {item.sub ? <small>{item.sub}</small> : null}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function EquityChart({ points, chartAdapter = lightweightEquityChartAdapter }) {
  const ref = useRef(null)
  useEffect(() => {
    if (!points.length || !ref.current) return undefined
    const chart = chartAdapter.create({ element: ref.current })
    chart.setData(points)
    const resize = () => chart.resize()
    window.addEventListener('resize', resize)
    return () => {
      window.removeEventListener('resize', resize)
      chart.dispose()
    }
  }, [chartAdapter, points])
  return <div className="equity-chart-pane" ref={ref} role="img" aria-label={`组合权益日线，共 ${points.length} 个观测点`} />
}

const observedValue = (observed) => observed?.availability === 'available' && observed.value != null

function PositionBars({ holdings }) {
  const weighted = holdings.filter((holding) => observedValue(holding.weight))
  const maxWeight = weighted.reduce((max, holding) => Math.max(max, Number(holding.weight.value)), 0)
  if (!weighted.length) return <div className="portfolio-bar-empty">仓位分布不可用：行情缺失时不计算权重。</div>
  return (
    <div className="portfolio-bar-block">
      <h3>仓位分布</h3>
      {weighted.map((holding) => {
        const weight = Number(holding.weight.value)
        return (
          <div className="portfolio-bar-row" key={holding.holding_id}>
            <span className="portfolio-bar-label">{holding.symbol}</span>
            <span className="portfolio-bar-track"><i style={{ width: maxWeight ? `${(weight / maxWeight) * 100}%` : '0%' }} /></span>
            <span className="portfolio-bar-value">{(weight * 100).toFixed(2)}%</span>
          </div>
        )
      })}
    </div>
  )
}

function ProfitLossBars({ holdings }) {
  const priced = holdings.filter((holding) => observedValue(holding.unrealized_profit_loss))
  const maxAbs = priced.reduce((max, holding) => Math.max(max, Math.abs(Number(holding.unrealized_profit_loss.value))), 0)
  if (!priced.length) return <div className="portfolio-bar-empty">盈亏分解不可用：行情缺失时不展示。</div>
  return (
    <div className="portfolio-bar-block">
      <h3>未实现盈亏分解</h3>
      {priced.map((holding) => {
        const profitLoss = Number(holding.unrealized_profit_loss.value)
        return (
          <div className="portfolio-bar-row" key={holding.holding_id}>
            <span className="portfolio-bar-label">{holding.symbol}</span>
            <span className={`portfolio-bar-track ${profitLoss < 0 ? 'negative' : ''}`}><i style={{ width: maxAbs ? `${(Math.abs(profitLoss) / maxAbs) * 100}%` : '0%' }} /></span>
            <span className={`portfolio-bar-value ${profitLoss < 0 ? 'negative' : ''}`}>{formatSignedMoney(profitLoss)}</span>
          </div>
        )
      })}
    </div>
  )
}

function EquityTable({ history }) {
  const rows = history.map((item, index) => {
    const previous = index > 0 ? history[index - 1] : null
    const change = previous ? Number(item.total_equity) - Number(previous.total_equity) : null
    const percent = previous && Number(previous.total_equity) ? change / Number(previous.total_equity) : null
    return { ...item, change, percent }
  }).toReversed()
  return (
    <details className="equity-accessible-table">
      <summary>查看权益日线数据表（{history.length} 个交易日）</summary>
      <div className="table-scroll compact-history">
        <table className="data-table">
          <caption className="sr-only">组合权益日线，最新在前。</caption>
          <thead><tr><th>交易日</th><th>权益</th><th>持仓市值</th><th>现金</th><th>日变动</th><th>变动率</th><th>覆盖</th><th>观测</th></tr></thead>
          <tbody>
            {rows.map((item) => (
              <tr key={item.market_day}>
                <td>{item.market_day}</td>
                <td className="mono strong">{formatMoney(Number(item.total_equity))}</td>
                <td className="mono">{formatMoney(Number(item.total_market_value))}</td>
                <td className="mono">{formatMoney(Number(item.usd_cash))}</td>
                <td className={`mono ${(item.change || 0) < 0 ? 'negative' : ''}`}>{item.change == null ? '—' : formatSignedMoney(item.change)}</td>
                <td className={`mono ${(item.percent || 0) < 0 ? 'negative' : ''}`}>{item.percent == null ? '—' : `${(item.percent * 100).toFixed(2)}%`}</td>
                <td>{item.priced_count}/{item.holdings_count}</td>
                <td>{item.after_close ? <b className="badge-close">收盘</b> : <b className="badge-intraday">盘中</b>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

function formatMoney(value) {
  return Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function formatSignedMoney(value) {
  const sign = Number(value) > 0 ? '+' : ''
  return `${sign}${formatMoney(value)}`
}

function formatTime(value) {
  if (!value) return '未知时间'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value))
}
