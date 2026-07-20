# 美股数据边界

当前仓库中的美股文件和数据库记录是 sample/实验夹具，不能冒充研究级实际市场数据，也不能单独支持正式研究结论。

## 当前允许

- 只读预览 `my_quant/us_research/` 的 sample 资产、快照和观察池。
- 将 sample 数据幂等写入明确标注的 sample schema。
- 在 API 和前端显著展示 sample 身份与限制。

建设研究级美股日线、企业行动、历史 universe 和许可合同属于独立功能债；在其完成前不得淡化 sample 标识。

## 数据源调研

- [美股日线开源仓库与正式数据源调研（2026-07-21）](open-source-daily-data-source-survey.md)

## 外部数据技能边界

- 仓库已收录 `.codex/skills/global-stock-data/`，用于检查新浪、Yahoo、东方财富和 SEC 等公开端点的字段与可用性。
- 技能代码的 Apache-2.0 许可证不替代上游行情数据的复制、持久化和衍生使用许可，也不提供服务等级承诺。
- 技能返回成功只证明端点在当次请求中可访问；在历史 universe、企业行动、复权、交易日历、退市身份、原始响应冻结、质量门禁和失败恢复合同完成前，其结果仍不得标记为研究级数据。
- 相关实施门禁与待确认项继续由 [功能债：建设研究级美股日线数据模块](https://github.com/Jettlin927/Quantitative_trading/issues/27) 管理。

## 历史实施记录

- [数据库确认清单（2026-06-27）](../../archive/data/us/us-db-confirmation-checklist-2026-06-27.md)
- [sample schema 实施记录（2026-06-27）](../../archive/data/us/us-sample-db-schema-implementation-2026-06-27.md)
- [sample 只读 API 记录（2026-06-27）](../../archive/data/us/us-sample-readonly-api-2026-06-27.md)
