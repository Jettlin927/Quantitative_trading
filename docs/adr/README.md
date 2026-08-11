# 架构决策

本目录保存系统级、难以逆转的稳定决定。新方案与既有 ADR 冲突时必须显式提出新的替代决策，不能静默改写。

## 当前决定

- [新服务器通过并行验收接替生产](0005-new-server-replaces-production-safely.md)
- [单一生产服务器与旧服务器退役](0008-single-production-server-and-retired-legacy-host.md)
- [个人 AI 分析 agent 化：通过工具访问持仓、行情与新闻](0009-personal-ai-analysis-agent-tools.md)
- [美股优先工作台与旧数据链路退役](0010-us-first-workbench-and-retired-legacy-data-paths.md)
- [个人投资工作台不再包含研究系统](0011-personal-investment-workbench-without-research.md)
- [本机私有只读 MCP 使用固定 actor 与确定性工具白名单](0013-local-read-only-personal-mcp.md)

## 已被当前方向取代

以下决定只解释遗留 schema、代码和文档，不再描述当前产品：

- [正式研究必须按冻结计划经过人工批准](0001-formal-research-requires-human-approval.md)
- [研究运行状态与研究评价分开建模](0002-separate-run-state-from-research-evaluation.md)
- [GitHub 控制研究，服务器执行研究](0003-github-controls-server-executes.md)
- [已发布研究不可覆盖](0004-published-research-is-immutable.md)
- [历史研究导入是来源记录，不是研究批准](0006-history-import-is-provenance-not-approval.md)
- [个人工作台使用私有存储并与正式研究隔离](0007-personal-workbench-is-private-and-separated-from-formal-research.md)
