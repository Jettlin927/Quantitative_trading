# Remote P0 Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在远端新增隔离目录 `/opt/quantitative-trading-todo-p0-20260629`，先落地 `TODO.md` 的 P0 PostgreSQL 数据底座，不污染原 `/opt/quantitative-trading` 目录、原容器和原 PostgreSQL volume。

**Architecture:** 以当前 FastAPI + SQLAlchemy 2.0 + PostgreSQL + React/Vite 数据工作台为基础，只新增 P0 数据表、幂等 upsert 同步接口、只读查询和 DB overview 覆盖展示。远端使用独立目录、独立 Compose project、独立容器名、独立端口和独立 Docker volume 验证，原服务只做只读状态比对。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2.0、PostgreSQL 16、Tushare、React + Vite、Docker Compose。

## Global Constraints

- 默认语言：中文。
- 本阶段只做 `TODO.md` 的 P0：交易日历、复权因子、指数基础信息、指数日线、ETF 基础信息、ETF 日线、申万行业分类与成员。
- P1/P2 不进入本阶段实现；资金流、研报、题材快照、分钟线、Tick、ETF 期权和龙虎榜均不实现。
- 不恢复策略执行、交易信号、买卖评级、真实账户、真实持仓、券商连接或自动交易。
- 允许先在本地隔离 Docker sandbox 做预验收；完成判定仍必须在远端服务器 sandbox 复验。
- 远端不得执行 `docker compose down -v`、`docker volume rm` 或任何删除原 PostgreSQL volume 的命令。
- 远端隔离目录固定为 `/opt/quantitative-trading-todo-p0-20260629`。
- 远端隔离 Compose project 固定为 `quant_todo_p0`。
- 远端隔离端口固定为 `127.0.0.1:15433:5432`、`127.0.0.1:18002:8000`、`127.0.0.1:15175:5173`。
- 原远端目录 `/opt/quantitative-trading` 只允许读取状态，不允许在该目录里构建、重启、写入代码或清理 volume。
- 不把 `.env`、Tushare token、数据库密码或任何凭据写入源码、日志、前端、README、测试或聊天输出。

---

## Assumptions

1. `TODO.md` 是当前数据底座路线图，不是恢复旧策略研究路线的授权。
2. 第一阶段成功标准是 P0 同步能力、只读查询和 DB overview 闭环成立；Tushare 权限敏感接口用固定小样本真实同步验收，不把全量数据拿齐作为阻塞条件。
3. 当前 `docker-compose.yml` 写死了 `container_name`，且 Compose 对 `ports` 列表会追加合并，所以远端隔离不能叠加原 Compose 文件，必须使用完整的远端-only `docker-compose.sandbox.yml`。
4. Tushare token 已存在于远端原目录 `.env` 中；执行时只在远端复制该 `.env` 到隔离目录，不在源码中保存。
5. 如果 Tushare 某个指数、基金或行业接口权限不足，本阶段允许对应同步接口返回 `partial`，但必须记录失败接口、入参、错误信息和未完成的验收项；不能因为单个高权限样本失败而阻塞其他 P0 表验收。

## Success Criteria

- 远端原服务验证前后容器名、容器 ID、端口和原 volume 名称不变。
- 隔离目录存在，隔离 Compose project 可通过 `docker compose config`。
- P0 新表均有自然键或唯一约束，并支持重复同步幂等 upsert：
  - `trade_calendars`：`exchange + cal_date` 唯一。
  - `stock_adjust_factors`：`ts_code + trade_date` 唯一。
  - `indices`：`ts_code` 主键或唯一。
  - `index_daily_bars`：`ts_code + trade_date` 唯一。
  - `funds`：`ts_code` 主键或唯一。
  - `fund_daily_bars`：`ts_code + trade_date` 唯一。
  - `industry_classifications`：`index_code` 主键或唯一。
  - `industry_members`：`index_code + con_code + in_date` 唯一。
- `GET /api/db/overview` 返回 P0 覆盖信息：交易日历最新开市日、复权因子覆盖、指数日线覆盖、ETF 日线覆盖、行业分类和成员数量。
- 远端后端编译、后端 unittest、Compose config、前端 build 全部通过。
- 权限敏感的 Tushare 接口只要求固定小样本真实同步闭环：`index_basic`、`index_daily`、`fund_basic`、`fund_daily`、`index_classify/index_member_all` 必须支持白名单入参，并在远端短窗口样本上返回 `ok` 或带具体原因的 `partial`。
- 远端 curl 验证通过：
  - `GET http://127.0.0.1:18002/api/health`
  - `GET http://127.0.0.1:18002/api/db/overview`
  - P0 新增只读查询接口。
