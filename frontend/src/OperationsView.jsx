import { Database, LockKeyhole, Server, ShieldCheck } from 'lucide-react'
import { Badge, translateStatus } from './viewSupport.jsx'

export function OperationsView({ health }) {
  return (
    <div className="view-stack enter">
      <section className="section-heading"><div><span>只读系统事实</span><h2>系统运维</h2><p>展示 API、PostgreSQL 与个人美股工作台的访问边界；不再承载已退役的公共数据同步、A 股覆盖率或实验数据诊断。</p></div><Badge value="无交易入口" /></section>
      <section className="operations-grid">
        <OperationCard icon={Server} title="API / PostgreSQL" value={`${translateStatus(health?.status)} / ${translateStatus(health?.database)}`} healthy={health?.status === 'ok' && ['connected', 'ok'].includes(health?.database)} />
        <OperationCard icon={LockKeyhole} title="个人工作台访问" value="同源前端代理 · 服务端授权校验" healthy={health?.status === 'ok'} />
        <OperationCard icon={ShieldCheck} title="产品边界" value="手工美股持仓 · 无券商连接 · 无下单" healthy />
        <OperationCard icon={Database} title="持久化边界" value="PostgreSQL 结构化事实 · 研究工件独立保存" healthy={['connected', 'ok'].includes(health?.database)} />
      </section>
    </div>
  )
}

function OperationCard({ icon: Icon, title, value, healthy }) {
  return <article className={`operation-card ${healthy ? 'healthy' : 'attention'}`}><Icon size={20} /><span><b>{title}</b><small>{String(value)}</small></span><i /></article>
}
