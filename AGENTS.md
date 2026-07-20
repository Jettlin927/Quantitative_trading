# AGENTS.md

本文件是仓库级长期规则。默认使用中文沟通；Issue、Pull Request、Commit、报告、前端文案和自动化评论必须使用中文。代码标识、数据库字段和第三方错误可以保留英文，但面向用户时补充中文解释。

## 先读这些入口

- 统一术语：`CONTEXT.md`。
- 架构决策：`docs/adr/`。
- 文档总入口：`docs/index.md`；代码职责见 `docs/architecture/code-map.md`，历史资料只从 `docs/archive/` 读取。
- Issue 与 Wayfinder 规则：`docs/agents/issue-tracker.md`。
- Agent 领域文档规则：`docs/agents/domain.md`。
- 具体策略研究强制规范：`docs/research/contracts/strategy-evaluation-standard.md`。
- 阶段事实审计：根目录 `操作日志.md`。

易变化的生产提交、表行数、磁盘余量、运行 ID、Issue 状态和部署状态必须现场核验，不能只引用文档快照。

## 产品边界

本仓库是离线量化研究与策略优化系统，不是真实交易系统。

允许：

- 同步、持久化、质检和展示实际市场数据。
- 使用 point-in-time、历史 universe、复权和下一交易日执行口径做离线研究。
- 形成冻结研究计划、研究运行、结构化评价、研究结论、证据和后续研究提案。
- 在 GitHub Issues 和服务器只读前端发布完整研究过程与结果。

禁止：

- 连接真实券商、交易账户或资金账户。
- 导入真实持仓、真实成交或券商导出，除非用户先确认新的数据治理方案。
- 自动下单、撤单、调仓、融资融券、申购赎回或任何真实资金动作。
- 把数据、sample、研究结论或观察池写成买入、卖出、持有、评级、收益承诺或实盘指令。

## 工作与 Issue 规则

1. 开始任务前读取目标 Issue、父 Issue、原生阻塞关系和验收条件。
2. 只有无未完成阻塞且标记 `可由智能体处理` 的普通工程 Issue 可以自动领取；领取后先分配给执行者。
3. `需人工处理`、`待补充信息`、生产部署、生产数据库迁移和不可逆操作必须停下等待用户。
4. GitHub Issues 是唯一活动路线图；`TODO.md`、dated 计划和操作日志不得承担当前任务状态。
5. 所有新标签使用中文。研究批准不能靠标签推断，唯一授权来源是指定用户对精确计划哈希的批准评论。
6. 代码任务使用独立分支或 worktree，运行与风险匹配的验证，创建中文 Pull Request；不得自动合并。
7. 只修改当前目标所需内容。保留用户既有修改，不使用 `git add -A` 混入无关文件，不顺手重构。

## 正式研究治理

- 系统可以创建和排序研究提案，但只有用户 `Jettlin927` 对冻结计划发布 `批准研究 <plan_sha256>` 后才能启动正式研究。
- 假设、规则、范围、期间、基准、成本、门禁、参数空间、试验预算或停止条件变化后，原批准失效，必须创建新版本并重新批准。
- 正式研究只执行已合并、CI 通过并静态登记的策略代码；自动化不能自行合并策略、公式、schema 或编排代码。
- 自动优化必须在冻结的有限搜索空间、目标函数、试验预算和停止规则内进行；不得根据同一 OOS 临时扩张搜索空间。
- 研究运行状态、策略生命周期、研究评价和研究结论分别建模。运行成功不等于研究通过。
- 研究结论只允许：`研究通过`、`有条件候选`、`证据不足`、`受阻`、`不通过`。
- 所有终态都要发布，包括失败运行审计；不得只展示胜出结果。
- 已发布研究不可原地覆盖。修正必须创建替代版本，并保留旧结论与证据。
- 新增市场数据不得自动覆盖旧结论；监测只能形成观察或新的未批准提案。

## 策略研究交付