- 重复执行 P0 同步后，唯一键重复检查返回 `0`。

## Real PG Migration Gate

本计划只负责远端 sandbox 里的 P0 落地和验收，不直接迁移原 `/opt/quantitative-trading` 的真实 PostgreSQL。

`TODO.md` 的“本地或临时验证库”在本阶段允许先用本地隔离 Docker sandbox 预验收，但完成判定以远端临时验证库为准，即 `/opt/quantitative-trading-todo-p0-20260629` 对应的独立 PostgreSQL volume。禁止把本阶段最终验收改成原远端项目目录或原真实 PG。

真实 PG 迁移必须另开执行确认，并至少满足：

- 原真实 PG 已备份且确认可回滚。
- schema 只做前向新增或兼容变更，不删除既有表、字段、索引或 volume。
- 远端 sandbox 完成小样本真实同步 dry-run、幂等验证、API health、DB overview 和关键样本查询；全量同步不作为进入迁移讨论的前置阻塞条件。
- 用户明确确认可以把已验收的 schema 和同步逻辑迁入原远端目录。
- 迁移后在原远端服务重新运行覆盖查询、API health、DB overview 和关键样本查询。

## Remote Sandbox Setup

**Files:**
- Remote create only: `/opt/quantitative-trading-todo-p0-20260629/docker-compose.sandbox.yml`
- Remote read only: `/opt/quantitative-trading/.env`
- Remote read only: `/opt/quantitative-trading`

**Interfaces:**
- Consumes: pushed branch `codex/todo-p0-data-foundation`
- Produces: isolated remote app at `http://127.0.0.1:15175` and isolated API at `http://127.0.0.1:18002`

- [x] **Step 1: Capture original remote service state**

Run on the local machine:

```bash
ssh quant-trading-server 'docker ps --filter name=quant_trading --format "{{.Names}} {{.ID}} {{.Ports}} {{.Status}}" | sort && docker volume ls --format "{{.Name}}" | grep "quant" | sort'
```

Expected: output lists the original `quant_trading_*` containers and existing quant volumes. Save the output in the implementation notes, not in source files.

- [x] **Step 2: Create isolated remote directory and clone branch**

Run on the local machine after pushing `codex/todo-p0-data-foundation`:

```bash
ssh quant-trading-server '
set -euo pipefail
sudo install -d -o "$(id -u)" -g "$(id -g)" /opt/quantitative-trading-todo-p0-20260629
if [ ! -d /opt/quantitative-trading-todo-p0-20260629/.git ]; then
  git clone --branch codex/todo-p0-data-foundation ssh://git@ssh.github.com:443/Jettlin927/Quantitative_trading.git /opt/quantitative-trading-todo-p0-20260629
else
  cd /opt/quantitative-trading-todo-p0-20260629
  git fetch --prune origin codex/todo-p0-data-foundation
  git checkout codex/todo-p0-data-foundation
  git reset --hard origin/codex/todo-p0-data-foundation
fi
cp /opt/quantitative-trading/.env /opt/quantitative-trading-todo-p0-20260629/.env
'
```

Expected: the isolated directory contains the feature branch and a remote-only `.env` copied from the original server directory.

- [x] **Step 3: Add remote-only standalone Compose file**

Run on the local machine:

