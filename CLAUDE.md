# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

本地运行的 A 股量化研究工作台。本项目是**研究工具**，不是自动交易系统——不连接券商，不处理真实资金，只用于策略验证、回测和复盘。

核心价值：把交易纪律、技术形态、基本面数据和消息面热点放到同一个页面里进行本地化复盘研究。

## 快速启动

```powershell
# 首次启动：复制环境变量模板
Copy-Item .env.example .env
notepad .env

# 至少填写：
# TUSHARE_TOKEN=你的_tushare_token
# DEEPSEEK_TOKEN=你的_deepseek_token

# 启动系统
.\启动回测系统.cmd

# 修改代码后重新构建
.\重新构建并启动回测系统.cmd

# 停止服务
.\停止回测系统.cmd
```

启动后访问：
- 前端工作台：http://localhost:15173
- API 文档：http://localhost:18000/docs

## 技术栈与架构

### 整体架构
- **前端**：React + Vite + lightweight-charts（K 线图表）
- **后端**：FastAPI + SQLAlchemy 2.0
- **数据库**：PostgreSQL 16
- **运行方式**：Docker Compose（三服务：db、api、frontend）

### 目录结构
```
backend/app/          # FastAPI 后端
  ├── main.py         # 主入口，路由定义
  ├── models.py       # SQLAlchemy 数据模型
  ├── schemas.py      # Pydantic 请求/响应模型
  ├── database.py    # 数据库连接
  ├── backtest_engine.py  # 回测引擎核心逻辑
  ├── tushare_client.py   # Tushare 数据同步
  └── ai_client.py    # DeepSeek AI 集成

frontend/src/         # React 前端（TypeScript + JSX）
  ├── main.jsx        # 入口
  └── styles.css      # 全局样式

scripts/research/     # 研究脚本（策略回测、滚动窗口验证等）
  ├── run_research_round.py        # 单次研究 run
  ├── run_portfolio_backtest.py    # 投资组合回测
  └── run_window_validation.py     # 滚动窗口验证

docs/research/        # 研究文档与证据
  ├── long-term-goal.md         # 长期目标与阶段规则
  ├── research-runs.json        # 研究 run 索引
  ├── stages/                   # 阶段目录（000-007）
  └── runs/                     # 具体 run 结果目录
```

## 常用开发命令

### 后端开发
```bash
# 安装依赖
pip install -r backend/requirements.txt

# 运行后端（需要先启动 PostgreSQL）
cd backend
uvicorn app.main:app --reload --port 8000
```

### 前端开发
```bash
cd frontend
npm install
npm run dev       # 开发模式（热重载）
npm run build     # 生产构建
npm run lint      # ESLint 检查
```

### 数据库
```bash
# 连接本地 PostgreSQL
docker exec -it quant_trading_db psql -U quant -d quant_trading
```

## 核心概念

### 研究阶段体系
项目采用严格的阶段推进制度，定义在 `docs/research/long-term-goal.md`：

| 阶段 | 目标 | 状态 |
| --- | --- | --- |
| 001-observation-diagnosis | 解释失败窗口归因 | completed |
| 002-candidate-repair-30 | 年化 >= 30%，滚动窗口 5/7 通过 | active |
| 003-005 | 研究级候选（50%/75%/100%） | planned |
| 006-007 | 纸面交易与实盘前验证 | planned |

**重要**：阶段只能顺序推进，不能跳级。当前活跃阶段记录在 `docs/research/research-runs.json`。

### 数据模型
核心表：
- `stocks`：股票基础信息（代码、名称、行业、市场）
- `stock_daily_bars`：日线 OHLCV（按 `ts_code + trade_date` 去重）
- `stock_daily_basic`：估值指标（PE、PB、换手率、市值）
- `stock_financial_indicators`：财务指标（ROE、毛利率、负债率等）
- `stock_pools` / `stock_pool_members`：自选标的池

### 回测引擎
`backtest_engine.py` 是核心策略执行层：
- 支持单票回测和投资组合回测
- 可配置交易纪律（周交易次数限制、仓位上限、止损止盈）
- 内置技术指标（MA、BOLL、MACD、RSI、KDJ、ATR）
- 支持滑点、印花税、涨跌停、T+1 等 A 股特性

**默认配置**见 `DEFAULT_CONFIG` 字典。

### 研究脚本工作流
1. `run_research_round.py`：单次策略验证，生成 run 结果
2. `run_portfolio_backtest.py`：投资组合级别回测（支持资金共享、仓位约束）
3. `run_window_validation.py`：滚动窗口稳健性验证

所有结果写入 `docs/research/runs/<run-id>/`，包含 `results.json` 和 `review.md`。

## 交易纪律默认值
- 每周最多交易 2 次
- 单票仓位上限 20%
- 单笔风险上限 1%
- 止损 5%，止盈 3%/5% 分级
- A 股 100 股一手取整

## 环境变量
关键变量见 `.env.example`：
- `TUSHARE_TOKEN`：数据源（必需）
- `DEEPSEEK_TOKEN`：AI 复盘（可选，降级为本地规则）
- `POSTGRES_*`：数据库连接
- `API_PORT` / `FRONTEND_PORT`：服务端口

## 重要约束
1. **不提交敏感信息**：`.env` 已被 `.gitignore` 忽略
2. **不删除数据**：避免 `docker compose down -v`（会删除 PostgreSQL volume）
3. **阶段纪律**：研究工作只能在当前活跃阶段内进行，不得临时发明新阶段
4. **证据保存**：失败的研究 run 不得删除，负证据必须保留

## 前端开发提示
- 使用 `lucide-react` 图标库
- K 线图表基于 `lightweight-charts`
- 前端有独立的 `/frontend-design` skill 用于高质量 UI 设计
