import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  CandlestickSeries,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
} from "lightweight-charts";
import {
  Activity,
  BarChart3,
  Bot,
  Building2,
  CheckCircle2,
  Database,
  Download,
  Filter,
  Gauge,
  LineChart,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  TrendingUp,
  UploadCloud,
} from "lucide-react";
import "./styles.css";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "http://localhost:18000").replace(/\/$/, "");
const FORM_SCHEMA_VERSION = "2026-05-29-research-desk";

const DEFAULT_FORM = {
  formSchemaVersion: FORM_SCHEMA_VERSION,
  tsCode: "600703.SH",
  stockName: "",
  startDate: dateYearsAgo(3),
  endDate: dateToday(),
  marketState: "normal",
  entryMode: "boll-rebound",
  initialCash: "16114.88",
  weeklyTradeLimit: "2",
  positionCapPct: "20",
  riskPct: "1",
  stopLossPct: "5",
  takeProfit1Pct: "3",
  takeProfit2Pct: "5",
  commissionPct: "0.025",
  stampDutyPct: "0.05",
  lotSize: "100",
  bollPeriod: "20",
  bollDev: "2",
  bollTolerancePct: "1.5",
  bollBandwidthMaxPct: "8",
  midlineTolerancePct: "2.5",
  trendFastPeriod: "5",
  trendSlowPeriod: "10",
  trendLongPeriod: "20",
  volumeMaPeriod: "20",
  volumeBreakoutMultiplier: "1.08",
  useTrendFilter: true,
  useMacdFilter: false,
  macdFastPeriod: "12",
  macdSlowPeriod: "26",
  macdSignalPeriod: "9",
  macdRequireZeroAxis: false,
  useRsiFilter: false,
  rsiPeriod: "14",
  rsiLowerBound: "35",
  rsiUpperBound: "78",
  kdjPeriod: "9",
  atrPeriod: "14",
  useAtrStop: false,
  atrStopMultiplier: "1.8",
  blockWeakMarket: true,
  forceStopOverridesLimit: true,
  blockSameDayReentry: true,
};

const DEFAULT_SCREEN_FILTERS = {
  q: "",
  industry: "",
  market: "",
  technical: "all",
  limit: "60",
};

const TABS = [
  ["screen", "选股池", Filter],
  ["lab", "策略实验", SlidersHorizontal],
  ["review", "AI复盘", Bot],
];

const TECHNICAL_OPTIONS = [
  ["all", "全部形态"],
  ["ma-bullish", "均线多头"],
  ["macd-bullish", "MACD多头"],
  ["macd-cross", "MACD金叉"],
  ["boll-lower", "BOLL下轨"],
  ["boll-breakout", "BOLL突破"],
  ["boll-squeeze", "BOLL收口"],
  ["rsi-neutral", "RSI健康"],
  ["volume-breakout", "放量"],
  ["ma-cross", "均线金叉"],
];

const ENTRY_PRESETS = [
  {
    value: "boll-rebound",
    label: "BOLL下轨反弹",
    bias: "回撤试错",
    config: { entryMode: "boll-rebound", useTrendFilter: true, useMacdFilter: false, useRsiFilter: false },
  },
  {
    value: "macd-cross",
    label: "MACD金叉",
    bias: "趋势动量",
    config: { entryMode: "macd-cross", useTrendFilter: true, useMacdFilter: false, useRsiFilter: true, rsiLowerBound: "35", rsiUpperBound: "82" },
  },
  {
    value: "boll-squeeze",
    label: "BOLL收口突破",
    bias: "波动扩张",
    config: { entryMode: "boll-squeeze", useTrendFilter: true, useMacdFilter: true, useRsiFilter: false, bollBandwidthMaxPct: "8" },
  },
  {
    value: "boll-breakout",
    label: "BOLL上轨突破",
    bias: "强势突破",
    config: { entryMode: "boll-breakout", useTrendFilter: true, useMacdFilter: true, useRsiFilter: false, volumeBreakoutMultiplier: "1.15" },
  },
  {
    value: "ma-cross",
    label: "均线金叉",
    bias: "趋势切换",
    config: { entryMode: "ma-cross", useTrendFilter: false, useMacdFilter: false, useRsiFilter: true, rsiLowerBound: "40", rsiUpperBound: "80" },
  },
  {
    value: "rsi-reversal",
    label: "RSI超卖反转",
    bias: "反转确认",
    config: { entryMode: "rsi-reversal", useTrendFilter: false, useMacdFilter: false, useRsiFilter: false, rsiLowerBound: "32", rsiUpperBound: "70" },
  },
  {
    value: "trend-follow",
    label: "MA趋势跟随",
    bias: "顺势持有",
    config: { entryMode: "trend-follow", useTrendFilter: true, useMacdFilter: true, useRsiFilter: false },
  },
];