```bash
ssh quant-trading-server 'cat > /opt/quantitative-trading-todo-p0-20260629/docker-compose.sandbox.yml <<'"'"'EOF'"'"'
services:
  db:
    image: postgres:16-alpine
    container_name: quant_trading_todo_p0_db
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-quant_trading}
      POSTGRES_USER: ${POSTGRES_USER:-quant}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-quant_password}
    ports:
      - "127.0.0.1:${POSTGRES_PORT:-15433}:5432"
    volumes:
      - postgres_data_todo_p0:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-quant} -d ${POSTGRES_DB:-quant_trading}"]
      interval: 5s
      timeout: 5s
      retries: 12

  api:
    build:
      context: .
      dockerfile: backend/Dockerfile
      args:
        PIP_INDEX_URL: ${PIP_INDEX_URL:-}
        PIP_TRUSTED_HOST: ${PIP_TRUSTED_HOST:-}
    container_name: quant_trading_todo_p0_api
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql+psycopg://${POSTGRES_USER:-quant}:${POSTGRES_PASSWORD:-quant_password}@db:5432/${POSTGRES_DB:-quant_trading}
      TUSHARE_TOKEN: ${TUSHARE_TOKEN:-}
      DEEPSEEK_TOKEN: ${DEEPSEEK_TOKEN:-}
      DEEPSEEK_MODEL: ${DEEPSEEK_MODEL:-deepseek-v4-flash}
      DEEPSEEK_API_BASE: ${DEEPSEEK_API_BASE:-https://api.deepseek.com}
    ports:
      - "127.0.0.1:${API_PORT:-18002}:8000"
    volumes:
      - .:/app

  frontend:
    build:
      context: .
      dockerfile: frontend/Dockerfile
      args:
        NPM_CONFIG_REGISTRY: ${NPM_CONFIG_REGISTRY:-}
    container_name: quant_trading_todo_p0_frontend
    restart: unless-stopped
    depends_on:
      - api
    environment:
      VITE_API_BASE_URL: http://localhost:${API_PORT:-18002}
    ports:
      - "127.0.0.1:${FRONTEND_PORT:-15175}:5173"
    volumes:
      - ./frontend:/app
      - frontend_node_modules_todo_p0:/app/node_modules

volumes:
  postgres_data_todo_p0:
  frontend_node_modules_todo_p0:
EOF'
```

Expected: this file exists only in the remote sandbox directory and is not committed. Do not combine it with `docker-compose.yml`; it is intentionally standalone to avoid Compose list-merge port collisions with the original service.

- [x] **Step 4: Verify isolated Compose config**

Run on the local machine:

```bash
ssh quant-trading-server '
cd /opt/quantitative-trading-todo-p0-20260629
POSTGRES_PORT=15433 API_PORT=18002 FRONTEND_PORT=15175 docker compose -p quant_todo_p0 -f docker-compose.sandbox.yml config >/tmp/quant_todo_p0.compose.yml
grep -E "quant_trading_todo_p0_(db|api|frontend)|15433|18002|15175|postgres_data_todo_p0" /tmp/quant_todo_p0.compose.yml
'
```

Expected: output includes only sandbox container names, sandbox ports and sandbox volume names.