用户要求研究、分析、评估、回测或比较具体策略时，必须完整阅读 `docs/research/contracts/strategy-evaluation-standard.md` 并按其顺序交付，**不得只凭累计收益、年化收益或 Sharpe** 判断策略好坏。

至少包含：

- 策略画像、经济假设、适用和失效条件。
- point-in-time 数据、历史 universe、执行时点、成本和可复现身份。
- test/OOS 指标、匹配基准、风险、交易、容量和市场环境矩阵。
- walk-forward、参数邻域、成本压力和试验登记；多次筛选时增加 DSR/PBO。
- 支持证据、反对证据、尚缺证据、限制项和强制结论状态。

缺少匹配基准、净成本、test/OOS、市场环境覆盖、关键可交易性、试验登记或复现身份时，不得输出 `研究通过`。

用户报告优先使用中文方案名称，不能用**内部编号代替名称**。确需保留编号时，先给出“编号—名称—具体规则”对照，后文使用“名称（编号）”。

研究期首存在上一信号日触发的首个执行日时，收益、基准、成本和回撤必须从显式初始净值计算，不能用首个收盘净值重新归一化而漏掉首日收益或费用。

## 研究发布合同

- PostgreSQL 保存结构化计划、运行、事件、评价、证据引用、结论、发布状态和后续提案。
- 服务器工件目录保存冻结输入、运行清单、账本、复现证据、机器摘要和原始 HTML；大型工件不得提交进 Git。
- GitHub Issue 保存冻结计划、用户批准、可读进度和最终中文结论。
- API 与前端提供只读投影。PG、工件、Issue、API 和前端读回同一评价版本与指纹后才算发布完成。
- 旧脚本的 `status=ok` 只能视为源执行状态，不能映射为 `研究通过`。

## 默认系统架构

本地与服务器均采用 Compose 管理的容器化系统：

- `frontend`：React + Vite，只读研究驾驶舱、A 股数据、美股数据边界和系统运维。
- `api`：FastAPI + SQLAlchemy 2.0，提供数据、研究与发布只读/控制 API。
- `worker`：持久数据同步任务；不得在 API 进程中执行长同步。
- `research-worker`：正式研究队列；与数据 Worker 分离，初期全局并发为 1。
- `db`：PostgreSQL 16，保存市场数据、任务和结构化研究事实。
- `artifacts`：冻结研究证据与报告发布目录。

`docker-compose.yml` 是统一启动入口。不要退回单文件静态 HTML，也不要把每份报告发布变成一次全应用重建。

## 代码职责

- `backend/app/models.py`：应用 schema 合同；生产演进必须通过 `backend/migrations/` 的 Alembic revision。
- `backend/app/main.py`：数据 API、持久任务入队和适配入口；避免继续堆积独立领域公式。
- `backend/app/sync_worker.py`：数据任务租约、心跳、退避和恢复。
- `backend/app/data_quality/`：研究范围质量、canonical 输入快照与可恢复质量运行。
- `backend/app/quant_research/`：无券商、无实盘副作用的研究协议、模拟、指标、复现和报告统计口径。
- `backend/app/quant_research/reporting.py`：报告共用收益、尾部风险、HAC alpha、DSR/PBO 口径；渲染脚本不得复制算法。
- `backend/app/strategy_results.py`：现有结果只读兼容层；新结构化发布不得继续以仓库 JSON 作为唯一结论来源。
- `scripts/research/render_*_report.py`：只组装 canonical 工件、图表和中文叙事，不重定义成交、成本或绩效公式。
- `frontend/`：只读产品界面，不承担研究计算或批准逻辑。
- `my_quant/us_research/`：美股 sample/实验夹具；不得冒充研究级美股数据。
- `outputs/research-runs/`：被 Git 忽略的一次性运行工件。

不要恢复 `strategy_research`、`backtest-reports`、`strategy-lab`、`research_engine` 等旧主线目录。

## 数据合同与安全红线

