import { FileText, RefreshCw } from 'lucide-react'
import {
  Badge,
  DomainFailure,
  EmptyRow,
  EvidenceList,
  Fact,
  FactGroup,
  Panel,
  SummaryMetric,
  conclusionTone,
  evaluationForPublication,
  formatDateTime,
  formatInt,
  formatStructuredItem,
  latestVersion,
  shortHash,
  translateEventType,
  translateStatus,
} from './viewSupport.jsx'

export function ResearchCockpitView({
  strategies,
  selectedStrategyId,
  setSelectedStrategyId,
  strategyProfile,
  selectedResearchId,
  setSelectedResearchId,
  researchDetail,
  publication,
  loading,
  error,
}) {
  const formalResearches = strategyProfile?.formal_researches || []
  const publishedRecord = latestVersion((researchDetail?.publications || []).filter((item) => item.status === 'published'))
  const evaluation = evaluationForPublication(researchDetail, publication)
  const publicationFact = publication || publishedRecord
  const proposals = strategyProfile?.follow_up_proposals || []
  const runningResearches = (researchDetail?.runs || []).filter((item) => ['running', 'retrying'].includes(item.status)).length
  const missingEvidence = evaluation?.missing_evidence?.length || 0
  const latestPublishedAt = publication?.published_at || publishedRecord?.published_at
  return (
    <div className="view-stack enter research-cockpit">
      <section className="section-heading cockpit-heading">
        <div>
          <span>结构化研究事实</span>
          <h2>研究驾驶舱</h2>
          <p>运行事实与研究结论分开呈现；结构化评价是主入口，原始 HTML 只作为可追溯证据。</p>
        </div>
        <Badge value={loading ? '加载中' : '只读'} />
      </section>
      {error ? <DomainFailure title="研究档案读取失败" detail={error} /> : null}

      <section className="cockpit-metrics" aria-label="研究摘要">
        <SummaryMetric label="待批准研究" value="功能债" detail="编排聚合投影尚未接入" />
        <SummaryMetric label="运行中" value={formatInt(runningResearches)} detail="当前研究的 running / retrying 运行" />
        <SummaryMetric label="受阻研究" value="功能债" detail="编排受阻聚合尚未接入" />
        <SummaryMetric label="最近发布" value={formatDateTime(latestPublishedAt)} detail={publicationFact?.conclusion || '当前研究暂无已发布结论'} />
        <SummaryMetric label="尚缺证据" value={formatInt(missingEvidence)} detail="当前已发布评价版本" />
        <SummaryMetric label="后续提案" value={formatInt(proposals.length)} detail="不等于已批准研究" />
      </section>

      <section className="cockpit-grid">
        <aside className="strategy-rail">
          <header><span>策略档案</span><h2>策略档案</h2></header>
          <div className="strategy-list">
            {strategies.map((strategy) => (
              <button
                className={strategy.strategy_id === selectedStrategyId ? 'active' : ''}
                key={strategy.strategy_id}
                onClick={() => setSelectedStrategyId(strategy.strategy_id)}
              >
                <span><b>{strategy.display_name}</b><small>{strategy.strategy_id}</small></span>
                <span><Badge value={strategy.lifecycle_status} /><small>{formatInt(strategy.formal_research_count)} 项研究</small></span>
              </button>
            ))}
            {!strategies.length && !error ? <div className="empty-state">暂无已登记策略</div> : null}
          </div>
        </aside>

        <div className="research-workspace">
          {strategyProfile ? (
            <>
              <section className="strategy-profile-card">
                <div>
                  <span>{strategyProfile.strategy_id}</span>
                  <h2>{strategyProfile.display_name}</h2>
                  <p>{strategyProfile.economic_thesis || '尚未登记经济假设。'}</p>
                </div>
                <dl>
                  <dt>生命周期</dt><dd><Badge value={strategyProfile.lifecycle_status} /></dd>
                  <dt>登记版本</dt><dd className="mono">{strategyProfile.registry_version}</dd>
                  <dt>代码提交</dt><dd className="mono">{shortHash(strategyProfile.code_commit)}</dd>
                </dl>
              </section>

              <nav className="research-tabs" aria-label="正式研究列表">
                {formalResearches.map((research) => (
                  <button
                    className={research.id === selectedResearchId ? 'active' : ''}
                    key={research.id}
                    onClick={() => setSelectedResearchId(research.id)}
                  >
                    <span>#{research.id.slice(0, 8)}</span>
                    <b>{translateStatus(research.phase)}</b>
                    <small>{research.latest_publication_conclusion || `${formatInt(research.run_count)} 次运行`}</small>
                  </button>
                ))}
                {!formalResearches.length ? <div className="research-empty">该策略暂无正式研究记录</div> : null}
              </nav>

              {researchDetail ? (
                <ResearchDetailView detail={researchDetail} evaluation={evaluation} publication={publicationFact} />
              ) : formalResearches.length && !error ? <div className="loading-state"><RefreshCw className="spin" size={18} />正在读取研究时间线…</div> : null}
            </>
          ) : !error ? <div className="loading-state">选择策略后查看结构化研究档案</div> : null}
        </div>
      </section>
    </div>
  )
}

