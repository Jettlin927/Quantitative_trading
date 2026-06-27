# AGENTS.md

`my_quant/` 当前只作为 sample 数据源目录，不再作为策略研究目录。

允许：

- 维护 `us_research/` 下的 sample 观察池、sample 持仓结构和 sample 快照。
- 修复 sample 文件读取、字段映射和快照刷新脚本。

禁止：

- 恢复历史策略、回测、报告生成或自动化交易脚本。
- 提交真实持仓、真实成交、券商导出、token 或凭据。
- 把 sample 观察池写成买卖建议。

如需重新开启策略研究，必须先回到根目录 `AGENTS.md` 的“必须先问用户”规则。
