# 研究历史迁移

Issue #21 的迁移只把治理合同建立前已经存在的研究事实映射到统一档案，不重新运行研究，也不产生新的研究批准。

## 冻结范围

- 来源运行共 50 个；只有 16 个 canonical 运行在运行 ID、策略 ID、`succeeded` 状态、结果指纹、复现键和代码提交全部一致时可关联。
- 三份当前可信报告拆成四个结构化研究，结论全部原样保留为“不通过”。波动率管理与低波动准入共用一个报告包，但在结构化档案中保持两个策略、两个评价。
- 三份 legacy 结果只建立“已归档”策略档案，原 `status=ok` 或“观察”只作为来源元数据保留，不创建当前研究评价。
- 其余运行保持未发布；不能依靠相同指纹、成功状态或相似策略名猜测归属。

冻结来源合同位于 [`configs/research-history-migration-v1.json`](../../../configs/research-history-migration-v1.json)。迁移代码会校验合同、manifest、三份当前 HTML 报告、机器摘要、双轮复现证据和 legacy 全部原始工件的哈希；四项当前研究的经济假设、支持证据、反对证据、尚缺证据、限制项与后续建议均使用合同中显式 JSON 路径读取，不按字段名猜测。低波动准入的后续建议明确取自 `lowVolatilityGateFollowup.researchClassification`，历史“不通过”评价同样不能省略后续研究建议。

## 三种模式

以下命令都读取 `DATABASE_URL`，不会打印连接串。先在夹具或隔离 PostgreSQL 16 执行；生产 Alembic upgrade 与生产数据迁移仍需单独人工批准。

```bash
python scripts/research/migrate_research_history.py --mode preview \
  --output-json outputs/history-migration/preview.json \
  --output-markdown outputs/history-migration/preview.md
```

`preview` 只读并输出来源运行数、可靠关联数、未发布数、来源清单指纹和迁移指纹。

```bash
python scripts/research/migrate_research_history.py --mode rollback \
  --output-json outputs/history-migration/rollback.json \
  --output-markdown outputs/history-migration/rollback.md
```

`rollback` 在单一事务中完成全部写入与约束检查，随后主动回滚，用于证明不会留下部分迁移。

```bash
python scripts/research/migrate_research_history.py --mode apply \
  --confirm-migration-fingerprint <preview 中的完整迁移指纹>
```

`apply` 必须显式提供同一数据库现状下的完整迁移指纹；运行数不是冻结的 50 个时也会拒绝。所有目标 ID 都由冻结来源确定，重复执行只接受逐字段一致的记录，任何既有记录漂移或非目标正式研究关联都会中止事务。历史正式研究写入 `stopped`，统一发布记录只写入 `pending` 且不填发布时间；数据库只允许迁移程序把早于历史研究、且完整身份存在于冻结计划中的既有成功运行关联进来，禁止新运行借用历史来源。已提交的不可变历史档案不做破坏性回滚；修正只能按研究发布合同创建前向替代版本。

## 人工门禁

本仓库只交付 revision、迁移程序、隔离演练和报告。不得在本任务中执行生产 `alembic upgrade`、生产 `apply`、发布状态晋升、覆盖恢复、volume 删除或 canonical 工件清理。生产迁移完成后，仍须另行取得 Issue 最终中文结论、API 和前端同一评价版本读回，才能把 `pending` 晋升为 `published`。