function App() {
  const [form, setForm] = usePersistentForm();
  const [view, setView] = useState(getInitialView);
  const [status, setStatus] = useState({ text: "等待连接 API", tone: "muted" });
  const [screenFilters, setScreenFilters] = useState(() => ({ ...DEFAULT_SCREEN_FILTERS, q: form.stockName || form.tsCode }));
  const [screenResults, setScreenResults] = useState([]);
  const [selectedStock, setSelectedStock] = useState(null);
  const [newsItems, setNewsItems] = useState([]);
  const [result, setResult] = useState(null);
  const [bars, setBars] = useState([]);
  const [busy, setBusy] = useState(false);
  const [sourceLabel, setSourceLabel] = useState("数据库未载入");
  const initialFormRef = useRef(form);

  const rows = result?.rows?.length ? result.rows : bars;
  const latestBar = rows.length ? rows[rows.length - 1] : null;
  const profile = selectedStock || buildFallbackProfile(form);
  const symbolTitle = profile?.name ? `${profile.name} · ${profile.ts_code}` : form.tsCode;
  const metrics = useMemo(() => buildMetrics(result, rows), [result, rows]);

  useEffect(() => {
    let ignore = false;
    async function loadInitialBars() {
      try {
        const req = getDataRequest(initialFormRef.current);
        const params = new URLSearchParams(req);
        const data = await apiFetch(`/api/daily-bars?${params.toString()}`);
        if (ignore || !data.length) return;
        setBars(data);
        setSourceLabel(`DB:${data[0].ts_code}`);
        setStatus({ text: `${data[0].ts_code} 本地日线已载入：${data.length} 条`, tone: "good" });
      } catch {
        if (!ignore) {
          setStatus({ text: "等待手动载入行情", tone: "muted" });
        }
      }
    }
    loadInitialBars();
    return () => {
      ignore = true;
    };
  }, []);

  async function checkApi() {
    await withBusy(async () => {
      const data = await apiFetch("/api/health");
      setStatus({ text: `API ${data.status}，本地链路可用`, tone: "good" });
    });
  }

  async function syncStockBasic() {
    await withBusy(async () => {
      setStatus({ text: "正在同步 A 股基础列表...", tone: "muted" });
      const data = await apiFetch("/api/tushare/sync-stock-basic", { method: "POST", body: JSON.stringify({}) });
      setStatus({ text: `股票列表同步完成：${data.rows_upserted} 条`, tone: "good" });
      await runScreener(false);
    });
  }

  async function runScreener(showStatus = true) {
    await withBusy(async () => {
      const params = new URLSearchParams();
      Object.entries(screenFilters).forEach(([key, value]) => {
        if (String(value).trim()) params.set(key, String(value).trim());
      });
      params.set("start_date", form.startDate);
      params.set("end_date", form.endDate);
      const data = await apiFetch(`/api/stocks/screen?${params.toString()}`);
      setScreenResults(data);
      if (data.length) {
        const activeCode = form.tsCode.trim().toUpperCase();
        setSelectedStock(data.find((stock) => stock.ts_code === activeCode) || data[0]);
      }
      if (showStatus) {
        setStatus({ text: `筛选完成：${data.length} 个候选`, tone: data.length ? "good" : "bad" });
      }
    });
  }

  async function syncDaily() {
    await withBusy(async () => {
      const req = getDataRequest(form);
      setStatus({ text: `正在同步 ${req.ts_code} 日线...`, tone: "muted" });
      const data = await apiFetch("/api/tushare/sync-daily", { method: "POST", body: JSON.stringify(req) });
      updateForm("tsCode", data.ts_code);
      setStatus({ text: `${data.ts_code} 日线同步完成：${data.rows_upserted} 条`, tone: "good" });
      await loadBars(false, data.ts_code);
    });
  }

  async function syncFundamentals() {
    await withBusy(async () => {
      const req = getDataRequest(form);
      setStatus({ text: `正在同步 ${req.ts_code} 基本面/估值...`, tone: "muted" });
      const data = await apiFetch("/api/tushare/sync-fundamentals", { method: "POST", body: JSON.stringify(req) });
      updateForm("tsCode", data.ts_code);
      setStatus({
        text: `${data.ts_code} 基本面同步完成：估值 ${data.daily_basic_rows} 条，财务 ${data.fina_indicator_rows} 条`,
        tone: "good",
      });
      await refreshSelectedFundamentals(data.ts_code);
      await runScreener(false);
    });
  }

  async function loadBars(showLoadedStatus = true, overrideCode = null) {
    const req = getDataRequest({ ...form, tsCode: overrideCode || form.tsCode });
    const params = new URLSearchParams(req);
    const data = await apiFetch(`/api/daily-bars?${params.toString()}`);
    setBars(data);
    setResult(null);
    setSourceLabel(data.length ? `DB:${data[0].ts_code}` : `DB:${req.ts_code}`);
    if (data.length) {
      updateForm("tsCode", data[0].ts_code);
    }
    if (showLoadedStatus) {
      setStatus({ text: `${req.ts_code} 已载入：${data.length} 根日线`, tone: "good" });
    }
    return data;
  }

  async function refreshSelectedFundamentals(tsCode = form.tsCode) {
    const params = new URLSearchParams({ start_date: form.startDate, end_date: form.endDate });
    const data = await apiFetch(`/api/stocks/${encodeURIComponent(tsCode)}/fundamentals?${params.toString()}`);
    setSelectedStock((current) => {
      if (!current || current.ts_code !== data.ts_code) return current;
      return {
        ...current,
        fundamental_score: data.score,
        fundamental_tags: data.tags,
        fundamentals: {
          ...(current.fundamentals || {}),
          估值: data.valuation,
          财务: data.financial,
        },
      };
    });
    return data;
  }

  async function refreshNews(keyword = screenFilters.q || form.stockName || form.tsCode) {
    await withBusy(async () => {
      const params = new URLSearchParams({ sources: "cls,wallstreetcn,xueqiu", count: "6" });
      const normalizedKeyword = keyword.trim();
      if (normalizedKeyword && !/^[0-9A-Z.]+$/i.test(normalizedKeyword)) params.set("q", normalizedKeyword);
      const data = await apiFetch(`/api/news/trends?${params.toString()}`);
      setNewsItems(data.items || []);
      setStatus({ text: data.items?.length ? `消息面已刷新：${data.items.length} 条` : "消息面暂无匹配热点", tone: data.items?.length ? "good" : "muted" });
    });
  }

  async function runBacktest() {
    await withBusy(async () => {
      const req = getDataRequest(form);
      setStatus({ text: `正在回测 ${req.ts_code}...`, tone: "muted" });
      const data = await apiFetch("/api/backtests/run", {
        method: "POST",
        body: JSON.stringify({ ...req, config: buildBacktestConfig(form) }),
      });
      setResult(data);
      setBars(data.rows || []);
      setSourceLabel(`BT:${req.ts_code}`);
      setStatus({ text: `${req.ts_code} 回测完成：${data.trades.length} 笔流水`, tone: "good" });
      setView("review");
    });
  }

  async function withBusy(task) {
    try {
      setBusy(true);
      await task();
    } catch (error) {
      setStatus({ text: error.message, tone: "bad" });
    } finally {
      setBusy(false);
    }
  }

  function updateForm(name, value) {
    setForm((current) => ({ ...current, [name]: value }));
  }

  function updateScreenFilter(name, value) {
    setScreenFilters((current) => ({ ...current, [name]: value }));
  }

  function applyPreset(preset) {
    setForm((current) => ({ ...current, ...preset.config }));
    setStatus({ text: `策略预设已切换：${preset.label}`, tone: "good" });
  }

  async function selectCandidate(stock, load = false) {
    setSelectedStock(stock);
    setForm((current) => ({ ...current, tsCode: stock.ts_code, stockName: stock.name }));
    setSourceLabel(`标的:${stock.ts_code}`);
    setStatus({ text: `已选择 ${stock.name}（${stock.ts_code}）`, tone: "good" });
    if (load) {
      setView("lab");
      await withBusy(() => loadBars(true, stock.ts_code));
    }
  }

  return (
    <main className="research-shell">
      <header className="desk-header">
        <div className="brand-lockup">
          <span className="brand-mark">
            <LineChart size={20} />
          </span>
          <div>
            <p className="eyebrow">Local Quant Research</p>
            <h1>选股与策略研究台</h1>
          </div>
        </div>
        <nav className="desk-tabs" aria-label="工作区">
          {TABS.map(([key, label, Icon]) => (
            <button key={key} type="button" className={view === key ? "active" : ""} onClick={() => setView(key)}>
              <Icon size={16} />
              {label}
            </button>
          ))}
        </nav>
      </header>

      <section className="command-strip">
        <div className="service-line">
          <i className={`pulse ${status.tone}`} />
          <span>{status.text}</span>
        </div>
        <TextField label="当前标的" value={form.tsCode} onChange={(value) => updateForm("tsCode", value.toUpperCase())} />
        <TextField label="开始日期" type="date" value={form.startDate} onChange={(value) => updateForm("startDate", value)} />
        <TextField label="结束日期" type="date" value={form.endDate} onChange={(value) => updateForm("endDate", value)} />
        <div className="command-actions">
          <ActionButton icon={<Activity size={16} />} label="检测API" onClick={checkApi} disabled={busy} />
          <ActionButton icon={<RefreshCw size={16} />} label="同步列表" onClick={syncStockBasic} disabled={busy} />
          <ActionButton icon={<UploadCloud size={16} />} label="同步日线" onClick={syncDaily} disabled={busy} />
          <ActionButton icon={<Database size={16} />} label="同步基本面" onClick={syncFundamentals} disabled={busy} />
          <ActionButton icon={<Database size={16} />} label="载入行情" onClick={() => withBusy(loadBars)} disabled={busy} />
          <button className="primary-button" type="button" onClick={runBacktest} disabled={busy}>
            <Play size={17} /> 回测并评价
          </button>
        </div>
      </section>

      <section className="research-grid">
        <aside className="context-rail">
          <FundamentalsPanel profile={profile} latestBar={latestBar} dataBars={rows.length} sourceLabel={sourceLabel} onSync={syncFundamentals} busy={busy} />
          <StrategyBrief form={form} result={result} />
          <section className="rail-panel">
            <PanelTitle icon={<Gauge size={17} />} title="指标快照" right={latestBar?.date || "无数据"} />
            <IndicatorTape bar={latestBar} />
          </section>
        </aside>

        <section className="work-surface">
          {view === "screen" ? (
            <ScreenerPanel
              filters={screenFilters}
              results={screenResults}
              busy={busy}
              onFilterChange={updateScreenFilter}
              onRun={() => runScreener(true)}
              onSelect={selectCandidate}
              newsItems={newsItems}
              onRefreshNews={refreshNews}
            />
          ) : null}

          {view === "lab" ? (
            <StrategyPanel
              form={form}
              metrics={metrics}
              rows={rows}
              trades={result?.trades || []}
              result={result}
              onChange={updateForm}
              onPreset={applyPreset}
              onRun={runBacktest}
              busy={busy}
            />
          ) : null}

          {view === "review" ? <ReviewPanel result={result} rows={rows} symbolTitle={symbolTitle} /> : null}
        </section>
      </section>
    </main>
  );
}

