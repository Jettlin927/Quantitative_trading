# 策略结果统一只读档案

本目录是已完成研究结果的可提交发布层。统一入口为 [`index.html`](index.html)，机器清单为 [`manifest.json`](manifest.json)，API 通过 `GET /api/strategy-results/overview` 只读投影同一清单。

这里没有回测执行、参数搜索、券商连接、交易信号或真实资金入口。HTML/JSON 只是 canonical 运行的展示投影；研究真相仍由冻结输入、代码和环境身份、运行 manifest、账本哈希与 result fingerprint 共同确定。

## 当前可信报告

| 报告包 | 包含策略 | 状态 | 文件 |
| --- | --- | --- | --- |
| `etf-volatility-managed-20260713/` | 波动率管理四变体与低波动准入追加验证 | 两项均 `不通过` | `index.html`、`summary.json` |
| `etf-trend-120d-long-history-20260713/` | 120 日均线月末趋势开关 | `不通过` | `index.html`、`summary.json` |
| `a-share-b1-trend-pullback-20260713/` | 公开 B1 描述的固定代理近似复现 | `不通过` | `index.html`、`summary.json` |

2026-07-19 的最终修复统一了 OOS 首日、被动基准、walk-forward 与子区间回撤口径：期首已有上一信号触发的持仓或执行时，必须从初始净值 1.0 计入首日收益和费用。三组报告共 16 个 canonical 运行均绑定代码提交 `26da0d347d77de7ee03a95277fc4ad45bdaa983a`；每个运行在同一精确镜像的断网容器中连续复现 2 次，16/16 × 2 的 result fingerprint 全部匹配。[`reproduction-evidence-20260719.json`](reproduction-evidence-20260719.json) 固化两轮运行 ID、指纹、镜像和断网条件；三个生成器在发布前校验各自运行子集，不能再靠硬编码声称复现通过。运行 ID、snapshot、配置哈希和结果指纹也写在各报告的 `summary.json` 中。

## 历史档案

- `b1_standard_phased_backtest_20260627_latest.json` 与 `b1_score_weight_scan_20260627_latest.csv`：旧 B1 短区间分阶段结果，已被长历史近似复现取代，只保留追溯。
- `ma-trend-reversal-20260629/`：旧均线趋势与趋势反转研究。
- `value-sector-stopfall-20260629/`：旧低估质量与行业止跌研究。

历史摘要中的 `status=ok` 只表示旧脚本执行成功，不等于当前规范的 `研究通过`。API 的 `summary.status` 统一取 manifest 的“历史档案”，原值只以 `sourceExecutionStatus=ok` 返回；`manifest.json` 和总览页都把这些结果显式标记为 `legacy`/“历史档案”。

把本目录映射到 PostgreSQL 统一档案时，必须遵守[研究历史迁移指南](../guides/history-migration.md)：当前三份报告保留既有“不通过”，legacy 不推断结论，旧运行只按精确身份关联。

## 发布合同

新增或更新面向用户的研究结果时：

1. canonical 运行和大型逐日账本保留在被 Git 忽略的 `outputs/research-runs/`。
2. 当前可信报告包写入 `docs/research/strategy-results/<report-id>/`，至少包含 `index.html` 和 `summary.json`。
3. 在 `manifest.json` 登记状态、样本、基准、结论边界以及 `reportHtml`/`summaryJson`/`reproductionEvidence` 相对路径。
4. 更新根 `index.html`，明确区分当前可信报告和旧档案。
5. 运行报告测试、完整后端门禁和断网复现；生成器必须读取证据文件校验两轮指纹，并从最新 canonical manifest 派生 `reportGeneratedAt`。如证据、展示与 canonical 工件不一致，停止发布并重新生成。
