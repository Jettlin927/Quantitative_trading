import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  AlertTriangle,
  BarChart3,
  Bot,
  Building2,
  CheckCircle2,
  ClipboardCheck,
  Database,
  Download,
  FileSearch,
  Filter,
  FolderPlus,
  Gauge,
  GitBranch,
  Inbox,
  Layers3,
  LineChart,
  Play,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  TrendingUp,
  Trash2,
  UploadCloud,
} from "lucide-react";
import "./styles.css";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "http://localhost:18000").replace(/\/$/, "");
const FORM_SCHEMA_VERSION = "2026-05-29-research-desk";
const MARKET_SYNC_MIN_ROWS = 5000;
const SYNC_PROGRESS_TARGET_KEY = "qt-sync-progress-target";
const MARKET_BACKTEST_JOB_KEY = "qt-market-backtest-job";
const STOCK_POOL_KEY = "qt-active-stock-pool";

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
  tailEntryMinPctChg: "3",
  tailEntryMaxPctChg: "5",
  tailPriorLimitUpLookback: "15",
  tailMinVolumeRatio: "1.5",
  tailMinTurnoverRatePct: "5",
  tailLimitUpPct: "9.5",
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
  rank_by: "fundamental",
  limit: "60",
};

const TABS = [
  ["dashboard", "总览", Activity],
  ["screen", "选股池", Filter],
  ["single", "单票验证", LineChart],
  ["market", "全市场验证", BarChart3],
  ["diagnostic", "复盘诊断", Gauge],
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

const RANK_OPTIONS = [
  ["fundamental", "基本面榜"],
  ["composite", "综合榜"],
  ["technical", "技术榜"],
  ["valuation", "估值榜"],
];

const ENTRY_MODE_OPTIONS = [
  ["boll-rebound", "BOLL下轨反弹"],
  ["midline-confirm", "BOLL中轨确认"],
  ["trend-follow", "MA多头跟随"],
  ["trend-pullback-confirm", "MA20回踩确认"],
  ["macd-cross", "MACD金叉"],
  ["boll-breakout", "BOLL上轨突破"],
  ["boll-squeeze", "BOLL收口突破"],
  ["rsi-reversal", "RSI超卖反转"],
  ["ma-cross", "均线金叉"],
  ["tail-active-next-day", "尾盘活跃次日纪律"],
];

function App() {
  const [form, setForm] = usePersistentForm();
  const [view, setView] = useState(getInitialView);
  const [status, setStatus] = useState({ text: "等待连接 API", tone: "muted" });
  const [screenFilters, setScreenFilters] = useState(() => ({ ...DEFAULT_SCREEN_FILTERS, q: form.stockName || form.tsCode }));
  const [screenResults, setScreenResults] = useState([]);
  const [stockPools, setStockPools] = useState([]);
  const [activePoolId, setActivePoolId] = useState(getInitialStockPoolId);
  const [activePool, setActivePool] = useState(null);
  const [selectedStock, setSelectedStock] = useState(null);
  const [newsItems, setNewsItems] = useState([]);
  const [newsMessage, setNewsMessage] = useState("");
  const [marketMainline, setMarketMainline] = useState(null);
  const [tailCandidates, setTailCandidates] = useState(null);
  const [result, setResult] = useState(null);
  const [marketResult, setMarketResult] = useState(null);
  const [baselineStrategy, setBaselineStrategy] = useState(null);
  const [baselineError, setBaselineError] = useState("");
  const [qualityAnalysis, setQualityAnalysis] = useState(null);
  const [bars, setBars] = useState([]);
  const [busy, setBusy] = useState(false);
  const [syncProgress, setSyncProgress] = useState(null);
  const [syncProgressTarget, setSyncProgressTarget] = useState(getInitialSyncProgressTarget);
  const [syncProgressPolling, setSyncProgressPolling] = useState(false);
  const [marketBacktestJob, setMarketBacktestJob] = useState(getInitialMarketBacktestJob);
  const [sourceLabel, setSourceLabel] = useState("数据库未载入");
  const [researchRuns, setResearchRuns] = useState([]);
  const [selectedResearchRunId, setSelectedResearchRunId] = useState("");
  const [researchRun, setResearchRun] = useState(null);
  const [researchRunError, setResearchRunError] = useState("");
  const [researchOverview, setResearchOverview] = useState(null);
  const [researchOverviewError, setResearchOverviewError] = useState("");
  const [selectedDocsStrategy, setSelectedDocsStrategy] = useState(null);
  const [docsStrategyConfig, setDocsStrategyConfig] = useState(null);
  const initialFormRef = useRef(form);

  const rows = result?.rows?.length ? result.rows : bars;
  const latestBar = rows.length ? rows[rows.length - 1] : null;
  const profile = selectedStock || buildFallbackProfile(form);
  const symbolTitle = profile?.name ? `${profile.name} · ${profile.ts_code}` : form.tsCode;
  const metrics = useMemo(() => (marketResult ? buildMarketMetrics(marketResult) : buildMetrics(result, rows)), [marketResult, result, rows]);
  const marketBacktestJobId = marketBacktestJob?.jobId || "";
  const marketBacktestActive = ["queued", "running"].includes(marketBacktestJob?.status);
  const activePoolMemberCodes = useMemo(() => new Set((activePool?.members || []).map((item) => item.ts_code)), [activePool]);

  const loadExecutableStrategy = useCallback(async (showView = true) => {
    try {
      const data = await apiFetch("/api/strategies/executable/cross-section-strength-risk8");
      setBaselineStrategy(data);
      setBaselineError("");
      if (showView) {
        setView("baseline");
        setStatus({ text: `已载入组合基线：${data.label}`, tone: "good" });
      }
    } catch (error) {
      setBaselineError(error.message);
      if (showView) setStatus({ text: `组合基线载入失败：${error.message}`, tone: "bad" });
    }
  }, []);

  const loadResearchRun = useCallback(async (runId, showStatus = false) => {
    if (!runId) return null;
    try {
      const data = await apiFetch(`/api/research/runs/${encodeURIComponent(runId)}`);
      setResearchRun(data);
      setSelectedResearchRunId(data.runId || runId);
      setResearchRunError("");
      if (showStatus) setStatus({ text: `已载入研究运行：${data.runId || runId}`, tone: "good" });
      return data;
    } catch (error) {
      setResearchRunError(error.message);
      if (showStatus) setStatus({ text: `研究运行载入失败：${error.message}`, tone: "bad" });
      return null;
    }
  }, []);

  const refreshResearchRuns = useCallback(
    async (preferredRunId = "", showStatus = false) => {
      try {
        const data = await apiFetch("/api/research/runs?limit=160");
        const runs = data.runs || [];
        setResearchRuns(runs);
        const nextRunId = preferredRunId && runs.some((item) => item.runId === preferredRunId) ? preferredRunId : runs[0]?.runId;
        if (nextRunId) {
          await loadResearchRun(nextRunId, showStatus);
        } else {
          setResearchRun(null);
          setResearchRunError("docs/research/runs 下没有可读取的 results.json");
        }
        if (showStatus) setStatus({ text: `已刷新研究运行目录：${runs.length} 条`, tone: "good" });
      } catch (error) {
        setResearchRunError(error.message);
        if (showStatus) setStatus({ text: `研究运行目录读取失败：${error.message}`, tone: "bad" });
      }
    },
    [loadResearchRun],
  );

  const refreshResearchOverview = useCallback(async (showStatus = false) => {
    try {
      const data = await apiFetch("/api/research/overview");
      setResearchOverview(data);
      setResearchOverviewError("");
      if (showStatus) setStatus({ text: `研究阶段已刷新：${data.stage?.stageId || "未声明阶段"}`, tone: "good" });
      return data;
    } catch (error) {
      setResearchOverviewError(error.message);
      if (showStatus) setStatus({ text: `研究阶段读取失败：${error.message}`, tone: "bad" });
      return null;
    }
  }, []);

  const fetchSyncProgress = useCallback(
    async (target) => {
      const params = new URLSearchParams({
        target,
        start_date: form.startDate,
        end_date: form.endDate,
        min_existing_rows: String(MARKET_SYNC_MIN_ROWS),
      });
      return apiFetch(`/api/tushare/sync-progress?${params.toString()}`);
    },
    [form.startDate, form.endDate],
  );

  useEffect(() => {
    if (view !== "single" || researchRuns.length || researchRunError) return;
    void refreshResearchRuns("", false);
  }, [refreshResearchRuns, researchRunError, researchRuns.length, view]);

  useEffect(() => {
    if (!["single", "market"].includes(view) || researchOverview || researchOverviewError) return;
    void refreshResearchOverview(false);
  }, [refreshResearchOverview, researchOverview, researchOverviewError, view]);

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

  useEffect(() => {
    let ignore = false;
    async function loadPools() {
      try {
        const pools = await apiFetch("/api/stock-pools");
        if (ignore) return;
        setStockPools(pools);
        if (!pools.length) {
          setActivePoolId(0);
          return;
        }
        const saved = getInitialStockPoolId();
        const nextPool = pools.find((pool) => pool.id === saved) || pools[0];
        setActivePoolId(nextPool.id);
      } catch {
        if (!ignore) setStockPools([]);
      }
    }
    loadPools();
    return () => {
      ignore = true;
    };
  }, []);

  useEffect(() => {
    if (!activePoolId) {
      setActivePool(null);
      localStorage.removeItem(STOCK_POOL_KEY);
      return undefined;
    }
    localStorage.setItem(STOCK_POOL_KEY, String(activePoolId));
    let ignore = false;
    async function loadPoolDetail() {
      try {
        const pool = await apiFetch(`/api/stock-pools/${activePoolId}`);
        if (!ignore) setActivePool(pool);
      } catch (error) {
        if (!ignore) {
          setActivePool(null);
          setStatus({ text: error.message, tone: "bad" });
        }
      }
    }
    loadPoolDetail();
    return () => {
      ignore = true;
    };
  }, [activePoolId]);

  useEffect(() => {
    if (!syncProgressTarget) return undefined;
    let ignore = false;
    async function refreshProgress() {
      try {
        const data = await fetchSyncProgress(syncProgressTarget);
        if (!ignore) setSyncProgress(data);
      } catch (error) {
        if (!ignore) {
          setSyncProgress((current) => ({
            ...(current?.target === syncProgressTarget ? current : { target: syncProgressTarget, label: syncTargetLabel(syncProgressTarget) }),
            status: "error",
            error: error.message,
          }));
        }
      }
    }
    refreshProgress();
    if (!syncProgressPolling) {
      return () => {
        ignore = true;
      };
    }
    const intervalId = window.setInterval(refreshProgress, 3000);
    return () => {
      ignore = true;
      window.clearInterval(intervalId);
    };
  }, [fetchSyncProgress, syncProgressPolling, syncProgressTarget]);

  useEffect(() => {
    if (!marketBacktestJobId || !marketBacktestActive) return undefined;
    let ignore = false;
    async function refreshMarketJob() {
      try {
        const data = await apiFetch(`/api/backtests/market/jobs/${marketBacktestJobId}`);
        if (ignore) return;
        setMarketBacktestJob(data);
        if (data.status === "ok" && data.result) {
          localStorage.removeItem(MARKET_BACKTEST_JOB_KEY);
          const scopeName = data.result.scope?.poolName || "全市场";
          setMarketResult(data.result);
          setResult(null);
          setSourceLabel(`${data.result.scope?.poolId ? "POOL" : "MARKET"}:${data.result.summary.tested}/${data.result.summary.candidates}`);
          setStatus({ text: `${scopeName}验证完成：测试 ${data.result.summary.tested} 只，正收益 ${data.result.summary.winners} 只`, tone: "good" });
          setView("market");
        } else if (data.status === "failed") {
          localStorage.removeItem(MARKET_BACKTEST_JOB_KEY);
          setStatus({ text: data.error || data.message || "全市场验证失败", tone: "bad" });
        }
      } catch (error) {
        if (!ignore) {
          localStorage.removeItem(MARKET_BACKTEST_JOB_KEY);
          setMarketBacktestJob((current) => ({ ...(current || { jobId: marketBacktestJobId }), status: "failed", message: error.message, error: error.message }));
          setStatus({ text: error.message, tone: "bad" });
        }
      }
    }
    refreshMarketJob();
    const intervalId = window.setInterval(refreshMarketJob, 2000);
    return () => {
      ignore = true;
      window.clearInterval(intervalId);
    };
  }, [marketBacktestActive, marketBacktestJobId]);

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
      setStatus({ text: `正在同步 ${req.ts_code} 单票日线...`, tone: "muted" });
      const data = await apiFetch("/api/tushare/sync-daily", { method: "POST", body: JSON.stringify(req) });
      updateForm("tsCode", data.ts_code);
      setStatus({ text: `${data.ts_code} 日线同步完成：${data.rows_upserted} 条`, tone: "good" });
      await loadBars(false, data.ts_code);
    });
  }

  async function syncMarketDaily() {
    await withBusy(async () => {
      if (new Date(form.endDate) < new Date(form.startDate)) throw new Error("结束日期不能早于开始日期。");
      if (!confirmMarketSync("全市场日线")) return;
      const progressTarget = "daily";
      activateSyncProgress(progressTarget);
      setSyncProgressPolling(true);
      void refreshSyncProgress(progressTarget).catch(() => null);
      setResult(null);
      setMarketResult(null);
      setStatus({ text: "正在补齐全市场日线，已完整的交易日会跳过...", tone: "muted" });
      try {
        const data = await apiFetch("/api/tushare/sync-market-daily", {
          method: "POST",
          body: JSON.stringify({
            start_date: form.startDate,
            end_date: form.endDate,
            max_trade_dates: 0,
            skip_existing: true,
            min_existing_rows: MARKET_SYNC_MIN_ROWS,
          }),
        });
        const failedCount = data.failed_dates?.length || 0;
        const skippedCount = data.skipped_trade_dates || 0;
        setSourceLabel(`MARKET-D:${data.rows_upserted}`);
        setStatus({
          text: `日线补齐完成：同步 ${data.trade_dates} 日，跳过 ${skippedCount} 日，${data.rows_upserted} 条${failedCount ? `，失败 ${failedCount} 日` : ""}`,
          tone: failedCount ? "bad" : "good",
        });
        await runScreener(false);
      } finally {
        setSyncProgressPolling(false);
        void refreshSyncProgress(progressTarget).catch(() => null);
      }
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

  async function syncMarketDailyBasic() {
    await withBusy(async () => {
      if (new Date(form.endDate) < new Date(form.startDate)) throw new Error("结束日期不能早于开始日期。");
      if (!confirmMarketSync("全市场每日估值")) return;
      const progressTarget = "daily_basic";
      activateSyncProgress(progressTarget);
      setSyncProgressPolling(true);
      void refreshSyncProgress(progressTarget).catch(() => null);
      setStatus({ text: "正在补齐全市场每日估值，已完整的交易日会跳过...", tone: "muted" });
      try {
        const data = await apiFetch("/api/tushare/sync-market-daily-basic", {
          method: "POST",
          body: JSON.stringify({
            start_date: form.startDate,
            end_date: form.endDate,
            max_trade_dates: 0,
            skip_existing: true,
            min_existing_rows: MARKET_SYNC_MIN_ROWS,
          }),
        });
        const failedCount = data.failed_dates?.length || 0;
        const skippedCount = data.skipped_trade_dates || 0;
        setStatus({
          text: `估值补齐完成：同步 ${data.trade_dates} 日，跳过 ${skippedCount} 日，${data.rows_upserted} 条${failedCount ? `，失败 ${failedCount} 日` : ""}`,
          tone: failedCount ? "bad" : "good",
        });
        await runScreener(false);
      } finally {
        setSyncProgressPolling(false);
        void refreshSyncProgress(progressTarget).catch(() => null);
      }
    });
  }

  async function syncMarketFundamentals() {
    await withBusy(async () => {
      if (new Date(form.endDate) < new Date(form.startDate)) throw new Error("结束日期不能早于开始日期。");
      if (!confirmMarketSync("全A基本面")) return;
      const progressTarget = "daily_basic";
      activateSyncProgress(progressTarget);
      setSyncProgressPolling(true);
      void refreshSyncProgress(progressTarget).catch(() => null);
      setResult(null);
      setMarketResult(null);
      try {
        setStatus({ text: "正在同步 A 股公司列表...", tone: "muted" });
        const stockBasic = await apiFetch("/api/tushare/sync-stock-basic", { method: "POST", body: JSON.stringify({}) });

        setStatus({ text: "正在补齐全市场估值数据...", tone: "muted" });
        const dailyBasic = await apiFetch("/api/tushare/sync-market-daily-basic", {
          method: "POST",
          body: JSON.stringify({
            start_date: form.startDate,
            end_date: form.endDate,
            max_trade_dates: 0,
            skip_existing: true,
            min_existing_rows: MARKET_SYNC_MIN_ROWS,
          }),
        });

        setStatus({ text: "正在补齐全市场财务指标...", tone: "muted" });
        const financial = await apiFetch("/api/tushare/sync-market-fundamentals", {
          method: "POST",
          body: JSON.stringify({
            start_date: form.startDate,
            end_date: form.endDate,
            max_stocks: 0,
            skip_existing: true,
          }),
        });

        const failedCount = (dailyBasic.failed_dates?.length || 0) + (financial.failed_stocks?.length || 0);
        setStatus({
          text: `全A基本面载入完成：公司 ${stockBasic.rows_upserted}，估值 ${dailyBasic.rows_upserted}，财务 ${financial.financial_rows}，跳过 ${financial.skipped_stocks}`,
          tone: failedCount ? "bad" : "good",
        });
        await runScreener(false);
      } finally {
        setSyncProgressPolling(false);
        void refreshSyncProgress(progressTarget).catch(() => null);
      }
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
      setNewsMessage(data.message || "");
      setStatus({ text: data.message || (data.items?.length ? `消息面已刷新：${data.items.length} 条` : "消息面暂无返回"), tone: data.items?.length ? "good" : "muted" });
    });
  }

  async function refreshMarketMainline() {
    await withBusy(async () => {
      const data = await apiFetch("/api/market-signals/mainline?top_n=12");
      setMarketMainline(data);
      const industryCount = data.industries?.total || 0;
      setStatus({ text: `主线快照已刷新：热点 ${data.hotStocks?.length || 0} 条 / 行业 ${industryCount} 个`, tone: data.status === "ok" ? "good" : "muted" });
    });
  }

  async function refreshTailCandidates() {
    await withBusy(async () => {
      const params = new URLSearchParams({
        min_change_pct: form.tailEntryMinPctChg || "3",
        max_change_pct: form.tailEntryMaxPctChg || "5",
        min_volume_ratio: form.tailMinVolumeRatio || "1.5",
        min_turnover_pct: form.tailMinTurnoverRatePct || "5",
        lookback_days: form.tailPriorLimitUpLookback || "15",
        limit: "40",
      });
      const data = await apiFetch(`/api/market-signals/tail-candidates?${params.toString()}`);
      setTailCandidates(data);
      setStatus({ text: `尾盘候选已刷新：${data.candidates?.length || 0} 只`, tone: data.candidates?.length ? "good" : "muted" });
    });
  }

  async function runQualityAnalysis() {
    await withBusy(async () => {
      const req = getDataRequest(form);
      const params = new URLSearchParams({
        start_date: req.start_date,
        end_date: req.end_date,
        use_ai: "true",
      });
      setStatus({ text: `正在运行 ${req.ts_code} 多Agent质量诊断...`, tone: "muted" });
      const data = await apiFetch(`/api/stocks/${encodeURIComponent(req.ts_code)}/quality-analysis?${params.toString()}`);
      setQualityAnalysis(data);
      setStatus({ text: `${data.name || data.symbol} 质量诊断完成：${data.rating} / ${data.score}分`, tone: data.score >= 58 ? "good" : "muted" });
      setView("diagnostic");
    });
  }

  async function refreshStockPools(preferredPoolId = activePoolId) {
    const pools = await apiFetch("/api/stock-pools");
    setStockPools(pools);
    if (!pools.length) {
      setActivePoolId(0);
      return pools;
    }
    if (!pools.some((pool) => pool.id === preferredPoolId)) {
      setActivePoolId(pools[0].id);
    }
    return pools;
  }

  async function createStockPool(name) {
    await withBusy(async () => {
      const pool = await apiFetch("/api/stock-pools", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      await refreshStockPools(pool.id);
      setActivePoolId(pool.id);
      setStatus({ text: `标的池已创建：${pool.name}`, tone: "good" });
    });
  }

  async function addStocksToActivePool(tsCodes) {
    await withBusy(async () => {
      if (!activePoolId) throw new Error("请先创建或选择一个标的池。");
      const codes = [...new Set(tsCodes.map((code) => code.trim()).filter(Boolean))];
      if (!codes.length) throw new Error("请至少提供一个有效标的。");
      const detail = await apiFetch(`/api/stock-pools/${activePoolId}/members`, {
        method: "POST",
        body: JSON.stringify({ ts_codes: codes }),
      });
      setActivePool(detail);
      await refreshStockPools(detail.id);
      setStatus({ text: `已更新标的池：${detail.name}（${detail.member_count} 只）`, tone: "good" });
    });
  }

  async function removeStockFromActivePool(tsCode) {
    await withBusy(async () => {
      if (!activePoolId) throw new Error("请先选择一个标的池。");
      const detail = await apiFetch(`/api/stock-pools/${activePoolId}/members/${tsCode}`, { method: "DELETE" });
      setActivePool(detail);
      await refreshStockPools(detail.id);
      setStatus({ text: `已从 ${detail.name} 移除 ${tsCode}`, tone: "good" });
    });
  }

  async function deleteActivePool() {
    await withBusy(async () => {
      if (!activePoolId || !activePool) throw new Error("当前没有可删除的标的池。");
      if (!window.confirm(`删除标的池「${activePool.name}」？池内标的组合会被移除，但不会删除行情数据。`)) return;
      await apiFetch(`/api/stock-pools/${activePoolId}`, { method: "DELETE" });
      setActivePool(null);
      await refreshStockPools(0);
      setStatus({ text: `已删除标的池：${activePool.name}`, tone: "good" });
    });
  }

  async function runBacktest() {
    await withBusy(async () => {
      const req = getDataRequest(form);
      setStatus({ text: `正在回测 ${req.ts_code}...`, tone: "muted" });
      const data = await apiFetch("/api/backtests/run", {
        method: "POST",
        body: JSON.stringify({ ...req, config: buildBacktestConfig(form, docsStrategyConfig) }),
      });
      setResult(data);
      setMarketResult(null);
      setBars(data.rows || []);
      setSourceLabel(`BT:${req.ts_code}`);
      setStatus({ text: `${req.ts_code} 回测完成：${data.trades.length} 笔流水`, tone: "good" });
      setView("diagnostic");
    });
  }

  async function runMarketBacktest(options = {}) {
    if (marketBacktestActive) {
      setView(options?.targetView || "market");
      return;
    }
    const poolId = Number(options?.poolId || 0);
    const poolName = options?.poolName || (poolId === activePool?.id ? activePool?.name : "");
    const scopeLabel = poolId ? `标的池「${poolName || poolId}」` : "全市场";
    const targetView = options?.targetView || "market";
    const startDate = options?.startDate || form.startDate;
    const endDate = options?.endDate || form.endDate;
    await withBusy(async () => {
      if (new Date(endDate) < new Date(startDate)) throw new Error("结束日期不能早于开始日期。");
      setStatus({ text: `正在创建${scopeLabel}后台验证任务...`, tone: "muted" });
      const data = await apiFetch("/api/backtests/market/jobs", {
        method: "POST",
        body: JSON.stringify({
          start_date: startDate,
          end_date: endDate,
          config: buildBacktestConfig(form, docsStrategyConfig),
          pool_id: poolId || null,
          min_bars: 120,
          max_stocks: 0,
        }),
      });
      localStorage.setItem(MARKET_BACKTEST_JOB_KEY, data.jobId);
      setMarketBacktestJob(data);
      setResult(null);
      setMarketResult(null);
      setSourceLabel(`JOB:${data.jobId.slice(0, 8)}`);
      setStatus({ text: `${scopeLabel}验证已进入后台：${startDate} 至 ${endDate}`, tone: "muted" });
      setView(targetView);
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

  async function refreshSyncProgress(target) {
    const data = await fetchSyncProgress(target);
    setSyncProgress(data);
    return data;
  }

  function activateSyncProgress(target) {
    localStorage.setItem(SYNC_PROGRESS_TARGET_KEY, target);
    setSyncProgressTarget(target);
  }

  function confirmMarketSync(label) {
    const days = Math.ceil((new Date(form.endDate).getTime() - new Date(form.startDate).getTime()) / 86400000) + 1;
    if (days <= 45) return true;
    return window.confirm(`${label}会补齐 ${form.startDate} 到 ${form.endDate} 的全 A 数据，已完整同步的交易日会跳过。\n\n首次补齐可能需要几分钟，期间会按数据库覆盖率刷新进度。确定开始？`);
  }

  function updateForm(name, value) {
    setResult(null);
    setMarketResult(null);
    setQualityAnalysis(null);
    setForm((current) => ({ ...current, [name]: value }));
  }

  function updateScreenFilter(name, value) {
    setScreenFilters((current) => ({ ...current, [name]: value }));
  }

  async function applyDocsStrategy(runId) {
    await withBusy(async () => {
      const data = await loadResearchRun(runId, false);
      if (!data) throw new Error("docs 策略读取失败。");
      const config = getResearchRunConfig(data);
      if (!Object.keys(config).length) throw new Error("这个 docs 策略没有可应用的 config。");
      setDocsStrategyConfig(config);
      setSelectedDocsStrategy({
        runId: data.runId,
        label: data.strategy?.label || data.label || data.runId,
        status: data.status,
        metrics: data.metrics || {},
        source: data.resultFiles?.strategies || data.resultFiles?.results || "docs/research/runs",
      });
      setResult(null);
      setMarketResult(null);
      setQualityAnalysis(null);
      setForm((current) => ({ ...current, ...mapDocsConfigToForm(config) }));
      setStatus({ text: `已套用 docs 策略：${data.strategy?.label || data.label || data.runId}`, tone: "good" });
    });
  }

  async function changeResearchRun(runId) {
    await loadResearchRun(runId, true);
  }

  async function openResearchRun(runId) {
    const data = await loadResearchRun(runId, true);
    if (data) setView("market");
  }

  async function selectCandidate(stock, load = false) {
    setSelectedStock(stock);
    setResult(null);
    setMarketResult(null);
    setQualityAnalysis(null);
    setForm((current) => ({ ...current, tsCode: stock.ts_code, stockName: stock.name }));
    setSourceLabel(`标的:${stock.ts_code}`);
    setStatus({ text: `已选择 ${stock.name}（${stock.ts_code}）`, tone: "good" });
    if (load) {
      setView("single");
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
            <p className="eyebrow">Local A-Share Lab</p>
            <h1>量策研究终端</h1>
            <div className="desk-header-meta">
              <span>研究版</span>
              <span>本地研究环境</span>
              <span>数据日期：{latestBar?.date || form.endDate}</span>
              <span>{rows.length ? `${rows.length} 根日线` : "等待行情"}</span>
              <span>{marketBacktestActive ? "后台验证中" : sourceLabel}</span>
            </div>
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

      <ViewCommandBar
        view={view}
        form={form}
        status={status}
        busy={busy}
        marketBacktestActive={marketBacktestActive}
        syncProgress={syncProgress}
        syncProgressPolling={syncProgressPolling}
        marketBacktestJob={marketBacktestJob}
        onFormChange={updateForm}
        onCheckApi={checkApi}
        onSyncStockBasic={syncStockBasic}
        onSyncDaily={syncDaily}
        onSyncFundamentals={syncFundamentals}
        onSyncMarketDaily={syncMarketDaily}
        onSyncMarketFundamentals={syncMarketFundamentals}
        onLoadBars={() => withBusy(loadBars)}
        onRunMarketBacktest={runMarketBacktest}
        onRunScreener={() => runScreener(true)}
        onRunBacktest={runBacktest}
        onRunQualityAnalysis={runQualityAnalysis}
      />

      <section className={`page-workspace page-${view}`}>
          {view === "dashboard" ? (
            <DashboardPanel
              form={form}
              rows={rows}
              result={result}
              marketResult={marketResult}
              marketJob={marketBacktestJob}
              activePool={activePool}
              screenResults={screenResults}
              researchRun={researchRun}
              selectedDocsStrategy={selectedDocsStrategy}
              newsItems={newsItems}
              busy={busy}
              marketBusy={marketBacktestActive}
              onOpenView={setView}
              onRunMarket={() => runMarketBacktest({ targetView: "dashboard" })}
              onRunScreener={() => runScreener(true)}
              onRunSingle={runBacktest}
              onRunQuality={runQualityAnalysis}
            />
          ) : null}

          {view === "screen" ? (
            <ScreenerPanel
              filters={screenFilters}
              results={screenResults}
              pools={stockPools}
              activePool={activePool}
              activePoolId={activePoolId}
              activePoolMemberCodes={activePoolMemberCodes}
              busy={busy}
              marketBusy={marketBacktestActive}
              onFilterChange={updateScreenFilter}
              onRun={() => runScreener(true)}
              onSelect={selectCandidate}
              onPoolSelect={(poolId) => setActivePoolId(Number(poolId) || 0)}
              onCreatePool={createStockPool}
              onDeletePool={deleteActivePool}
              onAddToPool={addStocksToActivePool}
              onAddResultsToPool={() => addStocksToActivePool(screenResults.map((stock) => stock.ts_code))}
              onRemoveFromPool={removeStockFromActivePool}
              onSyncMarketFundamentals={syncMarketFundamentals}
              onRunPoolBacktest={() => {
                if (!activePool?.member_count) {
                  setStatus({ text: "标的池里还没有股票。", tone: "bad" });
                  return;
                }
                void runMarketBacktest({ poolId: activePool.id, poolName: activePool.name });
              }}
              newsItems={newsItems}
              newsMessage={newsMessage}
              marketMainline={marketMainline}
              tailCandidates={tailCandidates}
              onRefreshNews={refreshNews}
              onRefreshMainline={refreshMarketMainline}
              onRefreshTailCandidates={refreshTailCandidates}
            />
          ) : null}

          {view === "single" ? (
            <>
              <ContextDeck
                form={form}
                result={result}
                selectedDocsStrategy={selectedDocsStrategy}
                profile={profile}
                latestBar={latestBar}
                rows={rows}
                sourceLabel={sourceLabel}
                onSyncFundamentals={syncFundamentals}
                busy={busy}
              />
              <StrategyPanel
                form={form}
                metrics={metrics}
                rows={rows}
                trades={result?.trades || []}
                result={result}
                researchRuns={researchRuns}
                selectedDocsStrategy={selectedDocsStrategy}
                onChange={updateForm}
                onApplyDocsStrategy={applyDocsStrategy}
                onRefreshResearchRuns={() => refreshResearchRuns(selectedResearchRunId, true)}
                onSingleRun={runBacktest}
                onOpenMarket={() => setView("market")}
                busy={busy}
              />
            </>
          ) : null}

          {view === "market" ? (
            <MarketValidationPanel
              form={form}
              metrics={metrics}
              marketResult={marketResult}
              marketJob={marketBacktestJob}
              activePool={activePool}
              selectedDocsStrategy={selectedDocsStrategy}
              busy={busy}
              marketBusy={marketBacktestActive}
              onRun={runMarketBacktest}
            />
          ) : null}

          {view === "diagnostic" ? (
            <>
              <ContextDeck
                form={form}
                result={result}
                selectedDocsStrategy={selectedDocsStrategy}
                profile={profile}
                latestBar={latestBar}
                rows={rows}
                sourceLabel={sourceLabel}
                onSyncFundamentals={syncFundamentals}
                busy={busy}
              />
              <DiagnosticPanel analysis={qualityAnalysis} result={result} rows={rows} symbolTitle={symbolTitle} onRunQuality={runQualityAnalysis} busy={busy} />
            </>
          ) : null}
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
  return TABS.some(([key]) => key === value) ? value : "dashboard";
}

function getInitialSyncProgressTarget() {
  const saved = localStorage.getItem(SYNC_PROGRESS_TARGET_KEY);
  return saved === "daily_basic" ? "daily_basic" : "daily";
}

function getInitialMarketBacktestJob() {
  const jobId = localStorage.getItem(MARKET_BACKTEST_JOB_KEY);
  return jobId ? { jobId, status: "running", message: "正在恢复后台验证任务", progressPct: 0, processed: 0, total: 0 } : null;
}

function getInitialStockPoolId() {
  const saved = Number(localStorage.getItem(STOCK_POOL_KEY));
  return Number.isFinite(saved) && saved > 0 ? saved : 0;
}

function ViewCommandBar({
  view,
  form,
  status,
  busy,
  marketBacktestActive,
  syncProgress,
  syncProgressPolling,
  marketBacktestJob,
  onFormChange,
  onCheckApi,
  onSyncStockBasic,
  onSyncDaily,
  onSyncFundamentals,
  onSyncMarketDaily,
  onSyncMarketFundamentals,
  onLoadBars,
  onRunMarketBacktest,
  onRunScreener,
  onRunBacktest,
  onRunQualityAnalysis,
}) {
  const configs = {
    dashboard: {
      title: "总览页",
      detail: "只聚合证据，不承载全部操作",
      primaryLabel: marketBacktestActive ? "验证中" : "全市场验证",
      primaryIcon: <Play size={17} />,
      primaryAction: () => onRunMarketBacktest({ targetView: "dashboard" }),
      primaryDisabled: busy || marketBacktestActive,
      clusters: [
        {
          label: "入口",
          actions: [
            [<Activity size={15} />, "检测API", onCheckApi],
            [<Search size={15} />, "刷新候选", onRunScreener],
            [<Gauge size={15} />, "质量诊断", onRunQualityAnalysis],
          ],
        },
      ],
    },
    screen: {
      title: "选股池页",
      detail: "筛选、分组和消息面集中在此页",
      primaryLabel: "刷新候选",
      primaryIcon: <Search size={17} />,
      primaryAction: onRunScreener,
      primaryDisabled: busy,
      clusters: [
        {
          label: "数据",
          actions: [
            [<Activity size={15} />, "检测API", onCheckApi],
            [<RefreshCw size={15} />, "同步列表", onSyncStockBasic],
            [<Database size={15} />, "全A基本面", onSyncMarketFundamentals],
          ],
        },
      ],
    },
    single: {
      title: "单票验证页",
      detail: "当前标的的行情、参数和交易流水",
      primaryLabel: "单票回测",
      primaryIcon: <LineChart size={17} />,
      primaryAction: onRunBacktest,
      primaryDisabled: busy,
      clusters: [
        {
          label: "当前标的",
          actions: [
            [<UploadCloud size={15} />, "单票日线", onSyncDaily],
            [<Database size={15} />, "单票基本面", onSyncFundamentals],
            [<Database size={15} />, "载入行情", onLoadBars],
          ],
        },
      ],
    },
    market: {
      title: "全市场验证页",
      detail: "批量回测、进度和样本审计独立运行",
      primaryLabel: marketBacktestActive ? "验证中" : "全市场验证",
      primaryIcon: <BarChart3 size={17} />,
      primaryAction: () => onRunMarketBacktest({ targetView: "market" }),
      primaryDisabled: busy || marketBacktestActive,
      clusters: [
        {
          label: "预热",
          actions: [
            [<UploadCloud size={15} />, "补齐日线", onSyncMarketDaily],
            [<Database size={15} />, "全A基本面", onSyncMarketFundamentals],
            [<Activity size={15} />, "检测API", onCheckApi],
          ],
        },
      ],
    },
    diagnostic: {
      title: "复盘诊断页",
      detail: "质量评分、AI/本地规则和风险解释",
      primaryLabel: "质量诊断",
      primaryIcon: <Gauge size={17} />,
      primaryAction: onRunQualityAnalysis,
      primaryDisabled: busy,
      clusters: [
        {
          label: "输入",
          actions: [
            [<UploadCloud size={15} />, "单票日线", onSyncDaily],
            [<Database size={15} />, "单票基本面", onSyncFundamentals],
            [<LineChart size={15} />, "单票回测", onRunBacktest],
          ],
        },
      ],
    },
  };
  const config = configs[view] || configs.dashboard;
  const showSymbol = ["dashboard", "single", "diagnostic"].includes(view);

  return (
    <section className={`page-command page-command-${view}`}>
      <div className="page-command-head">
        <div className="service-line page-service-line">
          <i className={`pulse ${status.tone}`} />
          <span>{status.text}</span>
        </div>
        <div>
          <strong>{config.title}</strong>
          <span>{config.detail}</span>
        </div>
      </div>
      <div className={`page-command-inputs ${showSymbol ? "with-symbol" : "date-only"}`}>
        {showSymbol ? <TextField label="当前标的" value={form.tsCode} onChange={(value) => onFormChange("tsCode", value.toUpperCase())} /> : null}
        <TextField label="开始日期" type="date" value={form.startDate} onChange={(value) => onFormChange("startDate", value)} />
        <TextField label="结束日期" type="date" value={form.endDate} onChange={(value) => onFormChange("endDate", value)} />
      </div>
      <button className="primary-button page-primary-action" type="button" onClick={config.primaryAction} disabled={config.primaryDisabled}>
        {config.primaryIcon}
        {config.primaryLabel}
      </button>
      <div className="page-command-actions" aria-label={`${config.title}快捷操作`}>
        {config.clusters.map((cluster) => (
          <Fragment key={cluster.label}>
          <ActionCluster label={cluster.label}>
            {cluster.actions.map(([icon, label, onClick]) => (
              <Fragment key={label}>
                <ActionButton icon={icon} label={label} onClick={onClick} disabled={busy} compact />
              </Fragment>
            ))}
          </ActionCluster>
          </Fragment>
        ))}
      </div>
      <SyncProgressStrip progress={syncProgress} polling={syncProgressPolling} />
      <MarketBacktestProgressStrip job={marketBacktestJob} />
    </section>
  );
}

function ContextDeck({ form, result, selectedDocsStrategy, profile, latestBar, rows, sourceLabel, onSyncFundamentals, busy }) {
  return (
    <section className="page-context-grid" aria-label="当前研究上下文">
      <FundamentalsPanel profile={profile} latestBar={latestBar} dataBars={rows.length} sourceLabel={sourceLabel} onSync={onSyncFundamentals} busy={busy} />
      <StrategyBrief form={form} result={result} selectedDocsStrategy={selectedDocsStrategy} />
      <section className="rail-panel">
        <PanelTitle icon={<Gauge size={17} />} title="指标快照" right={latestBar?.date || "无数据"} />
        <IndicatorTape bar={latestBar} />
      </section>
    </section>
  );
}

function DashboardPanel({
  form,
  rows,
  result,
  marketResult,
  marketJob,
  activePool,
  screenResults,
  researchRun,
  selectedDocsStrategy,
  newsItems,
  busy,
  marketBusy,
  onOpenView,
  onRunMarket,
  onRunScreener,
  onRunSingle,
  onRunQuality,
}) {
  const dashboardMetrics = buildDashboardMetrics({ result, marketResult, researchRun, rows, activePool });
  const equityCurve = buildDashboardEquityCurve(result, researchRun);
  const auditRows = buildDashboardAuditRows(marketResult, researchRun);
  const healthItems = buildDashboardHealth({ rows, result, marketResult, marketJob, activePool, form, researchRun });
  const headline = selectedDocsStrategy?.label || researchRun?.label || "本地 A 股研究工作台";
  const jobActive = ["queued", "running"].includes(marketJob?.status);
  const workflowItems = buildDashboardWorkflow({ rows, screenResults, result, marketResult, marketJob, newsItems });

  return (
    <section className="dashboard-layout terminal-dashboard">
      <section className="workspace-panel dashboard-hero terminal-hero">
        <div>
          <p className="eyebrow">Research Terminal</p>
          <h2>{headline}</h2>
          <strong>{buildDashboardVerdict(result, marketResult, researchRun, jobActive)}</strong>
          <span>
            {form.startDate} / {form.endDate} · {activePool?.name || "全市场"} · {rows.length ? `${rows.length} 根日线` : "等待行情"}
          </span>
        </div>
        <div className="terminal-hero-readout">
          <span>
            <em>研究项目</em>
            <strong>{activePool?.name || form.tsCode}</strong>
          </span>
          <span>
            <em>策略来源</em>
            <strong>{selectedDocsStrategy?.source ? "docs" : "本地参数"}</strong>
          </span>
          <span>
            <em>后台任务</em>
            <strong>{jobActive ? "运行中" : "空闲"}</strong>
          </span>
          <span>
            <em>消息样本</em>
            <strong>{formatInteger(newsItems.length)}</strong>
          </span>
        </div>
      </section>

      <section className="metric-grid dashboard-metrics dashboard-kpi-strip">
        {dashboardMetrics.map((item) => (
          <Fragment key={item.label}>
            <MetricTile {...item} />
          </Fragment>
        ))}
      </section>

      <section className="dashboard-action-row">
        <button className="primary-button" type="button" onClick={onRunMarket} disabled={busy || marketBusy}>
          <Play size={16} /> 全市场验证
        </button>
        <button className="ghost-button" type="button" onClick={onRunScreener} disabled={busy}>
          <Search size={16} /> 刷新候选
        </button>
        <button className="ghost-button" type="button" onClick={onRunSingle} disabled={busy}>
          <LineChart size={16} /> 单票回测
        </button>
        <button className="ghost-button" type="button" onClick={onRunQuality} disabled={busy}>
          <Gauge size={16} /> 质量诊断
        </button>
      </section>

      <section className="dashboard-evidence-grid">
        <section className="workspace-panel dashboard-equity-panel terminal-chart-panel">
          <PanelTitle icon={<LineChart size={17} />} title="权益与回撤" right={equityCurve.length ? `${equityCurve.length} 个交易日` : "等待回测"} />
          {equityCurve.length ? <PortfolioEquityChart points={equityCurve} /> : <div className="quant-image-empty">等待回测结果</div>}
        </section>
        <DashboardRiskSignals healthItems={healthItems} />
      </section>

      <section className="dashboard-chart-grid terminal-mini-charts">
        <section className="workspace-panel">
          <PanelTitle icon={<BarChart3 size={17} />} title="收益分布" right={auditRows.length ? `${auditRows.length} 个样本` : "等待全市场"} />
          <ReturnDistributionSvg rows={auditRows} />
        </section>
        <section className="workspace-panel">
          <PanelTitle icon={<Gauge size={17} />} title="收益-回撤散点" right="尾部审计" />
          <RiskScatterSvg rows={auditRows} />
        </section>
        <section className="workspace-panel">
          <PanelTitle icon={<TrendingUp size={17} />} title="收益排名曲线" right={auditRows.length ? "从弱到强" : "等待验证"} />
          <RankedReturnSvg rows={auditRows} />
        </section>
      </section>

      <section className="dashboard-bottom-grid terminal-bottom-grid">
        <DashboardFocusTable auditRows={auditRows} candidates={screenResults} onOpenView={onOpenView} />
        <DashboardWorkflowPanel items={workflowItems} onOpenView={onOpenView} />
      </section>
    </section>
  );
}

function DashboardRiskSignals({ healthItems }) {
  return (
    <section className="workspace-panel terminal-risk-panel">
      <PanelTitle icon={<ShieldCheck size={17} />} title="风险信号" right="研究态" />
      <div className="risk-signal-table">
        <div className="risk-signal-head">
          <span>信号</span>
          <span>状态</span>
          <span>强度</span>
          <span>阈值/说明</span>
        </div>
        {healthItems.map((item) => (
          <div key={item.label} className={`risk-signal-row ${item.tone}`}>
            <span>
              <i />
              {item.label}
            </span>
            <strong>{item.value}</strong>
            <b aria-label={`${item.label}强度`}>
              {Array.from({ length: 6 }).map((_, index) => (
                <em key={index} className={index < riskStrength(item.tone) ? "on" : ""} />
              ))}
            </b>
            <small>{item.detail}</small>
          </div>
        ))}
      </div>
    </section>
  );
}

function DashboardWorkflowPanel({ items, onOpenView }) {
  return (
    <section className="workspace-panel terminal-workflow-panel">
      <PanelTitle icon={<Activity size={17} />} title="研究流程与任务状态" right="本地闭环" />
      <div className="terminal-step-flow">
        {items.map((item, index) => (
          <button key={item.label} type="button" className={`terminal-step ${item.tone}`} onClick={() => onOpenView(item.target)}>
            <span>{item.label}</span>
            <strong>{item.status}</strong>
            <em>{item.detail}</em>
            {index < items.length - 1 ? <i /> : null}
          </button>
        ))}
      </div>
      <div className="terminal-task-table">
        <div className="terminal-task-head">
          <span>任务</span>
          <span>状态</span>
          <span>进度</span>
          <span>入口</span>
        </div>
        {items.map((item) => (
          <button key={item.label} type="button" className="terminal-task-row" onClick={() => onOpenView(item.target)}>
            <span>{item.label}</span>
            <strong className={item.tone}>{item.status}</strong>
            <b>
              <i style={{ "--progress": `${item.progress}%` }} />
            </b>
            <em>{item.action}</em>
          </button>
        ))}
      </div>
    </section>
  );
}

function DashboardFocusTable({ auditRows, candidates, onOpenView }) {
  const rows = auditRows.length ? auditRows.slice(0, 10) : candidates.slice(0, 10);
  const auditMode = Boolean(auditRows.length);
  return (
    <section className="workspace-panel">
      <PanelTitle icon={<Database size={17} />} title={auditMode ? "样本收益审计" : "因子/候选证据"} right={auditMode ? `${rows.length} 个样本` : `${rows.length} 个候选`} />
      <div className="table-wrap dashboard-focus-table">
        <table>
          <thead>
            <tr>
              <th>排名</th>
              <th>代码</th>
              <th>名称</th>
              <th>行业</th>
              <th>{auditMode ? "收益" : "评分"}</th>
              <th>{auditMode ? "回撤" : "技术"}</th>
              <th>{auditMode ? "胜率" : "信号"}</th>
            </tr>
          </thead>
          <tbody>
            {rows.length ? (
              rows.map((item, index) => (
                <tr key={item.ts_code || `${item.name}-${index}`}>
                  <td>{index + 1}</td>
                  <td>{item.ts_code}</td>
                  <td>{item.name || "--"}</td>
                  <td>{item.industry || item.market || "--"}</td>
                  {auditMode ? (
                    <>
                      <td className={item.totalReturn >= 0 ? "positive" : "negative"}>{formatPercent(item.totalReturn, 2)}</td>
                      <td>{formatPercent(item.maxDrawdown, 2)}</td>
                      <td>{formatPercent(item.winRate, 1)}</td>
                    </>
                  ) : (
                    <>
                      <td>{candidateScore(item)}</td>
                      <td>{formatNumber(item.technical_score)}</td>
                      <td>{item.technical_signal || item.rating || "--"}</td>
                    </>
                  )}
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan="7" className="empty-state">
                  等待筛选或验证结果
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="dashboard-table-actions">
        <button className="ghost-button" type="button" onClick={() => onOpenView(auditMode ? "market" : "screen")}>
          {auditMode ? <BarChart3 size={16} /> : <Filter size={16} />}
          {auditMode ? "打开全市场" : "打开选股池"}
        </button>
      </div>
    </section>
  );
}

function ScreenerPanel({
  filters,
  results,
  pools,
  activePool,
  activePoolId,
  activePoolMemberCodes,
  busy,
  marketBusy,
  onFilterChange,
  onRun,
  onSelect,
  onPoolSelect,
  onCreatePool,
  onDeletePool,
  onAddToPool,
  onAddResultsToPool,
  onRemoveFromPool,
  onSyncMarketFundamentals,
  onRunPoolBacktest,
  newsItems,
  newsMessage,
  marketMainline,
  tailCandidates,
  onRefreshNews,
  onRefreshMainline,
  onRefreshTailCandidates,
}) {
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
          <SelectField label="排行口径" value={filters.rank_by} onChange={(value) => onFilterChange("rank_by", value)} options={RANK_OPTIONS} />
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
        <div className="screen-action-row">
          <button className="primary-button" type="button" onClick={onSyncMarketFundamentals} disabled={busy}>
            <Database size={17} /> 载入全A基本面
          </button>
          <span>同步公司列表、每日估值和财务指标后重新评分排名</span>
        </div>
        <QualityLeaderboard results={results} onSelect={onSelect} />
        <ResearchPoolConsole
          pools={pools}
          activePool={activePool}
          activePoolId={activePoolId}
          results={results}
          busy={busy}
          marketBusy={marketBusy}
          onPoolSelect={onPoolSelect}
          onCreatePool={onCreatePool}
          onDeletePool={onDeletePool}
          onAddToPool={onAddToPool}
          onAddResultsToPool={onAddResultsToPool}
          onRemoveFromPool={onRemoveFromPool}
          onRunPoolBacktest={onRunPoolBacktest}
        />
        <CandidateCards results={results} activePool={activePool} activePoolMemberCodes={activePoolMemberCodes} onSelect={onSelect} onAddToPool={onAddToPool} busy={busy} />
      </section>
      <aside className="screen-rail">
        <MarketSignalPanel
          mainline={marketMainline}
          tailCandidates={tailCandidates}
          busy={busy}
          onRefreshMainline={onRefreshMainline}
          onRefreshTailCandidates={onRefreshTailCandidates}
        />
        <NewsPulsePanel items={newsItems} message={newsMessage} busy={busy} onRefresh={onRefreshNews} />
      </aside>
    </section>
  );
}

function ResearchPoolConsole({
  pools,
  activePool,
  activePoolId,
  results,
  busy,
  marketBusy,
  onPoolSelect,
  onCreatePool,
  onDeletePool,
  onAddToPool,
  onAddResultsToPool,
  onRemoveFromPool,
  onRunPoolBacktest,
}) {
  const [poolName, setPoolName] = useState("");
  const [manualCodes, setManualCodes] = useState("");
  const members = activePool?.members || [];
  const canUsePool = Boolean(activePoolId);

  async function submitPool() {
    const name = poolName.trim();
    if (!name) return;
    await onCreatePool(name);
    setPoolName("");
  }

  async function submitMembers() {
    const codes = parseStockCodes(manualCodes);
    if (!codes.length) return;
    await onAddToPool(codes);
    setManualCodes("");
  }

  return (
    <section className="pool-console" aria-label="自选标的池">
      <div className="pool-head">
        <PanelTitle icon={<Layers3 size={17} />} title="自选标的池" right={activePool ? `${members.length} 只` : "未选择"} />
        <div className="pool-actions">
          <button className="ghost-button" type="button" onClick={onAddResultsToPool} disabled={!canUsePool || busy || !results.length}>
            <Plus size={15} /> 候选入池
          </button>
          <button className="primary-button" type="button" onClick={onRunPoolBacktest} disabled={!canUsePool || !members.length || busy || marketBusy}>
            <Play size={15} /> 验证池子
          </button>
        </div>
      </div>
      <div className="pool-form-grid">
        <label className="field">
          当前池
          <select value={pools.length ? activePoolId || "" : ""} onChange={(event) => onPoolSelect(event.target.value)} disabled={!pools.length}>
            {pools.length ? (
              pools.map((pool) => (
                <option key={pool.id} value={pool.id}>
                  {pool.name}（{pool.member_count}）
                </option>
              ))
            ) : (
              <option value="">暂无池子</option>
            )}
          </select>
        </label>
        <label className="field">
          新建池
          <span className="inline-field">
            <input value={poolName} placeholder="低估值反转、半导体趋势..." onChange={(event) => setPoolName(event.target.value)} />
            <button className="icon-button" type="button" onClick={submitPool} disabled={busy || !poolName.trim()} title="新建标的池">
              <FolderPlus size={16} />
            </button>
          </span>
        </label>
        <label className="field">
          手动加标的
          <span className="inline-field">
            <input value={manualCodes} placeholder="600703.SH, 002594.SZ" onChange={(event) => setManualCodes(event.target.value)} />
            <button className="icon-button" type="button" onClick={submitMembers} disabled={!canUsePool || busy || !manualCodes.trim()} title="加入当前池">
              <Plus size={16} />
            </button>
          </span>
        </label>
        <button className="ghost-button danger-action" type="button" onClick={onDeletePool} disabled={!canUsePool || busy}>
          <Trash2 size={15} /> 删除池
        </button>
      </div>
      <div className="pool-member-strip">
        {members.length ? (
          members.map((stock) => (
            <button key={stock.ts_code} className="pool-member-chip" type="button" onClick={() => onRemoveFromPool(stock.ts_code)} disabled={busy} title="从当前池移除">
              <strong>{stock.ts_code}</strong>
              <span>{stock.name}</span>
              <Trash2 size={13} />
            </button>
          ))
        ) : (
          <span className="pool-empty">当前池暂无标的</span>
        )}
      </div>
    </section>
  );
}

function QualityLeaderboard({ results, onSelect }) {
  const leaders = useMemo(
    () => [...results].sort((a, b) => (b.fundamental_score || 0) - (a.fundamental_score || 0)).slice(0, 5),
    [results],
  );
  if (!leaders.length) return null;
  return (
    <section className="quality-board" aria-label="基本面质量排行榜">
      <div className="quality-board-head">
        <PanelTitle icon={<ShieldCheck size={17} />} title="基本面质量榜" right="非投资建议" />
        <span>盈利、成长、负债、估值、流动性五维评分</span>
      </div>
      <div className="quality-rank-grid">
        {leaders.map((stock, index) => (
          <button key={stock.ts_code} className="quality-rank-card" type="button" onClick={() => onSelect(stock, true)}>
            <em>#{index + 1}</em>
            <strong>{stock.name}</strong>
            <span>{stock.ts_code}</span>
            <b className={fundamentalGradeClass(stock.fundamental_grade)}>{stock.fundamental_grade || "待同步"}</b>
            <i>{stock.fundamental_score || 0}</i>
          </button>
        ))}
      </div>
    </section>
  );
}

function CandidateCards({ results, activePool, activePoolMemberCodes, onSelect, onAddToPool, busy }) {
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
            <div className="quality-line">
              <span className={fundamentalGradeClass(stock.fundamental_grade)}>{stock.fundamental_grade || "待同步"}</span>
              <QualityBreakdown breakdown={stock.fundamental_breakdown} />
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
              <div className="candidate-actions">
                <button className="row-button" type="button" onClick={() => onAddToPool([stock.ts_code])} disabled={!activePool || activePoolMemberCodes.has(stock.ts_code) || busy}>
                  {activePoolMemberCodes.has(stock.ts_code) ? "已入池" : "入池"}
                </button>
                <button className="row-button" type="button" onClick={() => onSelect(stock, true)}>
                  单票验证
                </button>
              </div>
            </div>
          </article>
        );
      })}
    </div>
  );
}

function MarketSignalPanel({ mainline, tailCandidates, busy, onRefreshMainline, onRefreshTailCandidates }) {
  const industries = mainline?.industries?.top || [];
  const candidates = tailCandidates?.candidates || [];
  return (
    <section className="workspace-panel signal-panel">
      <PanelTitle icon={<TrendingUp size={17} />} title="主线/尾盘" right={tailCandidates?.asOf ? tailCandidates.asOf.slice(11, 16) : "未刷新"} />
      <div className="signal-actions">
        <button className="ghost-button" type="button" onClick={onRefreshMainline} disabled={busy}>
          <RefreshCw size={16} /> 主线
        </button>
        <button className="primary-button" type="button" onClick={onRefreshTailCandidates} disabled={busy}>
          <Filter size={16} /> 尾盘候选
        </button>
      </div>
      <div className="signal-source-row">
        <span className={mainline?.sources?.thsHotReason ? "good" : ""}>同花顺热点</span>
        <span className={mainline?.sources?.eastmoneyIndustry ? "good" : ""}>东财行业</span>
        <span className={tailCandidates?.sources?.tencentQuote ? "good" : ""}>腾讯行情</span>
      </div>
      {candidates.length ? (
        <div className="signal-list">
          {candidates.slice(0, 8).map((item) => (
            <article key={item.tsCode}>
              <div>
                <strong>{item.name || item.tsCode}</strong>
                <span>{item.tsCode}</span>
              </div>
              <b>{formatPercentPoint(item.changePct)}</b>
              <em>量比 {formatNumber(item.volumeRatio)} / 换手 {formatPercentPoint(item.turnoverPct)}</em>
              {item.reason ? <i>{item.reason}</i> : null}
            </article>
          ))}
        </div>
      ) : (
        <div className="news-empty compact">{tailCandidates?.status === "empty" ? "当前阈值下暂无尾盘候选" : "等待尾盘候选"}</div>
      )}
      {industries.length ? (
        <div className="industry-strip">
          {industries.slice(0, 6).map((item) => (
            <span key={item.code || item.name}>
              <strong>{item.name}</strong>
              <em>{formatPercentPoint(item.changePct)}</em>
            </span>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function NewsPulsePanel({ items, message, busy, onRefresh }) {
  return (
    <section className="workspace-panel news-panel">
      <PanelTitle icon={<Activity size={17} />} title="消息面" right={items.length ? `${items.length} 条` : "未刷新"} />
      <button className="ghost-button wide-action" type="button" onClick={() => onRefresh()} disabled={busy}>
        <RefreshCw size={16} /> 刷新财联社/见闻/雪球
      </button>
      {message ? <div className="news-note">{message}</div> : null}
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
        <div className="news-empty">{message || "暂无真实消息面数据"}</div>
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

function QualityBreakdown({ breakdown }) {
  const entries = Object.entries(breakdown || {}).slice(0, 5);
  if (!entries.length) return <em className="quality-empty">等待基本面</em>;
  return (
    <div className="quality-breakdown">
      {entries.map(([label, value]) => (
        <span key={label}>
          {label}
          <strong>{value}</strong>
        </span>
      ))}
    </div>
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

function DocsStrategyChooser({ runs, selected, onApply, onRefresh, busy }) {
  const cards = useMemo(() => buildDocsStrategyCards(runs), [runs]);
  return (
    <section className="workspace-panel docs-strategy-panel">
      <div className="docs-strategy-head">
        <PanelTitle icon={<FileSearch size={17} />} title="docs 策略" right={cards.length ? `${cards.length} 个研究运行` : "等待 docs"} />
        <button className="ghost-button" type="button" onClick={onRefresh} disabled={busy}>
          <RefreshCw size={16} /> 刷新 docs
        </button>
      </div>
      {cards.length ? (
        <div className="docs-strategy-grid">
          {cards.map((run) => {
            const active = selected?.runId === run.runId;
            return (
              <button key={run.runId} type="button" className={`docs-strategy-card ${active ? "active" : ""}`} onClick={() => onApply(run.runId)} disabled={busy}>
                <span className={`docs-strategy-status ${strategyStatusClass(run.status)}`}>{strategyStatusText(run.status)}</span>
                <strong>{run.label || run.strategyName || run.runId}</strong>
                <em>{run.runId}</em>
                <div className="docs-strategy-meta">
                  <span>年化 {formatPercent(run.metrics?.annualizedReturn, 2)}</span>
                  <span>回撤 {formatPercent(run.metrics?.maxDrawdown, 2)}</span>
                  <span>盈亏比 {formatProfitLossRatio(run.metrics?.profitLossRatio)}</span>
                </div>
              </button>
            );
          })}
        </div>
      ) : (
        <div className="empty-state docs-strategy-empty">未读取到 docs/research/runs 策略</div>
      )}
    </section>
  );
}

function StrategyPanel({
  form,
  metrics,
  rows,
  trades,
  result,
  researchRuns,
  selectedDocsStrategy,
  onChange,
  onApplyDocsStrategy,
  onRefreshResearchRuns,
  onSingleRun,
  onOpenMarket,
  busy,
}) {
  return (
    <section className="strategy-layout">
      <DocsStrategyChooser
        runs={researchRuns}
        selected={selectedDocsStrategy}
        onApply={onApplyDocsStrategy}
        onRefresh={onRefreshResearchRuns}
        busy={busy}
      />

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
            <SelectField label="入场模型" value={form.entryMode} onChange={(value) => onChange("entryMode", value)} options={ENTRY_MODE_OPTIONS} />
            <TextField label="快线MA" type="number" value={form.trendFastPeriod} onChange={(value) => onChange("trendFastPeriod", value)} />
            <TextField label="慢线MA" type="number" value={form.trendSlowPeriod} onChange={(value) => onChange("trendSlowPeriod", value)} />
            <TextField label="长线MA" type="number" value={form.trendLongPeriod} onChange={(value) => onChange("trendLongPeriod", value)} />
            <TextField label="量均周期" type="number" value={form.volumeMaPeriod} onChange={(value) => onChange("volumeMaPeriod", value)} />
            <TextField label="尾盘涨幅下限%" type="number" value={form.tailEntryMinPctChg} onChange={(value) => onChange("tailEntryMinPctChg", value)} />
            <TextField label="尾盘涨幅上限%" type="number" value={form.tailEntryMaxPctChg} onChange={(value) => onChange("tailEntryMaxPctChg", value)} />
            <TextField label="近涨停天数" type="number" value={form.tailPriorLimitUpLookback} onChange={(value) => onChange("tailPriorLimitUpLookback", value)} />
            <TextField label="尾盘量比" type="number" value={form.tailMinVolumeRatio} onChange={(value) => onChange("tailMinVolumeRatio", value)} />
            <TextField label="尾盘换手%" type="number" value={form.tailMinTurnoverRatePct} onChange={(value) => onChange("tailMinTurnoverRatePct", value)} />
            <TextField label="涨停判定%" type="number" value={form.tailLimitUpPct} onChange={(value) => onChange("tailLimitUpPct", value)} />
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
          <div className="action-pair">
            <button className="primary-button wide-action" type="button" onClick={onSingleRun} disabled={busy}>
              <Play size={17} /> 运行单票验证
            </button>
            <button className="ghost-button wide-action" type="button" onClick={onOpenMarket} disabled={busy}>
              <Database size={16} /> 转到全市场验证
            </button>
          </div>
        </section>
      </section>

      <section className="workspace-panel">
        <PanelTitle icon={<Activity size={17} />} title="交易流水" right={result ? `${result.trades.length} 笔` : "0 笔"} />
        <TradeTable trades={result?.trades || []} />
      </section>
    </section>
  );
}

function MarketValidationPanel({ form, metrics, marketResult, marketJob, activePool, selectedDocsStrategy, busy, marketBusy, onRun }) {
  const endDate = form.endDate || dateToday();
  const year = Number(String(endDate).slice(0, 4)) || new Date().getFullYear();
  const yearStart = `${year}-01-01`;
  const threeYearStart = dateYearsBefore(endDate, 3);
  const runDisabled = busy || marketBusy;
  const routeCards = [
    ["单票", form.tsCode || "--", "先在单票验证页确认策略信号、K线层和交易流水"],
    ["年度全市场", `${yearStart} / ${endDate}`, "同一策略参数应用到当前年份全A样本"],
    ["三年全市场", `${threeYearStart} / ${endDate}`, "拉长窗口后检查收益、回撤和尾部样本"],
  ];

  return (
    <section className="market-validation-layout">
      <section className="workspace-panel market-validation-hero">
        <div>
          <p className="eyebrow">Scale Out Validation</p>
          <h2>从单票到全市场</h2>
          <strong>{selectedDocsStrategy?.label || "待选择 docs 策略"}</strong>
          <span>
            当前参数会原样应用到批量验证；这里只生成研究证据，不构成交易指令。
          </span>
        </div>
        <div className="market-validation-actions">
          <button className="primary-button" type="button" onClick={() => onRun({ targetView: "market" })} disabled={runDisabled}>
            <Play size={16} /> 当前区间全市场
          </button>
          <button className="ghost-button" type="button" onClick={() => onRun({ targetView: "market", startDate: yearStart, endDate })} disabled={runDisabled}>
            <BarChart3 size={16} /> 当前年份
          </button>
          <button className="ghost-button" type="button" onClick={() => onRun({ targetView: "market", startDate: threeYearStart, endDate })} disabled={runDisabled}>
            <Database size={16} /> 三年全市场
          </button>
          <button
            className="ghost-button"
            type="button"
            onClick={() => onRun({ targetView: "market", poolId: activePool?.id, poolName: activePool?.name })}
            disabled={runDisabled || !activePool?.member_count}
          >
            <Layers3 size={16} /> 验证当前池
          </button>
        </div>
      </section>

      <section className="metric-grid">
        {metrics.map((item) => (
          <MetricTile key={item.label} {...item} />
        ))}
      </section>

      <section className="workspace-panel">
        <PanelTitle icon={<GitBranch size={17} />} title="手动验证路径" right={marketBusy ? "后台运行中" : "点到面"} />
        <div className="market-route-grid">
          {routeCards.map(([label, value, detail]) => (
            <article key={label} className="market-route-card">
              <span>{label}</span>
              <strong>{value}</strong>
              <em>{detail}</em>
            </article>
          ))}
        </div>
      </section>

      <MarketBacktestPanel result={marketResult} job={marketJob} />
    </section>
  );
}

function ResearchStagePanel({ overview, error, onRefresh, onOpenRun, busy }) {
  const stage = overview?.stage || {};
  const target = overview?.target || {};
  const integratedRuns = overview?.integratedRuns || [];
  const sessions = overview?.activeSessions || [];
  const inbox = overview?.evidenceInbox || {};
  const unmergedRuns = inbox.unmergedRuns || [];
  const sessionEvidence = inbox.sessionEvidence || [];
  const warnings = overview?.integrationWarnings || [];
  const stageTitle = stage.stageId || overview?.activeStage?.stageId || "研究阶段未载入";
  const gateItems = [
    ["年化收益", `>= ${formatPercent(target.annualizedReturn, 0)}`, "当前阶段硬门槛"],
    ["最大回撤", `< ${formatPercent(target.maxAbsDrawdown, 0)}`, "按绝对值审计"],
    ["盈亏比", `>= ${formatNumber(target.profitLossRatio)}:1`, "已完成交易口径"],
    ["滚动窗口", ">= 5/7", "仅整合证据可判定"],
  ];

  return (
    <section className="research-stage-layout">
      <section className="workspace-panel research-stage-hero">
        <div>
          <p className="eyebrow">Parallel Research Control</p>
          <h2>{stageTitle}</h2>
          <strong>{stage.status || "unknown"} · {overview?.currentMainline || "未声明主线 run"}</strong>
          <span>{stage.objective || overview?.activeStage?.objective || "等待阶段目标"}</span>
        </div>
        <div className="research-stage-actions">
          <button className="ghost-button" type="button" onClick={onRefresh} disabled={busy}>
            <RefreshCw size={16} /> 刷新阶段证据
          </button>
          {error ? <span className="stage-error">{error}</span> : <span>{overview?.officialConclusionSource || "正式结论只来自整合证据"}</span>}
        </div>
      </section>

      <section className="metric-grid stage-metrics">
        <MetricTile label="正式 run" value={formatInteger(overview?.integratedRunCount)} tone="neutral" sub="已写入 research-runs.json" />
        <MetricTile label="并行 session" value={formatInteger(overview?.sessionCount)} tone={sessions.length ? "good" : "neutral"} sub={stage.sessionsDirExists ? "sessions/ 已存在" : "等待 session 认领"} />
        <MetricTile label="待整合证据" value={formatInteger(inbox.count)} tone={inbox.count ? "bad" : "good"} sub="不参与阶段结论" />
        <MetricTile label="长期目标" value={formatPercent(overview?.ultimateTarget?.annualizedReturn, 0)} tone="neutral" sub={`终极盈亏比 ${formatNumber(overview?.ultimateTarget?.profitLossRatio)}:1`} />
      </section>

      <div className="research-stage-grid">
        <section className="workspace-panel stage-gate-panel">
          <PanelTitle icon={<ClipboardCheck size={17} />} title="阶段闸门" right="只读目标" />
          <div className="stage-gate-grid">
            {gateItems.map(([label, value, detail]) => (
              <span key={label}>
                <em>{label}</em>
                <strong>{value}</strong>
                <b>{detail}</b>
              </span>
            ))}
          </div>
          <StageList title="优先验证假设" items={stage.priorityHypotheses} />
          <StageList title="禁止重复尝试" items={stage.forbiddenAttempts} danger />
        </section>

        <section className="workspace-panel stage-session-panel">
          <PanelTitle icon={<GitBranch size={17} />} title="并行 Session" right={sessions.length ? `${sessions.length} 条线` : "未认领"} />
          <div className="stage-session-stack">
            {sessions.length ? (
              sessions.map((session) => (
                <Fragment key={session.sessionId}>
                  <ResearchSessionCard session={session} />
                </Fragment>
              ))
            ) : (
              <div className="stage-empty">
                <strong>当前阶段还没有 session 目录</strong>
                <span>{stage.sessionsDir || "docs/research/stages/<stage>/sessions"} 出现后会自动归入这里。</span>
              </div>
            )}
          </div>
        </section>
      </div>

      <section className="workspace-panel">
        <PanelTitle icon={<Inbox size={17} />} title="证据收件箱" right={inbox.count ? `${inbox.count} 项待复核` : "清洁"} />
        {warnings.length ? (
          <div className="stage-warning-strip">
            {warnings.map((item) => (
              <span key={item}>
                <AlertTriangle size={14} /> {item}
              </span>
            ))}
          </div>
        ) : null}
        <ResearchInbox runs={unmergedRuns} sessionEvidence={sessionEvidence} onOpenRun={onOpenRun} busy={busy} />
      </section>

      <section className="workspace-panel">
        <PanelTitle icon={<FileSearch size={17} />} title="正式 Run 对比账本" right={`${integratedRuns.length} 条`} />
        <ResearchRunLedger runs={integratedRuns} onOpenRun={onOpenRun} busy={busy} />
      </section>
    </section>
  );
}

function StageList({ title, items = [], danger = false }) {
  return (
    <div className={`stage-list${danger ? " danger" : ""}`}>
      <strong>{title}</strong>
      {items.length ? (
        <ul>
          {items.slice(0, 5).map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <span>暂无结构化条目</span>
      )}
    </div>
  );
}

function ResearchSessionCard({ session }) {
  return (
    <article className={`stage-session-card ${session.hasEvidence ? "has-evidence" : ""}`}>
      <div>
        <strong>{session.topic || session.sessionId}</strong>
        <span>{session.sessionId}</span>
      </div>
      <b>{session.status || "进行中"}</b>
      <p>{session.question || session.hypothesis || "session.md 暂未写入研究问题。"}</p>
      <em>{session.hasEvidence ? "已有 evidence.md，等待整合复核" : "暂无证据文件"}</em>
    </article>
  );
}

function ResearchInbox({ runs, sessionEvidence, onOpenRun, busy }) {
  if (!runs.length && !sessionEvidence.length) {
    return (
      <div className="stage-empty inline">
        <strong>暂无待整合证据</strong>
        <span>正式结论目前只来自 research-runs.json。</span>
      </div>
    );
  }
  return (
    <div className="stage-inbox-grid">
      <div className="table-wrap stage-run-table">
        <table>
          <thead>
            <tr>
              <th>待整合 run</th>
              <th>状态</th>
              <th>年化</th>
              <th>回撤</th>
              <th>盈亏比</th>
              <th>证据边界</th>
              <th>动作</th>
            </tr>
          </thead>
          <tbody>
            {runs.length ? (
              runs.map((run) => (
                <Fragment key={run.runId}>
                  <ResearchRunRow run={run} onOpenRun={onOpenRun} busy={busy} />
                </Fragment>
              ))
            ) : (
              <tr>
                <td colSpan="7" className="empty-state">没有发现当前阶段未整合 run</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="stage-session-evidence">
        {sessionEvidence.length ? (
          sessionEvidence.map((session) => (
            <span key={session.sessionId}>
              <strong>{session.sessionId}</strong>
              <em>{session.evidenceSummary || "evidence.md 已存在，等待整合。"}</em>
            </span>
          ))
        ) : (
          <span>
            <strong>session evidence</strong>
            <em>暂无 session 级 evidence.md</em>
          </span>
        )}
      </div>
    </div>
  );
}

function ResearchRunLedger({ runs, onOpenRun, busy }) {
  return (
    <div className="table-wrap stage-run-table">
      <table>
        <thead>
          <tr>
            <th>正式 run</th>
            <th>结论</th>
            <th>窗口</th>
            <th>年化</th>
            <th>总收益</th>
            <th>最大回撤</th>
            <th>盈亏比</th>
            <th>下一步</th>
            <th>动作</th>
          </tr>
        </thead>
        <tbody>
          {runs.length ? (
            runs.map((run) => (
              <Fragment key={run.runId}>
                <ResearchRunRow run={run} onOpenRun={onOpenRun} busy={busy} integrated />
              </Fragment>
            ))
          ) : (
            <tr>
              <td colSpan="9" className="empty-state">research-runs.json 暂无正式 run</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function ResearchRunRow({ run, onOpenRun, busy, integrated = false }) {
  const metrics = run.metrics || {};
  const canOpen = Boolean(run.resultFiles?.results);
  return (
    <tr>
      <td>
        <strong>{run.runId}</strong>
        <em>{run.parameterSummary || run.label || "--"}</em>
      </td>
      <td>{run.statusTier || run.status || (integrated ? "正式" : "待整合")}</td>
      {integrated ? <td>{formatWindowPass(run)}</td> : null}
      <td>{formatPercent(metrics.annualizedReturn, 2)}</td>
      {integrated ? <td>{formatPercent(metrics.totalReturn, 2)}</td> : null}
      <td>{formatPercent(metrics.maxDrawdown, 2)}</td>
      <td>{metrics.profitLossRatio == null ? "n/a" : `${formatNumber(metrics.profitLossRatio)}:1`}</td>
      <td>{integrated ? run.nextAction || run.failureReason || "--" : run.warning || "待整合"}</td>
      <td>
        <button className="row-button" type="button" onClick={() => onOpenRun(run.runId)} disabled={busy || !canOpen}>
          载入
        </button>
      </td>
    </tr>
  );
}

function formatWindowPass(run) {
  if (run.passedWindows != null || run.failedWindows != null) {
    return `${formatInteger(run.passedWindows || 0)}/${formatInteger((run.passedWindows || 0) + (run.failedWindows || 0))}`;
  }
  if (run.rollingWindowPass === true) return "通过";
  if (run.rollingWindowPass === false) return "未过";
  return "--";
}

function ResearchStageBrief({ overview, error }) {
  const inboxCount = overview?.evidenceInbox?.count || 0;
  const stage = overview?.stage || {};
  return (
    <section className="rail-panel">
      <PanelTitle icon={<GitBranch size={17} />} title="阶段证据边界" right={stage.status || "unknown"} />
      <ul className="audit-list">
        <li>
          <i className={`audit-dot ${error ? "warn" : "ok"}`} />
          <span>{error || stage.stageId || "等待阶段信息"}</span>
        </li>
        <li>
          <i className="audit-dot ok" />
          <span>正式 run：{formatInteger(overview?.integratedRunCount)}，只读自 research-runs.json</span>
        </li>
        <li>
          <i className={`audit-dot ${inboxCount ? "warn" : "ok"}`} />
          <span>待整合证据：{formatInteger(inboxCount)}，不参与阶段结论</span>
        </li>
        <li>
          <i className="audit-dot warn" />
          <span>并行 session：{formatInteger(overview?.sessionCount)}，只展示进度与证据</span>
        </li>
      </ul>
    </section>
  );
}

function QuantCommandCenter({
  researchRuns,
  selectedResearchRunId,
  researchRun,
  researchRunError,
  baselineRunId,
  onResearchRunChange,
  onRefreshResearchRuns,
  onLoadBaseline,
  busy,
}) {
  const run = researchRun;
  const metrics = run?.metrics || {};
  const allTrades = run?.allTrades?.length ? run.allTrades : run?.recentTrades || [];
  const completedTrades = run?.completedTrades || [];
  const headline = run?.strategy?.label || run?.label || "研究运行全景";
  const verdict = buildResearchRunVerdict(run, researchRunError);
  const target = getResearchTarget(run);
  const targetPassed = researchTargetPassed(run);
  const statusLabel = run?.spec?.statusTier || (!targetPassed && run ? "观察" : run?.status || "--");
  const evidenceLine = run
    ? `${run.runId} · ${run.resultFiles?.results || "docs/research/runs/.../results.json"}`
    : researchRunError || "正在读取 docs/research/runs/ 下的研究运行证据。";

  return (
    <section className="quant-layout">
      <section className="workspace-panel quant-hero">
        <div>
          <p className="eyebrow">Tab2 Research Run Panorama</p>
          <h2>{headline}</h2>
          <strong>{verdict}</strong>
          <span>{evidenceLine}</span>
        </div>
        <div className="quant-hero-actions">
          <label className="quant-strategy-select">
            研究运行
            <select value={selectedResearchRunId || run?.runId || ""} onChange={(event) => onResearchRunChange(event.target.value)} disabled={!researchRuns.length || busy}>
              {researchRuns.length ? (
                researchRuns.map((item) => (
                  <option key={item.runId} value={item.runId}>
                    {item.runId} · {item.label}
                  </option>
                ))
              ) : (
                <option value="">暂无可读运行</option>
              )}
            </select>
          </label>
          <button className="ghost-button" type="button" onClick={onRefreshResearchRuns} disabled={busy}>
            <RefreshCw size={16} /> 刷新运行目录
          </button>
          <button className="ghost-button" type="button" onClick={onLoadBaseline} disabled={busy || !baselineRunId}>
            <ShieldCheck size={16} /> 固化基线证据
          </button>
        </div>
      </section>

      <section className="metric-grid quant-metrics">
        <MetricTile label="策略状态" value={statusLabel} tone={targetPassed ? "good" : "neutral"} sub={verdict} />
        <MetricTile label="年化收益" value={formatPercent(metrics.annualizedReturn, 2)} tone={targetPassed ? "good" : "bad"} sub={`目标 ${formatPercent(target.annualized, 0)} / 三年 ${formatPercent(metrics.totalReturn, 2)}`} />
        <MetricTile label="最大回撤" value={formatPercent(metrics.maxDrawdown, 2)} tone={metrics.maxDrawdown >= -0.1 ? "good" : "bad"} sub={`Calmar ${formatNumber(metrics.calmarRatio)}`} />
        <MetricTile label="盈亏比" value={metrics.profitLossRatio == null ? "n/a" : `${formatNumber(metrics.profitLossRatio)}:1`} tone={metrics.profitLossRatio >= 2 ? "good" : "bad"} sub={`胜率 ${formatPercent(metrics.winRate, 1)}`} />
        <MetricTile label="尾部风险" value={formatPercent(metrics.tailWorstReturn, 2)} tone={metrics.tailWorstReturn >= -0.1 ? "good" : "bad"} sub={`资本尾部 ${formatPercent(metrics.tailBottomPortfolioImpactPct, 2)}`} />
        <MetricTile label="样本规模" value={formatInteger(metrics.completedTradeCount)} tone="neutral" sub={`动作 ${formatInteger(allTrades.length)}`} />
      </section>

      <div className="quant-main-grid">
        <QuantFocusBoard run={run} />
        <QuantGateBoard run={run} />
      </div>

      <QuantImageBoard run={run} />

      <BaselineResultCoverage baseline={run} />

      <BaselineFullBacktestBoard baseline={run} />

      <section className="quant-ledger-grid">
        <section className="workspace-panel">
          <PanelTitle icon={<Activity size={17} />} title="研究运行交易动作" right={`${allTrades.length} 笔`} />
          <TradeTable trades={allTrades} />
        </section>
        <CompletedTradeTable rows={completedTrades} />
      </section>
    </section>
  );
}

function buildResearchRunVerdict(run, error) {
  if (error) return "研究运行读取失败";
  if (!run) return "等待研究运行证据";
  const target = getResearchTarget(run);
  const targetPassed = researchTargetPassed(run);
  if (run.metrics?.strictTargetMet && targetPassed) return "严格目标已通过，进入复核视角";
  if (run.metrics?.targetMet && targetPassed) return "硬门槛已通过，诊断项继续审计";
  if (isFiniteNumber(run.metrics?.annualizedReturn) && !targetPassed) return `低于年化 ${formatPercent(target.annualized, 0)} 合格线，保留观察`;
  if (run.spec?.statusTier) return `${run.spec.statusTier}级证据`;
  if (run.status && run.status !== "completed") return `运行状态：${run.status}`;
  return "未达硬门槛，保留为研究证据";
}

function getResearchTarget(run) {
  const qualification = run?.spec?.qualificationObjective || {};
  const annualized = Number(run?.metrics?.targetAnnualizedReturn ?? qualification.targetAnnualizedReturn ?? 0.3);
  const total = Number(run?.metrics?.targetTotalReturn ?? qualification.targetTotalReturnOver3Years ?? (Math.pow(1 + annualized, 3) - 1));
  return { annualized, total };
}

function researchTargetPassed(run) {
  const metrics = run?.metrics || {};
  const target = getResearchTarget(run);
  if (!isFiniteNumber(metrics.annualizedReturn)) {
    return Boolean(metrics.targetMet);
  }
  return metrics.annualizedReturn >= target.annualized;
}

function QuantFocusBoard({ run }) {
  const spec = run?.spec || {};
  const strategy = run?.strategy || {};
  const capital = spec.capital || {};
  const costs = spec.costs || {};
  const entry = spec.entry || {};
  const exit = spec.exit || {};
  const universe = spec.universe || {};
  const entryRisk = entry.entryRiskFilter || {};
  const focusItems = [
    {
      label: "研究假设",
      value: strategy.name || strategy.label || run?.runId || "--",
      detail: strategy.hypothesis || "该运行未写入 hypothesis 字段，详情可回看 run 目录文档。",
    },
    {
      label: "入场结构",
      value: entry.entryMode || "未标注",
      detail: `评分 ${entry.entryScoreMode || "--"}；趋势过滤 ${entry.useTrendFilter ? "开" : "关"}，MACD ${entry.useMacdFilter ? "开" : "关"}，RSI ${entry.useRsiFilter ? "开" : "关"}。`,
    },
    {
      label: "执行压力",
      value: `买 ${formatPercent(costs.buySlippagePct, 2)} / 卖 ${formatPercent(costs.sellSlippagePct, 2)}`,
      detail: `跳空止损按开盘成交：${exit.stopGapFillAtOpen ? "是" : "否"}；跌停延迟：${exit.limitDownStopDelay ? "是" : "否"}。`,
    },
    {
      label: "风险与样本",
      value: `${formatPercent(capital.maxSinglePositionPct, 1)} 单票 / ${formatPercent(capital.riskPct, 1)} 风险`,
      detail: `入场振幅上限 ${formatPercent(entryRisk.maxEntryRangePct, 1)}；上市 ${formatInteger(universe.minListDays)} 天以上，最少 ${formatInteger(universe.minBars)} 根日线。`,
    },
    {
      label: "退出纪律",
      value: `${formatPercent(exit.stopLossPct, 1)} 止损`,
      detail: `第一止盈 ${formatPercent(exit.takeProfit1Pct, 1)}，第二止盈 ${formatPercent(exit.takeProfit2Pct, 1)}；缺口冷却 ${formatInteger(exit.gapStopMarketCooldownDays)} 日。`,
    },
    {
      label: "证据链",
      value: run?.sourceRun || "独立运行",
      detail: run?.resultFiles?.review ? `复盘文件：${run.resultFiles.review}` : "该运行暂未提供 review.md。",
    },
  ];

  return (
    <section className="workspace-panel">
      <PanelTitle icon={<ShieldCheck size={17} />} title="运行证据侧重点" right={run?.runId || "docs/research/runs"} />
      <div className="quant-focus-grid">
        {focusItems.map((item) => (
          <article key={item.label} className="quant-focus-card">
            <span>{item.label}</span>
            <strong>{item.value}</strong>
            <em>{item.detail}</em>
          </article>
        ))}
      </div>
    </section>
  );
}

function QuantGateBoard({ run }) {
  const metrics = run?.metrics || {};
  const objective = run?.objectiveGates || {};
  const diagnostic = run?.diagnosticGates || {};
  const entries = Object.entries({ ...objective, ...diagnostic });
  const target = getResearchTarget(run);
  const targetPassed = researchTargetPassed(run);
  const gates = [
    ["年化", `≥${formatPercent(target.annualized, 0)}`, formatPercent(metrics.annualizedReturn, 2), targetPassed],
    ["总收益", `三年 ≥${formatPercent(target.total, 0)}`, formatPercent(metrics.totalReturn, 2), isFiniteNumber(metrics.totalReturn) && metrics.totalReturn >= target.total],
    ["盈亏比", "≥2:1", metrics.profitLossRatio == null ? "n/a" : formatNumber(metrics.profitLossRatio), metrics.profitLossRatio >= 2],
    ["回撤", "最大回撤 ≥-10%", formatPercent(metrics.maxDrawdown, 2), metrics.maxDrawdown >= -0.1],
    ["尾部", "后10亏损 ≥-10%", formatPercent(metrics.tailWorstReturn, 2), metrics.tailWorstReturn >= -0.1],
    ["交易", "样本 ≥30", formatInteger(metrics.completedTradeCount), metrics.completedTradeCount >= 30],
  ];
  return (
    <section className="workspace-panel">
      <PanelTitle icon={<CheckCircle2 size={17} />} title="研究门槛审计" right={metrics.strictTargetMet && targetPassed ? "严格通过" : targetPassed ? "目标通过" : "未通过"} />
      <div className="quant-gate-stack">
        {gates.map(([label, target, value, passed]) => (
          <span key={label} className={passed ? "pass" : "fail"}>
            <em>{label}</em>
            <strong>{value}</strong>
            <b>{target}</b>
          </span>
        ))}
      </div>
      <div className="gate-grid diagnostic">
        {entries.length ? (
          entries.map(([key, value]) => (
            <Fragment key={key}>
              <GatePill label={key} value={value} />
            </Fragment>
          ))
        ) : (
          <span className="empty-state">等待 objectiveGates / diagnosticGates</span>
        )}
      </div>
    </section>
  );
}

function QuantImageBoard({ run }) {
  const rows = run?.symbolAuditRows || run?.symbolAudit?.rows || [];
  const distributionLabel = rows.length ? `${rows.length} 个成交标的` : "等待运行证据";
  return (
    <section className="quant-image-grid">
      <section className="workspace-panel quant-image-wide">
        <PanelTitle icon={<LineChart size={17} />} title="组合权益与回撤" right={`${run?.equityCurve?.length || 0} 个交易日`} />
        <PortfolioEquityChart points={run?.equityCurve || []} />
      </section>
      <section className="workspace-panel">
        <PanelTitle icon={<BarChart3 size={17} />} title="成交标的收益分布" right={distributionLabel} />
        <ReturnDistributionSvg rows={rows} />
      </section>
      <section className="workspace-panel">
        <PanelTitle icon={<Gauge size={17} />} title="收益-回撤散点" right="成交标的审计" />
        <RiskScatterSvg rows={rows} />
      </section>
      <section className="workspace-panel quant-image-wide">
        <PanelTitle icon={<TrendingUp size={17} />} title="收益排名曲线" right={rows.length ? "从弱到强" : "等待运行证据"} />
        <RankedReturnSvg rows={rows} />
      </section>
    </section>
  );
}

function BaselineResultCoverage({ baseline }) {
  if (!baseline) return null;
  const counts = baseline.resultCounts || {};
  const spec = baseline.spec || {};
  const window = spec.window || {};
  const files = baseline.resultFiles || {};
  const items = [
    ["权益图像", formatInteger(counts.equityDays), "组合权益/回撤完整交易日"],
    ["交易动作", formatInteger(counts.tradeActions), "买入、减仓、卖出全量流水"],
    ["完成交易", formatInteger(counts.completedTrades), "全部已平仓交易"],
    ["标的审计", formatInteger(counts.symbolAuditRows), "成交标的逐票收益、回撤、盈亏比"],
    ["最终持仓", formatInteger(counts.finalPositions), "回测结束仍未平仓持仓"],
    ["验证窗口", `${window.startDate || "--"} / ${window.endDate || "--"}`, "三年全市场复核口径"],
  ];
  return (
    <section className="workspace-panel quant-coverage-panel">
      <PanelTitle icon={<Database size={17} />} title="回测结果覆盖" right={baseline.runId || "等待证据"} />
      <div className="quant-coverage-grid">
        {items.map(([label, value, detail]) => (
          <span key={label} className="quant-coverage-item">
            <em>{label}</em>
            <strong>{value}</strong>
            <b>{detail}</b>
          </span>
        ))}
      </div>
      <div className="quant-file-strip">
        <span>
          证据 run <strong>{baseline.runId || "--"}</strong>
        </span>
        <span>
          来源 run <strong>{spec.sourceEvidenceRun || "--"}</strong>
        </span>
        <span>
          结果文件 <code>{files.results || "docs/research/runs/.../results.json"}</code>
        </span>
      </div>
    </section>
  );
}

function ReturnDistributionSvg({ rows }) {
  const values = rows.map((item) => Number(item.totalReturn)).filter((value) => Number.isFinite(value));
  if (!values.length) return <div className="quant-image-empty">运行全市场验证后显示全量收益分布</div>;
  const binCount = 18;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 0.01;
  const bins = Array.from({ length: binCount }, () => 0);
  values.forEach((value) => {
    const index = Math.min(binCount - 1, Math.max(0, Math.floor(((value - min) / span) * binCount)));
    bins[index] += 1;
  });
  const maxCount = Math.max(...bins, 1);
  const width = 720;
  const height = 260;
  const gutter = 28;
  const barGap = 4;
  const barWidth = (width - gutter * 2) / binCount - barGap;
  return (
    <svg className="quant-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="全量收益分布">
      <line x1={gutter} y1={height - gutter} x2={width - gutter} y2={height - gutter} />
      {bins.map((count, index) => {
        const x = gutter + index * (barWidth + barGap);
        const barHeight = ((height - gutter * 2) * count) / maxCount;
        const centerReturn = min + (span * (index + 0.5)) / binCount;
        return <rect key={index} x={x} y={height - gutter - barHeight} width={barWidth} height={barHeight} className={centerReturn >= 0 ? "good" : "bad"} rx="2" />;
      })}
      <text x={gutter} y={height - 6}>{formatPercent(min, 0)}</text>
      <text x={width - gutter - 46} y={height - 6}>{formatPercent(max, 0)}</text>
    </svg>
  );
}

function RiskScatterSvg({ rows }) {
  const points = rows
    .map((item) => ({ code: item.ts_code, r: Number(item.totalReturn), d: Number(item.maxDrawdown) }))
    .filter((item) => Number.isFinite(item.r) && Number.isFinite(item.d));
  if (!points.length) return <div className="quant-image-empty">运行全市场验证后显示收益-回撤散点</div>;
  const width = 720;
  const height = 260;
  const gutter = 28;
  const minReturn = Math.min(...points.map((item) => item.r));
  const maxReturn = Math.max(...points.map((item) => item.r));
  const minDrawdown = Math.min(...points.map((item) => item.d));
  const xSpan = maxReturn - minReturn || 0.01;
  const ySpan = 0 - minDrawdown || 0.01;
  const zeroX = gutter + ((0 - minReturn) / xSpan) * (width - gutter * 2);
  const targetY = gutter + ((0 - -0.1) / ySpan) * (height - gutter * 2);
  return (
    <svg className="quant-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="全量收益和最大回撤散点">
      <line x1={gutter} y1={height - gutter} x2={width - gutter} y2={height - gutter} />
      <line x1={gutter} y1={gutter} x2={gutter} y2={height - gutter} />
      {zeroX > gutter && zeroX < width - gutter ? <line className="axis-soft" x1={zeroX} y1={gutter} x2={zeroX} y2={height - gutter} /> : null}
      {targetY > gutter && targetY < height - gutter ? <line className="axis-warn" x1={gutter} y1={targetY} x2={width - gutter} y2={targetY} /> : null}
      {points.map((point) => {
        const x = gutter + ((point.r - minReturn) / xSpan) * (width - gutter * 2);
        const y = gutter + ((0 - point.d) / ySpan) * (height - gutter * 2);
        return <circle key={point.code} cx={x} cy={y} r="2.1" className={point.r >= 0 ? "good" : "bad"} />;
      })}
      <text x={gutter} y={height - 6}>{formatPercent(minReturn, 0)}</text>
      <text x={width - gutter - 46} y={height - 6}>{formatPercent(maxReturn, 0)}</text>
      <text x={gutter + 4} y={gutter - 8}>回撤</text>
    </svg>
  );
}

function RankedReturnSvg({ rows }) {
  const values = rows.map((item) => Number(item.totalReturn)).filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (!values.length) return <div className="quant-image-empty">等待全市场验证结果</div>;
  const width = 920;
  const height = 260;
  const gutter = 30;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 0.01;
  const points = values
    .map((value, index) => {
      const x = gutter + (index / Math.max(values.length - 1, 1)) * (width - gutter * 2);
      const y = height - gutter - ((value - min) / span) * (height - gutter * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const zeroY = height - gutter - ((0 - min) / span) * (height - gutter * 2);
  return (
    <svg className="quant-svg wide" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="全量收益排名曲线">
      <line x1={gutter} y1={height - gutter} x2={width - gutter} y2={height - gutter} />
      {zeroY > gutter && zeroY < height - gutter ? <line className="axis-soft" x1={gutter} y1={zeroY} x2={width - gutter} y2={zeroY} /> : null}
      <polyline points={points} />
      <text x={gutter} y={height - 6}>{formatPercent(min, 0)}</text>
      <text x={width - gutter - 46} y={height - 6}>{formatPercent(max, 0)}</text>
    </svg>
  );
}

function BaselineFullBacktestBoard({ baseline }) {
  if (!baseline) return null;
  const rows = baseline.symbolAuditRows || baseline.symbolAudit?.rows || [];
  const summary = baseline.summary || {};
  const counts = baseline.resultCounts || {};
  const capitalTail = baseline.tailCapitalRisk || baseline.symbolAudit?.tailCapitalRisk || {};
  return (
    <section className="quant-baseline-result-grid">
      <section className="workspace-panel quant-baseline-full">
        <PanelTitle icon={<Database size={17} />} title="研究运行标的审计" right={rows.length ? `${rows.length} 个成交标的` : "等待运行"} />
        <div className="market-summary quant-summary">
          <SummaryCell label="初始资金" value={formatMoney(summary.initialCash)} />
          <SummaryCell label="最终权益" value={formatMoney(summary.finalEquity)} />
          <SummaryCell label="全量动作" value={formatInteger(counts.tradeActions ?? baseline.allTrades?.length)} />
          <SummaryCell label="完成交易" value={formatInteger(counts.completedTrades ?? baseline.completedTrades?.length)} />
          <SummaryCell label="尾部资本最差" value={formatPercent(capitalTail.worstPortfolioImpactPct, 2)} />
          <SummaryCell label="尾部资本合计" value={formatPercent(capitalTail.totalBottomPortfolioImpactPct, 2)} />
        </div>
        <div className="quant-panel-actions">
          <button className="ghost-button export-button" type="button" onClick={() => downloadJson(baseline, `research-run-${baseline.runId || "latest"}`)}>
            <Download size={16} /> 导出研究运行JSON
          </button>
        </div>
        <div className="market-job-note">这里展示研究运行产生的全部成交标的审计；下方交易流水保留每一笔买入、减仓和卖出动作。</div>
        <div className="table-wrap quant-full-result-table">
          <table>
            <thead>
              <tr>
                <th>排名</th>
                <th>代码</th>
                <th>名称</th>
                <th>行业</th>
                <th>收益</th>
                <th>资本收益</th>
                <th>组合影响</th>
                <th>净盈亏</th>
                <th>回撤</th>
                <th>盈亏比</th>
                <th>胜率</th>
                <th>交易</th>
              </tr>
            </thead>
            <tbody>
              {rows.length ? (
                rows.map((item, index) => (
                  <tr key={item.ts_code}>
                    <td>{index + 1}</td>
                    <td>{item.ts_code}</td>
                    <td>{item.name || "--"}</td>
                    <td>{item.industry || "--"}</td>
                    <td className={item.totalReturn >= 0 ? "positive" : "negative"}>{formatPercent(item.totalReturn, 2)}</td>
                    <td className={item.capitalReturnPct >= 0 ? "positive" : "negative"}>{formatPercent(item.capitalReturnPct, 2)}</td>
                    <td className={item.portfolioImpactPct >= 0 ? "positive" : "negative"}>{formatPercent(item.portfolioImpactPct, 2)}</td>
                    <td className={item.netPnl >= 0 ? "positive" : "negative"}>{formatMoney(item.netPnl)}</td>
                    <td>{formatPercent(item.maxDrawdown, 2)}</td>
                    <td>{item.profitLossRatio == null ? "n/a" : `${formatNumber(item.profitLossRatio)}:1`}</td>
                    <td>{formatPercent(item.winRate, 1)}</td>
                    <td>{formatInteger(item.completedTrades ?? item.tradeCount)}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan="12" className="empty-state">等待组合基线全量结果</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
      <FinalPositionTable rows={baseline.finalPositions || []} />
    </section>
  );
}

function FinalPositionTable({ rows }) {
  return (
    <section className="workspace-panel">
      <PanelTitle icon={<Layers3 size={17} />} title="最终持仓" right={rows.length ? `${rows.length} 个` : "已清仓"} />
      <div className="table-wrap quant-position-table">
        <table>
          <thead>
            <tr>
              <th>代码</th>
              <th>名称</th>
              <th>行业</th>
              <th>入场</th>
              <th>入场价</th>
              <th>最新价</th>
              <th>止损</th>
              <th>数量</th>
            </tr>
          </thead>
          <tbody>
            {rows.length ? (
              rows.map((position) => (
                <tr key={position.ts_code}>
                  <td>{position.ts_code}</td>
                  <td>{position.name || "--"}</td>
                  <td>{position.industry || "--"}</td>
                  <td>{position.entryDate || "--"}</td>
                  <td>{formatMoney(position.entryPrice)}</td>
                  <td>{formatMoney(position.lastPrice)}</td>
                  <td>{formatMoney(position.stopPrice)}</td>
                  <td>{formatInteger(position.shares)}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan="8" className="empty-state">组合回测结束时无未平仓持仓</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function CompletedTradeTable({ rows }) {
  return (
    <section className="workspace-panel">
      <PanelTitle icon={<CheckCircle2 size={17} />} title="全量完成交易" right={`${rows.length} 笔`} />
      <div className="table-wrap quant-completed-table">
        <table>
          <thead>
            <tr>
              <th>代码</th>
              <th>名称</th>
              <th>入场</th>
              <th>出场</th>
              <th>入场价</th>
              <th>出场价</th>
              <th>净收益</th>
              <th>末次价差</th>
              <th>资本收益</th>
              <th>组合影响</th>
              <th>净盈亏</th>
              <th>原因</th>
              <th>成交规则</th>
            </tr>
          </thead>
          <tbody>
            {rows.length ? (
              rows.map((trade, index) => (
                <tr key={`${trade.ts_code}-${trade.entryDate}-${trade.exitDate}-${index}`}>
                  <td>{trade.ts_code}</td>
                  <td>{trade.name || "--"}</td>
                  <td>{trade.entryDate}</td>
                  <td>{trade.exitDate}</td>
                  <td>{formatMoney(trade.entryPrice)}</td>
                  <td>{formatMoney(trade.exitPrice)}</td>
                  <td className={trade.returnPct >= 0 ? "positive" : "negative"}>{formatPercent(trade.returnPct, 2)}</td>
                  <td className={(trade.exitPriceReturnPct ?? trade.returnPct) >= 0 ? "positive" : "negative"}>{formatPercent(trade.exitPriceReturnPct ?? trade.returnPct, 2)}</td>
                  <td className={trade.capitalReturnPct >= 0 ? "positive" : "negative"}>{formatPercent(trade.capitalReturnPct, 2)}</td>
                  <td className={trade.pnlPctOfEntryEquity >= 0 ? "positive" : "negative"}>{formatPercent(trade.pnlPctOfEntryEquity, 2)}</td>
                  <td className={trade.netPnl >= 0 ? "positive" : "negative"}>{formatMoney(trade.netPnl)}</td>
                  <td>{trade.exitReason || "--"}</td>
                  <td>{trade.exitPriceRule || "--"}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan="13" className="empty-state">暂无完成交易</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
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

function BaselineStrategyPanel({ data, error, onReload, busy }) {
  if (!data) {
    return (
      <section className="workspace-panel baseline-empty-panel">
        <PanelTitle icon={<ShieldCheck size={17} />} title="组合基线" right="cross-section-strength-risk8" />
        <div className="empty-state tall">
          <button className="primary-button" type="button" onClick={onReload} disabled={busy}>
            <RefreshCw size={17} /> 载入策略基线
          </button>
          {error ? <p className="llm-warning">{error}</p> : null}
        </div>
      </section>
    );
  }

  const metrics = data.metrics || {};
  const ai = data.aiAnalysis || {};
  const spec = data.spec || {};
  const capital = spec.capital || {};
  const entry = spec.entry || {};
  const exit = spec.exit || {};
  const robustness = data.robustness || {};
  const target = getResearchTarget(data);
  const targetPassed = researchTargetPassed(data);
  return (
    <section className="baseline-layout">
      <section className="workspace-panel baseline-hero">
        <div>
          <p className="eyebrow">Observation Candidate Baseline</p>
          <h2>{data.label}</h2>
          <strong>{ai.verdict || "等待分析"}</strong>
          <span>{data.runId}</span>
        </div>
        <div className="baseline-score">
          <span>{ai.score ?? "--"}</span>
          <em>AI审查分</em>
        </div>
      </section>

      <section className="metric-grid baseline-metrics">
        <MetricTile label="总收益" value={formatPercent(metrics.totalReturn, 2)} tone={isFiniteNumber(metrics.totalReturn) && metrics.totalReturn >= target.total ? "good" : "bad"} sub={`三年目标 ${formatPercent(target.total, 0)}`} />
        <MetricTile label="年化" value={formatPercent(metrics.annualizedReturn, 2)} tone={targetPassed ? "good" : "bad"} sub={`目标 ${formatPercent(target.annualized, 0)}`} />
        <MetricTile label="最大回撤" value={formatPercent(metrics.maxDrawdown, 2)} tone="good" sub="目标 ≤10%" />
        <MetricTile label="盈亏比" value={`${formatNumber(metrics.profitLossRatio)}:1`} tone="good" sub={`PF ${formatNumber(metrics.profitFactor)}`} />
        <MetricTile label="完成交易" value={formatInteger(metrics.completedTradeCount)} tone="neutral" sub={`${formatInteger(metrics.tradeCount)} 个动作`} />
        <MetricTile label="尾部亏损" value={formatPercent(metrics.tailWorstReturn, 2)} tone="good" sub="后10最差" />
      </section>

      <section className="workspace-panel baseline-chart-panel">
        <PanelTitle icon={<LineChart size={17} />} title="组合收益曲线" right={`${data.equityCurve?.length || 0} 个交易日`} />
        <PortfolioEquityChart points={data.equityCurve || []} />
      </section>

      <section className="workspace-panel baseline-ai-panel">
        <PanelTitle icon={<Bot size={17} />} title="AI策略审查" right={ai.provider || "local"} />
        <p className="market-fit">{ai.marketFit}</p>
        <ul className="compact-list">
          {(ai.summary || []).map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
        <div className="factor-grid baseline-factor-grid">
          {(ai.factorRead || []).map((item) => (
            <article key={item.name} className="factor-card">
              <span>{item.name}</span>
              <strong>{item.value}</strong>
              <em>{item.comment}</em>
            </article>
          ))}
        </div>
      </section>

      <RobustnessPanel robustness={robustness} />

      <section className="baseline-columns">
        <ReviewList title="有效证据" tone="good" items={ai.strengths || []} />
        <ReviewList title="风险残差" tone="bad" items={ai.risks || []} />
        <ReviewList title="下一步验证" tone="neutral" items={ai.nextChecks || []} />
      </section>

      <section className="workspace-panel baseline-spec-panel">
        <PanelTitle icon={<SlidersHorizontal size={17} />} title="执行参数" right={spec.id || data.id} />
        <div className="baseline-spec-grid">
          <FactorMini label="最大持仓" value={capital.maxPositions ?? "--"} />
          <FactorMini label="单票上限" value={formatPercent(capital.maxSinglePositionPct, 0)} />
          <FactorMini label="行业上限" value={formatPercent(capital.maxIndustryExposurePct, 0)} />
          <FactorMini label="每周开仓" value={capital.weeklyBuyLimit ?? "--"} />
          <FactorMini label="入场排序" value={entry.entryPriority || "--"} />
          <FactorMini label="买入振幅" value={formatPercent(entry.entryRiskFilter?.maxEntryRangePct, 0)} />
          <FactorMini label="止损" value={formatPercent(exit.stopLossPct, 0)} />
          <FactorMini label="二止" value={formatPercent(exit.takeProfit2Pct, 0)} />
        </div>
      </section>

      <section className="workspace-panel baseline-gates-panel">
        <PanelTitle icon={<CheckCircle2 size={17} />} title="门槛审计" right={targetPassed ? "目标已达标" : "未达标"} />
        <div className="gate-grid">
          {Object.entries(data.objectiveGates || {}).map(([key, value]) => (
            <Fragment key={key}>
              <GatePill label={key} value={value} />
            </Fragment>
          ))}
        </div>
        <div className="gate-grid diagnostic">
          {Object.entries(data.diagnosticGates || {}).map(([key, value]) => (
            <Fragment key={key}>
              <GatePill label={key} value={value} />
            </Fragment>
          ))}
        </div>
      </section>

      <StrategyRankTable title="收益前 10" rows={data.top10 || []} />
      <StrategyRankTable title="收益后 10" rows={data.bottom10 || []} />
    </section>
  );
}

function RobustnessPanel({ robustness }) {
  const coverage = robustness.dataCoverage || {};
  const gates = robustness.gates || [];
  const segments = robustness.availableSegments || [];
  const nextActions = robustness.nextActions || [];
  const status = robustness.status || "unknown";
  return (
    <section className="workspace-panel robustness-panel">
      <PanelTitle icon={<ShieldCheck size={17} />} title="二阶段稳健性闸门" right={robustnessStatusText(status)} />
      <div className="robustness-headline">
        <div>
          <strong>{robustness.verdict || "等待三年基线结果"}</strong>
          <span>{robustness.requiredBeforeStage2 || "三年硬门槛通过后才进入长周期验证。"}</span>
        </div>
        <span className={`robustness-status ${robustnessStatusClass(status)}`}>{robustness.stage2Enabled ? "已触发" : "未启动"}</span>
      </div>

      <div className="robustness-data-grid">
        <FactorMini label="本地日线起点" value={coverage.startDate || "--"} />
        <FactorMini label="本地日线终点" value={coverage.endDate || "--"} />
        <FactorMini label="覆盖年限" value={formatNumber(coverage.calendarYears)} />
        <FactorMini label="交易日" value={formatInteger(coverage.tradeDateCount)} />
        <FactorMini label="标的数" value={formatInteger(coverage.symbolCount)} />
        <FactorMini label="记录数" value={formatInteger(coverage.rowCount)} />
      </div>

      <div className="robustness-gate-grid">
        {gates.map((gate) => (
          <Fragment key={gate.key}>
            <RobustnessGateCard gate={gate} />
          </Fragment>
        ))}
      </div>

      <div className="robustness-split">
        <section>
          <h3>当前三年内部切片</h3>
          <div className="table-wrap robustness-table">
            <table>
              <thead>
                <tr>
                  <th>窗口</th>
                  <th>区间</th>
                  <th>收益</th>
                  <th>回撤</th>
                  <th>交易日</th>
                </tr>
              </thead>
              <tbody>
                {segments.map((item) => (
                  <tr key={item.label}>
                    <td>{item.label}</td>
                    <td>{item.startDate} / {item.endDate}</td>
                    <td className={item.returnPct >= 0 ? "positive" : "negative"}>{formatPercent(item.returnPct, 2)}</td>
                    <td>{formatPercent(item.maxDrawdown, 2)}</td>
                    <td>{formatInteger(item.tradeDays)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <section>
          <h3>下一步只在三年过线后执行</h3>
          <ul className="compact-list">
            {nextActions.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      </div>
    </section>
  );
}

function RobustnessGateCard(props) {
  const { gate } = props;
  const statusClass = robustnessGateClass(gate.status);
  return (
    <article className={`robustness-gate ${statusClass}`}>
      <span>
        <CheckCircle2 size={14} />
        {robustnessGateText(gate.status)}
      </span>
      <strong>{gate.label}</strong>
      <em>{gate.value}</em>
      <p>{gate.detail}</p>
    </article>
  );
}

function PortfolioEquityChart({ points }) {
  const containerRef = useRef(null);
  const latest = points.length ? points[points.length - 1] : null;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    container.replaceChildren();
    if (!points.length) return undefined;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: Math.round(container.getBoundingClientRect().height || 420),
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
      rightPriceScale: {
        borderColor: "rgba(232, 239, 235, 0.12)",
      },
      timeScale: {
        borderColor: "rgba(232, 239, 235, 0.12)",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 8,
      },
    });
    const returnSeries = chart.addSeries(LineSeries, {
      color: "#2ed49b",
      lineWidth: 2,
      title: "收益%",
      priceFormat: { type: "custom", formatter: (value) => `${value.toFixed(1)}%` },
    });
    returnSeries.setData(points.map((point) => ({ time: point.date, value: Number(point.returnPct || 0) * 100 })));
    const drawdownSeries = chart.addSeries(LineSeries, {
      color: "#f05f50",
      lineWidth: 1,
      title: "回撤%",
      priceFormat: { type: "custom", formatter: (value) => `${value.toFixed(1)}%` },
    });
    drawdownSeries.setData(points.map((point) => ({ time: point.date, value: Number(point.drawdown || 0) * 100 })));
    chart.timeScale().fitContent();

    return () => {
      chart.remove();
    };
  }, [points]);

  return (
    <div className="portfolio-equity-shell">
      <div className="portfolio-equity-readout">
        <span>最新权益 <strong>{formatMoney(latest?.equity)}</strong></span>
        <span>累计收益 <strong>{formatPercent(latest?.returnPct, 2)}</strong></span>
        <span>当前回撤 <strong>{formatPercent(latest?.drawdown, 2)}</strong></span>
        <span>持仓 <strong>{latest?.positions ?? "--"}</strong></span>
      </div>
      <div ref={containerRef} className="portfolio-equity-chart" />
    </div>
  );
}

function GatePill(props) {
  const { label, value } = props;
  return (
    <span className={`gate-pill ${value ? "pass" : "fail"}`}>
      <CheckCircle2 size={14} />
      <em>{label}</em>
      <strong>{value ? "通过" : "观察"}</strong>
    </span>
  );
}

function StrategyRankTable({ title, rows }) {
  return (
    <section className="workspace-panel baseline-rank-panel">
      <PanelTitle icon={<BarChart3 size={17} />} title={title} right={`${rows.length} 只`} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>代码</th>
              <th>名称</th>
              <th>行业</th>
              <th>收益</th>
              <th>回撤</th>
              <th>盈亏比</th>
              <th>交易</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => (
              <tr key={item.ts_code}>
                <td>{item.ts_code}</td>
                <td>{item.name || "--"}</td>
                <td>{item.industry || "--"}</td>
                <td className={item.totalReturn >= 0 ? "positive" : "negative"}>{formatPercent(item.totalReturn, 2)}</td>
                <td>{formatPercent(item.maxDrawdown, 2)}</td>
                <td>{item.profitLossRatio == null ? "n/a" : `${formatNumber(item.profitLossRatio)}:1`}</td>
                <td>{item.completedTrades}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function QualityAnalysisPanel({ analysis, onRun, busy }) {
  if (!analysis) {
    return (
      <section className="workspace-panel quality-empty-panel">
        <PanelTitle icon={<Gauge size={17} />} title="多Agent质量诊断" right="技术 / 消息 / 基本面" />
        <div className="empty-state tall">
          <button className="primary-button" type="button" onClick={onRun} disabled={busy}>
            <Play size={17} /> 运行当前标的诊断
          </button>
        </div>
      </section>
    );
  }
  const ai = analysis.ai || {};
  return (
    <section className="quality-analysis-layout">
      <section className={`workspace-panel quality-decision ${ratingClass(analysis.rating)}`}>
        <div>
          <p className="eyebrow">Multi-Agent Research Rating</p>
          <h2>
            {analysis.name} <span>{analysis.symbol}</span>
          </h2>
          <strong>{analysis.rating}</strong>
          <p>{analysis.consensus}</p>
          <em>{analysis.disclaimer}</em>
        </div>
        <div className="decision-dials">
          <span>
            <strong>{analysis.score}</strong>
            <em>质量分</em>
          </span>
          <span>
            <strong>{analysis.confidence}</strong>
            <em>置信度</em>
          </span>
        </div>
      </section>

      <section className="workspace-panel">
        <PanelTitle icon={<Bot size={17} />} title="AI综合" right={ai.provider === "deepseek" ? "DeepSeek" : "本地规则"} />
        <div className="agent-meta-row">
          <span>状态：{ai.status || "local"}</span>
          <span>模型：{ai.model || "--"}</span>
          <span>日线：{analysis.dataStatus?.dailyBars || 0}</span>
          <span>新闻：{analysis.dataStatus?.newsItems || 0}</span>
        </div>
        {ai.message ? <p className="llm-warning">{ai.message}</p> : null}
      </section>

      <section className="agent-grid">
        {(analysis.agents || []).map((agent) => (
          <article key={agent.id} className="workspace-panel agent-card">
            <PanelTitle icon={<Activity size={17} />} title={agent.name} right={agent.rating} />
            <div className="agent-score-line">
              <ScoreBar label="分数" value={agent.score || 0} />
              <ScoreBar label="置信" value={agent.confidence || 0} />
            </div>
            <p>{agent.summary}</p>
            <div className="agent-columns">
              <MiniList title="证据" items={agent.evidence || []} />
              <MiniList title="风险" items={agent.risks || []} />
            </div>
          </article>
        ))}
      </section>

      <section className="review-columns">
        <ReviewList title="支撑买点" tone="good" items={analysis.bullCase || []} />
        <ReviewList title="主要风险" tone="bad" items={analysis.bearCase || []} />
        <ReviewList title="观察清单" tone="neutral" items={analysis.watchPoints || []} />
      </section>
    </section>
  );
}

function DiagnosticPanel({ analysis, result, rows, symbolTitle, onRunQuality, busy }) {
  return (
    <section className="diagnostic-layout">
      <QualityAnalysisPanel analysis={analysis} onRun={onRunQuality} busy={busy} />
      <ReviewPanel result={result} rows={rows} symbolTitle={symbolTitle} />
    </section>
  );
}

function MiniList({ title, items }) {
  return (
    <div className="mini-list">
      <strong>{title}</strong>
      <ul>
        {(items.length ? items : ["暂无"]).slice(0, 5).map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
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
          <div className="fundamental-scoreline">
            <strong>{synced ? profile?.fundamental_score || 0 : "未同步"}</strong>
            <span className={fundamentalGradeClass(profile?.fundamental_grade)}>{profile?.fundamental_grade || "待同步"}</span>
          </div>
        </div>
        <QualityBreakdown breakdown={profile?.fundamental_breakdown} />
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

function StrategyBrief({ form, result, selectedDocsStrategy }) {
  return (
    <section className="rail-panel">
      <PanelTitle icon={<ShieldCheck size={17} />} title="当前策略" right={selectedDocsStrategy?.runId || "docs 待选择"} />
      <div className="strategy-stamp">
        <strong>{selectedDocsStrategy?.label || "请选择 docs 策略"}</strong>
        <span>{selectedDocsStrategy?.source || "来源：docs/research/runs"}</span>
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

function ResearchRunBrief({ run, runs }) {
  const metrics = run?.metrics || {};
  const completedTradeCount = metrics.completedTradeCount ?? run?.resultCounts?.completedTrades;
  const runCount = Array.isArray(runs) ? runs.length : 0;
  const target = getResearchTarget(run);
  const targetPassed = researchTargetPassed(run);
  return (
    <section className="rail-panel">
      <PanelTitle icon={<Database size={17} />} title="研究运行索引" right={run?.runId ? `${runCount} 条` : "读取中"} />
      <div className="strategy-stamp">
        <strong>{run?.runId || "docs/research/runs"}</strong>
        <span>{run?.strategy?.label || run?.label || "等待研究运行证据"}</span>
      </div>
      <ul className="audit-list">
        <li>
          <i className={`audit-dot ${targetPassed ? "ok" : "warn"}`} />
          <span>年化目标：{targetPassed ? "通过" : "未通过/待读取"} / {formatPercent(target.annualized, 0)}</span>
        </li>
        <li>
          <i className={`audit-dot ${isFiniteNumber(metrics.maxDrawdown) && metrics.maxDrawdown >= -0.1 ? "ok" : "warn"}`} />
          <span>
            回撤：{formatPercent(metrics.maxDrawdown, 2)} / 收益：{formatPercent(metrics.totalReturn, 2)}
          </span>
        </li>
        <li>
          <i className={`audit-dot ${isFiniteNumber(completedTradeCount) ? "ok" : "warn"}`} />
          <span>样本：{formatInteger(completedTradeCount)} 笔完成交易</span>
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

function ActionCluster({ label, children }) {
  return (
    <div className="action-cluster">
      <span>{label}</span>
      <div className="action-cluster-buttons">{children}</div>
    </div>
  );
}

function ActionButton({ icon, label, onClick, disabled, compact = false }) {
  return (
    <button className={`ghost-button${compact ? " action-chip" : ""}`} type="button" onClick={onClick} disabled={disabled}>
      {icon}
      {label}
    </button>
  );
}

function SyncProgressStrip({ progress, polling }) {
  if (!progress) return null;
  const pct = progress.status === "error" ? 0 : Math.round(Math.max(0, Math.min(1, Number(progress.progressPct) || 0)) * 100);
  const latestStamp = progress.lastUpdatedAt || progress.lastRun?.createdAt;
  return (
    <div className={`sync-progress-strip${polling ? " active" : ""}${progress.status === "error" ? " bad" : ""}`}>
      <div className="sync-progress-head">
        <span>
          <Database size={14} />
          {progress.label || syncTargetLabel(progress.target)}覆盖
        </span>
        <strong>{progress.status === "error" ? "读取失败" : `${pct}%`}</strong>
      </div>
      <div className="sync-progress-meter" role="progressbar" aria-label="全市场数据覆盖进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow={pct}>
        <i style={{ "--progress": `${pct}%` }} />
      </div>
      <div className="sync-progress-meta">
        {progress.status === "error" ? (
          <span>{progress.error || "进度接口暂不可用"}</span>
        ) : (
          <>
            <span>完整 {formatInteger(progress.completeDates)}/{formatInteger(progress.totalDates)} 日</span>
            <span>稀疏 {formatInteger(progress.sparseDates)} 日</span>
            <span>空缺 {formatInteger(progress.emptyDates)} 日</span>
            <span>记录 {formatInteger(progress.rows)} 条</span>
            <span>最新 {progress.latestDate || "--"} · {formatInteger(progress.latestDateRows)} 条</span>
            <span>更新 {formatDateTime(latestStamp)}</span>
          </>
        )}
      </div>
    </div>
  );
}

function MarketBacktestProgressStrip({ job }) {
  if (!job) return null;
  const pct = Math.round(Math.max(0, Math.min(1, Number(job.progressPct) || 0)) * 100);
  const active = ["queued", "running"].includes(job.status);
  const bad = job.status === "failed";
  return (
    <div className={`sync-progress-strip backtest-progress-strip${active ? " active" : ""}${bad ? " bad" : ""}`}>
      <div className="sync-progress-head">
        <span>
          <BarChart3 size={14} />
          全市场验证
        </span>
        <strong>{bad ? "失败" : `${pct}%`}</strong>
      </div>
      <div className="sync-progress-meter" role="progressbar" aria-label="全市场回测验证进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow={pct}>
        <i style={{ "--progress": `${pct}%` }} />
      </div>
      <div className="sync-progress-meta">
        <span>处理 {formatInteger(job.processed)}/{formatInteger(job.total)} 只</span>
        <span>完成 {formatInteger(job.tested)} 只</span>
        <span>跳过 {formatInteger(job.skipped)} 只</span>
        <span>失败 {formatInteger(job.failed)} 只</span>
        <span>并发 {formatInteger(job.workers)} · 批次 {formatInteger(job.batchSize)}</span>
        <span>{job.message || (active ? "验证中" : "等待验证")}</span>
      </div>
    </div>
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

function MarketBacktestPanel({ result, job }) {
  const rows = result?.results || [];
  const summary = result?.summary || {};
  const scope = result?.scope || {};
  const title = scope.poolName ? "标的池验证" : "全市场验证";
  const emptyText = scope.poolName ? "等待标的池验证" : "等待全市场验证";
  const jobActive = ["queued", "running"].includes(job?.status);
  return (
    <section className="workspace-panel">
      <PanelTitle icon={<Database size={17} />} title={title} right={jobActive ? "后台运行中" : rows.length ? `${rows.length} 只标的` : "仅读数据库"} />
      {scope.poolName ? <div className="market-job-note">范围：{scope.poolName}</div> : null}
      {jobActive ? <div className="market-job-note">后台分批验证中：已处理 {formatInteger(job.processed)}/{formatInteger(job.total)} 只，页面可继续浏览。</div> : null}
      {result ? (
        <div className="market-summary">
          <SummaryCell label="候选" value={summary.candidates ?? 0} />
          <SummaryCell label="完成" value={summary.tested ?? 0} />
          <SummaryCell label="正收益" value={summary.winners ?? 0} />
          <SummaryCell label="中位收益" value={formatPercent(summary.medianReturn, 2)} />
        </div>
      ) : null}
      <div className="table-wrap market-result-table">
        <table>
          <thead>
            <tr>
              <th>排名</th>
              <th>代码</th>
              <th>名称</th>
              <th>行业</th>
              <th>样本</th>
              <th>收益</th>
              <th>回撤</th>
              <th>胜率</th>
              <th>交易</th>
              <th>纪律</th>
            </tr>
          </thead>
          <tbody>
            {rows.length ? (
              rows.slice(0, 120).map((item, index) => (
                <tr key={item.ts_code}>
                  <td>{index + 1}</td>
                  <td>{item.ts_code}</td>
                  <td>{item.name}</td>
                  <td>{item.industry || "--"}</td>
                  <td>{item.dataBars}</td>
                  <td className={item.totalReturn >= 0 ? "positive" : "negative"}>{formatPercent(item.totalReturn, 2)}</td>
                  <td>{formatPercent(item.maxDrawdown, 2)}</td>
                  <td>{formatPercent(item.winRate, 1)}</td>
                  <td>{item.tradeCount}</td>
                  <td>{item.disciplineScore}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan="10" className="empty-state">
                  {emptyText}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function TradeTable({ trades }) {
  return (
    <div className="table-wrap trades-table">
      <table>
        <thead>
          <tr>
            <th>日期</th>
            <th>代码</th>
            <th>名称</th>
            <th>动作</th>
            <th>价格</th>
            <th>数量</th>
            <th>费用</th>
            <th>现金</th>
            <th>成交规则</th>
            <th>原因</th>
          </tr>
        </thead>
        <tbody>
          {trades.length ? (
            trades.map((trade, index) => (
              <tr key={`${trade.date}-${trade.action}-${index}`}>
                <td>{trade.date}</td>
                <td>{trade.ts_code || "--"}</td>
                <td>{trade.name || "--"}</td>
                <td>
                  <span className={`trade-action ${trade.action === "买入" ? "buy" : "sell"}`}>{trade.action}</span>
                </td>
                <td>{formatMoney(trade.price)}</td>
                <td>{trade.quantity}</td>
                <td>{formatMoney(trade.fee)}</td>
                <td>{formatMoney(trade.cash)}</td>
                <td>{trade.priceRule || "--"}</td>
                <td>{trade.reason}</td>
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan="10" className="empty-state">
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

function buildMarketMetrics(result) {
  const summary = result?.summary || {};
  const scopeLabel = result?.scope?.poolName ? "池内算术平均" : "全市场算术平均";
  return [
    { label: "测试标的", value: formatInteger(summary.tested), tone: "neutral", sub: `候选 ${formatInteger(summary.candidates)} / 跳过 ${formatInteger(summary.skipped)}` },
    { label: "正收益占比", value: formatPercent(summary.positiveRate, 1), tone: summary.positiveRate >= 0.5 ? "good" : "bad", sub: `${formatInteger(summary.winners)} 只为正` },
    { label: "平均收益", value: formatPercent(summary.avgReturn, 2), tone: summary.avgReturn >= 0 ? "good" : "bad", sub: scopeLabel },
    { label: "中位收益", value: formatPercent(summary.medianReturn, 2), tone: summary.medianReturn >= 0 ? "good" : "bad", sub: "更抗极端值" },
  ];
}

function buildDashboardMetrics({ result, marketResult, researchRun, rows, activePool }) {
  const runMetrics = researchRun?.metrics || {};
  const marketSummary = marketResult?.summary || {};
  const totalReturn = firstFinite(result?.totalReturn, runMetrics.totalReturn, marketSummary.avgReturn);
  const annualizedReturn = firstFinite(result?.annualizedReturn, runMetrics.annualizedReturn);
  const maxDrawdown = firstFinite(result?.maxDrawdown, runMetrics.maxDrawdown, marketSummary.avgMaxDrawdown);
  const sharpeRatio = firstFinite(result?.sharpeRatio, runMetrics.sharpeRatio);
  const calmarRatio = firstFinite(result?.calmarRatio, runMetrics.calmarRatio);
  const finalEquity = firstFinite(result?.finalEquity, researchRun?.summary?.finalEquity);
  const positiveRate = firstFinite(marketSummary.positiveRate);
  const completedTrades = firstFinite(result?.completedTradeCount, result?.completedTrades?.length, runMetrics.completedTradeCount);

  return [
    {
      label: marketResult && !result ? "平均收益" : "总收益",
      value: formatPercent(totalReturn, 2),
      tone: totalReturn >= 0 ? "good" : "bad",
      sub: marketResult && !result ? "批量样本均值" : rows.length ? `${rows.length} 根日线` : "等待行情",
    },
    {
      label: "年化收益",
      value: formatPercent(annualizedReturn, 2),
      tone: annualizedReturn >= 0.3 ? "good" : annualizedReturn < 0 ? "bad" : "neutral",
      sub: "阶段目标按 docs 判定",
    },
    {
      label: "最大回撤",
      value: formatPercent(maxDrawdown, 2),
      tone: maxDrawdown >= -0.1 ? "good" : "bad",
      sub: "硬线参考 10%",
    },
    {
      label: "夏普比率",
      value: formatNumber(sharpeRatio),
      tone: sharpeRatio >= 1 ? "good" : "neutral",
      sub: `完成交易 ${formatInteger(completedTrades)}`,
    },
    {
      label: "卡玛比率",
      value: formatNumber(calmarRatio),
      tone: calmarRatio >= 1 ? "good" : "neutral",
      sub: "收益 / 回撤",
    },
    {
      label: marketResult ? "正收益占比" : "最终资产",
      value: marketResult ? formatPercent(positiveRate, 1) : formatMoney(finalEquity),
      tone: marketResult ? (positiveRate >= 0.5 ? "good" : "bad") : "neutral",
      sub: activePool?.name || "全市场视角",
    },
  ].map((item) => ({ ...item, value: item.value || "--" }));
}

function buildDashboardEquityCurve(result, researchRun) {
  if (result?.equity?.length) return buildEquityCurveFromRaw(result.equity);
  if (researchRun?.equityCurve?.length) return researchRun.equityCurve;
  return [];
}

function buildEquityCurveFromRaw(points) {
  if (!points.length) return [];
  const initialEquity = Number(points[0]?.equity || 0) || 1;
  let peak = initialEquity;
  return points
    .filter((point) => point?.date)
    .map((point) => {
      const equity = Number(point.equity || 0);
      peak = Math.max(peak, equity);
      return {
        date: point.date,
        equity,
        returnPct: initialEquity ? equity / initialEquity - 1 : 0,
        drawdown: peak ? equity / peak - 1 : 0,
        cash: point.cash,
        positions: point.positions,
      };
    });
}

function buildDashboardAuditRows(marketResult, researchRun) {
  const marketRows = marketResult?.results || [];
  if (marketRows.length) return marketRows;
  return researchRun?.symbolAuditRows || researchRun?.symbolAudit?.rows || [];
}

function buildDashboardHealth({ rows, result, marketResult, marketJob, activePool, form, researchRun }) {
  const jobActive = ["queued", "running"].includes(marketJob?.status);
  const metrics = result || researchRun?.metrics || {};
  const maxDrawdown = firstFinite(metrics.maxDrawdown, marketResult?.summary?.avgMaxDrawdown);
  const sampleSize = firstFinite(marketResult?.summary?.tested, researchRun?.metrics?.completedTradeCount, result?.completedTradeCount, rows.length);
  const riskRulesOn = Boolean(form.blockWeakMarket && form.forceStopOverridesLimit && form.blockSameDayReentry);
  return [
    {
      label: "数据引擎",
      value: rows.length ? "在线" : "待载入",
      detail: rows.length ? `${rows.length} 根日线` : "数据库未返回行情",
      tone: rows.length ? "good" : "warn",
    },
    {
      label: "回测状态",
      value: jobActive ? "运行中" : result || marketResult || researchRun ? "有结果" : "等待",
      detail: marketJob?.message || "本地研究输出",
      tone: jobActive ? "warn" : result || marketResult || researchRun ? "good" : "muted",
    },
    {
      label: "回撤预警",
      value: formatPercent(maxDrawdown, 2),
      detail: isFiniteNumber(maxDrawdown) ? "最大回撤线" : "缺少回测指标",
      tone: !isFiniteNumber(maxDrawdown) ? "muted" : maxDrawdown >= -0.1 ? "good" : "bad",
    },
    {
      label: "样本规模",
      value: formatInteger(sampleSize),
      detail: marketResult ? "批量验证标的" : "行情/交易样本",
      tone: sampleSize >= 30 ? "good" : "warn",
    },
    {
      label: "资金纪律",
      value: riskRulesOn ? "已启用" : "需复核",
      detail: `单票上限 ${form.positionCapPct}% / 单笔风险 ${form.riskPct}%`,
      tone: riskRulesOn ? "good" : "warn",
    },
    {
      label: "标的池",
      value: activePool?.member_count ? `${activePool.member_count} 只` : "未选择",
      detail: activePool?.name || "默认全市场",
      tone: activePool?.member_count ? "good" : "muted",
    },
  ];
}

function buildDashboardWorkflow({ rows, screenResults, result, marketResult, marketJob, newsItems }) {
  const jobActive = ["queued", "running"].includes(marketJob?.status);
  return [
    {
      label: "数据准备",
      status: rows.length ? "已完成" : "等待中",
      detail: rows.length ? `${formatInteger(rows.length)} 根日线` : "等待行情载入",
      progress: rows.length ? 100 : 0,
      tone: rows.length ? "good" : "muted",
      target: "single",
      action: "载入行情",
    },
    {
      label: "候选筛选",
      status: screenResults.length ? "已完成" : "待执行",
      detail: screenResults.length ? `${formatInteger(screenResults.length)} 个候选` : "等待刷新候选",
      progress: screenResults.length ? 100 : 0,
      tone: screenResults.length ? "good" : "warn",
      target: "screen",
      action: "选股池",
    },
    {
      label: "回测验证",
      status: jobActive ? "运行中" : result || marketResult ? "已完成" : "等待中",
      detail: marketJob?.message || (marketResult ? "批量结果可审计" : result ? "单票结果可复盘" : "等待回测"),
      progress: jobActive ? Math.max(4, Math.round(Number(marketJob?.progressPct || 0))) : result || marketResult ? 100 : 0,
      tone: jobActive ? "warn" : result || marketResult ? "good" : "muted",
      target: "market",
      action: "验证",
    },
    {
      label: "复盘诊断",
      status: result?.aiAnalysis || newsItems.length ? "有证据" : "待补充",
      detail: result?.aiAnalysis?.verdict || (newsItems.length ? `${formatInteger(newsItems.length)} 条消息` : "等待质量诊断"),
      progress: result?.aiAnalysis || newsItems.length ? 100 : 0,
      tone: result?.aiAnalysis || newsItems.length ? "good" : "muted",
      target: "diagnostic",
      action: "复盘",
    },
  ];
}

function buildDashboardVerdict(result, marketResult, researchRun, jobActive) {
  if (jobActive) return "后台批量验证正在运行";
  if (researchRun) return buildResearchRunVerdict(researchRun, "");
  if (marketResult) {
    const summary = marketResult.summary || {};
    return `批量验证完成：${formatInteger(summary.tested)} 只，正收益 ${formatPercent(summary.positiveRate, 1)}`;
  }
  if (result) {
    return `单票回测完成：收益 ${formatPercent(result.totalReturn, 2)}，回撤 ${formatPercent(result.maxDrawdown, 2)}`;
  }
  return "等待筛选、回测或全市场验证结果";
}

function riskStrength(tone) {
  if (tone === "bad") return 6;
  if (tone === "warn") return 4;
  if (tone === "good") return 2;
  return 1;
}

function firstFinite(...values) {
  return values.find((value) => isFiniteNumber(value));
}

function getDataRequest(form) {
  if (!form.tsCode.trim()) throw new Error("请填写股票代码或股票名称。");
  if (new Date(form.endDate) < new Date(form.startDate)) throw new Error("结束日期不能早于开始日期。");
  return { ts_code: form.tsCode.trim().toUpperCase(), start_date: form.startDate, end_date: form.endDate };
}

function parseStockCodes(text) {
  return text
    .split(/[\s,，;；]+/)
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean);
}

function buildBacktestConfig(form, baseConfig = null) {
  const visibleConfig = {
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
    tailEntryMinPctChg: Number(form.tailEntryMinPctChg) / 100,
    tailEntryMaxPctChg: Number(form.tailEntryMaxPctChg) / 100,
    tailPriorLimitUpLookback: Number(form.tailPriorLimitUpLookback),
    tailMinVolumeRatio: Number(form.tailMinVolumeRatio),
    tailMinTurnoverRatePct: Number(form.tailMinTurnoverRatePct),
    tailLimitUpPct: Number(form.tailLimitUpPct) / 100,
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
  return { ...(baseConfig || {}), ...visibleConfig };
}

function getResearchRunConfig(run) {
  return run?.strategy?.config || run?.payload?.config || {};
}

function mapDocsConfigToForm(config) {
  const mapped = {};
  assignFormValue(mapped, "marketState", config.marketState);
  assignFormValue(mapped, "entryMode", config.entryMode);
  assignFormNumber(mapped, "initialCash", config.initialCash);
  assignFormNumber(mapped, "weeklyTradeLimit", config.weeklyTradeLimit);
  assignFormPercent(mapped, "positionCapPct", config.positionCapPct ?? config.maxSinglePositionPct);
  assignFormPercent(mapped, "riskPct", config.riskPct);
  assignFormPercent(mapped, "stopLossPct", config.stopLossPct);
  assignFormPercent(mapped, "takeProfit1Pct", config.takeProfit1Pct);
  assignFormPercent(mapped, "takeProfit2Pct", config.takeProfit2Pct);
  assignFormPercent(mapped, "commissionPct", config.commissionPct);
  assignFormPercent(mapped, "stampDutyPct", config.stampDutyPct);
  assignFormNumber(mapped, "lotSize", config.lotSize);
  assignFormNumber(mapped, "bollPeriod", config.bollPeriod);
  assignFormNumber(mapped, "bollDev", config.bollDev);
  assignFormPercent(mapped, "bollTolerancePct", config.bollTolerancePct);
  assignFormPercent(mapped, "bollBandwidthMaxPct", config.bollBandwidthMaxPct);
  assignFormPercent(mapped, "midlineTolerancePct", config.midlineTolerancePct);
  assignFormNumber(mapped, "trendFastPeriod", config.trendFastPeriod);
  assignFormNumber(mapped, "trendSlowPeriod", config.trendSlowPeriod);
  assignFormNumber(mapped, "trendLongPeriod", config.trendLongPeriod);
  assignFormNumber(mapped, "volumeMaPeriod", config.volumeMaPeriod);
  assignFormNumber(mapped, "volumeBreakoutMultiplier", config.volumeBreakoutMultiplier);
  assignFormPercent(mapped, "tailEntryMinPctChg", config.tailEntryMinPctChg);
  assignFormPercent(mapped, "tailEntryMaxPctChg", config.tailEntryMaxPctChg);
  assignFormNumber(mapped, "tailPriorLimitUpLookback", config.tailPriorLimitUpLookback);
  assignFormNumber(mapped, "tailMinVolumeRatio", config.tailMinVolumeRatio);
  assignFormNumber(mapped, "tailMinTurnoverRatePct", config.tailMinTurnoverRatePct);
  assignFormPercent(mapped, "tailLimitUpPct", config.tailLimitUpPct);
  assignFormValue(mapped, "useTrendFilter", config.useTrendFilter);
  assignFormValue(mapped, "useMacdFilter", config.useMacdFilter);
  assignFormNumber(mapped, "macdFastPeriod", config.macdFastPeriod);
  assignFormNumber(mapped, "macdSlowPeriod", config.macdSlowPeriod);
  assignFormNumber(mapped, "macdSignalPeriod", config.macdSignalPeriod);
  assignFormValue(mapped, "macdRequireZeroAxis", config.macdRequireZeroAxis);
  assignFormValue(mapped, "useRsiFilter", config.useRsiFilter);
  assignFormNumber(mapped, "rsiPeriod", config.rsiPeriod);
  assignFormNumber(mapped, "rsiLowerBound", config.rsiLowerBound);
  assignFormNumber(mapped, "rsiUpperBound", config.rsiUpperBound);
  assignFormNumber(mapped, "kdjPeriod", config.kdjPeriod);
  assignFormNumber(mapped, "atrPeriod", config.atrPeriod);
  assignFormValue(mapped, "useAtrStop", config.useAtrStop);
  assignFormNumber(mapped, "atrStopMultiplier", config.atrStopMultiplier);
  assignFormValue(mapped, "blockWeakMarket", config.blockWeakMarket);
  assignFormValue(mapped, "forceStopOverridesLimit", config.forceStopOverridesLimit);
  assignFormValue(mapped, "blockSameDayReentry", config.blockSameDayReentry);
  return mapped;
}

function assignFormValue(target, key, value) {
  if (value !== undefined && value !== null && value !== "") target[key] = value;
}

function assignFormNumber(target, key, value) {
  if (value === undefined || value === null || value === "") return;
  const numberValue = Number(value);
  if (Number.isFinite(numberValue)) target[key] = compactNumberString(numberValue);
}

function assignFormPercent(target, key, value) {
  if (value === undefined || value === null || value === "") return;
  const numberValue = Number(value);
  if (Number.isFinite(numberValue)) target[key] = compactNumberString(numberValue * 100);
}

function compactNumberString(value) {
  return Number(value).toFixed(6).replace(/\.?0+$/, "");
}

function buildDocsStrategyCards(runs) {
  return (runs || [])
    .filter((run) => run?.runId && (run.strategyName || run.label || run.resultFiles?.strategies))
    .slice(0, 12);
}

function strategyStatusText(status) {
  if (status === "target_pass") return "阶段通过";
  if (status === "completed" || status === "ok") return "观察";
  if (status === "review") return "复核";
  if (status === "failed") return "失败";
  return status || "docs";
}

function strategyStatusClass(status) {
  if (status === "target_pass") return "pass";
  if (status === "failed") return "fail";
  if (status === "review") return "review";
  return "observe";
}

function formatProfitLossRatio(value) {
  return isFiniteNumber(value) ? `${formatNumber(value)}:1` : "--";
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

function fundamentalGradeClass(grade) {
  const normalized = String(grade || "").toLowerCase();
  return `quality-grade ${["a", "b", "c", "d"].includes(normalized) ? `grade-${normalized}` : "grade-pending"}`;
}

function ratingClass(rating) {
  if (rating === "买入") return "rating-buy";
  if (rating === "持有") return "rating-hold";
  if (rating === "卖出") return "rating-sell";
  return "rating-neutral";
}

function robustnessStatusText(status) {
  if (status === "needs_longer_history") return "缺长周期数据";
  if (status === "ready_for_stage2") return "待跑稳健性";
  if (status === "blocked_stage1") return "三年未过线";
  if (status === "passed") return "稳健性通过";
  return "待判定";
}

function robustnessStatusClass(status) {
  if (status === "passed" || status === "ready_for_stage2") return "pass";
  if (status === "needs_longer_history") return "blocked";
  if (status === "blocked_stage1") return "fail";
  return "pending";
}

function robustnessGateText(status) {
  if (status === "pass") return "通过";
  if (status === "fail") return "失败";
  if (status === "blocked") return "缺数据";
  if (status === "locked") return "未启动";
  return "待运行";
}

function robustnessGateClass(status) {
  if (status === "pass") return "pass";
  if (status === "fail") return "fail";
  if (status === "blocked") return "blocked";
  if (status === "locked") return "locked";
  return "pending";
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

function addLineSeries(chart, rows, key, color, title, lineWidth = 2, priceScaleId = "right") {
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

function syncTargetLabel(target) {
  return target === "daily_basic" ? "全市场估值" : "全市场日线";
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

function formatInteger(value) {
  if (!isFiniteNumber(value)) return "--";
  return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

function formatMarketCap(value) {
  if (!isFiniteNumber(value)) return "--";
  return `${(Number(value) / 10000).toLocaleString("zh-CN", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}亿`;
}

function formatDateTime(value) {
  if (!value) return "--";
  return String(value).replace("T", " ").slice(0, 16);
}

function dateToday() {
  return formatDate(new Date());
}

function dateYearsAgo(years) {
  const date = new Date();
  date.setFullYear(date.getFullYear() - years);
  return formatDate(date);
}

function dateYearsBefore(value, years) {
  const [year, month, day] = String(value || dateToday()).split("-").map(Number);
  const date = new Date(year, (month || 1) - 1, day || 1);
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
  downloadJson(result, "backtest-report");
}

function downloadJson(result, prefix = "backtest-report") {
  if (!result) return;
  const blob = new Blob([JSON.stringify(result, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${prefix}-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

createRoot(document.getElementById("root")).render(<App />);
