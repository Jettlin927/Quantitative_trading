# 个人 AI 分析 agent 化：通过工具访问持仓、行情与新闻

个人美股 AI 投研工作台的 AI 分析从"单发冻结证据"模式扩展为"tool-use agent"模式：
模型在受控循环中主动调用服务端工具（查持仓、查 K 线、查新闻）后再产出结构化影响
分析。该模式仅用于个人工作台，与正式研究路径完全隔离。

## 背景

单发路径（`AnalysisWorkspace` + `DeepSeekChatAdapter`）把冻结证据以纯文本注入
prompt，且 `DENIED_AI_SOURCES`/`DENIED_AI_FIELDS` 明确禁止模型接触行情与持仓
衍生指标——这是"证据可信、模型不可信"旧契约的组成部分。用户期望把模块解耦为
带 tool use 的 agent，让模型按需获取持仓、目标标 K 线与产业新闻。这是对旧契约
**针对个人工作台**的产品级放开，必须显式记录边界与门禁。

## 决定

- 新增 `backend/app/personal_workspace/agent/`：provider 无关的 tool-use 运行时
  （多轮 tool-call 循环）、DeepSeek agent 适配器（放开 tools/多轮消息，保留
  model/stream/thinking/max_tokens 安全约束）、三个工具（`get_holdings`、
  `get_kline`、`get_news`）与技能注册表（Skill = 提示片段 + 工具子集）。
- 与单发路径并行共存：`PERSONAL_ANALYSIS_MODE=legacy|agent`（默认 legacy）在
  worker 与 API runtime 一致选择；agent 模式复用既有 drafts/runs/events/租约
  存储与月度软预算记账，最终输出仍遵守 4 类 claims 契约与禁买卖评级约束。
- 数据访问门禁：K 线/现价通过 `AlpacaMarketObservationAdapter` 以
  `purpose="ai_context"` 授权获取——授权文件未授予时 fail-closed；新闻通过
  `INVESTMENT_NEWS_DIR` 指向的 investment-news（MIT）本地子进程抓取并解析
  `data.js`（argv 列表、无 shell、TTL 缓存）；持仓仅读取当前 actor 自己的
  `PortfolioStore` 数据，解密只在进程内。
- 工具结果回灌模型时包装为带 `evidence_id`（`tool:工具名:序号`）的可引用证据；
  最终 claims 的 evidence_ids 必须引用真实工具证据，否则校验失败。
- 正式研究隔离不变：`quant_research`、research worker 与发布路径不得导入
  个人工作台模块（含 agent 包）；docker-compose.yml 的 worker/research-worker
  不获得 DeepSeek、Alpaca 或私有配置。

## 边界

- 本决定不授权生产部署、migration、数据库角色或 secret 变更；切换到 agent 模式
  需要用户手工在 alpaca 授权文件中授予 daily bars 的 `ai_context` 用途，并配置
  `INVESTMENT_NEWS_DIR`。
- agent 输出的行情/新闻为快照，时效以工具返回的 as_of 为准；AI 分析结论不构成
  投资建议，也不映射为正式研究批准或结论。
- 前端"工具/技能选择"与 agent 模式的可视化预览属于后续迭代，当前前端流程
  （问题 + 标的 → 预览 → 确认）在 agent 模式下继续可用。