## Task 1: P0 Schema And Unit Tests

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_data_api_contracts.py`

**Interfaces:**
- Produces SQLAlchemy models: `TradeCalendar`, `StockAdjustFactor`, `Index`, `IndexDailyBar`, `Fund`, `FundDailyBar`, `IndustryClassification`, `IndustryMember`
- Produces helper functions: `trade_calendar_record_to_row`, `adjust_factor_record_to_row`, `index_basic_record_to_row`, `index_daily_record_to_row`, `fund_basic_record_to_row`, `fund_daily_record_to_row`, `industry_classification_record_to_row`, `industry_member_record_to_row`

- [x] **Step 1: Write failing tests for P0 model uniqueness and row mappers**

Add tests in `backend/tests/test_data_api_contracts.py` that create an in-memory SQLite DB, call `Base.metadata.create_all`, and assert:

```python
self.assertIn("trade_calendars", Base.metadata.tables)
self.assertIn("stock_adjust_factors", Base.metadata.tables)
self.assertIn("indices", Base.metadata.tables)
self.assertIn("index_daily_bars", Base.metadata.tables)
self.assertIn("funds", Base.metadata.tables)
self.assertIn("fund_daily_bars", Base.metadata.tables)
self.assertIn("industry_classifications", Base.metadata.tables)
self.assertIn("industry_members", Base.metadata.tables)
```

Also assert mapper outputs:

```python
self.assertEqual(main.trade_calendar_record_to_row({"exchange": "SSE", "cal_date": "20260629", "is_open": 1})["cal_date"], date(2026, 6, 29))
self.assertEqual(main.adjust_factor_record_to_row({"ts_code": "600703.SH", "trade_date": "20260629", "adj_factor": 12.3456})["ts_code"], "600703.SH")
self.assertEqual(main.index_basic_record_to_row({"ts_code": "000300.SH", "name": "沪深300", "market": "SSE"})["ts_code"], "000300.SH")
self.assertEqual(main.fund_basic_record_to_row({"ts_code": "512480.SH", "name": "半导体ETF", "market": "E"})["ts_code"], "512480.SH")
self.assertEqual(main.industry_classification_record_to_row({"index_code": "801081.SI", "industry_name": "半导体", "level": "L2", "src": "SW2021"})["index_code"], "801081.SI")
```

- [x] **Step 2: Run tests remotely and verify failure**

```bash
ssh quant-trading-server '
cd /opt/quantitative-trading-todo-p0-20260629
POSTGRES_PORT=15433 API_PORT=18002 FRONTEND_PORT=15175 docker compose -p quant_todo_p0 -f docker-compose.sandbox.yml run --rm api python -m unittest backend.tests.test_data_api_contracts -v
'
```

Expected: FAIL because P0 models and mapper functions do not exist yet.

- [x] **Step 3: Add minimal schema and mappers**

In `backend/app/models.py`, add only the P0 models listed above. In `backend/app/main.py`, add only mapper helpers needed by the tests and later sync routes. Reuse `parse_tushare_date`, `decimal_or_none`, `upsert_rows`, `record_sync_run`, `query_date_coverage` and existing style.

- [x] **Step 4: Run tests remotely and verify pass**

```bash
ssh quant-trading-server '
cd /opt/quantitative-trading-todo-p0-20260629
POSTGRES_PORT=15433 API_PORT=18002 FRONTEND_PORT=15175 docker compose -p quant_todo_p0 -f docker-compose.sandbox.yml run --rm api python -m unittest backend.tests.test_data_api_contracts -v
'
```

Expected: PASS.

## Task 2: Tushare P0 Sync APIs

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_data_api_contracts.py`

**Interfaces:**
- Produces request schemas: `SyncTradeCalendarRequest`, `SyncAdjustFactorsRequest`, `SyncIndexBasicRequest`, `SyncIndexDailyRequest`, `SyncFundBasicRequest`, `SyncFundDailyRequest`, `SyncIndustryClassificationsRequest`
- Produces sync APIs:
  - `POST /api/tushare/sync-trade-calendar`
  - `POST /api/tushare/sync-adjust-factors`
  - `POST /api/tushare/sync-index-basic`
  - `POST /api/tushare/sync-index-daily`
  - `POST /api/tushare/sync-fund-basic`
  - `POST /api/tushare/sync-fund-daily`
  - `POST /api/tushare/sync-industry-classifications`

- [x] **Step 1: Write failing route and fake Tushare tests**

Use FastAPI route inspection and fake Tushare objects. Tests should not call the network. Assert all seven routes exist and each route calls `upsert_rows` with the expected conflict columns.

- [x] **Step 2: Run remote failure**

```bash
ssh quant-trading-server '
cd /opt/quantitative-trading-todo-p0-20260629
POSTGRES_PORT=15433 API_PORT=18002 FRONTEND_PORT=15175 docker compose -p quant_todo_p0 -f docker-compose.sandbox.yml run --rm api python -m unittest backend.tests.test_data_api_contracts -v
'
```

Expected: FAIL because sync schemas and routes do not exist yet.

- [x] **Step 3: Implement minimal sync routes**

Use the existing pattern from `sync_stock_basic`, `sync_daily`, `sync_market_daily` and `sync_fundamentals`. Keep each route narrow:

- `sync-trade-calendar`: call `pro.trade_cal(exchange=payload.exchange or "", start_date=..., end_date=..., fields="exchange,cal_date,is_open,pretrade_date")`.
- `sync-adjust-factors`: call `pro.adj_factor(ts_code=payload.ts_code, start_date=..., end_date=..., fields="ts_code,trade_date,adj_factor")`.
- `sync-index-basic`: call `pro.index_basic(market=market, fields=...)` for requested markets, then filter to optional `ts_codes` before upsert. This keeps remote live verification on a fixed small sample.
- `sync-index-daily`: call `pro.index_daily(ts_code=code, start_date=..., end_date=..., fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount")` only for requested `ts_codes`.
- `sync-fund-basic`: call `pro.fund_basic(market="E", fields=...)`, then filter to optional `ts_codes` before upsert. This avoids treating the whole ETF catalog as a blocking live test.
- `sync-fund-daily`: call `pro.fund_daily(ts_code=code, start_date=..., end_date=..., fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount")` only for requested `ts_codes`.
- `sync-industry-classifications`: call `pro.index_classify(src="SW2021")`, filter to requested `index_codes`, and call `pro.index_member_all(index_code=...)` only for those sample industry codes.

