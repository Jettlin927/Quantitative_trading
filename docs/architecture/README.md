# 架构

本模块描述稳定的系统、领域、数据流和代码职责；易变化的容器实例、提交、端口占用和部署状态不在这里冻结。

## 默认拓扑

- `frontend`：美股个人工作台的前端界面，不再暴露旧 A 股策略驾驶舱。
- `api`：个人工作台、市场观察、研究和健康 API，不在请求进程执行长任务。
- `personal-analysis-worker`：私有 AI 分析队列，只通过个人 Compose 覆盖启用。
- `research-worker`：正式研究队列；启用前必须完成相应工程 Issue 和人工批准门。
- `db`：PostgreSQL 结构化事实。
- `artifacts`：冻结输入、账本、复现证据和原始报告。

GitHub Issues 是计划、批准和用户可见结论的控制面；服务器是队列、数据、计算和工件的执行面。结构化事实与冻结工件相互补充，任一单独存在都不代表发布完成。

A 股、公共同步 Worker、免费美股实验和个人不可变记录已退役；Alembic 历史与研究证据继续保留。详见 [ADR 0010](../adr/0010-us-first-workbench-and-retired-legacy-data-paths.md)。

## 入口

- [系统流程导航](system-flow.md)：拓扑、研究生命周期与数据流的每一步归属
- [代码地图](code-map.md)：按领域分组的稳定模块职责
- [统一领域语言](../../CONTEXT.md)
- [架构决策](../adr/)
- [研究合同](../research/contracts/)
