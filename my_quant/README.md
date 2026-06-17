# my_quant 研究工作区

`my_quant/` 是从 `xquant-beginner` 迁入的量化研究档案，包含 ETF 组合实验、A 股 B1 趋势回调复刻、Tushare 质量过滤结果、Web 报告和盘前预案自动化脚本。

这个目录只做研究、回测、报告和人工复盘辅助，不连接券商，不自动下单。

## 环境要求

- Python `3.12`。macOS 自带的 `/usr/bin/python3` 常是 `3.9`，不要直接用它建这个环境。
- 仓库根目录 `.venv`。自动化脚本默认调用 `./.venv/bin/python`。
- 需要实时 Tushare 数据时，在仓库根目录 `.env.local` 写入 `TUSHARE_TOKEN=...`；不要提交 `.env.local`。

## 方式一：uv

在仓库根目录执行：

```bash
uv venv .venv --python 3.12
. .venv/bin/activate
uv pip install -r my_quant/requirements.txt
```

也可以直接按 `pyproject.toml` 使用 uv project：

```bash
uv sync --project my_quant
uv run --project my_quant python -m unittest discover my_quant/strategy_research/tests -v
```

注意：盘前自动化默认读仓库根目录 `.venv/bin/python`。如果只使用 `uv sync --project my_quant` 生成了 `my_quant/.venv`，运行自动化时需要显式指定：

```bash
B1_PYTHON=my_quant/.venv/bin/python my_quant/strategy_research/automation/run_b1_daily.sh
```

## 方式二：venv + pip

在仓库根目录执行：

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r my_quant/requirements.txt
```

Windows PowerShell 可用：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r my_quant\requirements.txt
```

## Tushare token

只在需要 Tushare 数据源时配置：

```bash
printf 'TUSHARE_TOKEN=你的本地token\n' > .env.local
set -a; source .env.local; set +a
```

不要把真实 token 写进 README、源码、测试、日志或提交信息。

## 验证环境

先跑单元测试：

```bash
.venv/bin/python -m unittest discover my_quant/strategy_research/tests -v
```

再跑一个默认 ETF 组合实验：

```bash
.venv/bin/python my_quant/strategy_research/run_full_experiment.py
```

如果要验证 B1 Tushare 路线，先加载 `.env.local`，再执行：

```bash
set -a; source .env.local; set +a
.venv/bin/python -m my_quant.strategy_research.run_b1_walk_forward \
  --data-provider tushare \
  --max-symbols 300 \
  --stride 10 \
  --output-prefix b1_tushare_walk_forward_stride10_300
```

## 常用入口

- 研究说明：`my_quant/strategy_research/README.md`
- 依赖文件：`my_quant/requirements.txt`
- uv 项目配置：`my_quant/pyproject.toml`
- 默认实验：`my_quant/strategy_research/run_full_experiment.py`
- Walk-Forward：`my_quant/strategy_research/run_walk_forward.py`
- B1 趋势回调：`my_quant/strategy_research/run_b1_trend_pullback.py`
- B1 盘前预案：`my_quant/strategy_research/automation/run_b1_daily.sh`
- 最新 HTML 报告：`my_quant/strategy_research/web_report/index.html`
