import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { LineSeries, createChart } from "lightweight-charts";
import {
  ArrowLeft,
  BarChart3,
  CheckCircle2,
  CircleAlert,
  CircleDashed,
  Database,
  Gauge,
  LineChart,
  RefreshCw,
  UserRound,
  XCircle,
} from "lucide-react";
import "./styles.css";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "http://localhost:18000").replace(/\/$/, "");
const EXECUTABLE_STRATEGY_ID = "new-research-reset";
const TODAY = new Date().toISOString().slice(0, 10);

const VALIDATION_PHASES = [
  {
    id: "train-2020-2024",
    label: "第一轮",
    title: "2020-01-01 至 2024-12-31",
    objective: "策略定型与主评估窗口，收益、回撤、盈亏比必须同时达标。",
    role: "负责评估",
    startDate: "2020-01-01",
    endDate: "2024-12-31",
  },
  {
    id: "oos-2025-now",
    label: "第二轮",
    title: `2025-01-01 至 ${TODAY}`,
    objective: "样本外复核，通过后才允许进入当前适用性讨论。",
    role: "负责复核",
    startDate: "2025-01-01",
    endDate: TODAY,
  },
  {
    id: "bear-market-observe",
    label: "最终观察",
    title: "标志性熊市压力段",
    objective: "只观察熊市韧性、流动性和交易纪律，不作为策略达标判定。",
    role: "只观察",
    startDate: null,
    endDate: null,
  },
];

const WORKSPACE_NAV = [
  { id: "screen", label: "选股池", target: "strategy-overview", icon: Database, hint: "候选与基线" },
  { id: "single", label: "单票验证", target: "risk-evidence", icon: LineChart, hint: "尾部与交易" },
  { id: "market", label: "全市场验证", target: "metrics-overview", icon: BarChart3, hint: "组合指标" },
  { id: "diagnostic", label: "复盘诊断", target: "stage-gates", icon: Gauge, hint: "阶段闸门" },
];

const TOP_NAV = [
  { label: "概览", target: "strategy-overview" },
  { label: "权益", target: "equity-section" },
  { label: "指标", target: "metrics-overview" },
  { label: "闸门", target: "stage-gates" },
  { label: "证据", target: "evidence-files" },
];

const EVIDENCE_TABS = [
  { label: "权益曲线", target: "equity-section" },
  { label: "指标概览", target: "metrics-overview" },
  { label: "阶段闸门", target: "stage-gates" },
  { label: "证据文件", target: "evidence-files" },
];