- PostgreSQL 与研究工件是持久资产。未经用户明确确认，禁止 `docker compose down -v`、`docker volume rm`、覆盖恢复、删除 canonical 工件或清理旧服务器数据。
- 不把 `.env`、Tushare token、数据库密码、SSH 私钥、GitHub token、真实账户数据或任何凭据写入源码、Issue、前端、日志、README 或测试。
- Tushare token 来自环境变量；请求体临时 token 只用于调试兜底。
- API 数值必须 JSON-safe；`NaN`、`Infinity` 转成 `null` 或明确兜底。
- A 股表继续遵守现有自然键与幂等 upsert；调整键前先检查模型、migration 和集成测试。
- 美股 sample 与未来实际市场数据必须在 schema、API、前端和研究门禁中明确隔离。

## 前端规则

涉及页面、组件、布局或视觉调整时，先阅读 `.codex/skills/frontend-design/SKILL.md`。

- 审美方向是工业化数据终端：高信息密度、克制、网格感、纪律感，不做营销落地页。
- 一级区域为研究驾驶舱、A 股数据、美股数据和系统运维。
- 研究界面原生展示策略档案、研究时间线、运行事实、评价、结论、证据、限制和后续建议；原始 HTML 是证据入口，不是唯一界面。
- 必须显著区分实际市场数据、sample、运行状态、研究结论和系统健康。
- 不出现实盘、账户、下单、买卖评级或收益承诺入口。

## 启动与验证

日常启动：

```powershell
.\启动数据工作台.cmd
```

修改 Dockerfile、依赖、后端或前端后：

```powershell
.\重新构建并启动数据工作台.cmd
```

停止：

```powershell
.\停止数据工作台.cmd
```

日常启动脚本不加 `--build`。`.cmd` 保留 UTF-8 code page 与脚本目录切换：

```bat
chcp 65001 >nul
cd /d "%~dp0"
```

后端至少运行：

```powershell
python -m py_compile backend\app\database.py backend\app\models.py backend\app\schemas.py backend\app\tushare_client.py backend\app\us_research.py backend\app\main.py backend\app\sync_worker.py backend\app\quant_research\metrics.py backend\app\quant_research\reporting.py backend\app\strategy_results.py
python -m unittest discover backend\tests -v
```

涉及 migration、质量、快照、runner 或 Worker 时还要运行：

```bash
scripts/ops/test_postgres_integration.sh
```

前端：

```bash
scripts/ops/test_frontend_production_image.sh
```

Compose：

```powershell
docker compose config
```

Docker 不可用时如实记录，不能假装验证通过。

## 服务器与生产门禁

- 服务器连接使用本机 SSH config 与 `.env`，私钥路径和 IP 不写入仓库长期文档。
- 数据库、API 和前端端口默认只监听 `127.0.0.1`；PostgreSQL 不开放公网。
- 当前生效的远程访问决定是**仅使用 SSH 隧道**访问 loopback 服务；不购买或申请域名，不部署 Cloudflare Tunnel、Cloudflare Access 或 Tailscale Serve，也不开放公网 IP 端口。除非用户明确提出变更，否则后续任务不得再次把域名、外部认证入口或公网开放作为待确认项。
- 构建、验证和排查优先在目标服务器执行，减少本机资源占用；仍须先确认目标主机和分支。
- 新服务器迁移采用并行搭建、停写逻辑备份恢复、工件同步、schema/行数/日期/指纹/API/前端读回和旧服务器回滚。
- 生产 Alembic upgrade、baseline stamp、`DROP INDEX`、覆盖恢复、生产切换、旧服务器清理和凭据变更必须由用户单独批准。
- 代码合并、CI 通过、镜像构建、容器启动、生产读回和研究结论是不同事实，报告时必须分开。

## 操作日志

每个阶段性任务开始或结束时追加 `操作日志.md`，至少记录：

- 本机时间与时区。
- 阶段目标。
- 实际修改、关键命令和重要决策。
- 验证结果与未验证原因。
- 后续事项和人工门禁。

日志只记录事实和工程判断，不写任何凭据。
