import { RefreshCw, Search } from 'lucide-react'
import { TechnicalChart } from './TechnicalChart.jsx'
import {
  Badge,
  CoverageMatrix,
  DomainFailure,
  EmptyRow,
  EvidenceList,
  Fact,
  FactGroup,
  Panel,
  SummaryMetric,
  formatDailyAmount,
  formatDateTime,
  formatInt,
  formatNumber,
  formatPercent,
  formatSignedPercent,
  formatStructuredItem,
  formatWanYi,
  inventoryLabel,
  priceTone,
  translateStatus,
} from './viewSupport.jsx'

export function AShareDataView(props) {
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
  const failedSyncRuns = syncRuns.filter((run) => ['failed', 'error', 'partial'].includes(String(run.status).toLowerCase())).length
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
        <SummaryMetric label="同步异常" value={formatInt(failedSyncRuns)} detail="最近同步运行的 failed / partial" />
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
      <DataQualityDetails readiness={readiness} coverageRows={coverageRows} syncRuns={syncRuns} />
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
          {[...valuation].reverse().map((row) => <tr key={row.tradeDate}><td>{row.tradeDate}</td><td>{formatNumber(row.peTtm)}</td><td>{formatNumber(row.pb)}</td><td>{formatPercent(row.turnoverRate)}</td><td>{formatWanYi(row.totalMv)}</td></tr>)}
          {!valuation.length ? <EmptyRow colSpan={5} /> : null}
        </tbody></table></div>
      </Panel>
      <Panel title="财务历史" eyebrow="公告日时点可见">
        <div className="table-scroll compact-history"><table className="data-table"><thead><tr><th>公告日</th><th>报告期</th><th>EPS</th><th>ROE</th><th>营收同比</th></tr></thead><tbody>
          {[...financial].reverse().map((row) => <tr key={`${row.annDate}-${row.endDate}`}><td>{row.annDate}</td><td>{row.endDate}</td><td>{formatNumber(row.eps)}</td><td>{formatPercent(row.roe)}</td><td>{formatPercent(row.trYoy)}</td></tr>)}
          {!financial.length ? <EmptyRow colSpan={5} /> : null}
        </tbody></table></div>
      </Panel>
    </section>
  )
}

function CatalogPanel({ title, eyebrow, kind, rows, codeKey, nameKey = 'name', metaKey, selected, onSelect }) {
  const loadSummary = `已加载 ${formatInt(rows.length)}${rows.length >= 1000 ? ' · 已达 API 上限' : ''}`
  return (
    <Panel title={title} eyebrow={`${eyebrow} · ${loadSummary}`}>
      <div className="catalog-list">
        {rows.map((row) => (
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
  const matchesSelection = detail.kind === selection.kind && detail.code === selection.code
  const currentDetail = matchesSelection ? detail : { bars: [], adjustments: [], members: [] }
  const adjustmentByDate = new Map((currentDetail.adjustments || []).map((item) => [item.tradeDate, item.adjFactor]))
  return (
    <Panel
      title={selection.code || '请选择目录标的'}
      eyebrow={isIndustry ? `当前交易日行业成员 · ${formatInt(currentDetail.members?.length || 0)}` : `${selection.kind === 'fund' ? 'ETF' : '指数'}近一年日线 · ${formatInt(currentDetail.bars?.length || 0)}`}
    >
      {loading ? <div className="loading-state"><RefreshCw className="spin" size={18} />正在读取目录明细…</div> : (
        <div className="table-scroll compact-history">
          {isIndustry ? (
            <table className="data-table"><thead><tr><th>成分代码</th><th>名称</th><th>纳入日</th><th>移出日</th><th>最新成员</th></tr></thead><tbody>
              {(currentDetail.members || []).map((row) => <tr key={`${row.conCode}-${row.inDate}`}><td className="mono strong">{row.conCode}</td><td>{row.conName || '-'}</td><td>{row.inDate}</td><td>{row.outDate || '-'}</td><td><Badge value={row.isNew ? '是' : '否'} /></td></tr>)}
              {!currentDetail.members?.length ? <EmptyRow colSpan={5} /> : null}
            </tbody></table>
          ) : (
            <table className="data-table"><thead><tr><th>交易日</th><th>收盘</th><th>涨跌幅</th><th>成交额</th>{selection.kind === 'fund' ? <th>复权因子</th> : null}</tr></thead><tbody>
              {[...(currentDetail.bars || [])].reverse().map((row) => <tr key={row.tradeDate}><td>{row.tradeDate}</td><td className="mono strong">{formatNumber(row.close)}</td><td className={priceTone(row.pctChg)}>{formatSignedPercent(row.pctChg)}</td><td>{formatDailyAmount(row.amount)}</td>{selection.kind === 'fund' ? <td>{formatNumber(adjustmentByDate.get(row.tradeDate))}</td> : null}</tr>)}
              {!currentDetail.bars?.length ? <EmptyRow colSpan={selection.kind === 'fund' ? 5 : 4} /> : null}
            </tbody></table>
          )}
        </div>
      )}
    </Panel>
  )
}

function DataQualityDetails({ readiness, coverageRows, syncRuns }) {
  const blockers = [
    ...(readiness.stocks?.blockers || []).map((item) => `A 股：${formatStructuredItem(item)}`),
    ...(readiness.etf?.blockers || []).map((item) => `ETF：${formatStructuredItem(item)}`),
  ]
  const gaps = coverageRows.filter((item) => item.status !== 'available').map((item) => `${item.label}：${translateStatus(item.status)}`)
  const localGapDebt = ['功能债：当前只读 API 尚未提供按日期与标的定位的局部缺口质量结果；整表非空不代表局部完整。']
  const problematicRuns = syncRuns.filter((run) => ['failed', 'error', 'partial'].includes(String(run.status).toLowerCase()))
  return (
    <Panel title="覆盖缺口与同步异常" eyebrow="质量明细">
      <div className="quality-detail-grid">
        <EvidenceList title="库存阻塞项" items={blockers} tone="missing" />
        <EvidenceList title="空缺数据集" items={gaps} tone="missing" />
        <EvidenceList title="局部缺口探针" items={localGapDebt} tone="missing" />
        <div className="table-scroll"><table className="data-table"><thead><tr><th>同步目标</th><th>状态</th><th>范围</th><th>失败解释</th><th>时间</th></tr></thead><tbody>
          {problematicRuns.map((run) => <tr key={run.id || `${run.target}-${run.createdAt}`}><td className="mono strong">{run.target}</td><td><Badge value={run.status} /></td><td>{[run.startDate, run.endDate].filter(Boolean).join(' → ') || '-'}</td><td>{run.message || '未提供错误说明'}</td><td>{formatDateTime(run.createdAt)}</td></tr>)}
          {!problematicRuns.length ? <EmptyRow colSpan={5} /> : null}
        </tbody></table></div>
      </div>
    </Panel>
  )
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
