# 10 小仓卫星策略：50% 年化 / 30% 回撤闸门

## 目标对应

- 研究目标：小仓卫星策略，目标年化 `50%+`。
- 硬约束：任何全样本、样本外或 Walk-Forward 阶段最大回撤不得跌破 `-30%`。
- 执行纪律：低位不是入场信号，止跌才是入场信号。

## 策略假设

全仓追求 `50%+` 年化容易破坏执行纪律，因此该策略只作为独立卫星仓研究。收益来源不是永久组合式分散，而是在高 beta ETF 中识别强趋势，并在趋势破坏或组合回撤扩大时主动退回防守资产。

## 执行流程

1. 投资宇宙包含核心防守资产、黄金、海外权益和高 beta 主题 ETF。
2. 对每个进攻资产计算动量、波动率和 `RAM = momentum / volatility`。
3. 仅保留 RAM 为正且价格高于趋势均线的资产。
4. 在合格资产中选择 Top1 或 Top2。
5. 没有合格资产时切到 `511880`。
6. 组合回撤超过 `15%` 时降低进攻仓位。
7. 组合回撤超过 `22%` 时进一步降低进攻仓位。
8. 组合回撤触及 `30%` 闸门时进入防守和冷却期。
9. 每次调仓纳入 `0.1%` 单边成本。
10. 输出全样本、样本外、Walk-Forward 和最终候选报告。

## 通过条件

- 全样本年化收益率不低于 `50%`。
- 全样本最大回撤不低于 `-30%`。
- 样本外最大回撤不低于 `-30%`。
- Walk-Forward 每折最大回撤不跌破 `-30%`。
- 收益不由单一年份或单一参数点贡献。

## 淘汰条件

- 任一关键阶段最大回撤跌破 `-30%`。
- 全样本年化超过 `50%` 但样本外失效。
- 候选只依赖一个孤立参数点。
- 换手和成本拖累吞噬主要收益。
- Top1 路径过度集中，真实执行中难以承受。

## 对比输出

- `results/satellite_asset_diagnostics.csv`
- `results/satellite_parameter_scan.csv`
- `results/satellite_train_parameter_scan.csv`
- `results/satellite_oos_result.csv`
- `results/satellite_walk_forward.csv`
- `results/satellite_final_candidate.md`
- `results/satellite_manifest.json`

## 当前验证结论

### ETF 卫星初筛

当前 `results/satellite_final_candidate.md` 中的最优 near-miss 是：

```text
fixed_513100_518880_50_50_x2_0
```

- 年化收益率：`28.16%`
- 最大回撤：`-26.68%`
- 状态：回撤通过，但远低于 `50%` 年化目标。

### 外部高波动资产探针

外部资产池包含 `TQQQ`、`SOXL`、`TECL`、`UPRO`、`BTC-USD`、`ETH-USD`、`GLD`、`TLT`。结果写入：

- `results/satellite_external_probe_summary.md`
- `results/satellite_external_ram_probe.csv`
- `results/satellite_external_fixed_probe.csv`

结论：

- 同时通过 `50%` 年化和 `-30%` 最大回撤的行数：`0`
- 最高收益行年化约 `50.97%`，但最大回撤约 `-40.98%`
- 最优回撤合格 near-miss 年化约 `24.14%`，最大回撤约 `-27.78%`

### 外部高波动 vol-target 探针

为测试“高波动 RAM + 目标波动率 + 更早回撤降档”是否能把 near-miss 推过门槛，增加一次窄扫探索，结果写入：

- `results/satellite_external_vol_target_probe_summary.md`

结论：

- 聚焦网格扫描配置数：`3,456`
- 回撤合格配置数：`25`
- 同时通过 full-sample `50%` 年化和 `-30%` 最大回撤配置数：`0`
- 最优回撤合格行年化 `24.63%`，最大回撤 `-29.74%`
- 最高收益行年化 `44.60%`，最大回撤 `-45.11%`

vol-target 没有解决目标。更严的回撤降档会把收益压到远低于 `50%`，更高收益配置仍然出现 `40%+` 回撤。继续调同一信号的杠杆和降档参数，边际价值很低。

### 正反向杠杆 ETF 趋势探针

为测试“只做多标的，但允许买入反向杠杆 ETF 表达下跌趋势”是否能改善 payoff 形状，增加一次长/反向杠杆 ETF 探针，结果写入：

- `results/satellite_inverse_leveraged_trend_probe_summary.md`

资产池包含 `TQQQ`、`SOXL`、`TECL`、`UPRO`、`SQQQ`、`SOXS`、`TECS`、`SPXU`、`GLD`、`TLT` 和现金。探针扫描 `5,184` 个配置。

结论：

- 同时通过 `50%` 年化和 `-30%` 最大回撤配置数：`0`
- 回撤合格配置数：`1,718`
- 最高收益行年化 `28.19%`，最大回撤 `-33.38%`
- 最优回撤合格行年化 `18.69%`，最大回撤 `-26.36%`

反向杠杆 ETF 没有把策略推进到目标区间。日频趋势框架下，反向产品带来的路径依赖和反复打脸成本大于新增收益来源。

## 下一步研究方向

1. 不再把“提高杠杆 + 回撤降档”作为主线，它已经被多次证伪。
2. 不再把“加入更多杠杆 ETF 或反向 ETF”作为主线，除非同时引入 materially different signal。
3. ETF/RAM/外部高波动/反向杠杆 ETF 路线仍未证明 goal 完成。
4. A 股 B1/Tushare 质量过滤路线已经产生第一个本地 `50% / 30%` 闸门通过候选，详见 `strategies/11_a_share_b1_trend_pullback/flow.md` 和 `results/b1_tushare_quality_gate_top300.md`。
5. 下一步不再优先扫杠杆 ETF，而应对 B1 质量过滤候选做实盘约束验证：涨跌停成交、停牌、滚动股票池、容量、滑点和更细 walk-forward。
