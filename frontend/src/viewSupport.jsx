import { AlertTriangle, CheckCircle2 } from 'lucide-react'

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

export function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

export function formatDailyAmount(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  const number = Number(value)
  if (Math.abs(number) >= 100000) return `${(number / 100000).toFixed(2)}亿`
  return `${(number / 10).toFixed(0)}万`
}

export function translateStatus(value) {
  const text = String(value ?? 'unknown')
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