function App() {
  const [apiStatus, setApiStatus] = useState({ tone: "muted", text: "等待后端证据源" });
  const [overview, setOverview] = useState(null);
  const [baseline, setBaseline] = useState(null);
  const [strategyEvaluation, setStrategyEvaluation] = useState(null);
  const [usOverview, setUsOverview] = useState(null);
  const [usImportPreview, setUsImportPreview] = useState(null);
  const [strategyLifecycle, setStrategyLifecycle] = useState(null);
  const [runs, setRuns] = useState([]);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [activeRun, setActiveRun] = useState(null);
  const [activeSection, setActiveSection] = useState("strategy-overview");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const loadRun = useCallback(async (runId) => {
    if (!runId) return null;
    const data = await apiFetch(`/api/research/runs/${encodeURIComponent(runId)}`);
    setActiveRun(data);
    return data;
  }, []);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      let dashboardError = null;
      try {
        const dashboard = await apiFetch("/api/research/dashboard?run_limit=160");
        const nextOverview = dashboard.overview || null;
        const nextBaseline = dashboard.baseline || null;
        const nextEvaluation = dashboard.strategyEvaluation || null;
        const nextLifecycle = dashboard.strategyLifecycle || null;
        const nextUsOverview = dashboard.usOverview || null;
        const nextUsImportPreview = dashboard.usImportPreview || null;
        const nextRuns = dashboard.researchRuns?.runs || [];

        setApiStatus({ tone: "good", text: `API ${dashboard.health?.status || "ok"} · 聚合证据` });
        setOverview(nextOverview);
        setBaseline(nextBaseline);
        setStrategyEvaluation(nextEvaluation);
        setStrategyLifecycle(nextLifecycle);
        setUsOverview(nextUsOverview);
        setUsImportPreview(nextUsImportPreview);
        setRuns(nextRuns);

        const preferredRunId = nextBaseline?.runId || nextRuns[0]?.runId || "";
        setSelectedRunId(preferredRunId);
        setActiveRun(null);
        return;
      } catch (err) {
        dashboardError = err;
      }

      const [healthResult, overviewResult, baselineResult, evaluationResult, lifecycleResult, usOverviewResult, usImportPreviewResult, runsResult] = await Promise.allSettled([
        apiFetch("/api/health"),
        apiFetch("/api/research/overview"),
        apiFetch(`/api/strategies/executable/${EXECUTABLE_STRATEGY_ID}`),
        apiFetch("/api/strategy-evaluations"),
        apiFetch("/api/strategy-lifecycle"),
        apiFetch("/api/us-research/overview"),
        apiFetch("/api/us-research/import-preview"),
        apiFetch("/api/research/runs?limit=160"),
      ]);

      if (healthResult.status === "fulfilled") {
        setApiStatus({ tone: "good", text: `API ${healthResult.value.status || "ok"}` });
      } else {
        setApiStatus({ tone: "bad", text: `API 不可用：${healthResult.reason.message}` });
      }

      const nextOverview = valueOrNull(overviewResult);
      const nextBaseline = valueOrNull(baselineResult);
      const nextEvaluation = valueOrNull(evaluationResult);
      const nextLifecycle = valueOrNull(lifecycleResult);
      const nextUsOverview = valueOrNull(usOverviewResult);
      const nextUsImportPreview = valueOrNull(usImportPreviewResult);
      const nextRuns = valueOrNull(runsResult)?.runs || [];
      setOverview(nextOverview);
      setBaseline(nextBaseline);
      setStrategyEvaluation(nextEvaluation);
      setStrategyLifecycle(nextLifecycle);
      setUsOverview(nextUsOverview);
      setUsImportPreview(nextUsImportPreview);
      setRuns(nextRuns);

      const preferredRunId = nextBaseline?.runId || nextRuns[0]?.runId || "";
      setSelectedRunId(preferredRunId);
      if (preferredRunId) {
        await loadRun(preferredRunId);
      } else {
        setActiveRun(null);
      }

      const failures = [overviewResult, baselineResult, evaluationResult, lifecycleResult, usOverviewResult, usImportPreviewResult, runsResult]
        .filter((item) => item.status === "rejected")
        .map((item) => item.reason.message);
      if (failures.length) {
        const prefix = dashboardError ? `聚合证据读取失败：${dashboardError.message}；` : "";
        setError(`${prefix}部分证据读取失败：${failures.join("；")}`);
      } else if (dashboardError) {
        setError(`聚合证据读取失败，已回落到分散接口：${dashboardError.message}`);
      }
    } catch (err) {
      setError(err.message);
      setApiStatus({ tone: "bad", text: `证据读取失败：${err.message}` });
    } finally {
      setLoading(false);
    }
  }, [loadRun]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refreshAll();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refreshAll]);

  const openRun = async (runId) => {
    setSelectedRunId(runId);
    setLoading(true);
    setError("");
    try {
      await loadRun(runId);
    } catch (err) {
      setError(`研究 run 读取失败：${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const jumpToSection = useCallback((sectionId) => {
    setActiveSection(sectionId);
    document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  const displayRun = useMemo(() => activeRun || baseline, [activeRun, baseline]);
  const displaySpec = useMemo(() => baseline?.spec || displayRun?.spec || {}, [baseline, displayRun]);
  const displayMetrics = useMemo(() => displayRun?.metrics || baseline?.metrics || {}, [baseline, displayRun]);
  const displayAnalysis = useMemo(() => displayRun?.analysis || baseline?.analysis || {}, [baseline, displayRun]);
  const backendEvaluation = strategyEvaluation?.evaluations?.[0] || null;
  const resetEvaluationWindows = useMemo(() => strategyEvaluation?.evaluationWindows || [], [strategyEvaluation]);
  const equityPoints = useMemo(() => displayRun?.equityCurve || baseline?.equityCurve || [], [baseline, displayRun]);
  const phaseRows = useMemo(
    () =>
      backendEvaluation?.evaluationWindows?.length
        ? buildBackendPhaseRows(backendEvaluation.evaluationWindows, displaySpec)
        : resetEvaluationWindows.length
          ? buildBackendPhaseRows(resetEvaluationWindows, displaySpec)
        : buildValidationPhaseRows(VALIDATION_PHASES, displaySpec, displayAnalysis),
    [backendEvaluation, displayAnalysis, displaySpec, resetEvaluationWindows],
  );
  const gateRows = useMemo(() => buildGateRows(displayRun || baseline), [baseline, displayRun]);
  const metricRows = useMemo(() => buildMetricsOverviewRows(displayRun || baseline, displaySpec, displayMetrics), [displayMetrics, displayRun, displaySpec, baseline]);
  const rollingRows = useMemo(() => buildRollingStats(equityPoints), [equityPoints]);
  const kpis = useMemo(() => buildKpiStrip(displayMetrics, displaySpec, displayRun || baseline), [baseline, displayMetrics, displayRun, displaySpec]);

  return (
    <div className="qc-shell">
      <aside className="qc-sidebar" aria-label="主导航">
        <div className="qc-logo">
          <span>QUANT</span>
          <strong>DESK</strong>
        </div>
        <nav>
          {WORKSPACE_NAV.map(({ id, label, target, icon: Icon, hint }) => (
            <button key={id} type="button" className={activeSection === target ? "active" : ""} data-target={target} onClick={() => jumpToSection(target)}>
              <Icon size={19} />
              <span>
                <strong>{label}</strong>
                <em>{hint}</em>
              </span>
            </button>
          ))}
        </nav>
      </aside>

      <main className="qc-main">
        <header className="qc-topbar">
          <div className="search-slot">策略 / 证据 / 指标</div>
          <nav>
            {TOP_NAV.map((item) => (
              <button key={item.target} type="button" className={activeSection === item.target ? "active" : ""} data-target={item.target} onClick={() => jumpToSection(item.target)}>
                {item.label}
              </button>
            ))}
          </nav>
          <StatusLine tone={apiStatus.tone} text={apiStatus.text} />
        </header>

        {error ? (
          <section className="alert-strip">
            <CircleAlert size={17} />
            <span>{error}</span>
          </section>
        ) : null}

        <div className="strategy-layout">
          <section className="strategy-content">
            <StrategyHero baseline={baseline} displayRun={displayRun} onRefresh={refreshAll} loading={loading} onBack={() => jumpToSection("strategy-overview")} />
            <KpiStrip items={kpis} />
            <StrategyDescription baseline={baseline} displayRun={displayRun} overview={overview} />
            <AnchorTabs activeSection={activeSection} onJump={jumpToSection} />

            <ChartPanel id="equity-section" title="策略权益" subtitle={formatRunWindow(displaySpec)}>
              <EquityChart points={equityPoints} />
            </ChartPanel>

            <section className="chart-grid">
              <ChartPanel id="risk-evidence" title="尾部证据" subtitle="按当前 run 的风险样本映射">
                <AssetTreemap run={displayRun || baseline} />
              </ChartPanel>
              <ChartPanel title="回撤" subtitle="Drawdown">
                <MiniLineChart points={buildDrawdownSeries(equityPoints)} color="#5f8fff" emptyText="当前 run 没有回撤曲线。" />
              </ChartPanel>
              <ChartPanel title="基准" subtitle="Benchmark">
                <MiniLineChart points={buildReturnSeries(equityPoints)} color="#6f9cff" emptyText="当前 run 没有收益曲线。" />
              </ChartPanel>
              <ChartPanel title="容量" subtitle="Capacity">
                <CapacityPanel metrics={displayMetrics} run={displayRun || baseline} />
              </ChartPanel>
            </section>

            <section id="metrics-overview" className="research-tabs">
              <div className="local-tabs">
                <button type="button" className="active">概述</button>
                <button type="button">订单</button>
              </div>
              <MetricsOverviewTable rows={metricRows} loading={loading} />
              <RollingStatsTable rows={rollingRows} loading={loading} />
            </section>
          </section>

          <aside className="strategy-aside">
            <AuthorPanel baseline={baseline} overview={overview} />
            <UsResearchPanel overview={usOverview} importPreview={usImportPreview} />
            <StrategyArchivePanel lifecycle={strategyLifecycle} />
            <PhasePanel id="stage-gates" rows={phaseRows} />
            <GatePanel rows={gateRows} />
            <RunSelector runs={runs} selectedRunId={selectedRunId} onSelect={openRun} loading={loading} />
            <EvidencePanel id="evidence-files" run={displayRun || baseline} overview={overview} />
          </aside>
        </div>
      </main>
    </div>
  );
}

function valueOrNull(result) {
  return result.status === "fulfilled" ? result.value : null;
}

async function apiFetch(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

function StatusLine({ tone, text }) {
  return (
    <div className={`status-line ${tone}`}>
      <i />
      <span>{text}</span>
    </div>
  );
}

function StrategyHero({ baseline, displayRun, onRefresh, loading, onBack }) {
  const spec = baseline?.spec || displayRun?.spec || {};
  return (
    <section id="strategy-overview" className="strategy-hero">
      <button type="button" className="back-button" aria-label="回到概览" onClick={onBack}>
        <ArrowLeft size={18} />
      </button>
      <div className="strategy-title">
        <h1>{baseline?.label || displayRun?.label || "新策略研究台"}</h1>
        <div className="strategy-meta">
          <span>
            <UserRound size={14} />
            后端研究引擎
          </span>
          <span>{baseline || displayRun ? `V ${spec.version || "1.0.0"}` : "从头开始"}</span>
          <span>{baseline || displayRun ? `提交日期：${formatDisplayDate(displayRun?.finishedAt || baseline?.finishedAt || TODAY)}` : "旧策略已退场"}</span>
        </div>
      </div>
      <button type="button" className="clone-button" onClick={onRefresh} disabled={loading}>
        <RefreshCw size={16} />
        刷新
      </button>
    </section>
  );
}

function KpiStrip({ items }) {
  return (
    <section className="kpi-strip">
      {items.map((item) => (
        <article key={item.label} className={item.tone}>
          <strong>{item.value}</strong>
          <span>{item.label}</span>
        </article>
      ))}
    </section>
  );
}

function StrategyDescription({ baseline, displayRun, overview }) {
  const spec = baseline?.spec || displayRun?.spec || {};
  const analysis = baseline?.analysis || displayRun?.analysis || {};
  const hasStrategy = Boolean(baseline || displayRun);
  const firstParagraph =
    !hasStrategy
      ? "旧策略已全部从当前主线退场。新的研究台只保留数据、评估窗口和证据归档，不再把历史策略当作候选。"
      : spec.hypothesis ||
        analysis.verdict ||
        "该策略由后端研究证据驱动，当前页面只展示策略评估、指标、回撤、交易证据和阶段闸门，不提供实盘交易执行。";
  const secondParagraph =
    !hasStrategy
      ? "下一条策略需要从假设开始：先写清楚赚什么钱、失败条件和三段验证口径，再进入回测。"
      : spec.researchBoundary ||
        analysis.summary ||
        overview?.stage?.objective ||
        "策略必须先通过 2020-2024 主评估窗口，再通过 2025 至当前样本外复核；熊市窗口只观察韧性和纪律，不作为策略达标判定。";
  return (
    <section className="strategy-description">
      <p>{firstParagraph}</p>
      <p>{secondParagraph}</p>
    </section>
  );
}

function AnchorTabs({ activeSection, onJump }) {
  return (
    <nav className="anchor-tabs">
      {EVIDENCE_TABS.map((tab) => (
        <button key={tab.target} type="button" className={activeSection === tab.target ? "active" : ""} data-target={tab.target} onClick={() => onJump(tab.target)}>
          {tab.label}
        </button>
      ))}
    </nav>
  );
}

function ChartPanel({ id, title, subtitle, children }) {
  return (
    <section id={id} className="chart-panel">
      <header>
        <h2>{title}</h2>
        {subtitle ? <span>{subtitle}</span> : null}
      </header>
      {children}
    </section>
  );
}

function EquityChart({ points }) {
  const latest = points.length ? points[points.length - 1] : null;
  return (
    <div className="equity-chart">
      <div className="equity-readout">
        <span>Equity</span>
        <strong>{formatMoney(latest?.equity)}</strong>
        <em>累计收益 {formatPercent(latest?.returnPct)}</em>
      </div>
      <MiniLineChart points={buildEquitySeries(points)} color="#7fad6f" height={320} emptyText="当前 run 没有净值曲线。" />
    </div>
  );
}

function MiniLineChart({ points, color = "#5f8fff", height = 230, emptyText }) {
  const containerRef = useRef(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    container.replaceChildren();
    if (!points.length) return undefined;

    const chart = createChart(container, {
      width: container.clientWidth,
      height,
      autoSize: true,
      layout: {
        background: { color: "#ffffff" },
        textColor: "#7c858f",
        attributionLogo: false,
        fontFamily: "Aptos, Microsoft YaHei UI, Microsoft YaHei, sans-serif",
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { color: "#e4e8ed" },
      },
      rightPriceScale: { borderColor: "#d7dce2" },
      timeScale: {
        borderColor: "#d7dce2",
        timeVisible: false,
        secondsVisible: false,
        rightOffset: 4,
      },
      crosshair: {
        vertLine: { color: "rgba(95, 143, 255, 0.28)" },
        horzLine: { color: "rgba(95, 143, 255, 0.18)" },
      },
    });
    const line = chart.addSeries(LineSeries, {
      color,
      lineWidth: 2,
      priceFormat: { type: "custom", formatter: (value) => formatChartNumber(value) },
    });
    line.setData(points);
    chart.timeScale().fitContent();

    return () => chart.remove();
  }, [color, height, points]);

  return (
    <div className="mini-chart-wrap" style={{ minHeight: height }}>
      <div ref={containerRef} className="mini-chart" />
      {!points.length ? <div className="empty-state">{emptyText || "暂无图表数据。"}</div> : null}
    </div>
  );
}

function AssetTreemap({ run }) {
  const assets = buildAssetRows(run);
  if (!assets.length) {
    return <div className="empty-state static">当前证据没有持仓或尾部标的明细。</div>;
  }
  return (
    <div className="asset-map">
      {assets.slice(0, 8).map((asset, index) => (
        <article key={`${asset.label}-${index}`} style={{ "--shade": asset.shade }}>
          <strong>{asset.label}</strong>
          <span>{asset.value}</span>
        </article>
      ))}
    </div>
  );
}

function CapacityPanel({ metrics, run }) {
  const latest = (run?.equityCurve || []).at?.(-1) || null;
  return (
    <div className="capacity-panel">
      <MiniLineChart points={buildReturnSeries(run?.equityCurve || [])} color="#6f9cff" height={170} emptyText="当前 run 没有容量序列。" />
      <div className="capacity-readout">
        <span>最终权益 <strong>{formatMoney(latest?.equity)}</strong></span>
        <span>换手/样本 <strong>{formatRawValue(metrics?.turnover ?? run?.resultCounts?.tradeActions)}</strong></span>
      </div>
    </div>
  );
}

function MetricsOverviewTable({ rows, loading }) {
  return (
    <section className="overview-table">
      {rows.map((row) => (
        <div key={row.left.label + row.right.label} className="overview-row">
          <span>{row.left.label}</span>
          <strong>{row.left.value}</strong>
          <span>{row.right.label}</span>
          <strong>{row.right.value}</strong>
        </div>
      ))}
      {!rows.length ? <div className="empty-state static compact">{loading ? "正在读取后端证据..." : "当前 run 没有可展示的指标字段。"}</div> : null}
    </section>
  );
}

function RollingStatsTable({ rows, loading }) {
  return (
    <section className="rolling-section">
      <div className="rolling-header">
        <h2>滚动统计数据</h2>
        <span className="rolling-chip">窗口收益</span>
      </div>
      <table>
        <thead>
          <tr>
            <th />
            <th>1 个月</th>
            <th>3个月</th>
            <th>6个月</th>
            <th>12个月</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.date}>
              <th>{formatDisplayDate(row.date)}</th>
              <td>{formatPercent(row.m1)}</td>
              <td>{formatPercent(row.m3)}</td>
              <td>{formatPercent(row.m6)}</td>
              <td>{formatPercent(row.m12)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length ? <div className="empty-state static">{loading ? "正在读取后端证据..." : "当前 run 没有足够净值点生成滚动统计。"}</div> : null}
    </section>
  );
}

function AuthorPanel({ baseline, overview }) {
  return (
    <section className="aside-panel author-panel">
      <h2>作者</h2>
      <div className="author-line">
        <span>
          <UserRound size={20} />
        </span>
        <div>
          <strong>本地研究台</strong>
          <em>{baseline?.runId || overview?.stage?.stageId || "后端证据源"}</em>
        </div>
      </div>
    </section>
  );
}

function UsResearchPanel({ overview, importPreview }) {
  const assets = overview?.assets || [];
  const portfolio = overview?.portfolioSnapshots?.[0] || {};
  const boundary = overview?.dataBoundary || {};
  const topAssets = assets.slice(0, 4);
  const previewSummary = importPreview?.summary || {};
  return (
    <section className="aside-panel us-panel">
      <h2>美股 sample 数据</h2>
      <div className="us-boundary">
        <span className={overview?.isSample ? "sample" : "live"}>{overview?.isSample ? "sample" : "live"}</span>
        <strong>{overview?.marketSnapshot?.status || "未载入"}</strong>
        <em>{boundary.dbPersistence || "pending_confirmation"}</em>
      </div>
      <div className="us-facts">
        <span>资产 {assets.length}</span>
        <span>sample 持仓 {portfolio.holdingCount || 0}</span>
        <span>券商连接 {boundary.brokerConnected ? "已启用" : "未启用"}</span>
      </div>
      <div className="import-preview">
        <strong>入库预览</strong>
        <span>assets {previewSummary.assets || 0}</span>
        <span>prices {previewSummary.assetDailyPrices || 0}</span>
        <span>watchlist {previewSummary.watchlistItems || 0}</span>
        <em>{importPreview?.writesEnabled ? "writes enabled" : "writes disabled"}</em>
      </div>
      <div className="us-assets">
        {topAssets.map((asset) => (
          <article key={asset.ticker}>
            <strong>{asset.ticker}</strong>
            <span>{asset.role || asset.instrumentType || "-"}</span>
            <em>{formatMoney(asset.latestClose)}</em>
          </article>
        ))}
      </div>
      <p>{overview?.evidenceFiles?.watchlist || "等待后端 sample 文件"}</p>
    </section>
  );
}

function StrategyArchivePanel({ lifecycle }) {
  const counts = lifecycle?.counts || {};
  const hidden = (lifecycle?.strategies || []).filter((item) => !item.showInPrimaryDashboard);
  return (
    <section className="aside-panel archive-panel">
      <h2>策略档案</h2>
      <div className="archive-counts">
        <span><strong>{counts.active || 0}</strong> active</span>
        <span><strong>{counts.frozen || 0}</strong> frozen</span>
        <span><strong>{counts.archived_negative_evidence || 0}</strong> archived</span>
        <span><strong>{counts.legacy_reset || 0}</strong> reset</span>
      </div>
      <div className="archive-list">
        {hidden.slice(0, 5).map((item) => (
          <article key={item.strategyId}>
            <strong>{item.label}</strong>
            <span>{item.lifecycleStatus}</span>
          </article>
        ))}
      </div>
      <p>{lifecycle?.policy || "docs/research/strategy-archive-policy-2026-06-27.md"}</p>
    </section>
  );
}

function PhasePanel({ id, rows }) {
  return (
    <section id={id} className="aside-panel">
      <h2>三段验证闸门</h2>
      <div className="phase-list">
        {rows.map((row, index) => (
          <article key={row.id} className={row.tone}>
            <span>{index + 1}</span>
            <div>
              <strong>{row.label}</strong>
              <em>{row.title}</em>
              <p>{row.evidence}</p>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function GatePanel({ rows }) {
  return (
    <section className="aside-panel">
      <h2>硬门槛</h2>
      <div className="gate-list">
        {rows.rows.slice(0, 8).map((row) => (
          <article key={row.key} className={row.tone}>
            {row.tone === "pass" ? <CheckCircle2 size={15} /> : row.tone === "fail" ? <XCircle size={15} /> : <CircleDashed size={15} />}
            <span>{row.label}</span>
            <strong>{row.value}</strong>
          </article>
        ))}
      </div>
    </section>
  );
}

function RunSelector({ runs, selectedRunId, onSelect, loading }) {
  return (
    <section className="aside-panel">
      <h2>研究证据</h2>
      <div className="run-list">
        {runs.slice(0, 8).map((run) => (
          <button key={run.runId} type="button" className={run.runId === selectedRunId ? "active" : ""} onClick={() => onSelect(run.runId)} disabled={loading}>
            <strong>{run.runId}</strong>
            <span>{run.status || run.label || "review"}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function EvidencePanel({ id, run, overview }) {
  const files = Object.entries(run?.resultFiles || {});
  return (
    <section id={id} className="aside-panel">
      <h2>证据文件</h2>
      <div className="evidence-list">
        <article>
          <span>阶段目录</span>
          <strong>{overview?.stage?.stageDir || "docs/research/stages"}</strong>
        </article>
        <article>
          <span>研究索引</span>
          <strong>{overview?.registryPath || "docs/research/research-runs.json"}</strong>
        </article>
        {files.map(([name, path]) => (
          <article key={name}>
            <span>{name}</span>
            <strong>{path}</strong>
          </article>
        ))}
      </div>
    </section>
  );
}

function buildValidationPhaseRows(phases, spec, analysis) {
  const window = spec.window || {};
  const start = window.startDate;
  const end = window.endDate;
  return phases.map((phase) => {
    if (!phase.startDate) {
      return { ...phase, tone: "observe", evidence: "需在阶段通过后选择熊市样本，只做压力观察。" };
    }
    const covers = coversWindow(start, end, phase.startDate, phase.endDate);
    if (!covers) {
      return {
        ...phase,
        tone: "missing",
        evidence: `现有证据窗口 ${start || "未知"} 至 ${end || "未知"}，不能替代本轮。`,
      };
    }
    const passed = Boolean(analysis.targetMet || analysis.strictTargetMet);
    return {
      ...phase,
      tone: passed ? "pass" : "fail",
      evidence: passed ? "当前 run 覆盖本窗口且目标通过。" : "当前 run 覆盖本窗口但目标未通过。",
    };
  });
}

function buildBackendPhaseRows(windows, spec) {
  const specWindow = spec.window || {};
  return windows.map((window) => ({
    ...window,
    tone: backendWindowTone(window.status),
    evidence: backendWindowEvidence(window, specWindow),
  }));
}

function backendWindowTone(status) {
  if (status === "pass") return "pass";
  if (status === "fail") return "fail";
  if (status === "observation_pending") return "observe";
  return "missing";
}

function backendWindowEvidence(window, specWindow) {
  if (window.status === "pass") return "后端判定：本窗口覆盖且指标达标。";
  if (window.status === "fail") return "后端判定：本窗口覆盖但指标未达标。";
  if (window.status === "observation_pending") return "后端判定：该窗口只观察，不参与策略达标。";
  return `后端判定：当前证据窗口 ${specWindow.startDate || "未知"} 至 ${specWindow.endDate || "未知"}，不能替代本轮。`;
}

function buildGateRows(run) {
  const gates = { ...(run?.objectiveGates || {}), ...(run?.diagnosticGates || {}) };
  const rows = Object.entries(gates).map(([key, value]) => ({
    key,
    label: readableGateName(key),
    value: value === true ? "通过" : value === false ? "未通过" : "未判定",
    tone: value === true ? "pass" : value === false ? "fail" : "pending",
  }));
  const passCount = rows.filter((row) => row.tone === "pass").length;
  const failCount = rows.filter((row) => row.tone === "fail").length;
  return {
    rows: rows.length ? rows : [{ key: "empty", label: "等待后端闸门", value: "未判定", tone: "pending" }],
    summary: `${passCount} 通过 / ${failCount} 未过`,
  };
}

function buildKpiStrip(metrics, spec, run) {
  const targetAnnualized = metrics.targetAnnualizedReturn ?? spec.qualificationObjective?.targetAnnualizedReturn;
  return [
    { label: "累计收益", value: formatPercent(metrics.totalReturn), tone: metricClass(metrics.totalReturn, 0) },
    { label: "Sharpe", value: formatNumber(metrics.sharpeRatio, 2), tone: metricClass(metrics.sharpeRatio, 1) },
    { label: "盈亏比", value: formatNumber(metrics.profitLossRatio, 2), tone: metricClass(metrics.profitLossRatio, 2) },
    { label: "最大回撤", value: formatPercent(metrics.maxDrawdown), tone: metricClass(metrics.maxDrawdown, -0.1, true) },
    { label: "年化收益", value: formatPercent(metrics.annualizedReturn), tone: metricClass(metrics.annualizedReturn, targetAnnualized || 0) },
    { label: "闭环交易", value: formatCount(metrics.completedTradeCount ?? run?.resultCounts?.completedTrades), tone: "neutral" },
  ];
}

function buildMetricsOverviewRows(run, spec, metrics) {
  const counts = run?.resultCounts || {};
  const latest = (run?.equityCurve || []).at?.(-1) || {};
  const pairs = [
    ["交易动作", metrics.tradeCount ?? counts.tradeActions],
    ["闭环交易", metrics.completedTradeCount ?? counts.completedTrades],
    ["启动资金", spec.capital?.initialCash],
    ["最终权益", latest.equity],
    ["累计收益", metrics.totalReturn],
    ["年化收益", metrics.annualizedReturn],
    ["年化波动", metrics.annualizedVolatility],
    ["最大回撤", metrics.maxDrawdown],
    ["最大回撤持续", metrics.maxDrawdownDurationDays],
    ["Sharpe", metrics.sharpeRatio],
    ["Sortino", metrics.sortinoRatio],
    ["Calmar", metrics.calmarRatio],
    ["胜率", metrics.winRate],
    ["盈亏比", metrics.profitLossRatio],
    ["Profit Factor", metrics.profitFactor],
    ["最大持仓数", metrics.maxConcurrentPositions],
    ["单票峰值仓位", metrics.maxSinglePositionPct],
    ["行业峰值仓位", metrics.maxIndustryPositionPct],
    ["Risk-On 天数", metrics.marketRiskOnDays],
    ["Risk-Off 天数", metrics.marketRiskOffDays],
    ["尾部最差收益", metrics.tailWorstReturn],
    ["尾部最差回撤", metrics.tailWorstDrawdown],
    ["尾部组合冲击", metrics.tailWorstPortfolioImpactPct],
    ["尾部底部冲击", metrics.tailBottomPortfolioImpactPct],
    ["尾部样本检查", metrics.tailRatioCheckedCount],
  ]
    .filter(([, value]) => hasMetricValue(value))
    .map(([label, value]) => ({ label, value: formatMetricCell(label, value) }));

  const rows = [];
  for (let index = 0; index < pairs.length; index += 2) {
    rows.push({
      left: pairs[index],
      right: pairs[index + 1] || { label: "", value: "" },
    });
  }
  return rows;
}

function buildRollingStats(points) {
  if (!points.length) return [];
  const monthly = [];
  const byMonth = new Map();
  points.forEach((point) => {
    if (!point.date) return;
    byMonth.set(String(point.date).slice(0, 7), point);
  });
  Array.from(byMonth.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .forEach(([, point]) => monthly.push(point));

  const rows = [];
  for (let index = 0; index < monthly.length; index += 1) {
    const current = monthly[index];
    rows.push({
      date: current.date,
      m1: rollingReturn(monthly, index, 1),
      m3: rollingReturn(monthly, index, 3),
      m6: rollingReturn(monthly, index, 6),
      m12: rollingReturn(monthly, index, 12),
    });
  }
  return rows.slice(-12);
}

function rollingReturn(monthly, index, months) {
  const current = monthly[index]?.equity;
  const previous = monthly[index - months]?.equity;
  if (!Number.isFinite(Number(current)) || !Number.isFinite(Number(previous)) || Number(previous) === 0) return null;
  return Number(current) / Number(previous) - 1;
}

function buildEquitySeries(points) {
  return points
    .filter((point) => point.date && Number.isFinite(Number(point.equity)))
    .map((point) => ({ time: point.date, value: Number(point.equity) }));
}

function buildReturnSeries(points) {
  return points
    .filter((point) => point.date && Number.isFinite(Number(point.returnPct)))
    .map((point) => ({ time: point.date, value: Number(point.returnPct) * 100 }));
}

function buildDrawdownSeries(points) {
  return points
    .filter((point) => point.date && Number.isFinite(Number(point.drawdown)))
    .map((point) => ({ time: point.date, value: Number(point.drawdown) * 100 }));
}

function buildAssetRows(run) {
  const rows = run?.finalPositions?.length ? run.finalPositions : run?.top10 || run?.capitalBottom10 || run?.bottom10 || [];
  const normalized = rows.map((row, index) => {
    const rawValue = row.marketValue ?? row.positionValue ?? row.capital ?? row.totalReturn ?? row.capitalReturnPct ?? row.returnPct ?? 1;
    const numeric = Math.abs(Number(rawValue));
    return {
      label: row.symbol || row.ts_code || row.name || `资产 ${index + 1}`,
      value: formatMetricCell("资产", rawValue),
      numeric: Number.isFinite(numeric) ? numeric : 1,
    };
  });
  const max = Math.max(...normalized.map((row) => row.numeric), 1);
  return normalized.map((row) => ({ ...row, shade: 0.24 + (row.numeric / max) * 0.42 }));
}

function metricClass(value, target, lowerIsBetter = false) {
  if (value === null || value === undefined || target === null || target === undefined) return "neutral";
  const numeric = Number(value);
  const targetNumeric = Number(target);
  if (!Number.isFinite(numeric) || !Number.isFinite(targetNumeric)) return "neutral";
  return lowerIsBetter ? (numeric >= targetNumeric ? "good" : "bad") : numeric >= targetNumeric ? "good" : "bad";
}

function coversWindow(start, end, requiredStart, requiredEnd) {
  if (!start || !end) return false;
  return start <= requiredStart && end >= requiredEnd;
}

function formatRunWindow(spec) {
  const window = spec?.window || {};
  if (!window.startDate && !window.endDate) return "无窗口";
  return `${window.startDate || "?"} → ${window.endDate || "?"}`;
}

function readableGateName(key) {
  const known = {
    portfolioSymbolTailLossMet: "尾部单票亏损",
    annualizedReturnMet: "年化收益",
    totalReturnMet: "累计收益",
    profitLossRatioMet: "盈亏比",
    maxDrawdownMet: "最大回撤",
    minimumTradesMet: "交易数量",
    singleConcentrationMet: "单票集中度",
    industryConcentrationMet: "行业集中度",
    sourceSingleSymbolTailRiskMet: "源 run 单票尾部",
    portfolioSymbolTailRiskMet: "组合尾部风险",
    portfolioSymbolTailRatioEvidenceMet: "尾部盈亏比证据",
  };
  return known[key] || key.replace(/([A-Z])/g, " $1").replace(/_/g, " ");
}

function formatMetricCell(label, value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  if (label.includes("资金") || label.includes("权益") || label.includes("费用") || label.includes("能力")) return formatMoney(value);
  if (label.includes("持续")) return `${formatCount(value)} 天`;
  const percentLabels = ["累计收益", "年化收益", "年化波动", "最大回撤", "胜率", "仓位", "尾部"];
  if (percentLabels.some((name) => label.includes(name))) {
    return formatPercent(value, 3);
  }
  if (typeof value === "number" && Math.abs(value) > 1000) return formatCount(value);
  return formatRawValue(value);
}

function hasMetricValue(value) {
  if (value === null || value === undefined) return false;
  if (typeof value === "number") return Number.isFinite(value);
  return String(value).trim() !== "";
}

function formatPercent(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(digits);
}

function formatCount(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return new Intl.NumberFormat("zh-CN").format(Number(value));
}

function formatMoney(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(Number(value));
}

function formatRawValue(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") return Number.isInteger(value) ? formatCount(value) : Number(value).toFixed(3);
  return String(value);
}

function formatChartNumber(value) {
  if (Math.abs(value) >= 1000) return formatCount(value);
  return Number(value).toFixed(1);
}

function formatDisplayDate(value) {
  if (!value) return "-";
  const raw = String(value).slice(0, 10);
  const [year, month, day] = raw.split("-");
  if (!year || !month || !day) return raw;
  return `${year}年${Number(month)}月${Number(day)}日`;
}

const rootElement = document.getElementById("root");
const root = rootElement.__quantDeskRoot || createRoot(rootElement);
rootElement.__quantDeskRoot = root;
root.render(<App />);