- [x] **Step 4: Run remote pass**

```bash
ssh quant-trading-server '
cd /opt/quantitative-trading-todo-p0-20260629
POSTGRES_PORT=15433 API_PORT=18002 FRONTEND_PORT=15175 docker compose -p quant_todo_p0 -f docker-compose.sandbox.yml run --rm api python -m unittest backend.tests.test_data_api_contracts -v
'
```

Expected: PASS.

## Task 3: Read-Only Query APIs And DB Overview

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_data_api_contracts.py`

**Interfaces:**
- Produces read-only APIs:
  - `GET /api/trade-calendars/{cal_date}`
  - `GET /api/trade-calendars/recent?limit=20`
  - `GET /api/stocks/{ts_code}/adjust-factors`
  - `GET /api/indices`
  - `GET /api/indices/{ts_code}/daily-bars`
  - `GET /api/funds`
  - `GET /api/funds/{ts_code}/daily-bars`
  - `GET /api/industries`
  - `GET /api/industries/{index_code}/members`
- Extends `GET /api/db/overview` with `aShare.tradeCalendar`, `aShare.adjustFactors`, `aShare.indices`, `aShare.indexDailyBars`, `aShare.funds`, `aShare.fundDailyBars`, `aShare.industries`

- [x] **Step 1: Write failing overview and route tests**

Seed SQLite with one row per P0 table. Assert `get_db_overview(db)` includes non-zero counts and expected latest dates. Assert all query route paths exist.

- [x] **Step 2: Run remote failure**

```bash
ssh quant-trading-server '
cd /opt/quantitative-trading-todo-p0-20260629
POSTGRES_PORT=15433 API_PORT=18002 FRONTEND_PORT=15175 docker compose -p quant_todo_p0 -f docker-compose.sandbox.yml run --rm api python -m unittest backend.tests.test_data_api_contracts -v
'
```

Expected: FAIL because overview/query APIs are not wired.

- [x] **Step 3: Implement minimal read-only APIs**

Return JSON-safe values only. Convert `Decimal`, `date`, `datetime`, NaN and Infinity the same way existing endpoints do. Do not add trading recommendations or ranking language.

- [x] **Step 4: Run remote pass**

```bash
ssh quant-trading-server '
cd /opt/quantitative-trading-todo-p0-20260629
POSTGRES_PORT=15433 API_PORT=18002 FRONTEND_PORT=15175 docker compose -p quant_todo_p0 -f docker-compose.sandbox.yml run --rm api python -m unittest backend.tests.test_data_api_contracts -v
'
```

Expected: PASS.

## Task 4: Frontend P0 Coverage Panel

**Files:**
- Read first: `.codex/skills/frontend-design/SKILL.md`
- Modify: `frontend/src/main.jsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `GET /api/db/overview` P0 fields from Task 3
- Produces: read-only coverage rows for calendar, factors, indices, index bars, funds, fund bars and industries

- [x] **Step 1: Read frontend design rules**

```bash
sed -n '1,260p' .codex/skills/frontend-design/SKILL.md
```

Expected: confirms industrial data terminal direction and no marketing/strategy UI.

- [x] **Step 2: Add only coverage display**

Update the existing data workbench overview area. Keep it read-only and concise. Do not add strategy, signal, ranking, recommendation, backtest execution or real account language.

- [x] **Step 3: Build remotely**

```bash
ssh quant-trading-server '
cd /opt/quantitative-trading-todo-p0-20260629
POSTGRES_PORT=15433 API_PORT=18002 FRONTEND_PORT=15175 docker compose -p quant_todo_p0 -f docker-compose.sandbox.yml run --rm frontend npm run build
'
```

Expected: PASS.

## Task 5: Remote Full Verification

**Files:**
- Modify: `操作日志.md`

**Interfaces:**
- Consumes: all tasks above
- Produces: remote verification evidence and final task log

- [x] **Step 1: Run backend compile remotely**

