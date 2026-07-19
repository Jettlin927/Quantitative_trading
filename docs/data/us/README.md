# 美股数据边界

当前仓库中的美股文件和数据库记录是 sample/实验夹具，不能冒充研究级实际市场数据，也不能单独支持正式研究结论。

## 当前允许

- 只读预览 `my_quant/us_research/` 的 sample 资产、快照和观察池。
- 将 sample 数据幂等写入明确标注的 sample schema。
- 在 API 和前端显著展示 sample 身份与限制。

建设研究级美股日线、企业行动、历史 universe 和许可合同属于独立功能债；在其完成前不得淡化 sample 标识。

## 历史实施记录

- [数据库确认清单（2026-06-27）](../../archive/data/us/us-db-confirmation-checklist-2026-06-27.md)
- [sample schema 实施记录（2026-06-27）](../../archive/data/us/us-sample-db-schema-implementation-2026-06-27.md)
- [sample 只读 API 记录（2026-06-27）](../../archive/data/us/us-sample-readonly-api-2026-06-27.md)
