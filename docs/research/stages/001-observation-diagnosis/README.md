# 001-observation-diagnosis

## 阶段状态

`completed`

## 阶段目标

把 `cross-section-strength-risk8` 从观察级候选推进到可解释候选。

当前不追求直接年化 `100%`，而是先完成失败窗口归因，并寻找能够提升正期望的结构性证据。

## 验收口径

本阶段不以新收益达标为验收，而是以失败窗口归因完成为验收。收益修复门槛移交给下一阶段 `002-candidate-repair-30`。

## 起点证据

- 主线观察候选：`105-portfolio-cross-section-risk8-pos135-entry-gap6-netreturn-limitdelay-slip10bp-gap`。
- 滚动窗口失败：`106-portfolio-cross-section-risk8-pos135-entry-gap6-netreturn-window-validation`。
- 风险拦截归因：`110-portfolio-cross-section-risk8-pos135-entry-gap6-netreturn-window-riskreason-validation`。
- 负证据：`107/108/109/111/112/113`。

## 必做诊断

- 失败窗口 vs 通过窗口的行业暴露差异。
- 买入候选 `entryScoreParts` 分布。
- 盈利交易和亏损交易的评分组成差异。
- 被买入日振幅/高开风险过滤器挡掉的票，事后表现如何。
- 排序因子消融：收益来自真正选强，还是来自特定行情偶然有效。

## 禁止事项

- 不继续微调止损或止盈来碰结果。
- 不继续单独放宽高开、振幅、市场宽度或成交量权重。
- 不扩大仓位来冲收益。
- 不取消滑点、跳空止损、跌停延迟等现实成交压力。

## 验收条件

本阶段完成时，必须在 `evidence.md` 回答：

1. 失败窗口里排序选出的票为什么没有正期望？
2. 哪些评分组成在盈利交易和亏损交易之间有稳定差异？
3. 哪些行业暴露导致失败，哪些行业暴露贡献收益？
4. 风险过滤器挡掉的票，事后表现是保护收益还是错杀强票？
5. 下一阶段是否有足够证据进入 `002-candidate-repair-30`？

## 验收结论

本阶段已完成。通过的是失败窗口归因，不代表策略收益达标。

下一阶段：`002-candidate-repair-30`。