function usePersistentForm() {
  const [form, setForm] = useState(() => {
    try {
      const raw = localStorage.getItem("qt-react-form");
      if (!raw) return DEFAULT_FORM;
      const saved = JSON.parse(raw);
      if (saved.formSchemaVersion !== FORM_SCHEMA_VERSION) {
        return { ...DEFAULT_FORM, ...saved, startDate: DEFAULT_FORM.startDate, endDate: DEFAULT_FORM.endDate, formSchemaVersion: FORM_SCHEMA_VERSION };
      }
      return { ...DEFAULT_FORM, ...saved };
    } catch {
      return DEFAULT_FORM;
    }
  });

  useEffect(() => {
    localStorage.setItem("qt-react-form", JSON.stringify(form));
  }, [form]);

  return [form, setForm];
}

function getInitialView() {
  const value = new URLSearchParams(window.location.search).get("view");
  return TABS.some(([key]) => key === value) ? value : "screen";
}

function ScreenerPanel({ filters, results, busy, onFilterChange, onRun, onSelect, newsItems, onRefreshNews }) {
  const syncedCount = results.filter(hasFundamentalData).length;
  const strongCount = results.filter((stock) => candidateScore(stock) >= 60).length;
  return (
    <section className="screen-layout">
      <section className="workspace-panel screen-panel">
        <PanelTitle icon={<Search size={17} />} title="选股池" right={`${results.length} 个候选`} />
        <div className="filter-board">
          <TextField label="关键词" value={filters.q} onChange={(value) => onFilterChange("q", value)} placeholder="名称、代码、行业" />
          <TextField label="行业" value={filters.industry} onChange={(value) => onFilterChange("industry", value)} placeholder="半导体、医药..." />
          <SelectField
            label="市场"
            value={filters.market}
            onChange={(value) => onFilterChange("market", value)}
            options={[
              ["", "全部"],
              ["主板", "主板"],
              ["创业板", "创业板"],
              ["科创板", "科创板"],
              ["北交所", "北交所"],
            ]}
          />
          <SelectField label="技术形态" value={filters.technical} onChange={(value) => onFilterChange("technical", value)} options={TECHNICAL_OPTIONS} />
          <TextField label="数量上限" type="number" value={filters.limit} onChange={(value) => onFilterChange("limit", value)} />
          <button className="primary-button" type="button" onClick={onRun} disabled={busy}>
            <Filter size={17} /> 筛选股票
          </button>
        </div>
        <div className="screen-summary">
          <SummaryCell label="候选" value={results.length} />
          <SummaryCell label="已同步基本面" value={syncedCount} />
          <SummaryCell label="综合较强" value={strongCount} />
        </div>
        <CandidateCards results={results} onSelect={onSelect} />
      </section>
      <NewsPulsePanel items={newsItems} busy={busy} onRefresh={onRefreshNews} />
    </section>
  );
}

