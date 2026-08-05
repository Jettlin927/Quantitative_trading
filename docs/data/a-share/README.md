# A 股退役 schema 边界

A 股不再是当前产品或运行时能力。2026-08-05 起，A 股页面、Tushare 拉取、公共同步 Worker、cron、回填脚本和活动策略入口均已退役；不得从本页恢复生产同步或把旧接口当作当前合同。

## 为什么仍能看到 A 股表与文档

- 既有 Alembic revision 是生产 schema 的迁移历史，不能通过删除旧 migration 改写。
- 历史正式研究、发布评价和 canonical 工件需要保持来源身份与可读性。
- PostgreSQL 集成测试可建立完整历史 schema，并用合成数据验证 migration、自然键和兼容约束。

这些保留项是迁移与审计资产，不是活动数据源。测试不得需要 Tushare token、真实 A 股数据或网络拉取；生产表的物理删除需要新的前向 revision、保留方案和单独生产授权。

## 历史证据

- [A 股研究资料](../../research/)中的 dated 预登记和可行性报告按原事实保留。
- [历史归档](../../archive/)保存旧数据源、覆盖与运维快照。
- 当前方向与取代关系见 [ADR 0010](../../adr/0010-us-first-workbench-and-retired-legacy-data-paths.md)。
