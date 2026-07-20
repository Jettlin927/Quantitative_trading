import { AlertTriangle, CheckCircle2, ChevronRight, Table2 } from 'lucide-react'

export function Panel({ title, eyebrow, action = '', onAction = undefined, className = '', children }) {
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

export function CoverageMatrix({ rows, detailed = false }) {
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

export function RunTable({ runs, compact = false }) {
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

export function SummaryMetric({ label, value, detail }) {
  return <div className="summary-metric"><span>{label}</span><strong>{value}</strong><small>{detail || '-'}</small></div>
}

export function FactGroup({ title, children }) {
  return <section className="fact-group"><h3>{title}</h3><dl>{children}</dl></section>
}

export function Fact({ label, value, strong = false }) {
  return <><dt>{label}</dt><dd className={strong ? 'strong' : ''}>{value || '-'}</dd></>
}

export function Badge({ value }) {
  const text = String(value || 'unknown')
  const normalized = text.toLowerCase()
  const tone = ['ready', 'ok', 'success', 'succeeded', 'connected', 'available', 'published', '实际数据', '只读'].includes(normalized)
    ? 'good'
    : ['blocked', 'failed', 'fail', 'error', 'empty', '受阻', '不通过'].includes(normalized)
      ? 'bad'
      : 'warn'
  return <span className={`badge ${tone}`}><i />{translateStatus(text)}</span>
}

export function Notice({ tone, title, text }) {
  return <div className={`notice ${tone}`}>{tone === 'success' ? <CheckCircle2 size={17} /> : <AlertTriangle size={17} />}<span><b>{title}</b><small>{text}</small></span></div>
}

export function EmptyRow({ colSpan }) {
  return <tr><td className="empty-cell" colSpan={colSpan}>暂无记录</td></tr>
}

export function EvidenceList({ title, items = [], tone }) {
  return (
    <section className={`evidence-list ${tone}`}>
      <h3>{title}<b>{formatInt(items?.length || 0)}</b></h3>
      <ul>
        {(items || []).map((item, index) => <li key={`${title}-${index}`}>{formatStructuredItem(item)}</li>)}
        {!items?.length ? <li className="muted">暂无记录</li> : null}
      </ul>
    </section>
  )
}

export function DomainFailure({ title, detail }) {
  return <div className="domain-failure" role="alert"><AlertTriangle size={19} /><span><b>{title}</b><small>{detail}</small><em>其他只读区域仍可继续浏览。</em></span></div>
}

export function isInventoryAvailable(data) {
  return data?.level === 'inventory' && data?.status === 'inventory_available'
}

export function formatInt(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return Number(value).toLocaleString('zh-CN')
}

export function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

export function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return `${Number(value).toFixed(2)}%`
}

export function formatSignedPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

export function priceTone(value) {
  const number = Number(value)
  return number > 0 ? 'price-up' : number < 0 ? 'price-down' : ''
}

export function formatWanYi(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  if (Math.abs(number) >= 10000) return `${(number / 10000).toFixed(2)}亿`
  return `${number.toFixed(2)}万`
}

export function formatDailyAmount(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  if (Math.abs(number) >= 100000) return `${(number / 100000).toFixed(2)}亿`
  return `${(number / 10).toFixed(0)}万`
}

export function formatDateTime(value) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export function latestVersion(items = []) {
  return [...(items || [])].sort((left, right) => Number(right.version || 0) - Number(left.version || 0))[0] || null
}

export function evaluationForPublication(detail, projection) {
  const evaluations = detail?.evaluations || []
  const published = latestVersion((detail?.publications || []).filter((item) => item.status === 'published'))
  const evaluationId = projection?.evaluation_id || published?.evaluation_id
  if (evaluationId) return evaluations.find((item) => String(item.id) === String(evaluationId)) || null
  return published ? null : latestVersion(evaluations)
}

export function shortHash(value) {
  if (!value) return '-'
  return String(value).slice(0, 12)
}

export function formatStructuredItem(item) {
  if (item === null || item === undefined) return '-'
  if (typeof item === 'string' || typeof item === 'number') return String(item)
  const preferred = ['statement', 'title', 'label', 'summary', 'description', 'rationale', 'reason', 'metric', 'name']
  for (const key of preferred) {
    if (item[key]) return String(item[key])
  }
  return JSON.stringify(item)
}

export function conclusionTone(value) {
  if (value === '研究通过') return 'passed'
  if (value === '不通过' || value === '受阻') return 'failed'
  return 'conditional'
}

export function inventoryLabel(value) {
  return isInventoryAvailable(value) ? '可用' : translateStatus(value?.status)
}

export function translateStatus(value) {
  const text = String(value || 'unknown')
  const labels = {
    unknown: '未知', ready: '就绪', ok: '正常', success: '成功', succeeded: '执行成功', connected: '已连接', available: '可用',
    blocked: '受阻', failed: '失败', fail: '失败', error: '错误', empty: '空', queued: '排队中', running: '运行中', retrying: '重试中',
    interrupted: '已中断', pending: '待发布', published: '已发布', active: '进行中', stopped: '已停止', approved: '已批准', invalidated: '已失效', historical_import: '历史导入', evaluating: '评价中', completed: '已完成',
    inventory_available: '库存可用', stalled: '停滞', partial: '部分完成', loading: '加载中',
  }
  return labels[text.toLowerCase()] || text
}

export function translateEventType(value) {
  const labels = {
    run_queued: '研究运行已排队', run_started: '研究运行已开始', run_succeeded: '研究运行完成', run_failed: '研究运行失败',
    evaluation_created: '研究评价已形成', publication_published: '研究结论已发布', research_stopped: '研究已停止',
  }
  return labels[String(value || '').toLowerCase()] || String(value || '未知事件')
}
