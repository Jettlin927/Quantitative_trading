import { ChevronRight, Globe2 } from 'lucide-react'
import { Badge, EmptyRow, Panel, formatInt, formatNumber } from './viewSupport.jsx'

export function USDataBoundaryView({ usDb }) {
  const usAssets = usDb?.assets || []
  const counts = usDb?.counts || {}
  return (
    <div className="view-stack enter">
      <section className="functional-debt-card">
        <div className="debt-icon"><Globe2 size={25} /></div>
        <div><span>功能债与样例边界</span><h2>美股研究级实际数据尚未接入</h2><p>当前内容仅是开发用样例，不具备时点可见、历史标的范围、复权与研究发布资格。不得将其解释为实际持仓、真实账户或研究结论。</p></div>
        <a href="https://github.com/Jettlin927/Quantitative_trading/issues/27" target="_blank" rel="noreferrer">查看待补信息 #27 <ChevronRight size={14} /></a>
      </section>
      <section className="sample-ribbon"><Badge value="仅样例" /><span>开发夹具只读投影</span><strong>{formatInt(counts.assets)} 个资产 · {formatInt(counts.assetDailyPrices)} 条价格</strong></section>
      <Panel title="美股样例资产" eyebrow="非研究级开发夹具">
        <div className="table-scroll"><table className="data-table"><thead><tr><th>代码</th><th>名称</th><th>类型</th><th>风险标签</th><th>样例收盘</th></tr></thead><tbody>
          {usAssets.map((asset) => <tr key={asset.naturalKey || asset.symbol}><td className="mono strong">{asset.symbol}</td><td>{asset.name || '-'}</td><td>{asset.instrumentType || '-'}</td><td>{asset.riskTag || '-'}</td><td>{formatNumber(asset.latestPrice?.close)}</td></tr>)}
          {!usAssets.length ? <EmptyRow colSpan={5} /> : null}
        </tbody></table></div>
      </Panel>
    </div>
  )
}
