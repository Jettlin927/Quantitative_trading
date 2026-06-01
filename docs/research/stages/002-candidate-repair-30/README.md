# 002-candidate-repair-30

## 阶段状态

`active`

## 阶段目标

把观察级主线 `cross-section-strength-risk8` 修复为阶段候选。

本阶段不追求直接冲击 `004-high-return-frontier-75`，而是先验证 `001-observation-diagnosis` 形成的结构性归因是否能转化为可复现收益。

## 硬门槛

- 年化收益率 `>= 30%`。
- 最大回撤绝对值 `< 10%`。
- 已完成交易盈亏比 `>= 2:1`。
- 滚动窗口至少 `5/7` 通过。
- 收益后 10 和资本尾部不能破当前阶段硬线。

## 起点证据

- 主线观察候选：`105-portfolio-cross-section-risk8-pos135-entry-gap6-netreturn-limitdelay-slip10bp-gap`。
- 失败窗口归因：`docs/research/stages/001-observation-diagnosis/evidence.md`。
- 禁止重复的简单负证据：`107/108/109/111/112/113`。
- 当前已滚动验证的最好观察候选：`002-repair-indicator-confluence-moneyflow-crowding-penalty-001`，完整三年年化 `27.26%`、总收益 `106.01%`、最大回撤 `-3.85%`、盈亏比 `2.66:1`、滚动 `5/7`；仍低于本阶段 `30%` 年化硬门槛，失败窗口仍是 `Y1/R18-1`。
- 当前完整三年最高观察候选：`002-repair-volume-inefficiency-crowding-penalty-003`，年化 `28.88%`、总收益 `113.98%`、最大回撤 `-3.57%`、盈亏比 `2.68:1`；因完整年化仍未达到 `30%`，暂不推进滚动验收。

## 优先验证假设

1. 复合指标因子：MA 只能作为结构信息之一，不再作为单一主轴；在 `high60Rank/baseScore` 基础上把 MACD、BOLL、RSI、MA 结构、量价状态和新增数据语义组合成可消融的横截面因子，先验证排序效果，不直接改核心买卖语义。
2. 行业状态过滤/行业内排序：用行业宽度、行业近期胜率或行业动量减少失败窗口里的错误行业暴露。
3. 近 60 日高点质量排序：保留 `high60Rank` 与 `baseScore`，降低单纯近期涨幅排名的解释权。
4. 拥挤风险非线性处理：对极端成交量、极端缺口、高振幅和指标过热做降分、降仓或预算约束，而不是简单放宽硬过滤。
5. point-in-time 事件/公告/题材风险标签：题材只作为可复盘上下文或风险识别线索，先诊断入场后路径，不直接把覆盖数量或热度当作追涨加分。
6. 非可比退出实验预备：已知当前主线存在浮盈回吐，若测试状态化利润保护、浮盈回吐约束或持仓时间管理，必须新建明确的非可比口径，不能悄悄覆盖当前主线语义。

## 禁止重复尝试

