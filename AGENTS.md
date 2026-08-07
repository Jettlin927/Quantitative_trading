# 仓库 Agent 规则

本文件只保存每次任务都需要的长期规则。默认使用中文沟通；Issue、Pull Request、
Commit、报告、前端文案和自动化评论使用中文。代码标识、数据库字段和第三方错误可
保留英文，面向用户时补充中文解释。

## 开始任务

1. 完整读取本文件、存在时的 `AGENTS.local.md` 和根目录 `CONTEXT.md`。
2. 明确目标、授权边界和可检查的完成条件；有多种解释时先说明，不静默选择。
3. 检查工作区并保留用户已有修改。易变化的 Issue、提交、CI、部署、数据库和运行
   状态必须现场核验，历史文档只作证据。
4. 按任务读取下表中的参考；只读取命中的分支。

| 任务 | 必读参考 |
| --- | --- |
| 架构、领域或职责边界 | `docs/agents/domain.md`、`docs/architecture/code-map.md`、相关 `docs/adr/` |
| Issue 驱动或自动领取 | 目标 Issue、父 Issue、原生依赖、验收条件、`docs/agents/issue-tracker.md` |
| 持仓、交易事实或权益 | `docs/product/README.md`、`docs/architecture/system-flow.md` |
| 行情、数据来源或证据 | `docs/data/us/README.md` |
| AI 分析、工具或模型调用 | ADR 0009、`docs/operations/personal-workbench-secrets.md` |
| 生产部署、迁移、切换或新电脑访问 | `docs/operations/production-deployment-and-home-access.md` |
| 页面、组件、布局或视觉调整 | `.codex/skills/frontend-design/SKILL.md` |
| 选择验证范围 | `docs/agents/validation.md` |
| 其他文档 | `docs/index.md` |

## 产品边界

本仓库是美股个人投资工作台，不是量化研究平台或真实交易系统。当前能力只有用户
手工维护的持仓与成交事实、Alpaca 市场观察、确定性规则提醒、个人 AI 分析和系统
健康检查。

- 持仓、现金、成本和成交只接受用户显式手工输入，不从券商、邮箱或导出文件自动同步。
- 系统不连接交易账户，不执行下单、撤单、调仓、融资融券、申购赎回或资金操作。
- 行情、规则和 AI 输出是个人分析材料，不表述成买卖评级、收益承诺或自动执行指令。
- 旧量化研究代码、配置、schema 和文档是遗留资产，不是当前产品能力或工作入口；
  未经新的产品决定和迁移方案，不恢复、不扩展、不接入当前界面或运行拓扑。

## 执行与授权

- GitHub Issues 是唯一活动路线图；dated 计划、旧 Issue 和归档文档只保存历史证据。
- 用户直接提出的小改动、排查或咨询无需先建 Issue。独立工程任务和生产变更进入
  对应 Issue。
- 自动领取只选择无未完成阻塞、带 `可由智能体处理` 标签的 Issue，并在开始前分配
  给执行者。
- `需人工处理`、`待补充信息`、生产数据变更和不可逆操作需要明确授权。当前请求中
  精确且范围清楚的授权有效；目标或范围不清时停下确认。
- Issue 驱动代码任务使用独立分支或 worktree，按风险验证并创建中文 Pull Request；
  不自动合并。用户指定的本地小改动可在当前工作区完成。
- 只改目标所需内容；保留既有修改，不使用 `git add -A` 混入无关文件，不顺手重构。
- 新建 GitHub 标签使用中文，映射见 `docs/agents/triage-labels.md`。

## 代码与数据边界

- `backend/app/personal_workspace/` 负责私有持仓、权益、规则和 AI 分析；
  `backend/app/market_observation/` 负责 Alpaca 适配、来源健康和用途授权。
- `backend/app/models.py` 是应用 schema 合同；生产演进只使用
  `backend/migrations/` 中的新 Alembic revision，不改写历史 migration。
- `frontend/` 只做产品交互和投影，不计算持仓、收益、规则或权限结论。
- 私有写请求保持 gateway、Origin、Fetch Metadata、JSON、个人请求头和幂等校验；
  私有配置缺失时 fail-closed。
- 实际市场数据、合成夹具和个人数据在 schema、权限、API 与测试中隔离。API 数值
  必须 JSON-safe，`NaN` 和 `Infinity` 转为 `null` 或明确兜底。
- 长任务和 AI 分析在独立 Worker 中执行，不在 API 请求进程内运行。
- 凭据只从受保护入口读取，不写入源码、命令参数、Issue、日志、前端、测试或工件。

## 生产与持久资产

- `quant-trading-prod` 是唯一生产服务器和数据权威；只通过 SSH 隧道访问 loopback
  服务。目标主机、精确提交、配置和授权每次现场确认。
- PostgreSQL、备份、个人数据和未知来源文件均视为持久资产。卷删除、覆盖恢复、
  生产 migration、baseline stamp、生产切换、数据清理和凭据变更需要对精确操作的
  明确授权及读回方案。
- 生产迁移必须包含停写、备份恢复、schema/行数/日期读回、API/前端验收和独立回滚
  方案；不得恢复已退役旧服务器或旧研究服务。

## 完成与报告

- 按 `docs/agents/validation.md` 从最接近改动的检查开始；未运行、跳过或环境受限的
  检查明确列出，不写成通过。
- 分开报告本地检查、Commit、Pull Request、CI、合并、镜像、部署、容器启动、生产
  读回和业务验收；前一项不能证明后一项。
- 活动状态与授权以 GitHub Issue 为准，代码历史以 Git/PR 为准，CI 与部署以自动化
  记录和目标环境读回为准。不可逆操作在对应 Issue 或 PR 留痕。
