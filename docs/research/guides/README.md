# 研究指南

本模块保存如何按研究合同准备、运行、复现和发布研究的稳定操作指南。当前 CLI 入口位于 `scripts/research/`，canonical 工件写入 `outputs/research-runs/`。

执行前至少确认：

1. 冻结研究计划已由授权用户按精确 `plan_sha256` 批准。
2. 策略代码已合并、CI 通过并静态登记。
3. 数据质量运行、快照、代码和环境身份一致。
4. 运行、评价、结论与发布状态分别记录。
5. 失败运行和反对证据同样发布，不只展示胜出结果。

评价顺序和最低证据见[策略画像与评价规范](../contracts/strategy-evaluation-standard.md)。

历史兼容流程见[研究历史迁移](history-migration.md)；该流程的 `historical_import` 来源身份不等于用户批准。