- 不继续单独放宽买入缺口或买入日振幅。
- 不关闭市场宽度过滤。
- 不继续只调成交量单一权重来碰结果。
- 不把 MACD、BOLL、RSI 等单个指标当作孤立硬过滤；必须作为可消融的组合因子验证。
- 不继续在 OHLCV 技术指标内部做小权重搜索来碰结果；已知 `indicatorSetup=0.50` 和成交额效率/RSI 交互会退化，除非先有新的失败窗口诊断证据。
- 不继续把主力资金流 3/5 日均值当作独立排序修复；已知 `moneyflowMainNetRank3/5=0.25` 不优于主线，`moneyflowMainNetRank1=0.25` 也未修复 `Y1/R18-1`。
- 不继续把粗粒度行业状态确认或 RSI 确认当作主力资金流的独立修复；已知 `moneyflowIndustryConfirm=0.25` 和 `moneyflowRsiConfirm=0.25` 不优于当前主线。
- 不继续只调主力资金流权重或简单市场强势门控；已知 `moneyflowMainNetRank1=0.10` 几乎不改变策略，`moneyflowMarketStrong=0.25` 年化仅 `21.98%`，滚动仍为 `5/7` 且失败窗口 `Y1/R18-1` 候选独有交易为负，不能作为 002 修复候选。
- 不继续把资金流、RSI 均衡和行业强度做全局乘法排序项；已知 `moneyflowMarketQuality=0.25` 虽改善 `Y1/R18-1` 全样本切片替换净差，但把完整年化降到 `19.86%`，且明显损害 `Y3/R18-4` 右尾。
- 不把 `moneyflowMarketSurgeQuality=0.25` 当作阶段候选；它是较干净的状态触发正证据，但年化仅 `21.76%`，滚动仍失败 `Y1/R18-1`。
- 不继续把 `moneyflowMarketSurgeQuality` 的行业绝对强度简单替换成行业相对强度；已知 `moneyflowMarketSurgeRelativeQuality=0.25` 与原脉冲质量因子成交完全一致，`0.50` 虽全样本年化微升至 `21.78%`，但相对原脉冲质量因子在 `Y1/R18-1` 替换净差为 `-2544.05`。
- 不继续在 `moneyflowMarketSurgeQuality` 上添加简单 RSI/行业相对强弱确认地板；已知 `moneyflowMarketSurgeConfirmedQuality=0.25` 年化仅 `21.70%`，低于原脉冲质量因子，且相对原因子换入两笔早段止损。
- 不把 `indicatorPulseQuality` 单独当作阶段候选；已知 `indicatorPulseQuality=0.50` 可修复部分 `Y1/R18-1` 替换交易，但全窗口年化仅 `21.36%`，且回撤和盈亏比弱于 `0.25`。
- 不继续提高 `indicatorPulseQuality + moneyflowMarketSurgeQuality` 的组合权重；已知 `indicatorPulseQuality=0.50 + moneyflowMarketSurgeQuality=0.25` 全窗口年化 `22.50%`，但滚动降为 `4/7`。当前较稳观察候选是 `indicatorPulseQuality=0.25 + moneyflowMarketSurgeQuality=0.25`，全窗口年化 `22.89%`、滚动 `5/7`，但仍低于阶段年化 `30%` 且失败 `Y1/R18-1`。
- 不继续把 `MACD柱改善 * BOLL位置均衡 * RSI均衡 * 前期跳空稳定` 的转向组合因子当作独立加权方向；已知 `indicatorTurnQuality=0.25` 全窗口年化仅升至 `23.53%`，但滚动降为 `4/7` 且 `R18-2` 尾部破到 `-32.21%`，`0.10` 又不改变交易选择。
- 不继续把 `moneyflowMarketSurgeQuality * RSI水平 * MACD柱改善` 的 RSI 动量跟随因子当作全局加权方向；已知 `rsiMomentumQuality=0.25` 全窗口年化仅升至 `23.60%`，但滚动降为 `4/7` 且 `R18-2` 尾部仍破到 `-32.21%`。
- 不继续把更严格资金流确认当作 RSI/MACD 动量修复；已知 `rsiMomentumConfirmedQuality=0.25` 完整三年年化降至 `22.47%`，`moneyflowMarketSurgeStrictQuality=0.25` 与当前最好观察候选完成交易一致，不能补足阶段收益缺口。
- 不继续简单提高个股特异突破因子权重；已知 `stockSpecificBreakoutQuality=0.25` 完整三年年化升至 `24.22%` 但伤害 `Y1/R18-1`，`0.50` 退化到 `21.51%`；成熟宽度门控版本 `stockSpecificMatureBreadthQuality=0.25` 年化升至 `25.04%`，但滚动仅 `4/7`，仍不能作为阶段候选。
- 不继续把 `MACD柱改善 * BOLL收口 * RSI均衡 * MA结构 * 成交额效率 * 前期缺口稳定` 当作全局叠加因子；已知 `indicatorConfluenceQuality=0.25` 年化降至 `22.43%` 且回撤扩大到 `-6.26%`，`0.10` 年化仍只有 `22.52%`，替换诊断在 `Y1/R18-1` 为 `-633.72`。
- 不把弱窗口未买入 top 信号或被行业/策略风险拦截信号当作可直接释放的收益池；已知 `002-weak-window-entry-state-pulse-moneyflow-002` 中 `Y1` 实际买入 5 日前瞻 `+4.29%`，未买 top 为 `-0.97%`，行业状态拦截为 `-0.37%`，策略风险拦截为 `-0.89%`；`R18-1/R18-2` 未买 top 只有均值略高但中位数和胜率不足。
- 不直接全局放宽市场宽度阈值；已知 `002-market-breadth-frontier-001` 中 `soft_all_minus_05` 在 `R18-2` 新增 `22` 天且 Fwd5/Fwd10 为 `+3.18%/+2.79%`，但在 `Y1` 新增 `15` 天且 Fwd5/Fwd10 为 `-0.33%/-1.36%`，不能作为统一门槛替代。
- 不继续当前“单项轻微不足”市场宽度软门控公式；已知 `002-repair-market-breadth-soft-gate-001` 新增 `32` 个软 Risk-On 状态日后，年化从当前观察基准 `22.89%` 降到 `13.55%`，尾部最差破到 `-10.29%`，替换净差 `-32805.53`。
- 不继续市场宽度软门控的 `upPct-only` 窄化公式；已知 `002-repair-market-breadth-up-soft-only-001` 只放宽 `upPct` 到 `40%` 后，年化仅 `11.74%`，替换净差 `-37134.09`，且候选独有 base Risk-On 交易净亏 `-4035.83`，说明新增软日期仍会通过资金/持仓路径挤掉高质量基础机会。
- 不把行业涨跌停结构当作简单拥挤过滤或追涨加分；已知 `002-industry-limit-structure-pulse-moneyflow-window-001` 显示 `R18-1` 候选独有坏样本的跌停类占比不高，且 `R18-4` 正替换并不依赖更高涨停类占比。
- 不把行业资金流持续性做简单门控；已知 `002-industry-moneyflow-persistence-pulse-moneyflow-window-001` 中低持续分既命中 `R18-1` 坏样本，也会错杀 `Y1` 正样本，高持续分也未避开 `600732.SH 爱旭股份`。
- 不用全局历史低开硬过滤或入场振幅扣分修复 `moneyflowMarketSurgeQuality`；已知 `maxPriorGapDown60Pct=0.03` 会把年化降到 `10.19%` 并重新破尾，`entryRange>=7.5%` 扣 `0.5` 分会把年化降到 `17.47%`。
- 不继续把行业聚合主力资金流总额排名当作独立追涨排序项；已知 `industryMoneyflowSumNetRank1=0.25` 替换进全止损交易并显著弱于主线。
- 不继续把 KPL 题材覆盖数量或题材热度当作独立正向排序项；已知 `kplConceptCountRank1=0.25` 年化降至 `20.53%`，且交易替换净差为 `-2525.45`。
- 不继续把东财概念板块涨幅、换手或成交额强度当作独立正向排序项；已知 `002-dc-concept-entry-edge-001` 对 `Y1/R18-1` 无有效覆盖，概念均值强度还出现反向。
- 不把入场日前 30 日巨潮公告关键词当作简单风险过滤；已知 `002-announcement-event-risk-pulse-moneyflow-delta-001` 中完整三年 `R18-1` 基准独有风险覆盖 `40.0%` 高于候选独有 `20.0%`，且担保/减值等关键词也命中正收益候选；`indicatorTurnQuality` 的 `R18-2` 尾部破线也完全不能由公告关键词解释。
- 不把入场前一日 `daily_basic.volume_ratio` 极端值当作全局拥挤扣分；已知 `priorVolumeRatioBasic>3.8` 扣 `0.5` 分时年化降至 `22.19%`，扣 `2.0` 分时年化降至 `19.85%`，虽改善 `Y1/R18-1` 替换但明显伤害后段右尾。
- 不继续简单加大行业资金拥挤-承接背离扣分强度；已知 `sumRank>=0.90` 且 `persistentScore<=0.70` 扣 `0.5` 分把年化提高到 `26.20%`，但扣 `1.0` 分回落到 `25.24%`，且两者仍不能修复 `R18-1`。
- 不把 `moneyflowMarketSurgeQualityRank>=0.70 + rsiBalanceRank>0.50` 的过热扣分当作已通过候选；它把完整年化提高到 `26.94%`，但滚动仍为 `5/7`，且 `Y1` 从 `8.34%` 降到 `7.30%`。
- 不继续缩窄 `surge+rsi` 的阈值或加简单跳空/振幅前置条件；`0.95/0.75`、`0.95/0.75+(gap>=2% or range>=5.5%)`、以及 broad+`gap>=0` 都低于当前最好观察。
- 不把 `stockSpecificMatureBreadthQuality + surge+rsi` 当作修复方向；已知完整年化降至 `25.49%`，替换净差 `-3454.64`，说明会截断后段右尾。
- 不把 `indicatorConfluenceQuality>=0.90 + moneyflowMainNetRank5>=0.60 + gap>=0` 作为独立充分修复；它与 `surge+rsi` 叠加后年化升至 `27.26%`，但仍低于 `30%`，滚动仍失败 `Y1/R18-1`；扣分强度从 `0.5` 提到 `1.0` 不再改变交易。
- 不把 `indicatorConfluenceQuality` 的成交样本负相关直接升级为全局过热扣分；已知 `confluence>=0.70` 且 `moneyflow5>=0` 扣 `0.5` 后年化降至 `24.40%`，完整替换净差 `-10962.03`，主要损害 `R18-4` 右尾 `-11444.87`。
- 不继续在“买入日跳空/振幅高但资金脉冲缺失”上堆惩罚强度；`gap>=3%`、`range>=6.5%`、`moneyflowMarketSurgeQualityRank<=5%` 扣 `1.0` 后年化仅 `27.51%`，只改善后段替换，不解决 `Y1/R18-1` 和 `30%` 年化缺口。
- 不把当前组合因子候选的止盈从 `10%/20%` 提高到 `12%/24%`；已知 `002-repair-composite-profit-12-24-001` 年化降至 `23.51%`、最大回撤扩大到 `-5.50%`，虽然 `Y1/R18-1` 替换净差各 `+1403.23`，但完整三年替换净差为 `-5623.53`，主要损害 `R18-4` 右尾 `-7026.76`。
- 不把当前成交标的财务诊断直接升级为全市场财务质量因子；已对当前完整三年最高观察的 `119` 只成交标的定向同步 `fina_indicator`，point-in-time 覆盖 `123/123` 笔交易，但仍不是候选全集覆盖，且 `002-financial-quality-edge-001` 未发现可直接全局化的稳定财务阈值。
- 不把入场日前一日放量当作独立负因子；只有 `priorVolumeRatioBasic>=2.0` 且 `amountEfficiencyRsiRank<=0.2` 的“放量但成交额/RSI效率弱”组合扣分有效。`priorVolumeRatioBasic>=3.0` 且 `amountEfficiencyRsiRank<=0.3` 扣 `0.5` 会把年化降至 `26.71%`，阈值不能继续放宽。
- 不继续加大“放量但成交额/RSI效率弱”扣分强度；同阈值扣 `1.5` 把年化从 `28.88%` 拉回 `26.35%`，主要损害 `R18-4`，说明 `1.0` 已接近当前口径上限。
- 不把行业 20 日涨幅过热当作全局扣分；`industryReturn20Rank>=0.95` 扣 `0.5` 虽改善 `Y1` 替换 `+5082.55`，但完整替换净差 `-7699.00`、`R18-4 -9484.60`。
- 不复测简单 `stockSpecificBreakoutQuality` 加权；在当前最高观察上下文下加 `0.10` 后年化降至 `24.92%`，完整替换净差 `-14215.28`。
- 不把“BOLL 收口但行业资金不支持”作为全局扣分；`bollSqueezeRank>=0.17` 且 `industryMoneyflowSumNetRank1Rank<=0.34` 扣 `0.5` 年化仅 `27.08%`，完整替换净差 `-7147.63`。
- 不把当前组合因子的止盈从 `10%/20%` 下调到 `8%/16%`；该口径年化仅 `18.61%`、盈亏比 `1.69:1`，且尾部最差收益破到 `-19.31%`。
- 不全局启用简单锁盈/保本/时间退出保护；已知 `lock2_after_5/lock3_after_5/trail50_after_5/be_after_3` 截断右尾，`time5_no_3` 全样本增量仅 `+0.04%`。
- 不把入场时的 MACD/BOLL/RSI/振幅粗状态直接当作退出保护触发；已知 `002-exit-protection-state-trigger-001` 最好条件 `time5_no_3 + MACD柱排名>=80%` 的定向全样本增量仅 `+0.05%`，低于 `+0.20%` 观察线。
- 不从当前最高观察的已成交样本路径均值直接反推全局扣分；`002-run-preentry-path-volume-inefficiency-001` 显示高 `macdHistDelta1d`、高 `entryRangePct`、高 `amountRatio` 在早段可能偏坏，但在 `R18-4` 和完整窗口贡献显著正收益，直接扣分会错杀右尾。
- 不扩大仓位、取消滑点、取消跳空止损或取消跌停延迟来提高收益。

## 必须产物

- 至少 1 个修复策略 run。
- 7 窗口滚动验证。
- 收益前 10、收益后 10 和资本尾部审计。
- 行业暴露复核。
- 排序因子消融或反事实报告。
- 失败样本和不可重复负证据清单。

## Run 命名

不得复用既有 `001..113` 历史 run id。建议使用带阶段前缀的新 id，例如：

- `002-repair-industry-state-001`
- `002-repair-high60-quality-001`
- `002-repair-gap-budget-001`

如果后续采用并行 session，必须先在本阶段 `sessions/` 下认领独立目录，再写入各自证据。