function CandidateCards({ results, onSelect }) {
  if (!results.length) {
    return <div className="empty-state tall">暂无候选</div>;
  }

  return (
    <div className="candidate-grid">
      {results.map((stock) => {
        const valuation = valuationOf(stock);
        const financial = financialOf(stock);
        const score = candidateScore(stock);
        return (
          <article key={stock.ts_code} className="candidate-card">
            <div className="candidate-head">
              <div>
                <strong>{stock.name}</strong>
                <span>
                  {stock.ts_code} · {stock.industry || stock.market || "--"}
                </span>
              </div>
              <span className={`score-pill ${score >= 70 ? "hot" : score >= 35 ? "warm" : ""}`}>{score}</span>
            </div>
            <div className="score-bars">
              <ScoreBar label="技术" value={stock.technical_score || 0} />
              <ScoreBar label="基本面" value={stock.fundamental_score || 0} />
            </div>
            <div className="factor-mini-grid">
              <FactorMini label="PE(TTM)" value={formatNumber(valuation.PE_TTM)} />
              <FactorMini label="PB" value={formatNumber(valuation.PB)} />
              <FactorMini label="市值" value={formatMarketCap(valuation.总市值_万元)} />
              <FactorMini label="换手" value={formatPercentPoint(valuation.换手率)} />
              <FactorMini label="ROE" value={formatPercentPoint(financial.ROE)} />
              <FactorMini label="毛利率" value={formatPercentPoint(financial.毛利率)} />
              <FactorMini label="负债率" value={formatPercentPoint(financial.资产负债率)} />
              <FactorMini label="净利同比" value={formatPercentPoint(financial.净利润同比)} />
            </div>
            <div className="tag-row">
              {[...(stock.technical_tags || []), ...(stock.fundamental_tags || [])].slice(0, 8).map((tag) => (
                <span key={tag}>{tag}</span>
              ))}
            </div>
            <div className="candidate-foot">
              <span>
                {stock.latest_date || "无日期"} · {formatMoney(stock.close)} · {formatPercentPoint(stock.pct_chg)}
              </span>
              <button className="row-button" type="button" onClick={() => onSelect(stock, true)}>
                载入策略
              </button>
            </div>
          </article>
        );
      })}
    </div>
  );
}

function NewsPulsePanel({ items, busy, onRefresh }) {
  return (
    <section className="workspace-panel news-panel">
      <PanelTitle icon={<Activity size={17} />} title="消息面" right={items.length ? `${items.length} 条` : "未刷新"} />
      <button className="ghost-button wide-action" type="button" onClick={() => onRefresh()} disabled={busy}>
        <RefreshCw size={16} /> 刷新财联社/见闻/雪球
      </button>
      {items.length ? (
        <div className="news-list">
          {items.map((item) => (
            <a key={`${item.source}-${item.rank}-${item.title}`} href={item.url || "#"} target="_blank" rel="noreferrer">
              <em>
                {item.source_name} #{item.rank || "-"}
              </em>
              <strong>{item.title}</strong>
            </a>
          ))}
        </div>
      ) : (
        <div className="news-empty">暂无真实消息面数据</div>
      )}
    </section>
  );
}

function SummaryCell({ label, value }) {
  return (
    <span>
      <em>{label}</em>
      <strong>{value}</strong>
    </span>
  );
}

function ScoreBar({ label, value }) {
  const width = Math.max(0, Math.min(100, Number(value) || 0));
  return (
    <span>
      <em>{label}</em>
      <i style={{ "--score": `${width}%` }} />
      <strong>{width}</strong>
    </span>
  );
}

function FactorMini({ label, value }) {
  return (
    <span>
      <em>{label}</em>
      <strong>{value}</strong>
    </span>
  );
}

function StrategyPanel({ form, metrics, rows, trades, result, onChange, onPreset, onRun, busy }) {
  return (
    <section className="strategy-layout">
      <section className="workspace-panel">
        <PanelTitle icon={<TrendingUp size={17} />} title="策略预设" right={ENTRY_PRESETS.find((item) => item.value === form.entryMode)?.bias || "自定义"} />
        <div className="preset-grid">
          {ENTRY_PRESETS.map((preset) => (
            <button key={preset.value} type="button" className={form.entryMode === preset.value ? "active" : ""} onClick={() => onPreset(preset)}>
              <strong>{preset.label}</strong>
              <span>{preset.bias}</span>
            </button>
          ))}
        </div>
      </section>

      <section className="metric-grid">
        {metrics.map((item) => (
          <MetricTile key={item.label} {...item} />
        ))}
      </section>

      <section className="workspace-panel chart-panel">
        <div className="chart-toolbar">
          <PanelTitle icon={<BarChart3 size={17} />} title="K线与指标层" right={rows.length ? `${rows.length} 根日线` : "等待数据"} />
        </div>
        <InteractiveMarketChart rows={rows} trades={trades} />
      </section>

      <section className="param-grid">
        <section className="workspace-panel">
          <PanelTitle icon={<ShieldCheck size={17} />} title="资金纪律" />
          <div className="field-grid four compact">
            <TextField label="初始资金" type="number" value={form.initialCash} onChange={(value) => onChange("initialCash", value)} />
            <TextField label="每周最多交易" type="number" value={form.weeklyTradeLimit} onChange={(value) => onChange("weeklyTradeLimit", value)} />
            <TextField label="单票仓位%" type="number" value={form.positionCapPct} onChange={(value) => onChange("positionCapPct", value)} />
            <TextField label="单笔风险%" type="number" value={form.riskPct} onChange={(value) => onChange("riskPct", value)} />
            <TextField label="止损%" type="number" value={form.stopLossPct} onChange={(value) => onChange("stopLossPct", value)} />
            <TextField label="一止%" type="number" value={form.takeProfit1Pct} onChange={(value) => onChange("takeProfit1Pct", value)} />
            <TextField label="二止%" type="number" value={form.takeProfit2Pct} onChange={(value) => onChange("takeProfit2Pct", value)} />
            <TextField label="A股手数" type="number" value={form.lotSize} onChange={(value) => onChange("lotSize", value)} />
          </div>
          <div className="toggle-grid">
            <ToggleField label="退潮期禁止开新仓" checked={form.blockWeakMarket} onChange={(value) => onChange("blockWeakMarket", value)} />
            <ToggleField label="止损不受周限制" checked={form.forceStopOverridesLimit} onChange={(value) => onChange("forceStopOverridesLimit", value)} />
            <ToggleField label="盈利卖出当天不新买" checked={form.blockSameDayReentry} onChange={(value) => onChange("blockSameDayReentry", value)} />
            <ToggleField label="启用 ATR 止损" checked={form.useAtrStop} onChange={(value) => onChange("useAtrStop", value)} />
          </div>
        </section>

        <section className="workspace-panel">
          <PanelTitle icon={<SlidersHorizontal size={17} />} title="技术因子" />
          <div className="field-grid four compact">
            <TextField label="快线MA" type="number" value={form.trendFastPeriod} onChange={(value) => onChange("trendFastPeriod", value)} />
            <TextField label="慢线MA" type="number" value={form.trendSlowPeriod} onChange={(value) => onChange("trendSlowPeriod", value)} />
            <TextField label="长线MA" type="number" value={form.trendLongPeriod} onChange={(value) => onChange("trendLongPeriod", value)} />
            <TextField label="量均周期" type="number" value={form.volumeMaPeriod} onChange={(value) => onChange("volumeMaPeriod", value)} />
            <TextField label="BOLL周期" type="number" value={form.bollPeriod} onChange={(value) => onChange("bollPeriod", value)} />
            <TextField label="BOLL倍数" type="number" value={form.bollDev} onChange={(value) => onChange("bollDev", value)} />
            <TextField label="下轨容差%" type="number" value={form.bollTolerancePct} onChange={(value) => onChange("bollTolerancePct", value)} />
            <TextField label="收口带宽%" type="number" value={form.bollBandwidthMaxPct} onChange={(value) => onChange("bollBandwidthMaxPct", value)} />
            <TextField label="MACD快" type="number" value={form.macdFastPeriod} onChange={(value) => onChange("macdFastPeriod", value)} />
            <TextField label="MACD慢" type="number" value={form.macdSlowPeriod} onChange={(value) => onChange("macdSlowPeriod", value)} />
            <TextField label="MACD信号" type="number" value={form.macdSignalPeriod} onChange={(value) => onChange("macdSignalPeriod", value)} />
            <TextField label="RSI周期" type="number" value={form.rsiPeriod} onChange={(value) => onChange("rsiPeriod", value)} />
            <TextField label="RSI下限" type="number" value={form.rsiLowerBound} onChange={(value) => onChange("rsiLowerBound", value)} />
            <TextField label="RSI上限" type="number" value={form.rsiUpperBound} onChange={(value) => onChange("rsiUpperBound", value)} />
            <TextField label="ATR周期" type="number" value={form.atrPeriod} onChange={(value) => onChange("atrPeriod", value)} />
            <TextField label="ATR倍数" type="number" value={form.atrStopMultiplier} onChange={(value) => onChange("atrStopMultiplier", value)} />
          </div>
          <div className="toggle-grid">
            <ToggleField label="趋势过滤" checked={form.useTrendFilter} onChange={(value) => onChange("useTrendFilter", value)} />
            <ToggleField label="MACD过滤" checked={form.useMacdFilter} onChange={(value) => onChange("useMacdFilter", value)} />
            <ToggleField label="MACD需在零轴上" checked={form.macdRequireZeroAxis} onChange={(value) => onChange("macdRequireZeroAxis", value)} />
            <ToggleField label="RSI过滤" checked={form.useRsiFilter} onChange={(value) => onChange("useRsiFilter", value)} />
          </div>
          <button className="primary-button wide-action" type="button" onClick={onRun} disabled={busy}>
            <Play size={17} /> 回测并生成DeepSeek评价
          </button>
        </section>
      </section>

      <section className="workspace-panel">
        <PanelTitle icon={<Activity size={17} />} title="交易流水" right={result ? `${result.trades.length} 笔` : "0 笔"} />
        <TradeTable trades={result?.trades || []} />
      </section>
    </section>
  );
}

