# 架构

本模块描述个人投资工作台的稳定系统流和代码职责；容器实例、提交、端口占用和部署
状态不在这里冻结。

## 当前拓扑

- `frontend`：今日、持仓、美股标的和系统健康界面。
- `api`：私有工作台、市场观察和健康 API；不在请求进程执行长任务。
- `personal-analysis-worker`：AI 分析队列与持仓规则周期评估，只通过私有 Compose 覆盖启用。
- `db`：PostgreSQL 公共 schema 与隔离的 `private_workbench` schema。
- Alpaca：按用途授权的只读市场观察来源。
- DeepSeek：仅由私有 Worker 调用的分析模型。

旧研究服务、脚本、schema 和工件不属于当前拓扑；存在这些资产不表示仍受支持。详见
[ADR 0011](../adr/0011-personal-investment-workbench-without-research.md)。

## 入口

- [系统流程](system-flow.md)：请求、持仓、规则和 AI 分析如何流转
- [代码地图](code-map.md)：按职责分组的当前入口与遗留边界
- [统一领域语言](../../CONTEXT.md)
- [架构决策](../adr/)
