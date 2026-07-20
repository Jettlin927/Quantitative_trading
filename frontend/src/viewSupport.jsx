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
  if (['string', 'number', 'boolean'].includes(typeof item)) return String(item)
  const preferred = ['statement', 'title', 'label', 'summary', 'description', 'rationale', 'reason', 'metric', 'name']
  for (const key of preferred) {
    if (item[key]) return String(item[key])
  }
  const entries = Object.entries(item)
  if (!entries.length) return '无附加信息'
  return entries.slice(0, 6).map(([key, value]) => `${structuredKeyLabel(key)}：${formatStructuredValue(value)}`).join('；')
}

function structuredKeyLabel(key) {
  const labels = {
    actorLogin: '操作人', approvalCommentId: '批准评论', artifactRoot: '工件目录', attemptCount: '尝试次数', commentId: '评论',
    conclusion: '结论', error: '错误', errorKind: '错误类型', evaluationId: '评价', evaluationSha256: '评价指纹',
    issueCommentId: '议题评论', issueNumber: '议题', manifestUrl: '清单地址', maxAttempts: '最大尝试次数',
    newPlanSha256: '新计划指纹', planSha256: '计划指纹', previousWorker: '原 Worker', publicationId: '发布',
    publicationStatus: '发布状态', resourceBudget: '资源预算', resumeRunId: '恢复运行', retryable: '可重试', runId: '运行',
    supersededByPlanId: '替代计划', supersedesPublicationId: '替代发布', workerId: 'Worker',
  }
  return labels[key] || key
}

function formatStructuredValue(value) {
  if (value === null || value === undefined || value === '') return '-'
  if (Array.isArray(value)) return value.map(formatStructuredValue).join('、') || '-'
  if (typeof value === 'object') {
    return Object.entries(value).slice(0, 4).map(([key, child]) => `${structuredKeyLabel(key)}=${formatStructuredValue(child)}`).join('，') || '无附加信息'
  }
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
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
    interrupted: '已中断', pending: '待发布', published: '已发布', active: '进行中', stopped: '已停止', stopping: '停止中', approved: '已批准', invalidated: '已失效', historical_import: '历史导入', evaluating: '评价中', publishing: '发布中', completed: '已完成', finalized: '已归档',
    quality_gate: '质量门禁', input_snapshot: '冻结输入', features_targets: '特征与目标', simulation: '组合模拟', metrics: '指标计算', manifest: '生成清单', finalize: '归档完成',
    proposed: '已提议', accepted: '已接受', rejected: '已拒绝', converted: '已转为计划',
    inventory_available: '库存可用', stalled: '停滞', partial: '部分完成', loading: '加载中',
  }
  return labels[text.toLowerCase()] || text
}

export function translateEventType(value) {
  const labels = {
    plan_approved: '冻结计划已批准', research_queued: '正式研究已排队', research_attempt_started: '研究尝试已开始',
    research_run_succeeded: '研究运行完成', research_retry_scheduled: '研究重试已排期', research_blocked: '研究已受阻',
    research_lease_recovered: '研究租约已恢复', research_succeeded_after_lease_expiry: '过期租约成功运行已核对', research_failed_after_lease_expiry: '过期租约失败运行已核对',
    research_stop_requested: '研究停止已请求', research_stopped: '研究已停止', research_stopped_before_start: '研究启动前已停止',
    research_stopped_before_attempt: '研究尝试前已停止', research_stopped_after_run: '研究运行后已停止', research_stopped_after_lease_expiry: '过期租约停止已落实',
    research_resume_requested: '研究恢复已请求', invalid_plan_stop_requested: '失效计划停止已请求', plan_approval_invalidated: '计划批准已失效',
    approval_comment_invalidated: '批准评论已失效', closed_issue_stop_requested: '关闭议题触发停止请求', closed_issue_blocked: '关闭议题已阻止研究',
    research_publication_prepared: '研究发布已准备', research_published: '研究结论已发布', research_publication_failed: '研究发布失败',
    research_publication_recovered: '研究发布已恢复',
    run_queued: '研究运行已排队', run_started: '研究运行已开始', run_succeeded: '研究运行完成', run_failed: '研究运行失败',
    evaluation_created: '研究评价已形成', publication_published: '研究结论已发布',
  }
  return labels[String(value || '').toLowerCase()] || String(value || '未知事件')
}
