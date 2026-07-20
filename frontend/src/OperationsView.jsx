import { Activity, Database, FileText, ListChecks, Server, ShieldCheck } from 'lucide-react'
import { Badge, CoverageMatrix, Panel, RunTable, inventoryLabel, isInventoryAvailable, translateStatus } from './viewSupport.jsx'

export function OperationsView({ health, readiness, coverageRows, syncRuns }) {
  return (
    <div className="view-stack enter">
      <section className="section-heading"><div><span>只读系统事实</span><h2>系统运维</h2><p>仅展示 API、数据库、同步 Worker、同步队列、数据覆盖与同步历史；研究编排和发布健康在专用投影接入前明确标为功能债。</p></div><Badge value="无写入控制" /></section>
      <section className="operations-grid">
        <OperationCard icon={Server} title="API / PostgreSQL" value={`${translateStatus(health?.status)} / ${translateStatus(health?.database)}`} healthy={health?.status === 'ok' && health?.database === 'ok'} />
        <OperationCard icon={Activity} title="同步 Worker 心跳" value={health?.worker ? `${translateStatus(health.worker.status)} · ${health.worker.ageSeconds ?? '-'} 秒` : '未知'} healthy={health?.worker?.status === 'ok' && !health?.worker?.stale} />
        <OperationCard icon={ListChecks} title="同步队列" value={health?.queue ? `${health.queue.active} 个运行中 · ${health.queue.queued} 个排队中` : '未知'} healthy={Boolean(health?.queue) && health.queue.status !== 'stalled'} />
        <OperationCard icon={ShieldCheck} title="研究库存" value={`${inventoryLabel(readiness.stocks)} / ${inventoryLabel(readiness.etf)}`} healthy={isInventoryAvailable(readiness.stocks) && isInventoryAvailable(readiness.etf)} />
        <OperationCard icon={Activity} title="研究 Worker / 队列" value="功能债：研究编排只读投影尚未接入" healthy={false} />
        <OperationCard icon={FileText} title="发布一致性" value="功能债：一致性健康投影尚未接入" healthy={false} />
        <OperationCard icon={Database} title="磁盘" value="功能债：只读磁盘探针尚未接入" healthy={false} />
        <OperationCard icon={ShieldCheck} title="备份" value="人工核验门禁：跟踪议题 #28" healthy={false} />
      </section>
      <section className="data-detail-grid">
        <Panel title="PostgreSQL 数据覆盖" eyebrow="数据库实时计数" className="full-coverage"><CoverageMatrix rows={coverageRows} detailed /></Panel>
        <Panel title="最近同步事实" eyebrow="最近数据写入" className="full-runs"><RunTable runs={syncRuns} /></Panel>
      </section>
    </div>
  )
}

function OperationCard({ icon: Icon, title, value, healthy }) {
  return <article className={`operation-card ${healthy ? 'healthy' : 'attention'}`}><Icon size={20} /><span><b>{title}</b><small>{String(value)}</small></span><i /></article>
}