```bash
ssh quant-trading-server '
cd /opt/quantitative-trading-todo-p0-20260629
POSTGRES_PORT=15433 API_PORT=18002 FRONTEND_PORT=15175 docker compose -p quant_todo_p0 -f docker-compose.sandbox.yml run --rm api python -m py_compile backend/app/database.py backend/app/models.py backend/app/schemas.py backend/app/tushare_client.py backend/app/us_research.py backend/app/main.py
'
```

Expected: exit code `0`.

- [x] **Step 2: Run backend tests remotely**

```bash
ssh quant-trading-server '
cd /opt/quantitative-trading-todo-p0-20260629
POSTGRES_PORT=15433 API_PORT=18002 FRONTEND_PORT=15175 docker compose -p quant_todo_p0 -f docker-compose.sandbox.yml run --rm api python -m unittest discover backend/tests -v
'
```

Expected: `OK`.

- [x] **Step 3: Run Compose and frontend checks remotely**

```bash
ssh quant-trading-server '
cd /opt/quantitative-trading-todo-p0-20260629
POSTGRES_PORT=15433 API_PORT=18002 FRONTEND_PORT=15175 docker compose -p quant_todo_p0 -f docker-compose.sandbox.yml config >/tmp/quant_todo_p0.compose.yml
POSTGRES_PORT=15433 API_PORT=18002 FRONTEND_PORT=15175 docker compose -p quant_todo_p0 -f docker-compose.sandbox.yml run --rm frontend npm run build
'
```

Expected: both commands exit `0`.

- [x] **Step 4: Start isolated stack remotely**

```bash
ssh quant-trading-server '
cd /opt/quantitative-trading-todo-p0-20260629
POSTGRES_PORT=15433 API_PORT=18002 FRONTEND_PORT=15175 docker compose -p quant_todo_p0 -f docker-compose.sandbox.yml up -d --build
POSTGRES_PORT=15433 API_PORT=18002 FRONTEND_PORT=15175 docker compose -p quant_todo_p0 -f docker-compose.sandbox.yml ps
'
```

Expected: only `quant_trading_todo_p0_*` containers are created or rebuilt.

- [x] **Step 5: Run small-sample live Tushare sync remotely**

This step is the live-data gate for Tushare permission-sensitive interfaces. Do not expand it to full-market or full-catalog sync during P0 acceptance. The required sample is:

- calendar: `2026-06-01` to `2026-06-29`
- adjust factor: `600703.SH`, `2026-06-01` to `2026-06-29`
- index basic/daily: `000300.SH`, `931743.CSI`, `801081.SI`
- fund basic/daily: `512480.SH`, `159995.SZ`
- industry classification/member: `801081.SI`

```bash
ssh quant-trading-server '
set -euo pipefail
curl -fsS -X POST http://127.0.0.1:18002/api/tushare/sync-trade-calendar -H "Content-Type: application/json" -d "{\"start_date\":\"2026-06-01\",\"end_date\":\"2026-06-29\"}"
curl -fsS -X POST http://127.0.0.1:18002/api/tushare/sync-index-basic -H "Content-Type: application/json" -d "{\"markets\":[\"CSI\",\"SSE\",\"SW\"],\"ts_codes\":[\"000300.SH\",\"931743.CSI\",\"801081.SI\"]}"
curl -fsS -X POST http://127.0.0.1:18002/api/tushare/sync-fund-basic -H "Content-Type: application/json" -d "{\"market\":\"E\",\"ts_codes\":[\"512480.SH\",\"159995.SZ\"]}"
curl -fsS -X POST http://127.0.0.1:18002/api/tushare/sync-index-daily -H "Content-Type: application/json" -d "{\"ts_codes\":[\"000300.SH\",\"931743.CSI\",\"801081.SI\"],\"start_date\":\"2026-06-01\",\"end_date\":\"2026-06-29\"}"
curl -fsS -X POST http://127.0.0.1:18002/api/tushare/sync-fund-daily -H "Content-Type: application/json" -d "{\"ts_codes\":[\"512480.SH\",\"159995.SZ\"],\"start_date\":\"2026-06-01\",\"end_date\":\"2026-06-29\"}"
curl -fsS -X POST http://127.0.0.1:18002/api/tushare/sync-adjust-factors -H "Content-Type: application/json" -d "{\"ts_code\":\"600703.SH\",\"start_date\":\"2026-06-01\",\"end_date\":\"2026-06-29\"}"
curl -fsS -X POST http://127.0.0.1:18002/api/tushare/sync-industry-classifications -H "Content-Type: application/json" -d "{\"src\":\"SW2021\",\"index_codes\":[\"801081.SI\"]}"
'
```

