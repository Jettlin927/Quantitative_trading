# 策略研究档案

这个目录把 `my_quant/TODO.md` 中的策略完善路线拆成可执行、可对比、可复查的研究档案。这里的目标不是把历史回测调到最高，而是回答一个更严格的问题：有没有策略能在统一口径下稳定超过“中国永久组合”。

## 目录结构

- `evaluation_framework.md`：统一数据、指标、基准和淘汰规则。
- `final_candidate.md`：当前最优研究候选，以及为什么还不能直接称为最终生产策略。
- `goal_and_plan.md`：后续研究 goal 与实施计划。
- `run_full_experiment.py`：完整实验入口，生成 `results/` 下的对比表、摘要和 manifest。
- `run_candidate_backtest.py`：兼容旧入口，内部调用完整实验入口。
- `experiment/`：可复用实验工程模块。
  - `config.py`：资产池、日期、成本、策略参数配置。
  - `data.py`：AkShare 数据拉取与本地缓存。
  - `strategies.py`：等权、风险平价、RAM TopN 权重函数。
  - `backtest.py`：再平衡、换手成本和净值回测。
  - `metrics.py`：收益、波动、回撤、夏普、卡玛、索提诺。
  - `reports.py`：摘要、JSON 和 manifest 生成。
  - `validation.py`：Rolling / Anchored Walk-Forward 验证。
  - `factor_diagnostics.py`：动量、RAM、低波动和趋势强度 IC 诊断。
  - `pipeline.py`：完整实验编排。
- `tests/`：标准库 `unittest` 测试，不额外引入 pytest。
- `strategies/00_baseline_china_permanent/`：中国永久组合基准。
- `strategies/01_universe_diversification/`：资产池扩展与低相关筛选。
- `strategies/02_risk_parity_permanent/`：风险平价版永久组合。
- `strategies/03_ram_topn_switch/`：风险调整动量 TopN 进攻/防守切换。
- `strategies/04_rebalance_cost_control/`：调仓频率与交易成本。
- `strategies/05_stoploss_trend_filter/`：止损与趋势过滤。
- `strategies/06_parameter_sensitivity/`：参数敏感性。
- `strategies/07_oos_walk_forward/`：样本外与 Walk-Forward。
- `strategies/08_factor_diagnostics/`：因子 IC 诊断。
- `strategies/09_final_candidate_ram_top2/`：TODO 中优先级最高的候选路线。

## 运行方式

先在仓库根目录准备 `my_quant` 独立研究环境。推荐用 uv：

```bash
uv venv .venv --python 3.12
. .venv/bin/activate
uv pip install -r my_quant/requirements.txt
```

如果不用 uv，也可以用标准 venv：

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r my_quant/requirements.txt
```

完整接手说明见 `my_quant/README.md`。

在仓库根目录执行：

```bash
.venv/bin/python my_quant/strategy_research/run_full_experiment.py
```

兼容旧入口也可用：

```bash
.venv/bin/python my_quant/strategy_research/run_candidate_backtest.py
```

全量 Walk-Forward 比默认实验慢，单独执行：

```bash
.venv/bin/python my_quant/strategy_research/run_walk_forward.py
```

B1 A 股趋势回调复刻默认使用 AkShare/Tencent 缓存。若要切换为 Tushare 数据源，先确保当前 Python 解释器安装了 `tushare`，并在环境变量中提供 token：

```bash
set -a; source .env.local; set +a
.venv/bin/python -m my_quant.strategy_research.run_b1_trend_pullback --data-provider tushare --max-symbols 100 --stride 10
.venv/bin/python -m my_quant.strategy_research.run_b1_walk_forward --data-provider tushare --max-symbols 300 --stride 10 --output-prefix b1_tushare_walk_forward_stride10_300
```

Tushare 路径会使用：

- `stock_basic` 构建 A 股候选池，并过滤 ST、退市和非沪深主市场代码。
- `pro_bar(adj=qfq)` 拉取个股前复权日线。
- `index_daily(000300.SH)` 作为大盘长均线过滤。
- 本地 CSV 缓存，避免重复消耗接口频率。

默认 ETF 组合实验脚本会读取或刷新 AkShare ETF 日线数据，并生成：

- `results/base_strategy_comparison.csv`
- `results/ram_parameter_scan.csv`
- `results/train_parameter_scan.csv`
- `results/train_best_oos_result.csv`
- `results/walk_forward_shortlist_summary.csv`
- `results/factor_ic_summary.csv`
- `results/latest_summary.md`
- `results/latest_summary.json`
- `results/experiment_manifest.json`

## 测试方式

在仓库根目录执行：

```bash
.venv/bin/python -m unittest discover my_quant/strategy_research/tests -v
```

当前测试覆盖：

- 权重归一化与未知代码校验。
- 全弱势时切到 `511880`。
- RAM TopN 只选择正分数资产并归一化。
- 初始换手成本和后续收益复利。
- 指标中的累计收益和最大回撤。
- manifest 记录候选策略和结果产物。
- shortlist Walk-Forward 输出 rolling / anchored 两类窗口。
- 因子 IC 输出 momentum / RAM / low volatility / trend strength。
- B1 指标、入场质量过滤、候选排序、大盘过滤、Tushare 缓存和 token 校验。
- B1 现实成交约束：开盘价成交、100 股一手取整、涨停买入阻断和跌停卖出阻断。
- B1 分数权重可配置，用于校准本地 `B1_proxy_score`。
- B1 active universe 支持按 as-of 日期或 walk-forward 窗口起点构建，避免固定股票池默认化。

## 研究纪律

- 先和中国永久组合比较，再谈 Alpha。
- 不接受只看累计收益的结论。
- 不接受只有一个参数点跑赢的“魔法参数”。
- 低位不是入场信号，止跌才是入场信号。
- 所有结论只用于课程研究和回测学习，不构成投资建议。
