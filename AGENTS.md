# AGENTS.md

本文件是本仓库的“项目主管规则”。它的作用不是重复 README，而是在模型上下文有限、无状态的前提下，固定本项目里的默认正确做法、红线和隐性设计背景。

用户常用语言是中文。除非用户明确切换语言，所有说明、计划、提交说明和交互文案默认使用中文。

当需要库/API 文档、代码生成、设置或配置步骤时，默认使用 Context7 MCP 查询当前文档，不需要用户额外提醒。

## 项目定位

这是一个本地量化回测工作台，用于把用户投资笔记中的交易纪律参数化，并结合 Tushare 行情、PostgreSQL 持久化数据、FastAPI 后端和 React 前端，展示策略在单标的日线上的收益、最大回撤、胜率、纪律评分和交易流水。

这不是生产交易系统，也不应自动下单。任何涉及真实交易、账户、券商接口、资金变动的功能都必须先停下来问用户。

## 当前架构

默认架构是三容器本地系统：

- `frontend`：React + Vite，目录是 `frontend/`，宿主机端口默认 `15173`，容器内仍监听 `5173`。
- `api`：FastAPI，目录是 `backend/`，宿主机端口默认 `18000`，容器内仍监听 `8000`。
- `db`：PostgreSQL，数据通过 Docker volume 持久化。

`docker-compose.yml` 是启动入口。不要把项目退回到单文件静态 HTML 方案，除非用户明确要求。

## 启动与构建规则

日常启动使用：

```powershell
.\启动回测系统.cmd
```

它应该只执行：

```powershell
docker compose up -d
```

不要在日常启动脚本里加入 `--build`，否则每次打开都会变慢。

只有在修改 Dockerfile、依赖文件、后端代码、前端代码，或用户明确说需要重建时，才使用：

```powershell
.\重新构建并启动回测系统.cmd
```

停止服务使用：

```powershell
.\停止回测系统.cmd
```

## 数据与安全红线

PostgreSQL volume 是 Tushare 行情和同步记录的持久化来源。不要执行以下操作，除非用户明确要求并确认会丢数据：

```powershell
docker compose down -v
docker volume rm ...
```

不要把 `.env`、Tushare token、数据库密码或任何真实凭据写入源码、README 示例之外的配置、前端代码或日志。

Tushare token 的默认来源是 `.env` 中的 `TUSHARE_TOKEN`。临时请求体传 token 只作为调试兜底，不应变成前端常规交互。

## 后端规则

后端使用 FastAPI + SQLAlchemy 2.0 + PostgreSQL。

数据库模型位于 `backend/app/models.py`。

API 入口位于 `backend/app/main.py`。

回测引擎位于 `backend/app/backtest_engine.py`。以后策略逻辑默认以后端为准，前端只负责提交参数和展示结果，避免前后端各维护一套分叉逻辑。

Tushare 相关逻辑位于 `backend/app/tushare_client.py`。新增 Tushare 接口时，要把字段映射、日期格式和空值处理集中在后端，不要散落到前端。

`stock_daily_bars` 以 `ts_code + trade_date` 去重 upsert。不要插入重复行情。

API 返回给前端的数据必须 JSON-safe。指标计算中出现的 `NaN`、`Infinity` 必须转成 `None/null` 或可展示的兜底值。

## 前端规则

前端使用 React + Vite，目录是 `frontend/`。

用户指定了 `.codex/skills/frontend-design/SKILL.md`。涉及前端页面、组件、布局或视觉优化时，必须先阅读并遵守这个 skill。

本项目前端的审美方向是：工业化风控终端。关键词是高信息密度、克制但有记忆点、网格感、纪律感、交易台感。不要做营销落地页，不要做泛 SaaS 大卡片首页。

前端第一屏应该是可操作工作台，而不是产品介绍。

按钮优先使用 `lucide-react` 图标加短文本。工具型动作要清楚，例如“检测 API”“同步日线”“数据库回测”。

页面必须直接服务核心工作流：

1. 检测 API。
2. 同步 Tushare 日线到 PostgreSQL。
3. 调整策略和风控参数。
4. 运行数据库回测。
5. 查看总收益、最大回撤、胜率、纪律评分、权益曲线和交易流水。

不要加入与当前任务无关的装饰性组件、营销区块、空洞说明文案或大面积 hero。

