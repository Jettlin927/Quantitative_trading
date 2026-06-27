# 前端 QuantConnect 式数据呈现重构报告（2026-06-27）

## 结论

本次前端已从深色卡片式驾驶舱改为更接近 QuantConnect 策略页的信息结构：左侧导航、顶部黑色工具栏、策略标题区、指标横条、图表网格、概览指标表、滚动统计表和右侧证据栏。

结论标签：`观察`。本次只调整展示与信息层级，不改变后端策略评估语义，不新增回测执行入口。

## 参考方向

用户给出的参考页是 QuantConnect 策略详情页，核心特征不是装饰，而是：

- 标题区展示策略名、作者、版本和提交日期。
- 首屏横向展示关键指标。
- 中段以权益、回撤、基准、容量等图表为主。
- 下段用密集表格展示概览指标和滚动统计。
- 右栏展示作者、克隆/关注类操作、标签和补充信息。

本仓库对应实现为：

- 左侧导航和顶部工具栏：模拟研究平台的信息架构。
- 指标横条：累计收益、Sharpe、盈亏比、最大回撤、年化收益、闭环交易。
- 图表网格：策略权益、资产销售量、回撤、基准、容量。
- 概览表：订单总数、复利年收益率、回撤、最终权益、纯利、Sharpe、Sortino、胜率、盈亏比等。
- 滚动统计：由当前 run 的权益曲线按月生成 1/3/6/12 个月窗口收益。
- 右栏：作者、三段验证闸门、硬门槛、研究 run 和证据文件。

## 修改文件

- `frontend/src/main.jsx`
- `frontend/src/styles.css`

## 后端语义边界

- 前端优先读取 `/api/research/dashboard?run_limit=160`，并以 `/api/strategy-evaluations`、`/api/strategy-lifecycle`、`/api/research/overview` 和 `/api/research/runs` 作为降级来源。
- 前端不新增回测按钮，不提交回测参数。
- 三段窗口仍由后端判定；旧策略全量 `legacy_reset` 后，页面展示 `新策略研究台`，`2020-2024` 和 `2025-当前` 均为等待新策略证据。
- 指标单位按后端值展示，Sharpe、Sortino、盈亏比不再误格式化为百分比。

## 验证

已运行：

```bash
docker compose run --rm frontend npm run lint
docker compose run --rm frontend npm run build
git diff --check -- frontend/src/main.jsx frontend/src/styles.css
```

均通过。

使用 Playwright 调用本机 Chrome 渲染 `http://localhost:15173/`，验证结果：

- 页面标题：`策略评估驾驶舱`
- H1：`新策略研究台`
- 图表面板：`5`
- 概览表行：`14`
- 滚动统计行：`12`
- 三段验证行：`3`
- 控制台错误：`0`
- Sharpe 百分比误格式化：`false`

## 后续事项

- 若后端补齐美股 sample DB 只读 API，前端可在同一信息结构中新增美股资产、观察池和 sample 持仓栏。
- 若后续选择冻结旧策略，应在右栏或单独列表中展示 `active`、`frozen`、`archived_negative_evidence` 状态，而不是删除历史证据。
