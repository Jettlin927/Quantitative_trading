import { ChevronRight, Clock3, Database, FlaskConical, Globe2, ShieldAlert } from 'lucide-react'
import {
  Badge,
  EmptyRow,
  Panel,
  SummaryMetric,
  formatDateTime,
  formatInt,
  formatNumber,
  formatPercent,
} from './viewSupport.jsx'

export function USDataBoundaryView({ usDb, usExperiment }) {
  const usAssets = usDb?.assets || []
  const sampleCounts = usDb?.counts || {}
  const universe = usExperiment?.universe || {}
  const coverage = usExperiment?.coverage || {}
  const validation = usExperiment?.validation || {}
  const schedule = usExperiment?.schedule || {}
  const byMarket = universe.byMarket || {}
  const validationStatuses = Object.entries(validation.byStatus || {})
  const recentJobs = usExperiment?.recentJobs || []
  const failedInstruments = usExperiment?.failedInstruments || []
  const validationAlerts = usExperiment?.recentValidationAlerts || []
  const marketDetail = [
    'NASDAQ ' + formatInt(byMarket['105']),
    'NYSE ' + formatInt(byMarket['106']),
    '其他 ' + formatInt(byMarket['107']),
  ].join(' · ')
  const validationTolerance = '价格容差 ' + (validation.priceTolerancePct ?? 0.5) + '% · 成交量容差 ' + (validation.volumeTolerancePct ?? 5) + '%'

  return (
    <div className="view-stack enter">
      <section className="functional-debt-card us-experiment-boundary">
        <div className="debt-icon"><FlaskConical size={25} /></div>
        <div>
          <span>免费实验组合 · researchEligible=false</span>
          <h2>美股日线实验数据已隔离接入</h2>
          <p>yfinance 保存未自动复权 OHLCV、Adj Close 与公司行为；AKShare 只做独立同日对照，不覆盖主数据。当前目录、历史范围与校验事实可审计，但尚不具备 point-in-time 历史标的范围，因此不能进入正式研究。</p>
        </div>
        <a href="https://github.com/Jettlin927/Quantitative_trading/issues/27" target="_blank" rel="noreferrer">查看工程合同 #27 <ChevronRight size={14} /></a>
      </section>

      <section className="sample-ribbon experiment-ribbon">
        <Badge value="实验数据" />
        <span><Clock3 size={14} /> 每日 {schedule.dailyAt || '10:00'} · {schedule.timezone || 'Asia/Shanghai'}</span>
        <strong>目标起点 {usExperiment?.targetStartDate || '2010-01-01'} · 当前目录全量、不设人工票数上限</strong>
      </section>

      <section className="summary-ribbon us-experiment-metrics">
        <SummaryMetric label="当前目录" value={formatInt(universe.current)} detail={marketDetail} />
        <SummaryMetric label="已有行情标的" value={formatInt(coverage.currentInstrumentsWithBars)} detail={'当前目录覆盖 ' + formatPercent(coverage.currentPercent)} />
        <SummaryMetric label="日线记录" value={formatInt(coverage.dailyBars)} detail={[coverage.startDate, coverage.endDate].filter(Boolean).join(' → ') || '尚未回填'} />
        <SummaryMetric label="独立校验" value={formatInt(validation.checks)} detail={[validation.startDate, validation.endDate].filter(Boolean).join(' → ') || '尚未校验'} />
      </section>

      <div className="us-experiment-grid">
        <Panel title="覆盖与来源合同" eyebrow="当前目录快照，不是历史 UNIVERSE">
          <div className="experiment-facts">
            <span><Globe2 size={15} /><b>目录</b><strong>{universe.selection || 'm:105,m:106,m:107 当前全量目录'}</strong></span>
            <span><Database size={15} /><b>主行情</b><strong>{usExperiment?.sources?.primaryDaily || 'yfinance 1d auto_adjust=false'}</strong></span>
            <span><ShieldAlert size={15} /><b>研究门禁</b><strong>实验可用 / 正式研究不可用</strong></span>
          </div>
        </Panel>
        <Panel title="AKShare 对照" eyebrow={validationTolerance}>
          <div className="table-scroll">
            <table className="data-table compact">
              <thead><tr><th>状态</th><th>记录</th><th>解释</th></tr></thead>
              <tbody>
                {validationStatuses.map(([status, count]) => <tr key={status}><td><Badge value={status} /></td><td className="mono">{formatInt(count)}</td><td>{validationLabel(status)}</td></tr>)}
                {!validationStatuses.length ? <EmptyRow colSpan={3} /> : null}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>

      <div className="us-experiment-grid operational-evidence-grid">
        <Panel title="近期同步任务" eyebrow={'覆盖快照 ' + formatDateTime(usExperiment?.snapshotAt)}>
          <div className="table-scroll">
            <table className="data-table compact">
              <thead><tr><th>任务</th><th>状态</th><th>写入</th><th>完成时间</th></tr></thead>
              <tbody>
                {recentJobs.map((job) => <tr key={job.id}><td><b>{jobActionLabel(job.action)}</b><small className="mono evidence-id">{job.id}</small></td><td><Badge value={job.status} /></td><td className="mono">{formatInt(job.rowsUpserted)}</td><td>{formatDateTime(job.finishedAt || job.createdAt)}</td></tr>)}
                {!recentJobs.length ? <EmptyRow colSpan={4} /> : null}
              </tbody>
            </table>
          </div>
        </Panel>
        <Panel title="失败标的" eyebrow="仅展示最近 20 项；完整目录可通过只读 API 查询">
          <div className="table-scroll">
            <table className="data-table compact">
              <thead><tr><th>源代码</th><th>名称</th><th>最近同步</th><th>错误</th></tr></thead>
              <tbody>
                {failedInstruments.map((item) => <tr key={item.sourceCode}><td className="mono strong">{item.sourceCode}</td><td>{item.name || '-'}</td><td>{formatDateTime(item.lastSyncAt)}</td><td className="error-detail">{item.lastSyncError || '-'}</td></tr>)}
                {!failedInstruments.length ? <EmptyRow colSpan={4} /> : null}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>

      <Panel title="校验异常明细" eyebrow="yfinance 与 AKShare 逐票同日事实；不自动覆盖主源">
        <div className="table-scroll">
          <table className="data-table validation-detail-table">
            <thead><tr><th>标的 / 日期</th><th>状态</th><th>yfinance OHLCV</th><th>AKShare OHLCV</th><th>最大价格差</th><th>成交量差</th><th>错误 / 解释</th></tr></thead>
            <tbody>
              {validationAlerts.map((item) => <tr key={`${item.sourceCode}-${item.tradeDate}`}><td><b className="mono">{item.sourceCode}</b><small>{item.tradeDate}</small></td><td><Badge value={item.status} /></td><td className="mono">{ohlcvSummary(item.yfinance)}</td><td className="mono">{ohlcvSummary(item.akshare)}</td><td className="mono">{relativePercent(item.maxPriceRelativeDiff)}</td><td className="mono">{relativePercent(item.volumeRelativeDiff)}</td><td className="error-detail">{item.message || validationLabel(item.status)}</td></tr>)}
              {!validationAlerts.length ? <EmptyRow colSpan={7} /> : null}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="实验边界与缺口" eyebrow="不得解释为研究结论或交易指令">
        <ul className="experiment-limitations">
          {(usExperiment?.limitations || [
            '当前目录不是历史 point-in-time universe，退市与历史成分尚未补齐。',
            '免费源可能限流、缺失或调整历史；失败和对照差异会单独留痕。',
            '该数据仅供实验与工程验证，不可作为正式研究输入。',
          ]).map((item) => <li key={item}>{item}</li>)}
        </ul>
      </Panel>

      <section className="sample-ribbon"><Badge value="仅样例" /><span>旧开发夹具继续只读保留，与实验行情表隔离</span><strong>{formatInt(sampleCounts.assets)} 个资产 · {formatInt(sampleCounts.assetDailyPrices)} 条价格</strong></section>
      <Panel title="美股样例资产" eyebrow="非研究级开发夹具">
        <div className="table-scroll"><table className="data-table"><thead><tr><th>代码</th><th>名称</th><th>类型</th><th>风险标签</th><th>样例收盘</th></tr></thead><tbody>
          {usAssets.map((asset) => <tr key={asset.naturalKey || asset.symbol}><td className="mono strong">{asset.symbol}</td><td>{asset.name || '-'}</td><td>{asset.instrumentType || '-'}</td><td>{asset.riskTag || '-'}</td><td>{formatNumber(asset.latestPrice?.close)}</td></tr>)}
          {!usAssets.length ? <EmptyRow colSpan={5} /> : null}
        </tbody></table></div>
      </Panel>
    </div>
  )
}

function jobActionLabel(action) {
  if (action === 'us_experiment_universe') return '刷新当前目录'
  if (action === 'us_experiment_prices') return '同步实验日线'
  return action || '-'
}

function ohlcvSummary(row) {
  if (!row) return '-'
  return [row.open, row.high, row.low, row.close, row.volume].map((value) => value ?? '-').join(' / ')
}

function relativePercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return formatPercent(Number(value) * 100)
}

function validationLabel(status) {
  if (status === 'match') return '同日未复权 OHLCV 在容差内'
  if (status === 'mismatch') return '同日价格或成交量超出容差'
  if (status === 'source_missing') return '至少一个数据源缺少同日记录'
  if (status === 'error') return '校验源调用失败，错误已留痕'
  return '待解释状态'
}