## 回测语义

当前策略约束来自用户投资笔记，默认包含：

- 每周最多交易 2 次。
- 单票仓位上限默认 20%。
- 单笔风险上限默认 1%。
- 默认止损 5%。
- 第一止盈 3% 减半。
- 第二止盈 5% 清仓。
- 退潮/弱势市场禁止开新仓。
- 盈利卖出当天禁止新买入。
- A 股默认按 100 股一手取整。

修改这些语义时必须谨慎。如果改的是规则本身，而不是代码 bug，要在回复里明确说明对回测结果解释的影响。

本工具只用于研究和复盘，不构成投资建议。不要在 UI 或回复里暗示确定收益。

## Windows 与编码

用户在 Windows/PowerShell 环境工作。处理路径时要正确引用，尤其是包含中文的路径和脚本名。

创建或编辑 PowerShell 脚本时使用 UTF-8 with BOM。当前 `.cmd` 启动脚本需要保留：

```bat
chcp 65001 >nul
cd /d "%~dp0"
```

不要把 Windows 路径硬编码到容器内部。容器内路径默认以 `/app` 为准。

## 何时停下来问用户

以下情况必须先问用户：

- 会删除数据库 volume 或持久化行情数据。
- 会改变 Tushare token、数据库密码或端口约定。
- 会引入新的大型依赖、框架替换或数据库迁移工具。
- 会改变核心回测规则语义。
- 会连接真实券商、交易账户或任何可触发真实资金变化的接口。

## 阶段操作日志

仓库根目录维护 `操作日志.md`，用于记录 Codex 在本文件夹内做过的阶段性工作。

每个阶段性任务开始或结束时，Codex 应追加一条日志，至少包含：

- 时间：使用本机时间，格式尽量为 `YYYY-MM-DD HH:mm +08:00`。
- 阶段目标：本阶段打算解决什么问题。
- 实际操作：列出改过的文件、运行过的关键命令、重要决策。
- 验证结果：写明已运行的检查；如果没验证或验证失败，要如实写原因。
- 后续事项：仍未完成、需要用户确认或下一阶段要接着做的事。

日志只记录事实和工程判断，不写入 `.env`、Tushare token、数据库密码或任何真实凭据。

## 验证清单

修改任何代码后，必须运行最小范围检查。本仓库已经按官方 Codex hooks 机制配置了项目级 hook：

```text
.codex/hooks.json
.codex/hooks/post_tool_use_check.py
```

该 hook 监听 `PostToolUse` 的 `apply_patch/Edit/Write`，从补丁里提取变更文件，并调用：

```powershell
.\scripts\check-file.ps1 <刚修改的文件路径>
```

第一次启用或修改 hook 后，需要在 Codex 的 `/hooks` 里 review/trust。未 trust 前，Codex 会跳过项目本地 hook。

如果一次改了多个文件，把所有路径都传进去。`scripts/check-file.ps1` 是 hook 的实际检查器：Python 文件跑 `py_compile`，PowerShell 文件跑语法解析，JSON 文件跑 JSON 解析，Compose 文件跑 `docker compose config --quiet`，前端文件跑 ESLint、TypeScript checkJs 和 Vite build。

改后端时至少运行：

```powershell
python -m py_compile backend\app\database.py backend\app\models.py backend\app\schemas.py backend\app\backtest_engine.py backend\app\tushare_client.py backend\app\main.py
```

改 Compose 时运行：

```powershell
docker compose config
```

改前端配置时至少运行：

```powershell
node --check frontend\vite.config.js
```

如果本地依赖已经安装，优先运行前端构建：

```powershell
docker compose run --rm frontend npm run build
```

如果 Docker daemon 权限不足，不要假装已验证。明确告诉用户卡在 Docker 权限或 Docker Desktop 状态。

## 常见命令

日常启动：

```powershell
.\启动回测系统.cmd
```

重新构建：

```powershell
.\重新构建并启动回测系统.cmd
```

停止：

```powershell
.\停止回测系统.cmd
```

查看服务：

```powershell
docker compose ps
```

查看 API 日志：

```powershell
docker compose logs -f api
```

查看前端日志：

```powershell
docker compose logs -f frontend
```

查看数据库日志：

```powershell
docker compose logs -f db
```
