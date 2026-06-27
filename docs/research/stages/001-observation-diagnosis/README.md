# 001-observation-diagnosis

## 阶段状态

`retired_legacy`

## 阶段目标

原目标是把 `cross-section-strength-risk8` 从观察级候选推进到可解释候选。

2026-06-27 用户决定旧策略全部退场，本阶段停止推进。以下内容只作为历史证据保留，不再代表当前研究主线。

## 当前硬门槛

- 年化收益率 `>= 30%`。
- 最大回撤绝对值 `< 10%`。
- 已完成交易盈亏比 `>= 2:1`。
- 滚动窗口不再只通过 `3/7`；至少需要证明失败窗口被结构性修复，而不是单窗口调参。

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

本阶段完成时，必须新增 `evidence.md`，并回答：

1. 失败窗口里排序选出的票为什么没有正期望？
2. 哪些评分组成在盈利交易和亏损交易之间有稳定差异？
3. 哪些行业暴露导致失败，哪些行业暴露贡献收益？
4. 风险过滤器挡掉的票，事后表现是保护收益还是错杀强票？
5. 下一阶段是否有足够证据进入 `002-research-qualified`？