function ReviewPanel({ result, rows, symbolTitle }) {
  const analysis = result?.aiAnalysis;
  if (!analysis) {
    return (
      <section className="workspace-panel empty-review">
        <PanelTitle icon={<Bot size={17} />} title="AI策略评价" right={rows.length ? `${rows.length} 根日线` : "无数据"} />
        <div className="empty-state tall">暂无评价</div>
      </section>
    );
  }

  return (
    <section className="review-layout">
      <section className="workspace-panel review-hero">
        <div>
          <p className="eyebrow">Strategy Evidence</p>
          <h2>{symbolTitle}</h2>
          <strong>{analysis.verdict}</strong>
          <AIEngineBadge analysis={analysis} />
        </div>
        <div className="score-ring">
          <span>{analysis.score}</span>
          <em>AI评分</em>
        </div>
      </section>

      <section className="workspace-panel">
        <PanelTitle icon={<Bot size={17} />} title="客观结论" />
        {analysis.llmStatus !== "ok" ? <p className="llm-warning">{analysis.llmError || "DeepSeek 未返回结果，当前显示本地规则兜底。"}</p> : null}
        <ul className="compact-list">
          {analysis.summary.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
        <p className="market-fit">{analysis.marketFit}</p>
      </section>

      <section className="factor-grid">
        {analysis.factorRead.map((item) => (
          <article key={item.name} className="factor-card">
            <span>{item.name}</span>
            <strong>{item.value}</strong>
            <em>{item.comment}</em>
          </article>
        ))}
      </section>

      <section className="review-columns">
        <ReviewList title="有效证据" tone="good" items={analysis.strengths} />
        <ReviewList title="主要风险" tone="bad" items={analysis.risks} />
        <ReviewList title="下一步验证" tone="neutral" items={analysis.nextChecks} />
      </section>

      <button className="ghost-button export-button" type="button" onClick={() => downloadReport(result)}>
        <Download size={16} /> 导出完整回测JSON
      </button>
    </section>
  );
}

function AIEngineBadge({ analysis }) {
  const ok = analysis.llmStatus === "ok";
  const label = ok ? "DeepSeek" : "本地规则兜底";
  return (
    <span className={`ai-engine-badge ${ok ? "ok" : "fallback"}`}>
      <Bot size={14} />
      {label} · {analysis.model || "deepseek-v4-flash"}
    </span>
  );
}

function ReviewList({ title, tone, items }) {
  return (
    <section className={`workspace-panel review-list ${tone}`}>
      <PanelTitle icon={<CheckCircle2 size={17} />} title={title} />
      <ul className="compact-list">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  );
}

function FundamentalsPanel({ profile, latestBar, dataBars, sourceLabel, onSync, busy }) {
  const valuation = valuationOf(profile);
  const financial = financialOf(profile);
  const synced = hasFundamentalData(profile);
  const facts = [
    ["代码", profile?.ts_code || "--"],
    ["行业", profile?.industry || profile?.fundamentals?.行业 || "--"],
    ["地区", profile?.area || profile?.fundamentals?.地区 || "--"],
    ["市场", profile?.market || profile?.fundamentals?.市场 || "--"],
    ["上市日期", profile?.list_date || profile?.fundamentals?.上市日期 || "--"],
    ["本地日线", dataBars || profile?.data_bars || 0],
    ["最新收盘", formatMoney(latestBar?.close || profile?.close)],
    ["涨跌幅", formatPercentPoint(profile?.pct_chg)],
  ];

  return (
    <section className="rail-panel profile-panel">
      <PanelTitle icon={<Building2 size={17} />} title="标的与基本面" right={sourceLabel} />
      <h2>{profile?.name || "未选择标的"}</h2>
      <div className="fact-grid">
        {facts.map(([label, value]) => (
          <span key={label}>
            <em>{label}</em>
            <strong>{value}</strong>
          </span>
        ))}
      </div>
      <div className="fundamental-slot">
        <div className="fundamental-head">
          <span>基本面评分</span>
          <strong>{synced ? profile?.fundamental_score || 0 : "未同步"}</strong>
        </div>
        <div className="tag-row">
          {(profile?.fundamental_tags || (synced ? [] : ["估值未同步", "财务未同步"])).slice(0, 5).map((tag) => (
            <span key={tag}>{tag}</span>
          ))}
        </div>
        <div className="factor-mini-grid rail-factors">
          <FactorMini label="PE(TTM)" value={formatNumber(valuation.PE_TTM)} />
          <FactorMini label="PB" value={formatNumber(valuation.PB)} />
          <FactorMini label="总市值" value={formatMarketCap(valuation.总市值_万元)} />
          <FactorMini label="ROE" value={formatPercentPoint(financial.ROE)} />
          <FactorMini label="毛利率" value={formatPercentPoint(financial.毛利率)} />
          <FactorMini label="负债率" value={formatPercentPoint(financial.资产负债率)} />
        </div>
        {!synced ? (
          <button className="ghost-button wide-action" type="button" onClick={onSync} disabled={busy}>
            <Database size={16} /> 同步基本面
          </button>
        ) : null}
      </div>
    </section>
  );
}

function StrategyBrief({ form, result }) {
  const preset = ENTRY_PRESETS.find((item) => item.value === form.entryMode);
  return (
    <section className="rail-panel">
      <PanelTitle icon={<ShieldCheck size={17} />} title="当前策略" right={preset?.bias || "自定义"} />
      <div className="strategy-stamp">
        <strong>{preset?.label || form.entryMode}</strong>
        <span>周交易≤{form.weeklyTradeLimit} · 仓位≤{form.positionCapPct}% · 风险≤{form.riskPct}%</span>
      </div>
      <ul className="audit-list">
        <li>
          <i className="audit-dot ok" />
          <span>止损 {form.stopLossPct}% / 第一止盈 {form.takeProfit1Pct}% / 第二止盈 {form.takeProfit2Pct}%</span>
        </li>
        <li>
          <i className={`audit-dot ${form.useTrendFilter ? "ok" : "warn"}`} />
          <span>趋势过滤：{form.useTrendFilter ? "启用" : "关闭"}</span>
        </li>
        <li>
          <i className={`audit-dot ${result ? "ok" : "warn"}`} />
          <span>纪律评分：{result ? `${result.disciplineScore} 分` : "等待回测"}</span>
        </li>
      </ul>
    </section>
  );
}

function PanelTitle({ icon, title, right = null }) {
  return (
    <div className="panel-title">
      <h2>
        {icon}
        {title}
      </h2>
      {right ? <span>{right}</span> : null}
    </div>
  );
}

function TextField({ label, value, onChange, type = "text", placeholder = "" }) {
  return (
    <label className="field">
      {label}
      <input type={type} value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function SelectField({ label, value, onChange, options }) {
  return (
    <label className="field">
      {label}
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map(([itemValue, text]) => (
          <option key={itemValue} value={itemValue}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}

function ToggleField({ label, checked, onChange }) {
  return (
    <label className="toggle-row">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

function ActionButton({ icon, label, onClick, disabled }) {
  return (
    <button className="ghost-button" type="button" onClick={onClick} disabled={disabled}>
      {icon}
      {label}
    </button>
  );
}

function MetricTile({ label, value, tone, sub }) {
  return (
    <article className={`metric-tile ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <em>{sub}</em>
    </article>
  );
}

function IndicatorTape({ bar }) {
  const items = [
    ["收盘", formatMoney(bar?.close)],
    ["MA5", formatNumber(bar?.ma5)],
    ["MA20", formatNumber(bar?.ma20)],
    ["BOLL宽度", formatPercent(bar?.bollBandwidthPct, 1)],
    ["MACD柱", formatNumber(bar?.macdHist)],
    ["RSI", formatNumber(bar?.rsiStrategy)],
    ["KDJ-J", formatNumber(bar?.kdjJ)],
    ["ATR", formatNumber(bar?.atrStrategy)],
  ];

  return (
    <div className="indicator-tape">
      {items.map(([label, value]) => (
        <span key={label}>
          <em>{label}</em>
          <strong>{value}</strong>
        </span>
      ))}
    </div>
  );
}

function InteractiveMarketChart({ rows, trades }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const rangeRef = useRef("6m");
  const [hovered, setHovered] = useState(null);
  const [range, setRange] = useState("6m");
  const [layers, setLayers] = useState({ ma: true, boll: true, macd: true, volume: true, trades: true });
  const latest = rows.length ? rows[rows.length - 1] : null;

  useEffect(() => {
    rangeRef.current = range;
  }, [range]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    container.replaceChildren();

    if (!rows.length) {
      return undefined;
    }

    const chart = createChart(container, {
      width: container.clientWidth,
      height: Math.round(container.getBoundingClientRect().height || 560),
      autoSize: true,
      layout: {
        background: { color: "#0a100e" },
        textColor: "#9fb0a8",
        attributionLogo: false,
        fontFamily: "Bahnschrift, Microsoft YaHei UI, sans-serif",
      },
      grid: {
        vertLines: { color: "rgba(232, 239, 235, 0.055)" },
        horzLines: { color: "rgba(232, 239, 235, 0.07)" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "rgba(46, 212, 155, 0.58)", labelBackgroundColor: "#179b72" },
        horzLine: { color: "rgba(107, 183, 255, 0.48)", labelBackgroundColor: "#256b8f" },
      },
      rightPriceScale: {
        borderColor: "rgba(232, 239, 235, 0.12)",
        scaleMargins: { top: 0.08, bottom: layers.macd || layers.volume ? 0.3 : 0.08 },
      },
      timeScale: {
        borderColor: "rgba(232, 239, 235, 0.12)",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 8,
        barSpacing: 7,
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
    });

    chartRef.current = chart;
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#2ed49b",
      downColor: "#f05f50",
      borderUpColor: "#2ed49b",
      borderDownColor: "#f05f50",
      wickUpColor: "#2ed49b",
      wickDownColor: "#f05f50",
      priceLineColor: "#2ed49b",
      lastValueVisible: true,
    });
    candleSeries.setData(toCandles(rows));

    if (layers.ma) {
      addLineSeries(chart, rows, "ma5", "#f0c35a", "MA快");
      addLineSeries(chart, rows, "ma20", "#6bb7ff", "MA长");
    }

    if (layers.boll) {
      addLineSeries(chart, rows, "bollUpper", "rgba(149, 163, 156, 0.74)", "BOLL上", 1);
      addLineSeries(chart, rows, "bollLower", "rgba(149, 163, 156, 0.74)", "BOLL下", 1);
    }

    if (layers.volume) {
      const volumeSeries = chart.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
        priceLineVisible: false,
        lastValueVisible: false,
      });
      volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      volumeSeries.setData(
        rows
          .filter((row) => isFiniteNumber(row.volume))
          .map((row) => ({
            time: row.date,
            value: Number(row.volume),
            color: row.close >= row.open ? "rgba(46, 212, 155, 0.22)" : "rgba(240, 95, 80, 0.24)",
          })),
      );
    }

    if (layers.macd) {
      const macdSeries = chart.addSeries(HistogramSeries, {
        priceScaleId: "macd",
        priceLineVisible: false,
        lastValueVisible: false,
        priceFormat: { type: "price", precision: 2, minMove: 0.01 },
      });
      macdSeries.priceScale().applyOptions({ scaleMargins: { top: 0.72, bottom: 0.06 } });
      macdSeries.setData(
        rows
          .filter((row) => isFiniteNumber(row.macdHist))
          .map((row) => ({
            time: row.date,
            value: Number(row.macdHist),
            color: row.macdHist >= 0 ? "rgba(46, 212, 155, 0.74)" : "rgba(240, 95, 80, 0.72)",
          })),
      );
      addLineSeries(chart, rows, "macdDif", "#f0c35a", "DIF", 1, "macd");
      addLineSeries(chart, rows, "macdDea", "#6bb7ff", "DEA", 1, "macd");
    }

    if (layers.trades && trades.length) {
      createSeriesMarkers(candleSeries, buildTradeMarkers(trades));
    }

    const onCrosshairMove = (param) => {
      if (!param.time) {
        setHovered(null);
        return;
      }
      const candle = param.seriesData.get(candleSeries);
      const row = rows.find((item) => item.date === param.time);
      if (!row && !candle) {
        setHovered(null);
        return;
      }
      setHovered({
        date: param.time,
        open: candle?.open ?? row?.open,
        high: candle?.high ?? row?.high,
        low: candle?.low ?? row?.low,
        close: candle?.close ?? row?.close,
        ma5: row?.ma5,
        ma20: row?.ma20,
        rsi: row?.rsiStrategy,
        atr: row?.atrStrategy,
        macd: row?.macdHist,
        volume: row?.volume,
      });
    };

    chart.subscribeCrosshairMove(onCrosshairMove);
    applyChartRange(chart, rows, rangeRef.current);

    return () => {
      chart.unsubscribeCrosshairMove(onCrosshairMove);
      chart.remove();
      chartRef.current = null;
    };
  }, [rows, trades, layers]);

  function updateRange(nextRange) {
    setRange(nextRange);
    if (chartRef.current) {
      applyChartRange(chartRef.current, rows, nextRange);
    }
  }

  const activeRow = rows.length ? hovered || latest : null;

  return (
    <div className="interactive-chart-shell">
      <div className="chart-control-strip">
        <div className="chart-readout">
          <strong>{activeRow?.date || "等待数据"}</strong>
          <span>O {formatMoney(activeRow?.open)}</span>
          <span>H {formatMoney(activeRow?.high)}</span>
          <span>L {formatMoney(activeRow?.low)}</span>
          <span>C {formatMoney(activeRow?.close)}</span>
          <span>RSI {formatNumber(activeRow?.rsi ?? activeRow?.rsiStrategy)}</span>
          <span>MACD {formatNumber(activeRow?.macd ?? activeRow?.macdHist)}</span>
        </div>
        <div className="chart-button-row">
          {["3m", "6m", "1y", "all"].map((item) => (
            <button key={item} type="button" className={range === item ? "active" : ""} onClick={() => updateRange(item)}>
              {rangeLabel(item)}
            </button>
          ))}
        </div>
      </div>
      <div className="chart-layer-strip">
        {[
          ["ma", "MA"],
          ["boll", "BOLL"],
          ["macd", "MACD"],
          ["volume", "成交量"],
          ["trades", "买卖点"],
        ].map(([key, label]) => (
          <button key={key} type="button" className={layers[key] ? "active" : ""} onClick={() => setLayers((current) => ({ ...current, [key]: !current[key] }))}>
            {label}
          </button>
        ))}
        <span>滚轮缩放 · 拖拽平移 · 悬浮查看OHLC</span>
      </div>
      <div ref={containerRef} className="market-chart" />
    </div>
  );
}

function TradeTable({ trades }) {
  return (
    <div className="table-wrap trades-table">
      <table>
        <thead>
          <tr>
            <th>日期</th>
            <th>动作</th>
            <th>价格</th>
            <th>数量</th>
            <th>现金</th>
            <th>原因</th>
          </tr>
        </thead>
        <tbody>
          {trades.length ? (
            trades.map((trade, index) => (
              <tr key={`${trade.date}-${trade.action}-${index}`}>
                <td>{trade.date}</td>
                <td>
                  <span className={`trade-action ${trade.action === "买入" ? "buy" : "sell"}`}>{trade.action}</span>
                </td>
                <td>{formatMoney(trade.price)}</td>
                <td>{trade.quantity}</td>
                <td>{formatMoney(trade.cash)}</td>
                <td>{trade.reason}</td>
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan="6" className="empty-state">
                没有回测交易
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function buildMetrics(result, rows) {
  if (!result) {
    return [
      { label: "总收益", value: "--", tone: "neutral", sub: rows.length ? "行情已载入" : "等待数据" },
      { label: "最大回撤", value: "--", tone: "neutral", sub: "等待回测" },
      { label: "胜率", value: "--", tone: "neutral", sub: "等待交易" },
      { label: "AI评分", value: "--", tone: "neutral", sub: "等待评价" },
    ];
  }
  return [
    { label: "总收益", value: formatPercent(result.totalReturn, 2), tone: result.totalReturn >= 0 ? "good" : "bad", sub: `终值 ${formatMoney(result.finalEquity)}` },
    { label: "最大回撤", value: formatPercent(result.maxDrawdown, 2), tone: result.maxDrawdown <= -0.08 ? "bad" : "good", sub: "越接近 0 越稳" },
    { label: "胜率", value: formatPercent(result.winRate, 1), tone: "neutral", sub: `${result.completedTrades.length} 笔完成交易` },
    { label: "AI评分", value: String(result.aiAnalysis?.score ?? "--"), tone: (result.aiAnalysis?.score ?? 0) >= 70 ? "good" : "neutral", sub: result.aiAnalysis?.verdict || "等待评价" },
  ];
}

function getDataRequest(form) {
  if (!form.tsCode.trim()) throw new Error("请填写股票代码或股票名称。");
  if (new Date(form.endDate) < new Date(form.startDate)) throw new Error("结束日期不能早于开始日期。");
  return { ts_code: form.tsCode.trim().toUpperCase(), start_date: form.startDate, end_date: form.endDate };
}

function buildBacktestConfig(form) {
  return {
    symbolName: form.stockName ? `${form.stockName} ${form.tsCode.trim().toUpperCase()}` : "",
    marketState: form.marketState,
    entryMode: form.entryMode,
    bollPeriod: Number(form.bollPeriod),
    bollDev: Number(form.bollDev),
    bollTolerancePct: Number(form.bollTolerancePct) / 100,
    bollBandwidthMaxPct: Number(form.bollBandwidthMaxPct) / 100,
    midlineTolerancePct: Number(form.midlineTolerancePct) / 100,
    trendFastPeriod: Number(form.trendFastPeriod),
    trendSlowPeriod: Number(form.trendSlowPeriod),
    trendLongPeriod: Number(form.trendLongPeriod),
    volumeMaPeriod: Number(form.volumeMaPeriod),
    volumeBreakoutMultiplier: Number(form.volumeBreakoutMultiplier),
    useTrendFilter: form.useTrendFilter,
    useMacdFilter: form.useMacdFilter,
    macdFastPeriod: Number(form.macdFastPeriod),
    macdSlowPeriod: Number(form.macdSlowPeriod),
    macdSignalPeriod: Number(form.macdSignalPeriod),
    macdRequireZeroAxis: form.macdRequireZeroAxis,
    useRsiFilter: form.useRsiFilter,
    rsiPeriod: Number(form.rsiPeriod),
    rsiLowerBound: Number(form.rsiLowerBound),
    rsiUpperBound: Number(form.rsiUpperBound),
    kdjPeriod: Number(form.kdjPeriod),
    atrPeriod: Number(form.atrPeriod),
    useAtrStop: form.useAtrStop,
    atrStopMultiplier: Number(form.atrStopMultiplier),
    blockWeakMarket: form.blockWeakMarket,
    initialCash: Number(form.initialCash),
    weeklyTradeLimit: Number(form.weeklyTradeLimit),
    positionCapPct: Number(form.positionCapPct) / 100,
    riskPct: Number(form.riskPct) / 100,
    stopLossPct: Number(form.stopLossPct) / 100,
    takeProfit1Pct: Number(form.takeProfit1Pct) / 100,
    takeProfit2Pct: Number(form.takeProfit2Pct) / 100,
    commissionPct: Number(form.commissionPct) / 100,
    stampDutyPct: Number(form.stampDutyPct) / 100,
    lotSize: Number(form.lotSize),
    forceStopOverridesLimit: form.forceStopOverridesLimit,
    blockSameDayReentry: form.blockSameDayReentry,
  };
}

async function apiFetch(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const detail = Array.isArray(payload.detail) ? payload.detail.map((item) => item.msg).join("; ") : payload.detail;
    throw new Error(detail || text || `API 请求失败：${response.status}`);
  }
  return payload;
}

function buildFallbackProfile(form) {
  return {
    ts_code: form.tsCode,
    name: form.stockName || form.tsCode,
    fundamentals: {},
    fundamental_tags: ["估值未同步", "财务未同步"],
  };
}

function valuationOf(stock) {
  return stock?.fundamentals?.估值 || {};
}

function financialOf(stock) {
  return stock?.fundamentals?.财务 || {};
}

function hasFundamentalData(stock) {
  const valuation = valuationOf(stock);
  const financial = financialOf(stock);
  return Boolean(valuation.日期 || financial.报告期);
}

function candidateScore(stock) {
  return Math.min(100, Math.round(((stock?.technical_score || 0) * 0.55) + ((stock?.fundamental_score || 0) * 0.45)));
}

function toCandles(rows) {
  return rows
    .filter((row) => ["open", "high", "low", "close"].every((key) => isFiniteNumber(row[key])))
    .map((row) => ({
      time: row.date,
      open: Number(row.open),
      high: Number(row.high),
      low: Number(row.low),
      close: Number(row.close),
    }));
}

function addLineSeries(chart, rows, key, color, title, lineWidth = 2, priceScaleId = "") {
  const series = chart.addSeries(LineSeries, {
    color,
    title,
    lineWidth,
    priceScaleId,
    priceLineVisible: false,
    lastValueVisible: false,
  });
  series.setData(
    rows
      .filter((row) => isFiniteNumber(row[key]))
      .map((row) => ({
        time: row.date,
        value: Number(row[key]),
      })),
  );
  return series;
}

function buildTradeMarkers(trades) {
  return trades
    .filter((trade) => trade.date)
    .map((trade, index) => {
      const buy = trade.action === "买入";
      return {
        id: `${trade.date}-${trade.action}-${index}`,
        time: trade.date,
        position: buy ? "belowBar" : "aboveBar",
        color: buy ? "#6bb7ff" : "#f0c35a",
        shape: buy ? "arrowUp" : "arrowDown",
        text: buy ? "买" : "卖",
        size: 1,
      };
    });
}

function applyChartRange(chart, rows, range) {
  if (!rows.length) return;
  if (range === "all") {
    chart.timeScale().fitContent();
    return;
  }
  const span = { "3m": 92, "6m": 183, "1y": 365 }[range] || 183;
  const last = new Date(rows[rows.length - 1].date);
  const first = new Date(last);
  first.setDate(first.getDate() - span);
  chart.timeScale().setVisibleRange({
    from: formatDate(first),
    to: rows[rows.length - 1].date,
  });
}

function rangeLabel(range) {
  return { "3m": "3M", "6m": "6M", "1y": "1Y", all: "ALL" }[range] || range;
}

function isFiniteNumber(value) {
  return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
}

function formatPercent(value, digits = 2) {
  if (!isFiniteNumber(value)) return "--";
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

function formatPercentPoint(value) {
  if (!isFiniteNumber(value)) return "--";
  return `${Number(value).toFixed(2)}%`;
}

function formatMoney(value) {
  if (!isFiniteNumber(value)) return "--";
  return Number(value).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatNumber(value) {
  if (!isFiniteNumber(value)) return "--";
  return Number(value).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatMarketCap(value) {
  if (!isFiniteNumber(value)) return "--";
  return `${(Number(value) / 10000).toLocaleString("zh-CN", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}亿`;
}

function dateToday() {
  return formatDate(new Date());
}

function dateYearsAgo(years) {
  const date = new Date();
  date.setFullYear(date.getFullYear() - years);
  return formatDate(date);
}

function formatDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function downloadReport(result) {
  if (!result) return;
  const blob = new Blob([JSON.stringify(result, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `backtest-report-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

createRoot(document.getElementById("root")).render(<App />);