Expected: every call returns JSON with `status` equal to `ok` or `partial`. Any `partial` response must include `failed_items` or an equivalent concrete reason that names the interface and sample code that failed. P0 acceptance is blocked by missing route/schema/upsert/query behavior, but not blocked by a documented Tushare permission failure for a sample code.

- [x] **Step 6: Verify live query and idempotency remotely**

Run the same small-sample sync commands from Step 5 a second time, then run:

```bash
ssh quant-trading-server '
set -euo pipefail
curl -fsS http://127.0.0.1:18002/api/health
curl -fsS http://127.0.0.1:18002/api/db/overview
curl -fsS "http://127.0.0.1:18002/api/trade-calendars/recent?limit=5"
curl -fsS "http://127.0.0.1:18002/api/stocks/600703.SH/adjust-factors?start_date=2026-06-01&end_date=2026-06-29"
curl -fsS "http://127.0.0.1:18002/api/indices?q=沪深300"
curl -fsS "http://127.0.0.1:18002/api/funds?q=半导体"
curl -fsS "http://127.0.0.1:18002/api/industries/801081.SI/members"
cd /opt/quantitative-trading-todo-p0-20260629
POSTGRES_PORT=15433 API_PORT=18002 FRONTEND_PORT=15175 docker compose -p quant_todo_p0 -f docker-compose.sandbox.yml exec -T db sh -lc '"'"'
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 <<SQL
SELECT COUNT(*) AS duplicate_trade_calendars FROM (SELECT exchange, cal_date FROM trade_calendars GROUP BY 1,2 HAVING COUNT(*) > 1) d;
SELECT COUNT(*) AS duplicate_adjust_factors FROM (SELECT ts_code, trade_date FROM stock_adjust_factors GROUP BY 1,2 HAVING COUNT(*) > 1) d;
SELECT COUNT(*) AS duplicate_index_daily FROM (SELECT ts_code, trade_date FROM index_daily_bars GROUP BY 1,2 HAVING COUNT(*) > 1) d;
SELECT COUNT(*) AS duplicate_fund_daily FROM (SELECT ts_code, trade_date FROM fund_daily_bars GROUP BY 1,2 HAVING COUNT(*) > 1) d;
SELECT COUNT(*) AS duplicate_industry_members FROM (SELECT index_code, con_code, in_date FROM industry_members GROUP BY 1,2,3 HAVING COUNT(*) > 1) d;
SQL
'"'"'
'
```

Expected: duplicate counts are all `0`; overview contains P0 coverage fields.

- [x] **Step 7: Verify original remote service was not touched**

```bash
ssh quant-trading-server 'docker ps --filter name=quant_trading --format "{{.Names}} {{.ID}} {{.Ports}} {{.Status}}" | sort && docker volume ls --format "{{.Name}}" | grep "quant" | sort'
```

Expected: original `quant_trading_db`, `quant_trading_api`, `quant_trading_frontend` container IDs and original volume names match Step 1. Additional sandbox containers/volumes may exist with `todo_p0` or `quant_todo_p0` in their names.

- [x] **Step 8: Append implementation log**

Append to `操作日志.md` with:

- remote sandbox path,
- changed files,
- exact remote verification commands,
- pass/fail result,
- any Tushare `partial` causes,
- confirmation that no original volume deletion command was run.

Do not paste `.env`, token, password or raw credential output.

## Out Of Scope

- P1/P2 data.
- Strategy research, strategy execution, backtests, trading signals and ratings.
- Real holdings, real trades, broker exports and broker connections.
- Public exposure of remote PostgreSQL, API or frontend ports.
- Deleting sandbox or original volumes. Cleanup needs a separate user confirmation.

## Execution Choice

Plan complete and saved to `docs/superpowers/plans/2026-06-29-remote-p0-data-foundation.md`. Two execution options:

1. Subagent-Driven (recommended) - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. Inline Execution - execute tasks in this session using executing-plans, batch execution with checkpoints.