function ResearchDetailView({ detail, evaluation, publication }) {
  const plan = detail.plan || {}
  const runs = detail.runs || []
  const events = [...(detail.events || [])].sort((left, right) => left.sequence_no - right.sequence_no)
  const publications = [...(detail.publications || [])].sort((left, right) => Number(right.version) - Number(left.version))
  const proposals = [...(detail.follow_up_proposals || [])].sort((left, right) => String(right.created_at || '').localeCompare(String(left.created_at || '')))
  return (
    <div className="research-detail-grid">
      <Panel title="计划与授权" eyebrow={`议题 #${plan.issue_number || '-'}`} className="research-plan-panel">
        <div className="research-facts">
          <FactGroup title="冻结计划">
            <Fact label="研究阶段" value={translateStatus(detail.phase)} strong />
            <Fact label="来源" value={detail.origin === 'historical_import' ? '历史迁移' : '原生研究'} />
            <Fact label="计划版本" value={`v${plan.version || '-'}`} />
            <Fact label="计划哈希" value={shortHash(plan.plan_sha256)} />
          </FactGroup>
          <FactGroup title="批准事实">
            <Fact label="动作" value={translateStatus(detail.approval?.action)} strong />
            <Fact label="批准人" value={detail.approval?.actor_login || '-'} />
            <Fact label="批准时间" value={formatDateTime(detail.approval?.created_at)} />
            <Fact label="代码提交" value={shortHash(plan.code_commit)} />
          </FactGroup>
        </div>
      </Panel>

      <Panel title="运行事实" eyebrow={`${formatInt(runs.length)} 次运行`} className="research-runs-panel">
        <div className="table-scroll">
          <table className="data-table">
            <thead><tr><th>运行</th><th>状态</th><th>阶段</th><th>结果指纹</th><th>完成时间</th><th>失败解释</th></tr></thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.run_id}>
                  <td className="mono strong">{run.run_id}</td>
                  <td><Badge value={run.status} /></td>
                  <td>{translateStatus(run.stage)}</td>
                  <td className="mono">{shortHash(run.result_fingerprint)}</td>
                  <td>{formatDateTime(run.finished_at)}</td>
                  <td>{run.error || '-'}</td>
                </tr>
              ))}
              {!runs.length ? <EmptyRow colSpan={6} /> : null}
            </tbody>
          </table>
        </div>
      </Panel>

      <section className="conclusion-panel">
        <header><span>研究评价</span><h2>研究评价</h2></header>
        <div className="conclusion-hero">
          <small>强制结论状态</small>
          <strong className={`conclusion ${conclusionTone(publication?.conclusion || evaluation?.conclusion)}`}>
            {publication?.conclusion || evaluation?.conclusion || '尚未形成结论'}
          </strong>
          <span>评价 v{publication?.evaluation_version || evaluation?.version || '-'} · {publication?.status === 'published' ? '已发布' : publication?.status || '未发布'}</span>
          {publication?.report_url ? (
            <a className="evidence-link" href={publication.report_url} target="_blank" rel="noreferrer"><FileText size={15} />打开原始 HTML 证据</a>
          ) : <span className="evidence-unavailable">当前没有可读的已发布 HTML 工件</span>}
        </div>
        <div className="evidence-matrix">
          <EvidenceList title="支持证据" items={evaluation?.supporting_evidence} tone="support" />
          <EvidenceList title="反对证据" items={evaluation?.opposing_evidence} tone="oppose" />
          <EvidenceList title="尚缺证据" items={evaluation?.missing_evidence} tone="missing" />
        </div>
      </section>

      <Panel title="限制与后续研究" eyebrow="限制与后续建议" className="research-limits-panel">
        <div className="limits-grid">
          <EvidenceList title="限制项" items={evaluation?.limitations} tone="missing" />
          <EvidenceList title="后续建议" items={evaluation?.follow_up_recommendations} tone="support" />
        </div>
      </Panel>

      <Panel title="发布与后续提案" eyebrow="带时间的只读事实" className="research-publications-panel">
        <div className="publication-proposal-grid">
          <div className="table-scroll"><table className="data-table"><thead><tr><th>发布版本</th><th>状态</th><th>评价</th><th>发布时间</th></tr></thead><tbody>
            {publications.map((item) => <tr key={item.id}><td className="mono strong">v{item.version}</td><td><Badge value={item.status} /></td><td className="mono">{shortHash(item.evaluation_id)}</td><td>{formatDateTime(item.published_at || item.created_at)}</td></tr>)}
            {!publications.length ? <EmptyRow colSpan={4} /> : null}
          </tbody></table></div>
          <div className="table-scroll"><table className="data-table"><thead><tr><th>后续提案</th><th>状态</th><th>形成时间</th></tr></thead><tbody>
            {proposals.map((item) => <tr key={item.id}><td><b>{item.title}</b><small className="table-note">{item.rationale}</small></td><td><Badge value={item.status} /></td><td>{formatDateTime(item.created_at)}</td></tr>)}
            {!proposals.length ? <EmptyRow colSpan={3} /> : null}
          </tbody></table></div>
        </div>
      </Panel>

      <Panel title="研究时间线" eyebrow={`${formatInt(events.length)} 个事件`} className="research-timeline-panel">
        <ol className="research-timeline">
          {events.map((event) => (
            <li key={event.id}>
              <span>{formatInt(event.sequence_no)}</span>
              <div><b>{translateEventType(event.event_type)}</b><small>{formatStructuredItem(event.payload_json)}</small></div>
              <time>{formatDateTime(event.occurred_at)}</time>
            </li>
          ))}
          {!events.length ? <li className="timeline-empty">暂无时间线事件</li> : null}
        </ol>
      </Panel>
    </div>
  )
}
